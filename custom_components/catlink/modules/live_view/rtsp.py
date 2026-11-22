"""Loopback RTSP server and the H.265 and PCMA packetisers behind live view."""

from __future__ import annotations

import asyncio
import base64
from collections.abc import Callable
import re
import secrets
import struct
import time

from ...const import _LOGGER

# The payload size WebRTC stacks use: a packet forwarded unchanged still fits a
# 1280-byte IPv6 MTU with SRTP overhead.
PAYLOAD_BUDGET = 1200
AUDIO_CHUNK = 320
SESSION_TIMEOUT = 60
READER_BUFFER_LIMIT = 1 << 20

VIDEO_TRACK = 0
AUDIO_TRACK = 1

_VIDEO_PAYLOAD_TYPE = 96
_AUDIO_PAYLOAD_TYPE = 8
_FU_TYPE = 49
_SPROP = {32: "sprop-vps", 33: "sprop-sps", 34: "sprop-pps"}
_CONTROLS = {"": None, "trackID=0": VIDEO_TRACK, "trackID=1": AUDIO_TRACK}
_PUBLIC = "OPTIONS, DESCRIBE, SETUP, PLAY, TEARDOWN, GET_PARAMETER"
_REASONS = {
    200: "OK",
    400: "Bad Request",
    404: "Not Found",
    461: "Unsupported Transport",
}


def _split_annex_b(access_unit: bytes) -> list[bytes]:
    """Split an Annex-B access unit on its 3- and 4-byte start codes."""
    units = []
    start = access_unit.find(b"\x00\x00\x01")
    while start != -1:
        end = access_unit.find(b"\x00\x00\x01", start + 3)
        unit = access_unit[start + 3 : end if end != -1 else None].rstrip(b"\x00")
        if unit:
            units.append(unit)
        start = end
    return units


def _h265_payloads(units: list[bytes]) -> list[bytes]:
    """Packetise NAL units per RFC 7798: whole if they fit, else as FUs."""
    payloads = []
    step = PAYLOAD_BUDGET - 3
    for unit in units:
        if len(unit) <= PAYLOAD_BUDGET:
            payloads.append(unit)
            continue
        header = bytes(((unit[0] & 0x81) | _FU_TYPE << 1, unit[1]))
        nal_type = unit[0] >> 1 & 0x3F
        for offset in range(2, len(unit), step):
            fu_header = nal_type
            if offset == 2:
                fu_header |= 0x80
            if offset + step >= len(unit):
                fu_header |= 0x40
            payloads.append(header + bytes((fu_header,)) + unit[offset : offset + step])
    return payloads


class _Track:
    """One RTP track's payload type, clock rate, SSRC, sequence and offset."""

    def __init__(self, payload_type: int, ticks_per_ms: int) -> None:
        """Draw a random SSRC, first sequence number and timestamp offset."""
        self.payload_type = payload_type
        self.ticks_per_ms = ticks_per_ms
        self.ssrc = secrets.randbits(32)
        self._sequence = secrets.randbits(16)
        self._offset = secrets.randbits(32)

    def timestamp(self, position_ms: int) -> int:
        """Return the RTP timestamp of a millisecond position on the timeline."""
        return (self._offset + position_ms * self.ticks_per_ms) & 0xFFFFFFFF

    def packet(self, payload: bytes, timestamp: int, marker: bool = False) -> bytes:
        """Return one RTP packet and advance the sequence number."""
        header = struct.pack(
            "!BBHII",
            0x80,
            self.payload_type | marker << 7,
            self._sequence,
            timestamp & 0xFFFFFFFF,
            self.ssrc,
        )
        self._sequence = (self._sequence + 1) & 0xFFFF
        return header + payload


class RtspServer:
    """Minimal RTSP server on loopback, serving one camera's media to its readers."""

    def __init__(self, on_demand: Callable[[bool], None]) -> None:
        """Take the owner told when the first reader comes and the last goes."""
        self._on_demand = on_demand
        self._server: asyncio.Server | None = None
        self._port = 0
        self._token = secrets.token_urlsafe()
        self._tracks = (
            _Track(_VIDEO_PAYLOAD_TYPE, 90),
            _Track(_AUDIO_PAYLOAD_TYPE, 8),
        )
        self._connections: set[_Connection] = set()
        self._slots = 0
        self._readers: list[_Connection] = []
        self._parameter_sets: dict[int, bytes] = {}
        self._origin: int | None = None
        self._latest = 0
        self._pushed_at = 0.0
        self._rebase = False

    @property
    def url(self) -> str:
        """Return the stream's RTSP URL, whose path is a secret token."""
        return f"rtsp://127.0.0.1:{self._port}/{self._token}"

    async def start(self) -> None:
        """Listen on a random loopback port."""
        self._server = await asyncio.start_server(self._accept, "127.0.0.1", 0)
        self._port = self._server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        """Stop listening and close every client, waiting until each has left."""
        if self._server is None:
            return
        self._server.close()
        self._server.abort_clients()
        tasks = [connection.task for connection in self._connections]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await self._server.wait_closed()

    def push_video(self, access_unit: bytes, clock_ms: int, keyframe: bool) -> None:
        """Send one Annex-B access unit, stamped from the camera's clock."""
        if self._rebase and not keyframe:
            return
        units = _split_annex_b(access_unit)
        if keyframe:
            for unit in units:
                nal_type = unit[0] >> 1 & 0x3F
                if nal_type in _SPROP:
                    self._parameter_sets[nal_type] = unit
        track = self._tracks[VIDEO_TRACK]
        timestamp = track.timestamp(self._position(clock_ms))
        readers = self._targets(keyframe)
        if readers:
            payloads = _h265_payloads(units)
            last = len(payloads) - 1
            packets = [
                track.packet(payload, timestamp, index == last)
                for index, payload in enumerate(payloads)
            ]
            self._send(readers, VIDEO_TRACK, packets)

    def push_audio(self, alaw: bytes, clock_ms: int) -> None:
        """Send A-law audio in 320-byte packets, stamped from the camera's clock."""
        if self._rebase:
            return
        track = self._tracks[AUDIO_TRACK]
        timestamp = track.timestamp(self._position(clock_ms))
        readers = self._targets(False)
        if readers:
            packets = [
                track.packet(alaw[offset : offset + AUDIO_CHUNK], timestamp + offset)
                for offset in range(0, len(alaw), AUDIO_CHUNK)
            ]
            self._send(readers, AUDIO_TRACK, packets)

    def discontinuity(self) -> None:
        """Restart every reader at the next keyframe, with timestamps still rising."""
        self._rebase = True
        for reader in self._readers:
            reader.started = False

    def _position(self, clock_ms: int) -> int:
        """Place a camera clock reading on the timeline, re-anchored after a gap."""
        now = time.monotonic()
        if self._origin is None:
            self._origin = clock_ms
        elif self._rebase:
            elapsed = round((now - self._pushed_at) * 1000)
            self._origin = clock_ms - self._latest - max(elapsed, 1)
        self._rebase = False
        self._pushed_at = now
        position = clock_ms - self._origin
        self._latest = max(self._latest, position)
        return position

    def _targets(self, keyframe: bool) -> list[_Connection]:
        """Return the readers to send to, starting the waiting ones on a keyframe."""
        if keyframe:
            for reader in self._readers:
                reader.started = True
        return [reader for reader in self._readers if reader.started]

    def _send(
        self, readers: list[_Connection], track: int, packets: list[bytes]
    ) -> None:
        """Frame the packets for each reader's interleaved channel and send them."""
        for reader in readers:
            if (channel := reader.channels.get(track)) is not None:
                head = struct.pack("!cB", b"$", channel)
                reader.send(b"".join(head + len(p).to_bytes(2) + p for p in packets))

    def _sdp(self) -> str:
        """Return the session description, with parameter sets once known."""
        lines = [
            "v=0",
            "o=- 0 0 IN IP4 127.0.0.1",
            "s=Live view",
            "c=IN IP4 0.0.0.0",
            "t=0 0",
            "a=control:*",
            f"m=video 0 RTP/AVP {_VIDEO_PAYLOAD_TYPE}",
            f"a=rtpmap:{_VIDEO_PAYLOAD_TYPE} H265/90000",
        ]
        if len(self._parameter_sets) == len(_SPROP):
            sprops = ";".join(
                f"{name}={base64.b64encode(self._parameter_sets[nal_type]).decode()}"
                for nal_type, name in _SPROP.items()
            )
            lines.append(f"a=fmtp:{_VIDEO_PAYLOAD_TYPE} {sprops}")
        lines += [
            f"a=control:trackID={VIDEO_TRACK}",
            f"m=audio 0 RTP/AVP {_AUDIO_PAYLOAD_TYPE}",
            f"a=rtpmap:{_AUDIO_PAYLOAD_TYPE} PCMA/8000",
            f"a=control:trackID={AUDIO_TRACK}",
        ]
        return "\r\n".join(lines) + "\r\n"

    def _join(self) -> None:
        """Count a reader; the first resets the timeline and signals the owner."""
        self._slots += 1
        if self._slots == 1:
            # Pushes go on through the idle timer, so a reset when the last reader
            # left would be re-anchored on the Live Session that is about to close.
            self._origin = None
            self._latest = 0
            self._on_demand(True)

    def _part(self) -> None:
        """Count one reader fewer, signalling the owner when none remain."""
        self._slots -= 1
        if not self._slots:
            self._on_demand(False)

    async def _accept(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        """Serve one client connection, unless stop() closed it before it got here."""
        if writer.transport.is_closing():
            return
        connection = _Connection(self, reader, writer)
        self._connections.add(connection)
        try:
            await connection.serve()
        finally:
            self._connections.discard(connection)


class _Connection:
    """One RTSP client: its requests, its session and its share of the stream."""

    def __init__(
        self,
        server: RtspServer,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Bind to the client's socket and the task serving it."""
        self._server = server
        self._reader = reader
        self._writer = writer
        self.task = asyncio.current_task()
        self.channels: dict[int, int] = {}
        self.started = False
        self._controls: dict[int, str] = {}
        self._session: str | None = None
        self._holds_slot = False
        self._playing = False

    async def serve(self) -> None:
        """Answer requests until a body, a timeout or the client leaves."""
        try:
            while True:
                method, url, headers = await self._read()
                if headers.get("content-length", "0") != "0":
                    self._writer.write(self._reply(400, headers.get("cseq")))
                    break
                self._writer.write(self._respond(method, url, headers))
                if method == "TEARDOWN":
                    self._leave()
        except (
            asyncio.IncompleteReadError,
            asyncio.LimitOverrunError,
            ConnectionError,
            TimeoutError,
        ):
            pass
        finally:
            self._leave()
            self._writer.transport.abort()

    def send(self, data: bytes) -> None:
        """Queue media for the client, or drop the client once it falls behind."""
        transport = self._writer.transport
        if transport.get_write_buffer_size() > READER_BUFFER_LIMIT:
            _LOGGER.debug("Dropping a live view reader that fell behind")
            self._stop_playing()
            transport.abort()
        elif not transport.is_closing():
            transport.write(data)

    async def _read(self) -> tuple[str, str, dict[str, str]]:
        """Return the next request's method, URL and headers, past media and CRLFs."""
        async with asyncio.timeout(None if self._playing else SESSION_TIMEOUT):
            while (first := await self._reader.readexactly(1)) in b"$\r\n":
                size = (await self._reader.readexactly(3))[1:] if first == b"$" else b""
                await self._reader.readexactly(int.from_bytes(size))
            head = first + await self._reader.readuntil(b"\r\n\r\n")
        request_line, *lines = head.decode("utf-8", "replace").split("\r\n")
        headers = {}
        for line in filter(None, lines):
            name, _, value = line.partition(":")
            headers[name.strip().lower()] = value.strip()
        method, _, rest = request_line.partition(" ")
        return method, rest.rpartition(" ")[0], headers

    def _respond(self, method: str, url: str, headers: dict[str, str]) -> bytes:
        """Handle one request and return the response to send."""
        cseq = headers.get("cseq")
        server = self._server
        token, _, control = url.split("/", 3)[-1].partition("/")
        control = control.rstrip("/")
        if control not in _CONTROLS or not secrets.compare_digest(
            token.encode(), server._token.encode()
        ):
            return self._reply(404, cseq)
        track = _CONTROLS[control]
        if method in ("OPTIONS", "GET_PARAMETER", "TEARDOWN"):
            return self._reply(200, cseq, {"Public": _PUBLIC})
        if method == "DESCRIBE":
            self._take_slot()
            sdp = server._sdp().encode()
            return self._reply(
                200,
                cseq,
                {
                    "Content-Base": f"{url.rstrip('/')}/",
                    "Content-Type": "application/sdp",
                    "Content-Length": str(len(sdp)),
                },
                sdp,
            )
        if method == "SETUP" and track is not None:
            transport = headers.get("transport", "")
            match = re.search(r"interleaved=(\d{1,3})(?!\d)", transport)
            if match is None or (channel := int(match[1])) > 254:
                return self._reply(461, cseq)
            self._session = self._session or secrets.token_hex(8)
            self.channels[track] = channel
            self._controls[track] = url
            transport = (
                f"RTP/AVP/TCP;unicast;interleaved={channel}-{channel + 1};"
                f"ssrc={server._tracks[track].ssrc:08X}"
            )
            return self._reply(200, cseq, {"Transport": transport})
        if method == "PLAY" and self.channels:
            self._take_slot()
            if not self._playing:
                self._playing = True
                self.started = False
                server._readers.append(self)
            # RTP-Info ties both tracks to one instant, which ffmpeg uses to sync them.
            rtp_info = ",".join(
                f"url={self._controls[track]};"
                f"rtptime={server._tracks[track].timestamp(server._latest)}"
                for track in sorted(self.channels)
            )
            return self._reply(200, cseq, {"Range": "npt=0.000-", "RTP-Info": rtp_info})
        return self._reply(400, cseq)

    def _reply(
        self,
        status: int,
        cseq: str | None,
        headers: dict[str, str] | None = None,
        body: bytes = b"",
    ) -> bytes:
        """Return a response carrying the request's CSeq and the RTSP session."""
        lines = [f"RTSP/1.0 {status} {_REASONS[status]}"]
        if cseq is not None:
            lines.append(f"CSeq: {cseq}")
        if self._session is not None:
            lines.append(f"Session: {self._session};timeout={SESSION_TIMEOUT}")
        lines += [f"{name}: {value}" for name, value in (headers or {}).items()]
        return ("\r\n".join(lines) + "\r\n\r\n").encode() + body

    def _take_slot(self) -> None:
        """Count this client as a reader of the stream."""
        if not self._holds_slot:
            self._holds_slot = True
            self._server._join()

    def _leave(self) -> None:
        """Stop sending and give back the reader slot."""
        self._stop_playing()
        if self._holds_slot:
            self._holds_slot = False
            self._server._part()

    def _stop_playing(self) -> None:
        """Stop sending media to this client."""
        if self._playing:
            self._playing = False
            self._server._readers.remove(self)
