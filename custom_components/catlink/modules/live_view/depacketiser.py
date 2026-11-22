"""Turns the camera's RTP datagrams into video frames and audio chunks."""

from __future__ import annotations

from dataclasses import dataclass, field

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

PAYLOAD_TYPE = 96
PROFILE_CLEAR = 0xBEDE
PROFILE_ENCRYPTED_HEAD = 0xBBBB
CLOCK_ELEMENT_ID = 6

FLAG_KEYFRAME = 0x01
FLAG_FIRST = 0x02
FLAG_AUDIO = 0x04
CODEC_SHIFT = 3
CODEC_H265 = 1
ZERO_IV = bytes(16)

MAX_MISORDER = 100


@dataclass(frozen=True)
class VideoFrame:
    """A complete access unit."""

    data: bytes
    rtp_timestamp: int
    clock: int | None
    keyframe: bool = False


@dataclass(frozen=True)
class AudioChunk:
    """One audio payload with the device clock from its header extension."""

    data: bytes
    rtp_timestamp: int
    clock: int | None


@dataclass
class _Frame:
    """A frame being assembled."""

    rtp_timestamp: int
    clock: int | None
    keyframe: bool
    parts: list = field(default_factory=list)


def _parse(datagram: bytes) -> dict | None:
    """Split an RTP datagram into its fields, or None if it is not ours."""
    if len(datagram) < 12 or datagram[0] >> 6 != 2:
        return None
    if datagram[1] & 0x7F != PAYLOAD_TYPE:
        return None
    padded = datagram[0] & 0x20
    has_ext = datagram[0] & 0x10
    offset = 12 + 4 * (datagram[0] & 0x0F)
    end = len(datagram)
    if padded:
        end -= datagram[-1]
    profile = None
    extension = b""
    if has_ext:
        if offset + 4 > end:
            return None
        profile = int.from_bytes(datagram[offset : offset + 2], "big")
        length = 4 * int.from_bytes(datagram[offset + 2 : offset + 4], "big")
        extension = datagram[offset + 4 : offset + 4 + length]
        offset += 4 + length
    if offset > end:
        return None
    return {
        "marker": bool(datagram[1] & 0x80),
        "seq": int.from_bytes(datagram[2:4], "big"),
        "timestamp": int.from_bytes(datagram[4:8], "big"),
        "profile": profile,
        "extension": extension,
        "payload": datagram[offset:end],
    }


def _clock(profile: int | None, extension: bytes) -> int | None:
    """Read the device clock element from a one-byte-header extension."""
    if profile != PROFILE_CLEAR:
        return None
    i = 0
    while i < len(extension):
        header = extension[i]
        if header == 0:
            i += 1
            continue
        element_id, size = header >> 4, (header & 0x0F) + 1
        if element_id == 15:
            return None
        if element_id == CLOCK_ELEMENT_ID:
            return int.from_bytes(extension[i + 1 : i + 1 + size], "big")
        i += 1 + size
    return None


def _decrypt(datagram: bytes, key: bytes) -> bytes:
    """Decrypt the whole AES blocks after the first 16 bytes; a partial trailing block stays clear."""
    blocks = (len(datagram) - 16) // 16 * 16
    decryptor = Cipher(algorithms.AES(key), modes.CBC(ZERO_IV)).decryptor()
    return datagram[:16] + decryptor.update(datagram[16 : 16 + blocks]) + datagram[16 + blocks :]


class Depacketiser:
    """Reassembles frames from datagrams fed in arrival order."""

    def __init__(self, key: bytes) -> None:
        """Start with the AES key for encrypted keyframe heads."""
        self._key = key
        self._last_seq: int | None = None
        self._frame: _Frame | None = None

    def feed(self, datagram: bytes) -> list:
        """Consume one datagram and return the frame or audio chunk it completes."""
        packet = _parse(datagram)
        if packet is None:
            return []
        if packet["profile"] == PROFILE_ENCRYPTED_HEAD:
            if len(datagram) < 32:
                return []
            packet = _parse(_decrypt(datagram, self._key))
            if packet is None:
                return []
            packet["profile"] = PROFILE_CLEAR
        if self._last_seq is not None:
            step = (packet["seq"] - self._last_seq) & 0xFFFF
            if not step or step >= 0x10000 - MAX_MISORDER:
                return []
            if step > 1:
                self._frame = None
        self._last_seq = packet["seq"]
        return self._handle(packet)

    def _handle(self, packet: dict) -> list:
        """Fold one parsed packet into the frame in progress."""
        payload = packet["payload"]
        timestamp = packet["timestamp"]
        flags, body = payload[0] if payload else 0, payload[2:]
        clock = _clock(packet["profile"], packet["extension"])
        if flags & FLAG_AUDIO:
            return [AudioChunk(body, timestamp, clock)]
        if flags >> CODEC_SHIFT != CODEC_H265:
            return []
        if flags & FLAG_FIRST:
            self._frame = _Frame(timestamp, clock, bool(flags & FLAG_KEYFRAME))
        elif self._frame is None or self._frame.rtp_timestamp != timestamp:
            return []
        elif self._frame.clock is None:
            self._frame.clock = clock
        self._frame.parts.append(body)
        if not packet["marker"]:
            return []
        frame, self._frame = self._frame, None
        return [
            VideoFrame(
                b"".join(frame.parts), frame.rtp_timestamp, frame.clock, frame.keyframe
            )
        ]
