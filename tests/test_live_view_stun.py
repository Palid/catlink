"""Tests for the live view STUN codec, against the RFC 5769 vectors."""

from __future__ import annotations

import hashlib
import struct

import pytest

from custom_components.catlink.modules.live_view import stun

from .live_view_fakes import ICE_CONTROLLED

PASSWORD = b"VOkJxbRl1RmTxUk/WvJxBt"
TID = bytes.fromhex("b7e7a701bc34d686fa87dfae")
SAMPLE_REQUEST = bytes.fromhex(
    "000100582112a442b7e7a701bc34d686fa87dfae"
    "802200105354554e207465737420636c69656e74"
    "002400046e0001ff"
    "80290008932ff9b151263b36"
    "000600096576746a3a68367659202020"
    "000800149aeaa70cbfd8cb56781ef2b5b2d3f249c1b571a2"
    "80280004e57a3bcf"
)
SAMPLE_IPV4_RESPONSE = bytes.fromhex(
    "0101003c2112a442b7e7a701bc34d686fa87dfae"
    "8022000b7465737420766563746f7220"
    "002000080001a147e112a643"
    "000800142b91f599fd9e90c38c7489f92af9ba53f06be7d7"
    "80280004c07d4c96"
)
SAMPLE_IPV6_RESPONSE = bytes.fromhex(
    "010100482112a442b7e7a701bc34d686fa87dfae"
    "8022000b7465737420766563746f7220"
    "002000140002a1470113a9faa5d3f179bc25f4b5bed2b9d9"
    "00080014a382954e4be67bf11784c97c8292c275bfe3ed41"
    "80280004c8fb0b4c"
)
SAMPLE_LONG_TERM_REQUEST = bytes.fromhex(
    "000100602112a44278ad3433c6ad72c029da412e"
    "00060012e3839ee38388e383aae38383e382afe382b90000"
    "0015001c662f2f3439396b39353464364f4c33346f4c39465354767936347341"
    "0014000b6578616d706c652e6f726700"
    "00080014f67024656dd64a3e02b8e0712e85c9a28ca89666"
)
OUR_ORDER = (
    stun.USERNAME,
    stun.GOOG_NETWORK_INFO,
    stun.ICE_CONTROLLING,
    stun.USE_CANDIDATE,
    stun.PRIORITY,
)
CAMERA_PWD = b"cameraPasswordForTests1"
OUR_PWD = b"ourPasswordForTestsAbcd"


def _order(message: stun.Message) -> tuple[int, ...]:
    """Return the attribute types of a message in wire order."""
    return tuple(attr.type for attr in message.attributes)


def _check(software: str | None = None) -> stun.Message:
    """Build and decode one of our checks, keyed with the camera's pwd."""
    return stun.decode(
        stun.binding_request(
            b"0123456789ab",
            "Ccam1:Aview",
            CAMERA_PWD,
            tiebreaker=bytes.fromhex("0102030405060708"),
            priority=stun.check_priority(2122260223),
            network_info=stun.network_info_value(1, 10),
            software=software,
        )
    )


def test_sample_request_decodes_and_verifies() -> None:
    """RFC 5769 2.1: every attribute decodes and MI and FINGERPRINT verify."""
    message = stun.decode(SAMPLE_REQUEST)

    assert message.type == stun.BINDING_REQUEST
    assert message.transaction_id == TID
    assert _order(message) == (
        stun.SOFTWARE,
        stun.PRIORITY,
        ICE_CONTROLLED,
        stun.USERNAME,
        stun.MESSAGE_INTEGRITY,
        stun.FINGERPRINT,
    )
    assert message.get(stun.SOFTWARE) == b"STUN test client"
    assert message.get(stun.USERNAME) == b"evtj:h6vY"
    assert struct.unpack(">I", message.get(stun.PRIORITY))[0] == 0x6E0001FF
    assert message.get(ICE_CONTROLLED) == bytes.fromhex("932ff9b151263b36")
    assert stun.verify_integrity(message, PASSWORD)
    assert stun.verify_fingerprint(message)


def test_sample_ipv4_response() -> None:
    """RFC 5769 2.2 decodes and verifies, and its mapped address is ours encoded."""
    message = stun.decode(SAMPLE_IPV4_RESPONSE)

    assert message.type == stun.BINDING_SUCCESS
    assert message.get(stun.XOR_MAPPED_ADDRESS) == stun.xor_address_value(
        "192.0.2.1", 32853
    )
    assert message.get(stun.SOFTWARE) == b"test vector"
    assert stun.verify_integrity(message, PASSWORD)
    assert stun.verify_fingerprint(message)


def test_sample_ipv6_response() -> None:
    """RFC 5769 2.3 decodes, and MI and FINGERPRINT verify."""
    message = stun.decode(SAMPLE_IPV6_RESPONSE)

    assert message.type == stun.BINDING_SUCCESS
    assert stun.verify_integrity(message, PASSWORD)
    assert stun.verify_fingerprint(message)


def test_sample_long_term_request() -> None:
    """RFC 5769 2.4: MI verifies with the long-term key, which has no FINGERPRINT."""
    message = stun.decode(SAMPLE_LONG_TERM_REQUEST)
    key = hashlib.md5(
        "マトリックス:example.org:TheMatrIX".encode()
    ).digest()

    assert stun.verify_integrity(message, key)
    assert not stun.verify_fingerprint(message)


def test_wrong_key_and_tampering_fail_verification() -> None:
    """A wrong key fails MI; a flipped byte fails MI and FINGERPRINT."""
    assert not stun.verify_integrity(stun.decode(SAMPLE_REQUEST), b"wrong")
    tampered = bytearray(SAMPLE_REQUEST)
    tampered[24] ^= 0x01
    message = stun.decode(bytes(tampered))

    assert not stun.verify_integrity(message, PASSWORD)
    assert not stun.verify_fingerprint(message)


def test_our_check_attribute_order() -> None:
    """Our checks follow the app's attribute order, SOFTWARE only with a command."""
    assert _order(_check()) == (
        *OUR_ORDER,
        stun.MESSAGE_INTEGRITY,
        stun.FINGERPRINT,
    )
    assert _order(_check('{"type":"request"}')) == (
        *OUR_ORDER,
        stun.SOFTWARE,
        stun.MESSAGE_INTEGRITY,
        stun.FINGERPRINT,
    )


def test_our_check_values() -> None:
    """Our check's values round-trip, with GOOG-NETWORK-INFO packed as id and cost."""
    software = '{"version":"2.0","seq_num":2,"type":"request"}' * 4
    message = _check(software)

    assert message.type == stun.BINDING_REQUEST
    assert message.get(stun.USERNAME) == b"Ccam1:Aview"
    assert message.get(stun.GOOG_NETWORK_INFO) == bytes.fromhex("0001000a")
    assert message.get(stun.ICE_CONTROLLING) == bytes.fromhex("0102030405060708")
    assert message.get(stun.USE_CANDIDATE) == b""
    assert struct.unpack(">I", message.get(stun.PRIORITY))[0] == 1853824767
    assert message.get(stun.SOFTWARE).decode() == software


def test_check_is_keyed_with_the_cameras_pwd() -> None:
    """MI on our check verifies with the camera's pwd only."""
    message = _check()

    assert stun.verify_integrity(message, CAMERA_PWD)
    assert not stun.verify_integrity(message, OUR_PWD)
    assert stun.verify_fingerprint(message)


def test_success_to_the_camera_is_keyed_with_our_pwd() -> None:
    """Our answer to the camera's check maps its address and is keyed with our pwd."""
    message = stun.decode(
        stun.binding_success(b"0123456789ab", "192.168.1.20", 47122, OUR_PWD)
    )

    assert message.type == stun.BINDING_SUCCESS
    assert _order(message) == (
        stun.XOR_MAPPED_ADDRESS,
        stun.MESSAGE_INTEGRITY,
        stun.FINGERPRINT,
    )
    assert message.get(stun.XOR_MAPPED_ADDRESS) == stun.xor_address_value(
        "192.168.1.20", 47122
    )
    assert stun.verify_integrity(message, OUR_PWD)
    assert not stun.verify_integrity(message, CAMERA_PWD)
    assert stun.verify_fingerprint(message)


@pytest.mark.parametrize("size", range(9))
def test_odd_lengths_are_padded(size: int) -> None:
    """Values of every length mod 4 round-trip and still verify."""
    data = stun.encode(
        stun.BINDING_REQUEST,
        b"ABCDEFGHIJKL",
        [(stun.SOFTWARE, b"x" * size), (stun.USERNAME, b"u")],
        key=b"k",
    )
    message = stun.decode(data)

    assert message.get(stun.SOFTWARE) == b"x" * size
    assert message.get(stun.USERNAME) == b"u"
    assert stun.verify_integrity(message, b"k")
    assert stun.verify_fingerprint(message)


def test_fingerprint_must_be_last() -> None:
    """A message without a trailing FINGERPRINT does not verify."""
    data = stun.encode(stun.BINDING_REQUEST, TID, [(stun.USERNAME, b"a:b")], key=b"k")
    unsigned = stun.decode(data[:2] + struct.pack(">H", len(data) - 28) + data[4:-8])

    assert stun.verify_integrity(unsigned, b"k")
    assert not stun.verify_fingerprint(unsigned)


def test_attributes_after_message_integrity_are_ignored() -> None:
    """Only FINGERPRINT counts after MESSAGE-INTEGRITY; a SOFTWARE there is unread."""
    signed = stun.decode(
        stun.encode(stun.BINDING_REQUEST, TID, [(stun.USERNAME, b"a:b")], key=b"k")
    )
    forged = stun.decode(
        stun.encode(
            stun.BINDING_REQUEST,
            TID,
            [
                (stun.USERNAME, b"a:b"),
                (stun.MESSAGE_INTEGRITY, signed.get(stun.MESSAGE_INTEGRITY)),
                (stun.SOFTWARE, b'{"type":"response"}'),
            ],
            key=b"other",
        )
    )

    assert stun.verify_integrity(forged, b"k")
    assert stun.verify_fingerprint(forged)
    assert forged.get(stun.USERNAME) == b"a:b"
    assert forged.get(stun.SOFTWARE) is None


def test_decode_rejects_malformed_messages() -> None:
    """Bad lengths and overrunning attributes raise ValueError."""
    overrun = struct.pack(">HH", stun.USERNAME, 40) + b"x" * 8
    with pytest.raises(ValueError):
        stun.decode(SAMPLE_REQUEST[:-4])
    with pytest.raises(ValueError):
        stun.decode(SAMPLE_REQUEST[:2] + b"\x00\x60" + SAMPLE_REQUEST[4:])
    with pytest.raises(ValueError):
        stun.decode(
            stun.HEADER.pack(stun.BINDING_REQUEST, len(overrun), stun.MAGIC_COOKIE, TID)
            + overrun
        )


def test_demux_tells_stun_from_rtp() -> None:
    """STUN starts with a byte below 4 and carries the cookie; RTP starts at 0x80."""
    rtp = bytes([0x80, 96]) + b"\x00" * 30

    assert stun.is_stun(SAMPLE_REQUEST)
    assert stun.is_stun(SAMPLE_IPV4_RESPONSE)
    assert not stun.is_stun(rtp)
    assert not stun.is_stun(b"\x80" + SAMPLE_REQUEST[1:])
    assert not stun.is_stun(SAMPLE_REQUEST[:4] + b"\x00" * 4 + SAMPLE_REQUEST[8:])
    assert not stun.is_stun(SAMPLE_REQUEST[:19])
