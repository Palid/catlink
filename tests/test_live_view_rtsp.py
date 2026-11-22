"""Tests for the live view RTSP server and its H.265 and PCMA packetisers."""

from __future__ import annotations

import asyncio
import base64
import json
import re
import shutil
import socket
from urllib.parse import urlsplit

from custom_components.catlink.modules.live_view import rtsp
from custom_components.catlink.modules.live_view.rtsp import PAYLOAD_BUDGET, RtspServer
import pytest

from .live_view_fakes import (
    PPS,
    SPS,
    TCP_AUDIO,
    TCP_VIDEO,
    VPS,
    Client,
    annex_b,
    keyframe,
    nal,
    p_frame,
    play,
    rtp,
    until,
)

FRAME_MS = 1000 / 22.5
FU = 49
RTCP = b"$\x01\x00\x08\x80\xc9\x00\x01" + bytes(4)


def later(newer: int, older: int) -> int:
    """Return how far a 32-bit RTP timestamp is ahead of another, signed."""
    delta = (newer - older) & 0xFFFFFFFF
    return delta - (1 << 32) if delta >= 1 << 31 else delta


def rtptimes(client: Client) -> list[int]:
    """Return the rtptime of each track in the client's last RTP-Info."""
    return [
        int(entry.rpartition("rtptime=")[2])
        for entry in client.last.headers["rtp-info"].split(",")
    ]


def raw(method: str, url: str, cseq: int, *lines: str) -> bytes:
    """Return one request as a client puts it on the wire."""
    head = "\r\n".join([f"{method} {url} RTSP/1.0", f"CSeq: {cseq}", *lines])
    return f"{head}\r\n\r\n".encode()


async def answer(client: Client) -> tuple[int, dict[str, str]]:
    """Read one response to a request written with raw(), queueing media before it."""
    while (first := await client.reader.readexactly(1)) == b"$":
        client.queued.append(await client._interleaved())
    head = first + await client.reader.readuntil(b"\r\n\r\n")
    status_line, *lines = head.decode().split("\r\n")
    headers = {}
    for line in filter(None, lines):
        name, _, value = line.partition(":")
        headers[name.strip().lower()] = value.strip()
    await client.reader.readexactly(int(headers.get("content-length", 0)))
    return int(status_line.split(" ")[1]), headers


@pytest.fixture
def signals() -> list[bool]:
    """Collect the demand signals the server gives its owner."""
    return []


@pytest.fixture
async def server(socket_enabled: None, signals: list[bool]):
    """Run an RTSP server for one test."""
    server = RtspServer(signals.append)
    await server.start()
    yield server
    await server.stop()


async def test_full_method_sequence(server, signals, connect) -> None:
    """OPTIONS, DESCRIBE, SETUP twice, PLAY, media, GET_PARAMETER and TEARDOWN."""
    url = server.url
    assert re.fullmatch(r"rtsp://127\.0\.0\.1:\d+/[\w-]{43}", url)
    client = await connect(url)

    options = await client.request("OPTIONS", url)
    assert options.status == 200
    assert set(options.headers["public"].split(", ")) == {
        "OPTIONS",
        "DESCRIBE",
        "SETUP",
        "PLAY",
        "TEARDOWN",
        "GET_PARAMETER",
    }
    assert signals == []

    describe = await client.request("DESCRIBE", url, {"Accept": "application/sdp"})
    assert describe.status == 200
    assert signals == [True]
    assert describe.headers["content-type"] == "application/sdp"
    assert describe.headers["content-base"] == f"{url}/"
    sdp = describe.body.decode()
    for line in (
        "m=video 0 RTP/AVP 96",
        "a=rtpmap:96 H265/90000",
        "a=control:trackID=0",
        "m=audio 0 RTP/AVP 8",
        "a=rtpmap:8 PCMA/8000",
        "a=control:trackID=1",
    ):
        assert f"\r\n{line}\r\n" in sdp
    assert "sprop" not in sdp

    video = await client.request("SETUP", f"{url}/trackID=0", {"Transport": TCP_VIDEO})
    assert video.status == 200
    assert re.fullmatch(r"[0-9a-f]{16};timeout=60", video.headers["session"])
    video_ssrc = re.fullmatch(
        r"RTP/AVP/TCP;unicast;interleaved=0-1;ssrc=([0-9A-F]{8})",
        video.headers["transport"],
    ).group(1)
    session = client.session
    audio = await client.request("SETUP", f"{url}/trackID=1", {"Transport": TCP_AUDIO})
    assert audio.status == 200
    assert client.session == session
    assert "interleaved=2-3" in audio.headers["transport"]

    play_response = await client.request("PLAY", f"{url}/")
    assert play_response.status == 200
    assert play_response.headers["session"].startswith(session)
    assert re.fullmatch(
        rf"url={re.escape(url)}/trackID=0;rtptime=\d+,"
        rf"url={re.escape(url)}/trackID=1;rtptime=\d+",
        play_response.headers["rtp-info"],
    )

    server.push_audio(b"\x55" * 320, 1000)
    server.push_video(keyframe(), 1000, True)
    server.push_audio(b"\xd5" * 320, 1040)
    received = await client.packets(5)
    assert [channel for channel, _ in received] == [0, 0, 0, 0, 2]
    headers = [rtp(packet) for _, packet in received]
    assert [h.payload for h in headers] == [VPS, SPS, PPS, nal(19, 64), b"\xd5" * 320]
    assert {h.version for h in headers} == {2}
    assert [h.payload_type for h in headers] == [96, 96, 96, 96, 8]
    assert {f"{h.ssrc:08X}" for h in headers[:4]} == {video_ssrc}

    keepalive = await client.request("GET_PARAMETER", f"{url}/")
    assert keepalive.status == 200
    assert client.queued == []
    teardown = await client.request("TEARDOWN", f"{url}/")
    assert teardown.status == 200
    assert signals == [True, False]
    client.writer.close()


async def test_unsupported_requests_are_refused(server, signals, connect) -> None:
    """UDP or an unusable channel gets 461, other unsupported requests 400."""
    url = server.url
    client = await connect(url)
    for channel in ("x", "", "-1", "255", "1" * 10):
        transport = f"RTP/AVP/TCP;unicast;interleaved={channel}"
        response = await client.request(
            "SETUP", f"{url}/trackID=0", {"Transport": transport}
        )
        assert response.status == 461
    for transport in (
        "RTP/AVP;unicast;client_port=5000-5001",
        "RTP/AVP/UDP;unicast;client_port=5000-5001",
    ):
        response = await client.request(
            "SETUP", f"{url}/trackID=0", {"Transport": transport}
        )
        assert response.status == 461
    assert (await client.request("SETUP", url, {"Transport": TCP_VIDEO})).status == 400
    assert (await client.request("RECORD", url)).status == 400
    assert client.session is None
    assert signals == []

    setup = await client.request("SETUP", f"{url}/trackID=0", {"Transport": TCP_VIDEO})
    assert setup.status == 200


async def test_wrong_path_is_not_found(server, signals, connect) -> None:
    """Any path but the token gets a bare 404 and takes no reader slot."""
    base, _, token = server.url.rpartition("/")
    wrong = f"{base}/{'A' * len(token)}"
    client = await connect(server.url)
    for method, url in (
        ("OPTIONS", wrong),
        ("DESCRIBE", wrong),
        ("SETUP", f"{wrong}/trackID=0"),
        ("PLAY", wrong),
        ("DESCRIBE", f"{base}/"),
        ("DESCRIBE", f"{server.url}/trackID=7"),
        ("DESCRIBE", f"{base}/{token[:-1]}"),
        ("DESCRIBE", f"{base}/{token}\u00e9"),
        ("OPTIONS", "rtsp://[abc/x"),
    ):
        response = await client.request(method, url, {"Transport": TCP_VIDEO})
        assert response.status == 404
        assert response.body == b""
        assert set(response.headers) == {"cseq"}
        assert token.encode() not in response.raw
    assert signals == []


async def test_two_readers_share_one_stream(server, signals, connect) -> None:
    """Both readers get the media; the demand signals fire once each."""
    first = await play(connect, server.url)
    second = await play(connect, server.url)
    assert signals == [True]

    server.push_video(keyframe(), 0, True)
    for client in (first, second):
        payloads = [rtp(packet).payload for _, packet in await client.packets(4)]
        assert payloads == [VPS, SPS, PPS, nal(19, 64)]

    assert (await second.request("TEARDOWN", f"{server.url}/")).status == 200
    assert signals == [True]
    first.writer.close()
    await until(lambda: len(signals) == 2)
    await asyncio.sleep(0.05)
    assert signals == [True, False]


async def test_describe_without_play_holds_a_slot(
    server, signals, connect, monkeypatch
) -> None:
    """A lone DESCRIBE holds a slot until its socket closes or its session expires."""
    monkeypatch.setattr(rtsp, "SESSION_TIMEOUT", 0.2)

    closing = await connect(server.url)
    await closing.request("DESCRIBE", server.url)
    assert signals == [True]
    closing.writer.close()
    await until(lambda: len(signals) == 2)
    assert signals == [True, False]

    idle = await connect(server.url)
    await idle.request("DESCRIBE", server.url)
    assert signals == [True, False, True]
    await until(lambda: len(signals) == 4)
    assert signals == [True, False, True, False]
    assert await idle.reader.read() == b""


async def test_playing_reader_outlives_the_session_timeout(
    server, signals, connect, monkeypatch
) -> None:
    """A playing TCP reader is tied to its socket, not to RTSP keepalives."""
    monkeypatch.setattr(rtsp, "SESSION_TIMEOUT", 0.1)
    client = await play(connect, server.url)
    await asyncio.sleep(0.3)
    server.push_video(keyframe(), 0, True)
    assert rtp((await client.packet())[1]).payload == VPS
    assert signals == [True]


async def test_joining_mid_stream_waits_for_a_keyframe(server, connect) -> None:
    """A reader gets nothing until a keyframe, which leads with VPS, SPS and PPS."""
    early = await play(connect, server.url)
    server.push_video(p_frame(), 0, False)
    server.push_audio(b"\x01" * 320, 0)
    server.push_video(keyframe(), 44, True)
    server.push_video(p_frame(), 89, False)
    late = await play(connect, server.url)
    server.push_video(p_frame(), 133, False)
    server.push_audio(b"\x02" * 320, 140)
    server.push_video(keyframe(), 178, True)
    server.push_audio(b"\x03" * 320, 180)

    key = [VPS, SPS, PPS, nal(19, 64)]
    early_payloads = [rtp(packet).payload for _, packet in await early.packets(12)]
    assert early_payloads == [
        *key,
        nal(1, 32),
        nal(1, 32),
        b"\x02" * 320,
        *key,
        b"\x03" * 320,
    ]
    late_packets = [rtp(packet) for _, packet in await late.packets(5)]
    assert [h.payload for h in late_packets] == [*key, b"\x03" * 320]
    for client in (early, late):
        await client.request("GET_PARAMETER", f"{server.url}/")
        assert client.queued == []


async def test_fragmentation_units_at_the_payload_budget(server, connect) -> None:
    """NAL units up to the budget go whole; larger ones become FUs with S and E bits."""
    client = await play(connect, server.url)
    under = nal(19, PAYLOAD_BUDGET - 1)
    at = nal(19, PAYLOAD_BUDGET)
    over = nal(19, PAYLOAD_BUDGET + 1)
    large = nal(19, 3 * PAYLOAD_BUDGET + 5)
    server.push_video(annex_b(VPS, SPS, PPS, under, at, over), 0, True)
    server.push_video(annex_b(nal(1, 40), large), 44, False)

    first = [rtp(packet) for _, packet in await client.packets(7)]
    second = [rtp(packet) for _, packet in await client.packets(5)]
    for access_unit in (first, second):
        markers = [h.marker for h in access_unit]
        assert markers == [False] * (len(access_unit) - 1) + [True]
        assert len({h.timestamp for h in access_unit}) == 1
        assert all(len(h.payload) <= PAYLOAD_BUDGET for h in access_unit)
    sequences = [h.sequence for h in first + second]
    assert sequences == [(sequences[0] + i) & 0xFFFF for i in range(len(sequences))]

    assert [h.payload for h in first[:5]] == [VPS, SPS, PPS, under, at]
    assert len(first[4].payload) == PAYLOAD_BUDGET
    assert second[0].payload == nal(1, 40)
    for unit, fragments in ((over, first[5:]), (large, second[1:])):
        payloads = [h.payload for h in fragments]
        assert {(p[0] >> 1) & 0x3F for p in payloads} == {FU}
        assert {(p[0] & 0x81, p[1]) for p in payloads} == {(unit[0] & 0x81, unit[1])}
        assert {p[2] & 0x3F for p in payloads} == {19}
        middle = [0] * (len(payloads) - 2)
        assert [p[2] & 0xC0 for p in payloads] == [0x80, *middle, 0x40]
        assert len(payloads[0]) == PAYLOAD_BUDGET
        assert unit[:2] + b"".join(p[3:] for p in payloads) == unit
    assert len(first[5:]) == 2
    assert len(second[1:]) == 4


async def test_timestamps_follow_the_millisecond_clock(server, connect) -> None:
    """90 kHz and 8 kHz timestamps come from the ms clock, so 22.5 fps never drifts."""
    client = await play(connect, server.url)
    rtptimes = [
        int(entry.rpartition("rtptime=")[2])
        for entry in client.last.headers["rtp-info"].split(",")
    ]
    base = 123_456_789
    video_clocks = [base + round(n * FRAME_MS) for n in range(451)]
    audio_clocks = [base + 20 + 40 * k for k in range(501)]
    pushes = sorted(
        [(clock, 0, n) for n, clock in enumerate(video_clocks)]
        + [(clock, 1, k) for k, clock in enumerate(audio_clocks)]
    )
    for clock, kind, index in pushes:
        if kind == 0:
            if index % 30 == 0:
                server.push_video(keyframe(), clock, True)
            else:
                server.push_video(p_frame(), clock, False)
        else:
            server.push_audio(b"\xd5" * 320, clock)

    video: list[int] = []
    audio: list[int] = []
    while len(video) < len(video_clocks) or len(audio) < len(audio_clocks):
        channel, packet = await client.packet()
        header = rtp(packet)
        if channel == 2:
            audio.append(header.timestamp)
        elif header.marker:
            video.append(header.timestamp)

    for n, clock in enumerate(video_clocks):
        assert later(video[n], video[0]) == 90 * (clock - video_clocks[0])
    assert later(video[450], video[0]) == 20 * 90_000
    assert {later(b, a) for a, b in zip(video, video[1:])} == {3960, 4050}
    for k in range(len(audio_clocks)):
        assert later(audio[k], audio[0]) == 320 * k
    assert later(video[0], rtptimes[0]) == 0
    assert later(audio[0], rtptimes[1]) == 8 * 20


async def test_sprop_appears_after_a_keyframe(server, connect) -> None:
    """DESCRIBE carries the parameter sets once a keyframe has been pushed."""
    client = await connect(server.url)
    assert "sprop" not in (await client.request("DESCRIBE", server.url)).body.decode()
    server.push_video(p_frame(), 0, False)
    assert "sprop" not in (await client.request("DESCRIBE", server.url)).body.decode()

    server.push_video(keyframe(), 44, True)
    sdp = (await client.request("DESCRIBE", server.url)).body.decode()
    vps, sps, pps = (base64.b64encode(unit).decode() for unit in (VPS, SPS, PPS))
    assert f"\r\na=fmtp:96 sprop-vps={vps};sprop-sps={sps};sprop-pps={pps}\r\n" in sdp


async def test_discontinuity_keeps_timestamps_rising(server, connect) -> None:
    """After a discontinuity readers restart at a keyframe, later by the gap."""
    client = await play(connect, server.url)
    server.push_video(keyframe(), 90_000, True)
    server.push_audio(b"\x01" * 320, 90_010)
    server.push_video(p_frame(), 90_044, False)
    before = [rtp(packet) for _, packet in await client.packets(6)]
    video_before, audio_before = before[5].timestamp, before[4].timestamp

    server.discontinuity()
    await asyncio.sleep(0.2)
    server.push_audio(b"\x02" * 320, 1_000)
    server.push_video(p_frame(), 1_000, False)
    server.push_video(keyframe(), 1_044, True)
    server.push_audio(b"\x03" * 320, 1_050)
    server.push_video(p_frame(), 1_088, False)
    after = [rtp(packet) for _, packet in await client.packets(6)]
    assert [h.payload for h in after] == [
        VPS,
        SPS,
        PPS,
        nal(19, 64),
        b"\x03" * 320,
        nal(1, 32),
    ]
    assert later(after[0].timestamp, video_before) >= 90 * 190
    assert later(after[4].timestamp, audio_before) > 0
    assert later(after[5].timestamp, after[0].timestamp) == 90 * 44
    await client.request("GET_PARAMETER", f"{server.url}/")
    assert client.queued == []


async def test_slow_reader_is_dropped_without_stalling_another(
    server, signals, connect, monkeypatch
) -> None:
    """A reader that stops reading is dropped; the other keeps every frame."""
    monkeypatch.setattr(rtsp, "READER_BUFFER_LIMIT", 256 * 1024)
    fast = await play(connect, server.url)
    slow = await play(connect, server.url)
    frames = 400
    frame = keyframe(64 * 1024)

    async def count_frames() -> int:
        seen = 0
        while seen < frames:
            _, packet = await fast.packet()
            seen += rtp(packet).marker
        return seen

    counting = asyncio.create_task(count_frames())
    for n in range(frames):
        server.push_video(frame, n * 44, True)
        await asyncio.sleep(0)
    assert await asyncio.wait_for(counting, 5) == frames

    backlog = await asyncio.wait_for(slow.reader.read(), 5)
    assert len(backlog) < frames * len(frame)
    assert signals == [True]


async def test_play_without_setup_is_refused_and_times_out(
    server, signals, connect, monkeypatch
) -> None:
    """PLAY with nothing set up gets 400, takes no slot and leaves the timeout on."""
    monkeypatch.setattr(rtsp, "SESSION_TIMEOUT", 0.2)
    client = await connect(server.url)
    assert (await client.request("PLAY", f"{server.url}/")).status == 400
    assert signals == []
    assert await asyncio.wait_for(client.reader.read(), 2) == b""
    assert signals == []


async def test_teardown_reply_arrives_behind_queued_media(
    server, signals, connect, monkeypatch
) -> None:
    """A playing client's TEARDOWN is answered after the media queued for it."""
    monkeypatch.setattr(rtsp, "READER_BUFFER_LIMIT", 64 << 20)
    parts = urlsplit(server.url)
    sock = socket.create_connection((parts.hostname, parts.port))
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
    sock.setblocking(False)
    client = Client(*await asyncio.open_connection(sock=sock))
    url = server.url
    assert (await client.request("DESCRIBE", url)).status == 200
    setup = await client.request(
        "SETUP", f"{url}/trackID=0", {"Transport": TCP_VIDEO}
    )
    assert setup.status == 200
    assert (await client.request("PLAY", f"{url}/")).status == 200
    for n in range(16):
        server.push_video(keyframe(256 * 1024), n * 44, True)
    await asyncio.sleep(0.1)
    assert next(iter(server._connections))._writer.transport.get_write_buffer_size()

    assert (await asyncio.wait_for(client.request("TEARDOWN", f"{url}/"), 2)).status == 200
    assert signals == [True, False]
    client.writer.close()
    await asyncio.sleep(0.05)
    assert signals == [True, False]


async def test_stop_before_start_and_twice_is_harmless(socket_enabled: None) -> None:
    """stop() does nothing on a server that is not running or already stopped."""
    server = RtspServer(lambda readers: None)
    await server.stop()
    await server.start()
    await server.stop()
    await server.stop()


async def test_stop_closes_readers(server, signals, connect) -> None:
    """stop() closes every reader, gives back their slots and stops listening."""
    client = await play(connect, server.url)
    await server.stop()
    assert signals == [True, False]
    assert await client.reader.read() == b""
    with pytest.raises(OSError):
        await connect(server.url)


async def test_stop_closes_a_client_not_yet_served(
    socket_enabled: None, monkeypatch
) -> None:
    """stop() returns at once with a client not yet handled, which is served nothing."""
    gate = asyncio.Event()
    accept = RtspServer._accept

    async def held_accept(self, reader, writer):
        await gate.wait()
        await accept(self, reader, writer)

    monkeypatch.setattr(RtspServer, "_accept", held_accept)
    signals: list[bool] = []
    server = RtspServer(signals.append)
    await server.start()
    parts = urlsplit(server.url)
    reader, writer = await asyncio.open_connection(parts.hostname, parts.port)
    writer.write(raw("DESCRIBE", server.url, 1))
    await writer.drain()
    await asyncio.sleep(0.05)
    await asyncio.wait_for(server.stop(), 1)
    gate.set()
    assert await asyncio.wait_for(reader.read(), 1) == b""
    await asyncio.sleep(0.05)
    assert signals == []
    writer.close()


@pytest.mark.parametrize("gap_ms", [600_000, -999_000])
async def test_first_reader_after_the_last_left_gets_a_fresh_timeline(
    server, connect, gap_ms
) -> None:
    """A reader joining others shares their timeline; the next lone one starts anew."""
    url = server.url
    first = await play(connect, url)
    for n in range(10):
        server.push_video(keyframe(), 1_000_000 + 44 * n, True)
    last_seen = rtp((await first.packets(40))[-1][1]).timestamp
    joined = await play(connect, url)
    assert later(rtptimes(joined)[0], last_seen) == 0
    for client in (first, joined):
        assert (await client.request("TEARDOWN", f"{url}/")).status == 200
    # The Live Session keeps pushing until its idle timer closes it.
    for n in range(10, 20):
        server.push_video(p_frame(), 1_000_000 + 44 * n, False)

    reader = await play(connect, url)
    video_rtptime, audio_rtptime = rtptimes(reader)
    clock = 1_000_000 + gap_ms
    server.push_video(keyframe(), clock, True)
    server.push_audio(b"\xd5" * 320, clock + 20)
    received = [rtp(packet) for _, packet in await reader.packets(5)]
    assert later(received[0].timestamp, video_rtptime) == 0
    assert later(received[4].timestamp, audio_rtptime) == 8 * 20


@pytest.mark.parametrize("length", ["1", "4096", "99999999", "x", ""])
async def test_request_with_a_body_is_refused(server, signals, connect, length) -> None:
    """A request with a body is not read: it gets 400, then a close."""
    client = await connect(server.url)
    response = await asyncio.wait_for(
        client.request("DESCRIBE", server.url, {"Content-Length": length}), 2
    )
    assert response.status == 400
    assert await asyncio.wait_for(client.reader.read(), 2) == b""
    assert signals == []


@pytest.mark.parametrize("prefix", [b"\r\n", b"\r\n\r\n", b"\n"])
async def test_empty_lines_before_a_request_are_skipped(
    server, connect, prefix
) -> None:
    """Line breaks a client sends before or between requests are skipped."""
    client = await connect(server.url)
    for method in ("OPTIONS", "DESCRIBE"):
        client.writer.write(prefix)
        assert (await client.request(method, server.url)).status == 200


async def test_client_rtcp_is_read_past(server, connect) -> None:
    """Interleaved RTCP from a playing client, around its requests, is skipped."""
    url = server.url
    client = await play(connect, url)
    client.writer.write(
        RTCP + raw("GET_PARAMETER", f"{url}/", 10, f"Session: {client.session}") + RTCP
    )
    status, headers = await answer(client)
    assert (status, headers["cseq"]) == (200, "10")
    server.push_video(keyframe(), 0, True)
    payloads = [rtp(packet).payload for _, packet in await client.packets(4)]
    assert payloads == [VPS, SPS, PPS, nal(19, 64)]
    client.writer.write(RTCP)
    assert (await client.request("GET_PARAMETER", f"{url}/")).status == 200


async def test_pipelined_requests_are_answered_in_order(
    server, signals, connect
) -> None:
    """Requests written at once are answered in order."""
    url = server.url
    client = await connect(url)
    client.writer.write(
        raw("DESCRIBE", url, 1)
        + raw("SETUP", f"{url}/trackID=0", 2, f"Transport: {TCP_VIDEO}")
        + raw("SETUP", f"{url}/trackID=1", 3, f"Transport: {TCP_AUDIO}")
        + raw("PLAY", f"{url}/", 4)
        + raw("OPTIONS", url, 5)
    )
    answers = [await answer(client) for _ in range(5)]
    assert [(status, headers["cseq"]) for status, headers in answers] == [
        (200, str(cseq)) for cseq in range(1, 6)
    ]
    assert "rtp-info" in answers[3][1]
    assert signals == [True]


async def test_requests_split_into_single_bytes(server, connect) -> None:
    """Requests and RTCP that arrive a byte at a time are put back together."""
    url = server.url
    client = await connect(url)
    data = (
        RTCP
        + raw("DESCRIBE", url, 1)
        + RTCP
        + raw("SETUP", f"{url}/trackID=0", 2, f"Transport: {TCP_VIDEO}")
    )
    for byte in data:
        client.writer.write(bytes((byte,)))
        await client.writer.drain()
        await asyncio.sleep(0)
    answers = [await answer(client) for _ in range(2)]
    assert [(status, headers["cseq"]) for status, headers in answers] == [
        (200, "1"),
        (200, "2"),
    ]


def access_units(elementary: bytes) -> list[tuple[bytes, bool]]:
    """Group an Annex-B H.265 elementary stream into (access unit, keyframe) pairs."""
    groups: list[list[bytes]] = [[]]
    has_slice = False
    for unit in elementary.split(b"\x00\x00\x01"):
        unit = unit.rstrip(b"\x00")
        if not unit:
            continue
        nal_type = unit[0] >> 1 & 0x3F
        starts = nal_type in (32, 33, 34, 35, 39) or (nal_type < 32 and unit[2] & 0x80)
        if has_slice and starts:
            groups.append([])
            has_slice = False
        groups[-1].append(unit)
        has_slice |= nal_type < 32
    return [
        (
            b"".join(b"\x00\x00\x00\x01" + unit for unit in group),
            any(16 <= unit[0] >> 1 & 0x3F <= 23 for unit in group),
        )
        for group in groups
    ]


async def run(command: str) -> bytes:
    """Run ffmpeg and return what it writes, skipping if it cannot."""
    process = await asyncio.create_subprocess_exec(
        *command.split(),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    output, _ = await process.communicate()
    if process.returncode:
        pytest.skip("ffmpeg could not generate the test media")
    return output


@pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg and ffprobe are not installed",
)
async def test_ffprobe_reads_the_stream_at_the_camera_clock(server, signals) -> None:
    """ffprobe sees hevc and pcm_alaw timed by the ms clock, not the SPS's 15 fps."""
    hevc = await run(
        "ffmpeg -v error -f lavfi -i testsrc2=size=320x240:rate=15 -frames:v 90"
        " -c:v libx265 -preset ultrafast -x265-params keyint=15:min-keyint=15"
        ":bframes=0:open-gop=0:repeat-headers=1:log-level=error -f hevc -"
    )
    alaw = await run(
        "ffmpeg -v error -f lavfi -i sine=frequency=440:sample_rate=8000 -t 4"
        " -c:a pcm_alaw -f alaw -"
    )
    frames = access_units(hevc)
    assert len(frames) == 90
    assert [n for n, (_, key) in enumerate(frames) if key] == list(range(0, 90, 15))
    video_clocks = [7000 + round(n * FRAME_MS) for n in range(len(frames))]
    audio_clocks = [7000 + 40 * k for k in range(len(alaw) // 320)]
    pushes = sorted(
        [(clock, 0, n) for n, clock in enumerate(video_clocks)]
        + [(clock, 1, k) for k, clock in enumerate(audio_clocks)]
    )

    server.push_video(frames[0][0], video_clocks[0], True)
    probe = await asyncio.create_subprocess_exec(
        *"ffprobe -v error -rtsp_transport tcp -of json -show_entries".split(),
        "stream=index,codec_name:packet=stream_index,pts_time",
        server.url,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    await until(lambda: signals, 5)
    previous = video_clocks[0]
    for clock, kind, index in pushes[1:]:
        await asyncio.sleep((clock - previous) / 4000)
        previous = clock
        if kind == 0:
            access_unit, key = frames[index]
            server.push_video(access_unit, clock, key)
        else:
            server.push_audio(alaw[index * 320 : (index + 1) * 320], clock)
    await asyncio.sleep(0.3)
    await server.stop()
    output, _ = await asyncio.wait_for(probe.communicate(), 5)

    result = json.loads(output)
    codecs = {entry["index"]: entry["codec_name"] for entry in result["streams"]}
    assert sorted(codecs.values()) == ["hevc", "pcm_alaw"]
    times: dict[str, list[int]] = {"hevc": [], "pcm_alaw": []}
    # ffprobe reports no pts for the first HEVC frame it assembles.
    for packet in result["packets"]:
        if "pts_time" in packet:
            pts_ms = round(float(packet["pts_time"]) * 1000)
            times[codecs[packet["stream_index"]]].append(pts_ms)
    video = sorted(set(times["hevc"]))
    audio = sorted(times["pcm_alaw"])
    assert len(video) >= 30
    assert len(audio) >= 30
    span = video[-1] - video[0]
    assert abs(span - (len(video) - 1) * FRAME_MS) <= 1
    assert span < (len(video) - 1) * 1000 / 15 * 0.9

    shift = next(
        clock - video[0]
        for clock in video_clocks
        if all(pts + clock - video[0] in video_clocks for pts in video)
    )
    first = video_clocks.index(video[0] + shift)
    assert [pts + shift for pts in video] == video_clocks[first : first + len(video)]
    assert all(pts + shift in audio_clocks for pts in audio)


@pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg and ffprobe are not installed",
)
async def test_ffprobe_starts_a_reopened_stream_at_zero(server, connect) -> None:
    """ffprobe playing before a reopened session's first keyframe starts near 0 s."""
    hevc = await run(
        "ffmpeg -v error -f lavfi -i testsrc2=size=320x240:rate=15 -frames:v 30"
        " -c:v libx265 -preset ultrafast -x265-params keyint=10:min-keyint=10"
        ":bframes=0:open-gop=0:repeat-headers=1:log-level=error -f hevc -"
    )
    frames = access_units(hevc)
    earlier = await play(connect, server.url)
    for n, (access_unit, key) in enumerate(frames * 2):
        if n == len(frames):
            assert (await earlier.request("TEARDOWN", f"{server.url}/")).status == 200
        server.push_video(access_unit, 1_000_000 + round(n * FRAME_MS), key)

    probe = await asyncio.create_subprocess_exec(
        *"ffprobe -v error -rtsp_transport tcp -of json -show_entries".split(),
        "packet=stream_index,pts_time",
        server.url,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    await until(lambda: server._readers, 5)
    for n, (access_unit, key) in enumerate(frames):
        clock = 1_600_000 + round(n * FRAME_MS)
        server.push_video(access_unit, clock, key)
        server.push_audio(b"\xd5" * 320, clock)
        await asyncio.sleep(0.01)
    await asyncio.sleep(0.3)
    await server.stop()
    output, _ = await asyncio.wait_for(probe.communicate(), 5)

    first_pts: dict[int, float] = {}
    for packet in json.loads(output)["packets"]:
        if "pts_time" in packet:
            first_pts.setdefault(packet["stream_index"], float(packet["pts_time"]))
    assert len(first_pts) == 2
    assert all(0 <= pts < 1 for pts in first_pts.values())
