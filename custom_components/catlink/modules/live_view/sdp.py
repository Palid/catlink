"""Offer SDP builder, host candidate lines and the camera's answer parser."""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
import hashlib
import hmac
import secrets
import string
import zlib

ICE_ALPHABET = string.ascii_letters + string.digits + "+/"
CNAME_ALPHABET = string.ascii_letters + string.digits
EXTDATA_VERSION = "0.8.5"
HOST_PRIORITY = 2122260223
NETWORK_ID = 1
NETWORK_COST = 10


def random_string(length: int, alphabet: str = ICE_ALPHABET) -> str:
    """Return a random string drawn from an alphabet."""
    return "".join(secrets.choice(alphabet) for _ in range(length))


@dataclass(frozen=True)
class Offer:
    """An offer SDP and the values the rest of the Live Session needs."""

    sdp: str
    ufrag: str
    pwd: str


@dataclass(frozen=True)
class Candidate:
    """One ICE candidate from the camera."""

    transport: str
    ip: str
    port: int


@dataclass(frozen=True)
class Answer:
    """The camera's answer: ICE credentials and candidates."""

    ufrag: str | None
    pwd: str | None
    candidates: list[Candidate]


def build_offer() -> Offer:
    """Build the offer the camera accepts, ending with its a=extdata check."""
    ufrag = "A" + random_string(4)
    pwd = random_string(24)
    ssrc = secrets.randbits(32)
    rtx_ssrc = secrets.randbits(32)
    while rtx_ssrc == ssrc:
        rtx_ssrc = secrets.randbits(32)
    cname = random_string(16, CNAME_ALPHABET)
    ssrc_group = f"FID {ssrc} {rtx_ssrc}"
    lines = [
        "v=0",
        f"o=- {secrets.randbits(62)} 2 IN IP4 127.0.0.1",
        "s=-",
        "t=0 0",
        "a=group:BUNDLE video",
        "a=msid-semantic: WMS stream_label0",
        "m=video 9 RTP/AVPF 96 97",
        "c=IN IP4 0.0.0.0",
        "a=rtcp:9 IN IP4 0.0.0.0",
        f"a=ice-ufrag:{ufrag}",
        f"a=ice-pwd:{pwd}",
        "a=mid:video",
        "a=sendrecv",
        "a=rtcp-mux",
        "a=rtcp-rsize",
        "a=rtpmap:96 H264/90000",
        "a=rtcp-fb:96 ccm fir",
        "a=rtcp-fb:96 nack",
        "a=fmtp:96 level-asymmetry-allowed=1;packetization-mode=1;"
        "profile-level-id=42e01f",
        "a=rtpmap:97 rtx/90000",
        "a=rtcp-fb:97 ccm fir",
        "a=rtcp-fb:97 nack",
        "a=fmtp:97 apt=96",
        f"a=ssrc-group:{ssrc_group}",
    ]
    for line_ssrc in (ssrc, rtx_ssrc):
        lines += [
            f"a=ssrc:{line_ssrc} cname:{cname}",
            f"a=ssrc:{line_ssrc} msid:stream_label0 video_label0",
            f"a=ssrc:{line_ssrc} mslabel:stream_label0",
            f"a=ssrc:{line_ssrc} label:video_label0",
        ]
    extdata = hmac.new(pwd.encode(), ssrc_group.encode(), hashlib.sha1).hexdigest()
    lines.append(f"a=extdata:{extdata}_{EXTDATA_VERSION}")
    return Offer("".join(line + "\r\n" for line in lines), ufrag, pwd)


def candidate_line(ip: str, port: int, ufrag: str) -> str:
    """Return a host candidate in the form the app trickles."""
    foundation = zlib.crc32(f"host{ip}udp".encode())
    return (
        f"candidate:{foundation} 1 udp {HOST_PRIORITY} {ip} {port} typ host "
        f"generation 0 ufrag {ufrag} network-id {NETWORK_ID} "
        f"network-cost {NETWORK_COST}"
    )


def parse_candidate(text: str) -> Candidate:
    """Parse a candidate line, with or without its a= prefix."""
    text = text.strip().removeprefix("a=")
    if not text.startswith("candidate:"):
        raise ValueError("not a candidate line")
    fields = text[len("candidate:") :].split()
    if len(fields) < 8 or fields[6] != "typ":
        raise ValueError("malformed candidate")
    return Candidate(fields[2].lower(), fields[4], int(fields[5]))


def parse_answer(text: str) -> Answer:
    """Parse the camera's answer: the last ICE credentials, the media-level ones, and every candidate."""
    credentials: dict[str, str] = {}
    candidates = []
    for line in text.splitlines():
        key, _, value = line.strip().partition(":")
        if key in ("a=ice-ufrag", "a=ice-pwd"):
            credentials[key] = value
        elif key == "a=candidate":
            with contextlib.suppress(ValueError):
                candidates.append(parse_candidate(line))
    return Answer(
        credentials.get("a=ice-ufrag"), credentials.get("a=ice-pwd"), candidates
    )
