"""Fakes shared by the live view tests: a loopback camera, Signalling, RTP and RTSP."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
import contextlib
import json
import struct
from types import SimpleNamespace
from unittest.mock import patch

from aiohttp import web
from aiohttp.test_utils import TestServer
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from custom_components.catlink.modules.live_view import ice, sdp, stun
import pytest

LOOPBACK = "127.0.0.1"
PUBLIC = "198.51.100.7"
HOLD_OFF = 0.15
OUR_UFRAG = "Aview"
OUR_PWD = "ourPwdSecret0123456789ab"
CAMERA_UFRAG = "Cam12"
CAMERA_PWD = "cameraPwdSecret012345678"
SESSION = "testSessionSecret0123"
PING_SEQ = 77
ICE_CONTROLLED = 0x8029
CHECK_ORDER = (
    stun.USERNAME,
    stun.GOOG_NETWORK_INFO,
    stun.ICE_CONTROLLING,
    stun.USE_CANDIDATE,
    stun.PRIORITY,
)

SIGNAL_PATH = "/iotx/vision/p2p/request"
TOKEN = "testToken%2Bsecret"
REPORT_SECRET = "reportTokenSecret"
CANDIDATE = (
    "candidate:2063896965 1 udp 2122260223 10.0.0.20 50020 typ host "
    "generation 0 ufrag Cam12 network-id 2 network-cost 10"
)
PUBLIC_CANDIDATE = (
    "candidate:11 1 udp 1686052607 203.0.113.9 50000 typ srflx "
    "raddr 10.0.0.20 rport 50020 generation 0"
)
ANSWER_SDP = "\r\n".join(
    [
        "v=0",
        "o=- 2 2 IN IP4 127.0.0.1",
        "s=-",
        "t=0 0",
        "m=video 50020 RTP/AVPF 96 97",
        "c=IN IP4 10.0.0.20",
        "a=candidate:2063896965 1 udp 2122260223 10.0.0.20 50020 typ host "
        "generation 0 ufrag Cam12",
        "a=ice-ufrag:Cam12",
        f"a=ice-pwd:{CAMERA_PWD}",
        "a=sendonly",
        "a=extdata:0123456789abcdef0123456789abcdef01234567_0.8.5",
        "",
    ]
)

KEY = b"0123456789abcdef"

TCP_VIDEO = "RTP/AVP/TCP;unicast;interleaved=0-1"
TCP_AUDIO = "RTP/AVP/TCP;unicast;interleaved=2-3"


def compact(body: dict) -> str:
    """Return a command body as compact JSON."""
    return json.dumps(body, separators=(",", ":"))


def response(seq: int, message_type: str) -> str:
    """Return a command response body."""
    return compact(
        {
            "version": "2.0",
            "type": "response",
            "seq_num": seq,
            "params": {"messageType": message_type},
        }
    )


def logged(caplog: pytest.LogCaptureFixture) -> str:
    """Return what the integration itself logged."""
    return "\n".join(
        record.getMessage()
        for record in caplog.records
        if record.name.startswith("custom_components.catlink")
    )


def probe_rtp(seq: int, payload_type: int = 96) -> bytes:
    """Return a minimal RTP or RTCP datagram."""
    return struct.pack(">BBHII", 0x80, payload_type, seq, 1000, 0x1234) + b"\x01\x00"


@contextlib.contextmanager
def loopback_ice(loss_timeout: float) -> Iterator[None]:
    """Shorten every agent interval and admit loopback as LAN for the fake camera."""
    is_lan = ice.is_lan_address
    with (
        patch.object(ice, "CHECK_INTERVAL", 0.01),
        patch.object(ice, "COMMAND_RETRY_INTERVAL", 0.05),
        patch.object(ice, "KEEPALIVE_INTERVAL", 0.05),
        patch.object(ice, "KEEPALIVE_RETRY_INTERVAL", 0.01),
        patch.object(ice, "LOSS_TIMEOUT", loss_timeout),
        patch.object(
            ice,
            "is_lan_address",
            lambda address: address == LOOPBACK or is_lan(address),
        ),
    ):
        yield


class FakeCamera(asyncio.DatagramProtocol):
    """Loopback camera: holds ICE, waits for our first check, scripts commands."""

    def __init__(
        self,
        *,
        ack_start_on: int | None = 3,
        our_ufrag: str = OUR_UFRAG,
        our_pwd: str = OUR_PWD,
        ufrag: str = CAMERA_UFRAG,
        pwd: str = CAMERA_PWD,
    ) -> None:
        """Start answering after HOLD_OFF; acknowledge startStreaming on one attempt."""
        self.ack_start_on = ack_start_on
        self.our_ufrag = our_ufrag
        self.our_pwd = our_pwd.encode()
        self.ufrag = ufrag
        self.pwd = pwd.encode()
        self.answering = True
        self.unanswered = 0
        self.loop = asyncio.get_running_loop()
        self.started = self.loop.time()
        self.transport: asyncio.DatagramTransport | None = None
        self.port = 0
        self.viewer: tuple[str, int] | None = None
        self.problems: list[str] = []
        self.held = 0
        self.checks = 0
        self.answers_ok = 0
        self.software: list[str] = []

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        """Keep the transport and its port."""
        self.transport = transport
        self.port = transport.get_extra_info("sockname")[1]

    def candidate(self) -> sdp.Candidate:
        """Return this camera's host candidate."""
        return sdp.Candidate("udp", LOOPBACK, self.port)

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        """Handle the viewer's checks and its answers to ours."""
        message = stun.decode(data)
        if message.type == stun.BINDING_REQUEST:
            self._viewer_check(message, addr)
        elif message.type == stun.BINDING_SUCCESS:
            mapped = message.get(stun.XOR_MAPPED_ADDRESS)
            if (
                stun.verify_integrity(message, self.our_pwd)
                and stun.verify_fingerprint(message)
                and mapped == stun.xor_address_value(LOOPBACK, self.port)
            ):
                self.answers_ok += 1
            else:
                self.problems.append("bad answer to a camera check")

    def streaming_started(self) -> None:
        """Send three RTP packets and one RTCP packet once startStreaming is acked."""
        for seq in range(3):
            self.transport.sendto(probe_rtp(seq), self.viewer)
        self.transport.sendto(probe_rtp(9, payload_type=200), self.viewer)

    def streaming_stopped(self) -> None:
        """React to stopStreaming; this camera has no media left to stop."""

    def _viewer_check(self, message: stun.Message, addr: tuple[str, int]) -> None:
        """Verify a check, hold ICE off, then answer it unless told to stay quiet."""
        order = tuple(attr.type for attr in message.attributes)
        software = message.get(stun.SOFTWARE)
        expected = (*CHECK_ORDER, *((stun.SOFTWARE,) if software else ()))
        if (
            message.get(stun.USERNAME) != f"{self.ufrag}:{self.our_ufrag}".encode()
            or not stun.verify_integrity(message, self.pwd)
            or not stun.verify_fingerprint(message)
            or order != (*expected, stun.MESSAGE_INTEGRITY, stun.FINGERPRINT)
            or message.get(stun.GOOG_NETWORK_INFO) != bytes.fromhex("0001000a")
        ):
            self.problems.append("viewer check rejected")
            return
        self.checks += 1
        if self.loop.time() - self.started < HOLD_OFF:
            self.held += 1
            return
        if not self.answering:
            return
        if self.unanswered and software is None:
            self.unanswered -= 1
            return
        self.viewer = addr
        self.transport.sendto(
            stun.binding_success(message.transaction_id, *addr, self.pwd), addr
        )
        if software is not None:
            self._command(software.decode())

    def _command(self, text: str) -> None:
        """Acknowledge one startStreaming and every stopStreaming; not stopP2p."""
        self.software.append(text)
        body = json.loads(text)
        message_type = body["params"]["messageType"]
        if body["type"] == "response":
            return
        if message_type == "startStreaming":
            attempts = sum('"startStreaming"' in sent for sent in self.software)
            if attempts == self.ack_start_on:
                self._send_check(response(2, "startStreaming"))
                self._send_check(
                    compact(
                        {
                            "version": "2.0",
                            "type": "request",
                            "seq_num": PING_SEQ,
                            "params": {"messageType": "cameraPing"},
                        }
                    )
                )
                self.streaming_started()
        elif message_type == "stopStreaming":
            self._send_check(response(body["seq_num"], "stopStreaming"))
            self.streaming_stopped()

    def _send_check(self, software: str) -> None:
        """Send the camera's own check to the address our check came from."""
        self.transport.sendto(
            stun.encode(
                stun.BINDING_REQUEST,
                sdp.random_string(12).encode(),
                [
                    (stun.USERNAME, f"{self.our_ufrag}:{self.ufrag}".encode()),
                    (stun.GOOG_NETWORK_INFO, stun.network_info_value(2, 10)),
                    (ICE_CONTROLLED, bytes(8)),
                    (stun.PRIORITY, struct.pack(">I", 1)),
                    (stun.SOFTWARE, software.encode()),
                ],
                key=self.our_pwd,
            ),
            self.viewer,
        )


def message(params: dict) -> str:
    """Return a camera-side Signalling message."""
    return json.dumps({"params": params, "version": "1.0.1"})


class FakeSignallingServer:
    """Scripted Signalling server: candidates before the answer, plus noise."""

    def __init__(self, answer_sdp: str | None = ANSWER_SDP) -> None:
        """Route the signalling path with an Expect handler that accepts 101."""
        self.answer_sdp = answer_sdp
        self.expect_headers: list[str | None] = []
        self.query_strings: list[str] = []
        self.received: list[str] = []
        self.closed = asyncio.Event()
        app = web.Application()
        app.router.add_get(SIGNAL_PATH, self._handle, expect_handler=self._expect)
        self.server = TestServer(app, host="127.0.0.1")

    def url(self, path: str = SIGNAL_PATH) -> str:
        """Return the signalUrl the cloud would hand out for this server."""
        query = f"token={TOKEN}&session={SESSION}"
        return f"ws://127.0.0.1:{self.server.port}{path}?{query}"

    async def replies(self, offer_sdp: str) -> list[str | float]:
        """Return the camera's messages to an offer; a float is a pause in seconds."""
        replies: list[str | float] = [
            message(
                {
                    "candidate": CANDIDATE,
                    "messageType": "candidate",
                    "sdpMLineIndex": 0,
                    "sdpMid": "video",
                    "sessionId": SESSION,
                }
            ),
            message({"candidate": PUBLIC_CANDIDATE, "messageType": "candidate"}),
            message({"candidate": "candidate:broken", "messageType": "candidate"}),
            message(
                {
                    "messageType": "p2pReportToken",
                    "accessKeySecret": REPORT_SECRET,
                    "securityToken": REPORT_SECRET,
                }
            ),
            message({"messageType": "somethingNew"}),
            "not json",
        ]
        if self.answer_sdp is not None:
            replies.append(
                message(
                    {
                        "clientType": "dev",
                        "messageType": "answerSdp",
                        "sdp": self.answer_sdp,
                        "sessionId": SESSION,
                        "version": "1.3.2",
                    }
                )
            )
        return replies

    async def _expect(self, request: web.Request) -> None:
        """Record the Expect header aiohttp's default handler would refuse."""
        self.expect_headers.append(request.headers.get("Expect"))

    async def _handle(self, request: web.Request) -> web.WebSocketResponse:
        """Answer an offer with the camera's script, then wait for the close."""
        self.query_strings.append(request.query_string)
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        async for received in ws:
            self.received.append(received.data)
            params = json.loads(received.data)["params"]
            if params["messageType"] != "offerSdp":
                continue
            for reply in await self.replies(params["sdp"]):
                if isinstance(reply, float):
                    await asyncio.sleep(reply)
                else:
                    await ws.send_str(reply)
            if self.answer_sdp is None:
                break
        self.closed.set()
        return ws


def clock_ext(clock):
    """Return a one-byte-header extension carrying the device clock."""
    return b"\xbe\xde\x00\x03" + b"\x67" + struct.pack(">Q", clock) + b"\x00\x00\x00"


def packet(seq, ts, payload, inner=b"", marker=False, clock=1000, ext=None, csrc=0, pad=0, ssrc=0x01020304):
    """Build a camera datagram: RTP header, clock extension, inner header, payload."""
    if ext is None:
        ext = clock_ext(clock)
    first = 0x80 | (0x10 if ext else 0) | (0x20 if pad else 0) | csrc
    head = struct.pack(">BBHII", first, (0x80 if marker else 0) | 96, seq & 0xFFFF, ts, ssrc)
    head += b"\xaa\xbb\xcc\xdd" * csrc
    body = inner + payload
    if pad:
        body += b"\x00" * (pad - 1) + bytes([pad])
    return head + ext + body


def encrypted(seq, ts, payload, inner, key=KEY, **kwargs):
    """Build a datagram with its body encrypted the way the camera does."""
    plain = bytearray(packet(seq, ts, payload, inner, **kwargs))
    blocks = (len(plain) - 16) // 16 * 16
    enc = Cipher(algorithms.AES(key), modes.CBC(bytes(16))).encryptor()
    plain[16:16 + blocks] = enc.update(bytes(plain[16:16 + blocks]))
    plain[12:14] = b"\xbb\xbb"
    return bytes(plain)


def nal(nal_type: int, size: int) -> bytes:
    """Return a NAL unit of a type and total size whose body has no zero bytes."""
    return bytes((nal_type << 1, 0x01)) + bytes(i % 251 + 1 for i in range(size - 2))


VPS = nal(32, 12)
SPS = nal(33, 20)
PPS = nal(34, 6)


def annex_b(*units: bytes) -> bytes:
    """Join NAL units into an access unit, alternating 4- and 3-byte start codes."""
    return b"".join(
        (b"\x00\x00\x00\x01" if index % 2 == 0 else b"\x00\x00\x01") + unit
        for index, unit in enumerate(units)
    )


def keyframe(size: int = 64) -> bytes:
    """Return an IDR access unit led by its VPS, SPS and PPS."""
    return annex_b(VPS, SPS, PPS, nal(19, size))


def p_frame(size: int = 32) -> bytes:
    """Return a single-slice non-key access unit."""
    return annex_b(nal(1, size))


def rtp(packet: bytes) -> SimpleNamespace:
    """Decode an RTP packet's fixed header."""
    first, second, sequence, timestamp, ssrc = struct.unpack("!BBHII", packet[:12])
    return SimpleNamespace(
        version=first >> 6,
        marker=bool(second & 0x80),
        payload_type=second & 0x7F,
        sequence=sequence,
        timestamp=timestamp,
        ssrc=ssrc,
        payload=packet[12:],
    )


class Client:
    """Scripted RTSP client on one loopback connection."""

    def __init__(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        """Wrap an open connection."""
        self.reader = reader
        self.writer = writer
        self.cseq = 0
        self.session: str | None = None
        self.queued: list[tuple[int, bytes]] = []
        self.last: SimpleNamespace | None = None

    async def request(
        self, method: str, url: str, headers: dict[str, str] | None = None
    ) -> SimpleNamespace:
        """Send a request and return its response, queueing media that comes first."""
        self.cseq += 1
        lines = [f"{method} {url} RTSP/1.0", f"CSeq: {self.cseq}"]
        if self.session:
            lines.append(f"Session: {self.session}")
        lines += [f"{name}: {value}" for name, value in (headers or {}).items()]
        self.writer.write(("\r\n".join(lines) + "\r\n\r\n").encode())
        while (first := await self.reader.readexactly(1)) == b"$":
            self.queued.append(await self._interleaved())
        head = first + await self.reader.readuntil(b"\r\n\r\n")
        status_line, *header_lines = head.decode().split("\r\n")
        response_headers = {}
        for line in filter(None, header_lines):
            name, _, value = line.partition(":")
            response_headers[name.strip().lower()] = value.strip()
        body = await self.reader.readexactly(
            int(response_headers.get("content-length", 0))
        )
        assert response_headers.get("cseq") == str(self.cseq)
        if "session" in response_headers:
            self.session = response_headers["session"].partition(";")[0]
        self.last = SimpleNamespace(
            status=int(status_line.split(" ")[1]),
            headers=response_headers,
            body=body,
            raw=head + body,
        )
        return self.last

    async def packet(self) -> tuple[int, bytes]:
        """Return the next interleaved packet as (channel, RTP packet)."""
        if self.queued:
            return self.queued.pop(0)
        assert await self.reader.readexactly(1) == b"$"
        return await self._interleaved()

    async def packets(self, count: int) -> list[tuple[int, bytes]]:
        """Return the next few interleaved packets."""
        return [await self.packet() for _ in range(count)]

    async def _interleaved(self) -> tuple[int, bytes]:
        """Read the rest of one interleaved frame."""
        channel, length = struct.unpack("!BH", await self.reader.readexactly(3))
        return channel, await self.reader.readexactly(length)


async def play(connect, url: str) -> Client:
    """Connect and run DESCRIBE, SETUP for both tracks and PLAY."""
    client = await connect(url)
    assert (await client.request("DESCRIBE", url)).status == 200
    for track, transport in ((0, TCP_VIDEO), (1, TCP_AUDIO)):
        setup = await client.request(
            "SETUP", f"{url}/trackID={track}", {"Transport": transport}
        )
        assert setup.status == 200
    assert (await client.request("PLAY", f"{url}/")).status == 200
    return client


async def until(predicate, timeout: float = 2) -> None:
    """Wait for a condition the code under test reaches on its own."""
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.01)
