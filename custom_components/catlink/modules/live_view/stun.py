"""STUN codec for the camera's ICE checks and the commands they carry."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import ipaddress
import struct
import zlib

MAGIC_COOKIE = 0x2112A442
FINGERPRINT_XOR = 0x5354554E
HEADER = struct.Struct(">HHI12s")
COOKIE_BYTES = struct.pack(">I", MAGIC_COOKIE)

BINDING_REQUEST = 0x0001
BINDING_SUCCESS = 0x0101

USERNAME = 0x0006
MESSAGE_INTEGRITY = 0x0008
XOR_MAPPED_ADDRESS = 0x0020
PRIORITY = 0x0024
USE_CANDIDATE = 0x0025
SOFTWARE = 0x8022
FINGERPRINT = 0x8028
ICE_CONTROLLING = 0x802A
GOOG_NETWORK_INFO = 0xC057

TYPE_PREFERENCE_PRFLX = 110


@dataclass(frozen=True)
class Attribute:
    """One decoded attribute and its offset in the message."""

    type: int
    value: bytes
    offset: int


@dataclass(frozen=True)
class Message:
    """A decoded STUN message that keeps its raw bytes for verification."""

    type: int
    transaction_id: bytes
    attributes: tuple[Attribute, ...]
    raw: bytes

    def get(self, attr_type: int) -> bytes | None:
        """Return the first attribute of a type up to MESSAGE-INTEGRITY, if present."""
        for attr in self.attributes:
            if attr.type == attr_type:
                return attr.value
            if attr.type == MESSAGE_INTEGRITY:
                return None
        return None


def is_stun(data: bytes) -> bool:
    """Return whether a datagram on the shared socket is STUN rather than RTP."""
    return len(data) >= 20 and data[0] < 4 and data[4:8] == COOKIE_BYTES


def decode(data: bytes) -> Message:
    """Decode a STUN message, raising ValueError when it is malformed."""
    if not is_stun(data):
        raise ValueError("not a STUN message")
    msg_type, length, _, transaction_id = HEADER.unpack_from(data)
    if length % 4 or length != len(data) - 20:
        raise ValueError("bad STUN length")
    attributes = []
    offset = 20
    while offset < len(data):
        if offset + 4 > len(data):
            raise ValueError("truncated STUN attribute header")
        attr_type, attr_len = struct.unpack_from(">HH", data, offset)
        end = offset + 4 + attr_len
        if end > len(data):
            raise ValueError("STUN attribute overruns the message")
        attributes.append(Attribute(attr_type, bytes(data[offset + 4 : end]), offset))
        offset = end + (-attr_len % 4)
    return Message(msg_type, transaction_id, tuple(attributes), bytes(data))


def _pack_attribute(attr_type: int, value: bytes) -> bytes:
    """Pack one attribute, zero-padded to a 4-byte boundary."""
    return struct.pack(">HH", attr_type, len(value)) + value + bytes(-len(value) % 4)


def encode(
    msg_type: int,
    transaction_id: bytes,
    attributes: list[tuple[int, bytes]],
    *,
    key: bytes,
) -> bytes:
    """Encode a message, appending MESSAGE-INTEGRITY and FINGERPRINT."""
    body = b"".join(_pack_attribute(t, v) for t, v in attributes)
    header = HEADER.pack(msg_type, len(body) + 24, MAGIC_COOKIE, transaction_id)
    mac = hmac.new(key, header + body, hashlib.sha1).digest()
    body += _pack_attribute(MESSAGE_INTEGRITY, mac)
    header = HEADER.pack(msg_type, len(body) + 8, MAGIC_COOKIE, transaction_id)
    crc = (zlib.crc32(header + body) ^ FINGERPRINT_XOR) & 0xFFFFFFFF
    body += _pack_attribute(FINGERPRINT, struct.pack(">I", crc))
    return HEADER.pack(msg_type, len(body), MAGIC_COOKIE, transaction_id) + body


def verify_integrity(message: Message, key: bytes) -> bool:
    """Return whether the message's MESSAGE-INTEGRITY matches the key."""
    for attr in message.attributes:
        if attr.type == MESSAGE_INTEGRITY:
            header = HEADER.pack(
                message.type, attr.offset + 4, MAGIC_COOKIE, message.transaction_id
            )
            expected = hmac.new(
                key, header + message.raw[20 : attr.offset], hashlib.sha1
            ).digest()
            return hmac.compare_digest(expected, attr.value)
    return False


def verify_fingerprint(message: Message) -> bool:
    """Return whether the message ends with a correct FINGERPRINT."""
    if not message.attributes or message.attributes[-1].type != FINGERPRINT:
        return False
    last = message.attributes[-1]
    crc = (zlib.crc32(message.raw[: last.offset]) ^ FINGERPRINT_XOR) & 0xFFFFFFFF
    return last.value == struct.pack(">I", crc)


def xor_address_value(ip: str, port: int) -> bytes:
    """Return an XOR-MAPPED-ADDRESS value for an IPv4 address."""
    xaddr = int(ipaddress.IPv4Address(ip)) ^ MAGIC_COOKIE
    return struct.pack(">BBHI", 0, 1, port ^ (MAGIC_COOKIE >> 16), xaddr)


def network_info_value(network_id: int, cost: int) -> bytes:
    """Return a GOOG-NETWORK-INFO value."""
    return struct.pack(">I", (network_id << 16) | cost)


def check_priority(local_priority: int) -> int:
    """Return the PRIORITY a check carries: peer-reflexive type preference."""
    return (TYPE_PREFERENCE_PRFLX << 24) | (local_priority & 0xFFFFFF)


def binding_request(
    transaction_id: bytes,
    username: str,
    key: bytes,
    *,
    tiebreaker: bytes,
    priority: int,
    network_info: bytes,
    software: str | None = None,
) -> bytes:
    """Build a controlling check in the attribute order the camera accepts."""
    attributes = [
        (USERNAME, username.encode()),
        (GOOG_NETWORK_INFO, network_info),
        (ICE_CONTROLLING, tiebreaker),
        (USE_CANDIDATE, b""),
        (PRIORITY, struct.pack(">I", priority)),
    ]
    if software is not None:
        attributes.append((SOFTWARE, software.encode()))
    return encode(BINDING_REQUEST, transaction_id, attributes, key=key)


def binding_success(transaction_id: bytes, ip: str, port: int, key: bytes) -> bytes:
    """Build the success response to a check from an address."""
    return encode(
        BINDING_SUCCESS,
        transaction_id,
        [(XOR_MAPPED_ADDRESS, xor_address_value(ip, port))],
        key=key,
    )
