"""Tests for the Live Session, against fake Signalling and cameras."""

from __future__ import annotations

import asyncio
from ipaddress import IPv4Address, IPv6Address
import json
import logging
import socket
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import urlsplit

from custom_components.catlink.const import (
    CONF_ALIYUN_CLOUD_KEY,
    CONF_ALIYUN_CLOUD_SECRET,
    CONF_API_BASE,
    CONF_PHONE,
    CONF_PHONE_IAC,
)
from custom_components.catlink.modules.account import Account
from custom_components.catlink.modules.live_view import sdp, session as live
from custom_components.catlink.modules.live_view.rtsp import RtspServer
from custom_components.catlink.modules.live_view.session import LiveSession
import pytest

from homeassistant.const import CONF_PASSWORD, CONF_TOKEN
from homeassistant.core import HomeAssistant

from .live_view_fakes import (
    LOOPBACK,
    PPS,
    PUBLIC,
    REPORT_SECRET,
    SESSION,
    SPS,
    TOKEN,
    VPS,
    Client,
    FakeCamera,
    FakeSignallingServer,
    annex_b,
    encrypted,
    logged,
    loopback_ice,
    message,
    nal,
    p_frame,
    packet,
    play,
    rtp,
    until,
)

LOSS_TIMEOUT = 0.3
IDLE_TIMEOUT = 0.4
BACKOFF = 0.25
FRAME_INTERVAL = 0.02
GOP = 10
CHUNK = 20
ENCRYPTED_HEAD = 10
CLOCK = 5_000_000
PUBLIC_SOURCE = "203.0.113.5"
STUN_HOST = "198.51.100.8"
RELAY_HOST = "198.51.100.9"


class MediaCamera(FakeCamera):
    """A camera that streams the vendor framing once startStreaming is acked."""

    def __init__(self, idr: bytes, **kwargs) -> None:
        """Stream keyframes ending in this IDR NAL unit."""
        super().__init__(**kwargs)
        self.idr = idr
        self.keyframe = annex_b(VPS, SPS, PPS, idr)
        self.seq = 0
        self.frames = 0
        self.media: asyncio.Task | None = None

    def streaming_started(self) -> None:
        """Start sending frames and audio."""
        self.media = asyncio.create_task(self._stream())

    def streaming_stopped(self) -> None:
        """Stop the media at once, as the camera does."""
        self.media.cancel()

    async def close(self) -> None:
        """Stop the media and close the socket."""
        self.answering = False
        if self.media is not None:
            self.media.cancel()
            await asyncio.wait({self.media})
        self.transport.close()

    async def _stream(self) -> None:
        """Send a frame and an audio chunk every FRAME_INTERVAL while answering."""
        while self.answering:
            clock = CLOCK + 44 * self.frames
            if self.frames % GOP == 0:
                self._frame(self.keyframe, clock, keyframe=True)
            else:
                self._frame(p_frame(), clock, keyframe=False)
            self._send(
                packet(
                    self.seq,
                    320 * self.frames,
                    b"\xd5" * 320,
                    b"\x06\x1b",
                    marker=True,
                    clock=clock,
                )
            )
            self.frames += 1
            await asyncio.sleep(FRAME_INTERVAL)

    def _frame(self, access_unit: bytes, clock: int, *, keyframe: bool) -> None:
        """Send one access unit; a keyframe's first packets go encrypted."""
        chunks = [
            access_unit[offset : offset + CHUNK]
            for offset in range(0, len(access_unit), CHUNK)
        ]
        for index, chunk in enumerate(chunks):
            inner = bytes((0x08 | (0x02 if index == 0 else 0) | keyframe, 0))
            fields = {"marker": index == len(chunks) - 1, "clock": clock}
            timestamp = 3960 * self.frames
            if keyframe and index < ENCRYPTED_HEAD:
                datagram = encrypted(
                    self.seq, timestamp, chunk, inner, key=self.pwd[:16], **fields
                )
            else:
                datagram = packet(self.seq, timestamp, chunk, inner, **fields)
            self._send(datagram)

    def _send(self, datagram: bytes) -> None:
        """Send one datagram to the viewer on the shared sequence."""
        self.transport.sendto(datagram, self.viewer)
        self.seq += 1


class CameraSignalling(FakeSignallingServer):
    """Signalling that answers every offer with a new loopback MediaCamera."""

    def __init__(self, trickle_delay: float | None = None) -> None:
        """Start with no cameras; trickle candidates after the answer if delayed."""
        super().__init__()
        self.trickle_delay = trickle_delay
        self.cameras: list[MediaCamera] = []

    def candidates(self) -> list[str]:
        """Return the candidate lines Home Assistant trickled."""
        params = [json.loads(text)["params"] for text in self.received]
        return [p["candidate"] for p in params if p["messageType"] == "candidate"]

    async def replies(self, offer_sdp: str) -> list[str | float]:
        """Bind a camera for the offer; send its LAN and a public candidate first."""
        ours = sdp.parse_answer(offer_sdp)
        ufrag, pwd = "C" + sdp.random_string(7), sdp.random_string(24)
        _, camera = await asyncio.get_running_loop().create_datagram_endpoint(
            lambda: MediaCamera(
                nal(19, 200 + len(self.cameras)),
                our_ufrag=ours.ufrag,
                our_pwd=ours.pwd,
                ufrag=ufrag,
                pwd=pwd,
            ),
            local_addr=(LOOPBACK, 0),
        )
        self.cameras.append(camera)
        answer = "\r\n".join(
            [
                "v=0",
                "o=- 2 2 IN IP4 127.0.0.1",
                "s=-",
                "t=0 0",
                "m=video 9 RTP/AVPF 96 97",
                f"a=ice-ufrag:{ufrag}",
                f"a=ice-pwd:{pwd}",
                "a=sendonly",
                "",
            ]
        )
        candidates = [
            message(
                {
                    "candidate": sdp.candidate_line(LOOPBACK, camera.port, ufrag),
                    "messageType": "candidate",
                }
            ),
            message(
                {
                    "candidate": sdp.candidate_line(PUBLIC, 5000, ufrag),
                    "messageType": "candidate",
                }
            ),
        ]
        answered = [
            message({"messageType": "p2pReportToken", "securityToken": REPORT_SECRET}),
            message({"messageType": "answerSdp", "sdp": answer, "sessionId": SESSION}),
        ]
        if self.trickle_delay is None:
            return [*candidates, *answered]
        return [*answered, self.trickle_delay, *candidates]


class FakeChannel:
    """An Aliyun Channel whose stream/query points at the fake Signalling."""

    def __init__(self, signalling: CameraSignalling) -> None:
        """Answer every call."""
        self.signalling = signalling
        self.calls: list[tuple[str, str, dict]] = []

    async def async_request(self, path: str, api_ver: str, params: dict) -> dict:
        """Record the call and answer with a signalUrl, a STUN server and a relay."""
        self.calls.append((path, api_ver, params))
        return {
            "p2pInfo": {
                "signalUrl": self.signalling.url(),
                "stunServers": [f"stun:{STUN_HOST}:3478"],
            },
            "relayUrl": f"wss://{RELAY_HOST}/relay",
        }


class FlakyChannel(FakeChannel):
    """A channel whose first few stream/query answers carry no usable signalUrl."""

    def __init__(
        self, signalling: CameraSignalling, failures: int, answer: object
    ) -> None:
        """Give this answer to the first failures calls, then the real one."""
        super().__init__(signalling)
        self.failures = failures
        self.answer = answer
        self.times: list[float] = []

    async def async_request(self, path: str, api_ver: str, params: dict) -> object:
        """Record when the call came; answer badly until the failures run out."""
        self.times.append(asyncio.get_running_loop().time())
        if len(self.times) > self.failures:
            return await super().async_request(path, api_ver, params)
        self.calls.append((path, api_ver, params))
        return self.answer


class FakeDevice:
    """A camera Device: its ids, its SleepMode predicate and its Account."""

    def __init__(self, account, device_id: str = "d1") -> None:
        """Start awake, with no warnings."""
        self.id = device_id
        self.name = f"Pro Ultra {device_id}"
        self.iot_id = f"IOT-{device_id}"
        self.camera_off = False
        self.account = account
        self.warnings: list[str] = []

    def warn_once(self, cause: str, msg: str, *args) -> None:
        """Record a warning."""
        self.warnings.append(msg % args)

    def clear_warning(self, cause: str) -> None:
        """Keep the recorded warnings for the test to read."""


class EvictedCloud:
    """A CatLink Cloud that has ended the Session: every call gets 1002."""

    def __init__(self) -> None:
        """Record nothing yet."""
        self.urls: list[str] = []

    async def request(self, method, url, **kwargs):
        """Record the call and reject its token."""
        self.urls.append(url)
        resp = MagicMock()
        resp.json = AsyncMock(return_value={"returnCode": 1002, "msg": "Illegal token"})
        return resp


def device_with(channel: FakeChannel, device_id: str = "d1") -> FakeDevice:
    """Return a Device whose Account hands out the fake channel."""
    account = SimpleNamespace(
        async_aliyun_channel=AsyncMock(return_value=channel), sign_in_paused=False
    )
    return FakeDevice(account, device_id)


def stream_query(iot_id: str) -> tuple[str, str, dict]:
    """Return the gateway call a Live Session makes for a Device."""
    return (
        "/vision/customer/stream/query",
        "2.2.0",
        {
            "iotId": iot_id,
            "streamType": 0,
            "relayEncrypted": True,
            "relayEncryptType": 0,
            "forceIFrame": True,
            "needDomainName": True,
            "enableWebSocket": True,
            "enablePortPredict": True,
            "clientType": "Android",
            "cacheDuration": 0,
        },
    )


def commands(camera: FakeCamera) -> list[str]:
    """Return the command requests the camera received, in order."""
    bodies = [json.loads(text) for text in camera.software]
    return [b["params"]["messageType"] for b in bodies if b["type"] == "request"]


async def keyframe_and_audio(reader: Client) -> list[bytes]:
    """Read to the next keyframe and an audio packet; return the keyframe's NALs."""
    units: list[bytes] = []
    heard = False
    async with asyncio.timeout(5):
        while len(units) < 4 or not heard:
            channel, data = await reader.packet()
            payload = rtp(data).payload
            if channel == 2:
                heard = True
            elif len(units) < 4 and (units or payload == VPS):
                units.append(payload)
    return units


@pytest.fixture
def fast(socket_enabled: None):
    """Shorten the session's and the agent's timings; loopback counts as LAN."""
    sources = [
        IPv4Address(LOOPBACK),
        IPv4Address(PUBLIC_SOURCE),
        IPv6Address("fd00::5"),
    ]
    with (
        loopback_ice(LOSS_TIMEOUT),
        patch.object(live, "IDLE_TIMEOUT", IDLE_TIMEOUT),
        patch.object(live, "RECONNECT_BACKOFF", BACKOFF),
        patch.object(
            live.network,
            "async_get_enabled_source_ips",
            AsyncMock(return_value=sources),
        ),
    ):
        yield


@pytest.fixture
async def signalling(fast: None, request: pytest.FixtureRequest):
    """Run Signalling that answers with loopback cameras; close them afterwards."""
    fake = CameraSignalling(getattr(request, "param", None))
    await fake.server.start_server()
    yield fake
    await fake.server.close()
    for camera in fake.cameras:
        await camera.close()


@pytest.fixture
async def start_session(hass: HomeAssistant, signalling: CameraSignalling):
    """Start Live Sessions for Devices; stop them after the test."""
    sessions: list[LiveSession] = []

    async def _start(device: FakeDevice) -> LiveSession:
        """Start one Device's Live Session."""
        session = LiveSession(hass, device)
        await session.async_start()
        sessions.append(session)
        return session

    yield _start
    for session in sessions:
        await session.async_stop()


@pytest.fixture
def channel(signalling: CameraSignalling) -> FakeChannel:
    """Return a channel whose stream/query points at the fake Signalling."""
    return FakeChannel(signalling)


@pytest.fixture
def device(channel: FakeChannel) -> FakeDevice:
    """Return an awake camera Device on the fake channel."""
    return device_with(channel)


async def test_first_reader_opens_one_session(
    start_session,
    channel: FakeChannel,
    device: FakeDevice,
    signalling: CameraSignalling,
    connect,
) -> None:
    """The first RTSP reader makes one stream/query and gets decrypted media."""
    session = await start_session(device)
    states: list[bool] = []
    session.async_add_listener(lambda: states.append(session.streaming))
    assert not session.streaming
    assert channel.calls == []

    reader = await play(connect, session.rtsp_url)
    units = await keyframe_and_audio(reader)

    camera = signalling.cameras[0]
    assert units == [VPS, SPS, PPS, camera.idr]
    assert channel.calls == [stream_query("IOT-d1")]
    assert signalling.query_strings == [f"token={TOKEN}&session={SESSION}"]
    assert signalling.closed.is_set()
    assert session.streaming
    assert states == [True]
    assert commands(camera)[0] == "startStreaming"
    assert camera.problems == []


async def test_readers_share_one_session(
    start_session,
    channel: FakeChannel,
    device: FakeDevice,
    signalling: CameraSignalling,
    connect,
) -> None:
    """Two RTSP readers share one stream/query."""
    session = await start_session(device)
    first = await play(connect, session.rtsp_url)
    second = await play(connect, session.rtsp_url)

    for reader in (first, second):
        assert (await keyframe_and_audio(reader))[0] == VPS
    assert session.streaming

    assert channel.calls == [stream_query("IOT-d1")]
    assert len(signalling.cameras) == 1
    assert len(signalling.query_strings) == 1


async def test_idle_timer_closes_and_a_reader_within_it_reuses(
    start_session,
    channel: FakeChannel,
    device: FakeDevice,
    signalling: CameraSignalling,
    connect,
) -> None:
    """A reader inside the idle window reuses the session; the timer then closes it."""
    loop = asyncio.get_running_loop()
    session = await start_session(device)
    reader = await play(connect, session.rtsp_url)
    await keyframe_and_audio(reader)
    reader.writer.close()
    await asyncio.sleep(IDLE_TIMEOUT / 2)

    reader = await play(connect, session.rtsp_url)
    await keyframe_and_audio(reader)
    assert session.streaming
    reader.writer.close()
    released = loop.time()
    camera = signalling.cameras[0]
    assert "stopStreaming" not in commands(camera)

    await until(lambda: not session.streaming, IDLE_TIMEOUT + 2)
    assert loop.time() - released >= IDLE_TIMEOUT
    assert commands(camera)[-2:] == ["stopStreaming", "stopP2p"]
    assert channel.calls == [stream_query("IOT-d1")]
    assert len(signalling.cameras) == 1


async def test_loss_reconnects_behind_a_discontinuity(
    start_session,
    channel: FakeChannel,
    device: FakeDevice,
    signalling: CameraSignalling,
    connect,
) -> None:
    """A lost camera is queried again; the reader sees a discontinuity, no error."""
    session = await start_session(device)
    states: list[bool] = []
    session.async_add_listener(lambda: states.append(session.streaming))
    with patch.object(
        RtspServer,
        "discontinuity",
        autospec=True,
        side_effect=RtspServer.discontinuity,
    ) as discontinuity:
        reader = await play(connect, session.rtsp_url)
        await keyframe_and_audio(reader)
        signalling.cameras[0].answering = False
        await until(lambda: len(signalling.cameras) == 2, 3)
        idr = signalling.cameras[1].idr
        async with asyncio.timeout(3):
            while rtp((await reader.packet())[1]).payload != idr:
                pass

    discontinuity.assert_called_once()
    assert channel.calls == [stream_query("IOT-d1")] * 2
    assert states == [True, False, True]
    assert session.streaming


async def test_sign_in_paused_fails_cleanly_without_signing_in(
    hass: HomeAssistant, start_session, signalling: CameraSignalling, connect
) -> None:
    """A paused, ended Session fails the open once, signs in never, and backs off."""
    cloud = EvictedCloud()
    with patch(
        "custom_components.catlink.modules.account.aiohttp_client."
        "async_create_clientsession",
        return_value=MagicMock(request=cloud.request),
    ):
        account = Account(
            hass,
            {
                CONF_API_BASE: "https://app.catlinks.cn/api/",
                CONF_PHONE_IAC: "86",
                CONF_PHONE: "13812345678",
                CONF_PASSWORD: "secret",
                CONF_TOKEN: "stale-tok",
                CONF_ALIYUN_CLOUD_KEY: "key",
                CONF_ALIYUN_CLOUD_SECRET: "secret",
            },
        )
    account.sign_in_paused = True
    device = FakeDevice(account)
    session = await start_session(device)

    reader = await play(connect, session.rtsp_url)
    await until(lambda: device.warnings)
    reader.writer.close()
    assert not session.streaming
    assert device.warnings == [
        "Live view for Pro Ultra d1 failed: CatLink sign-in is paused"
    ]

    await asyncio.sleep(IDLE_TIMEOUT + BACKOFF)
    attempts = len(cloud.urls)
    await asyncio.sleep(2 * BACKOFF)
    assert 1 <= attempts == len(cloud.urls) <= 3
    assert all(url.endswith("token/user/auth/aliLive/authCode") for url in cloud.urls)
    assert signalling.query_strings == []


async def test_asleep_camera_is_never_queried(
    start_session,
    channel: FakeChannel,
    device: FakeDevice,
    signalling: CameraSignalling,
    connect,
) -> None:
    """While SleepMode is 1 there is no stream/query; waking lets a reader in."""
    device.camera_off = True
    session = await start_session(device)

    reader = await play(connect, session.rtsp_url)
    await asyncio.sleep(3 * BACKOFF)
    assert channel.calls == []
    assert signalling.query_strings == []
    assert device.warnings[0] == (
        "Live view for Pro Ultra d1 failed: The camera is switched off"
    )

    device.camera_off = False
    assert (await keyframe_and_audio(reader))[0] == VPS
    assert channel.calls == [stream_query("IOT-d1")]
    assert session.streaming


async def test_only_lan_addresses_and_no_secrets_in_logs(
    start_session,
    device: FakeDevice,
    signalling: CameraSignalling,
    connect,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Nothing leaves loopback, candidates are LAN only, and no secret is logged."""
    destinations: list[str] = []
    sendto, sock_connect = socket.socket.sendto, socket.socket.connect

    def spy_sendto(sock: socket.socket, data: bytes, address: tuple) -> int:
        """Record a datagram's destination; drop it unless it is loopback."""
        destinations.append(address[0])
        if address[0] != LOOPBACK:
            return len(data)
        return sendto(sock, data, address)

    def spy_connect(sock: socket.socket, address: tuple) -> None:
        """Record a connection's destination."""
        destinations.append(address[0])
        return sock_connect(sock, address)

    session = await start_session(device)
    with (
        patch.object(socket.socket, "sendto", spy_sendto),
        patch.object(socket.socket, "connect", spy_connect),
        caplog.at_level(logging.DEBUG),
    ):
        reader = await play(connect, session.rtsp_url)
        await keyframe_and_audio(reader)

    assert set(destinations) == {LOOPBACK}
    assert [sdp.parse_candidate(line).ip for line in signalling.candidates()] == [
        LOOPBACK
    ]
    text = logged(caplog)
    assert "non-LAN camera candidate" in text
    camera = signalling.cameras[0]
    for secret in (
        PUBLIC,
        signalling.url(),
        TOKEN,
        SESSION,
        session.rtsp_url,
        urlsplit(session.rtsp_url).path.strip("/"),
        camera.our_ufrag,
        camera.our_pwd.decode(),
        camera.ufrag,
        camera.pwd.decode(),
        camera.pwd[:16].decode(),
    ):
        assert secret not in text
    assert device.warnings == []


async def test_last_keyframe_outlives_the_session(
    start_session, device: FakeDevice, signalling: CameraSignalling, connect
) -> None:
    """The last decrypted keyframe stays readable after the idle timer closes."""
    session = await start_session(device)
    assert session.last_keyframe is None

    reader = await play(connect, session.rtsp_url)
    await keyframe_and_audio(reader)
    reader.writer.close()
    await until(lambda: not session.streaming, IDLE_TIMEOUT + 2)

    camera = signalling.cameras[0]
    assert commands(camera)[-2:] == ["stopStreaming", "stopP2p"]
    assert session.last_keyframe == camera.keyframe


async def test_stop_closes_everything_without_reconnecting(
    start_session,
    channel: FakeChannel,
    device: FakeDevice,
    signalling: CameraSignalling,
    connect,
) -> None:
    """Stopping stops the camera, its readers and its server; nothing reconnects."""
    session = await start_session(device)
    url = session.rtsp_url
    readers = [await play(connect, url) for _ in range(2)]
    for reader in readers:
        await keyframe_and_audio(reader)

    await session.async_stop()

    assert not session.streaming
    assert commands(signalling.cameras[0])[-2:] == ["stopStreaming", "stopP2p"]
    for reader in readers:
        await asyncio.wait_for(reader.reader.read(), 2)
        assert reader.reader.at_eof()
    with pytest.raises(OSError):
        await connect(url)
    await asyncio.sleep(LOSS_TIMEOUT + 2 * BACKOFF)
    assert channel.calls == [stream_query("IOT-d1")]
    assert len(signalling.cameras) == 1


@pytest.mark.parametrize("signalling", [0.02], indirect=True)
async def test_candidates_trickled_after_the_answer_connect(
    start_session,
    channel: FakeChannel,
    device: FakeDevice,
    signalling: CameraSignalling,
    connect,
) -> None:
    """An answer without candidates, then the LAN one 20 ms later, still connects."""
    session = await start_session(device)

    await keyframe_and_audio(await play(connect, session.rtsp_url))
    assert session.streaming

    assert channel.calls == [stream_query("IOT-d1")]
    assert signalling.cameras[0].problems == []


async def test_new_demand_during_backoff_gets_a_fresh_attempt(
    start_session, signalling: CameraSignalling, connect
) -> None:
    """A reader leaving and another joining mid-backoff retries at once."""
    channel = FlakyChannel(signalling, 1, {})
    device = device_with(channel)
    session = await start_session(device)

    with patch.object(live, "RECONNECT_BACKOFF", 30):
        reader = await play(connect, session.rtsp_url)
        await until(lambda: device.warnings)
        session._on_demand(False)
        session._on_demand(True)
        async with asyncio.timeout(3):
            await keyframe_and_audio(reader)

    assert channel.calls == [stream_query("IOT-d1")] * 2


async def test_new_demand_resets_the_backoff(
    start_session, signalling: CameraSignalling, connect
) -> None:
    """After new demand cuts a backoff short, the next wait is the first delay again."""
    channel = FlakyChannel(signalling, 100, {})
    session = await start_session(device_with(channel))

    with patch.object(live, "RECONNECT_BACKOFF", 0.1):
        await play(connect, session.rtsp_url)
        await until(lambda: len(channel.times) == 3)
        session._on_demand(False)
        session._on_demand(True)
        await until(lambda: len(channel.times) == 5)

    assert channel.times[4] - channel.times[3] < 0.3


async def test_without_demand_nothing_retries(
    start_session, signalling: CameraSignalling, connect
) -> None:
    """A reader gone after a failure leaves one stream/query, past the backoff."""
    channel = FlakyChannel(signalling, 100, {})
    device = device_with(channel)
    session = await start_session(device)

    reader = await play(connect, session.rtsp_url)
    await until(lambda: device.warnings)
    reader.writer.close()
    await asyncio.sleep(IDLE_TIMEOUT + 2 * BACKOFF)

    assert channel.calls == [stream_query("IOT-d1")]
    assert not session.streaming


@pytest.mark.parametrize(
    "answer",
    [["p2pInfo"], {"p2pInfo": "wss://x"}, {"p2pInfo": {"signalUrl": 5}}],
)
async def test_odd_stream_query_answer_is_an_open_failure(
    start_session, signalling: CameraSignalling, answer: object, connect
) -> None:
    """An answer of the wrong shape fails the open with the usual reason."""
    channel = FlakyChannel(signalling, 1, answer)
    device = device_with(channel)
    session = await start_session(device)

    reader = await play(connect, session.rtsp_url)
    async with asyncio.timeout(5):
        await keyframe_and_audio(reader)
    assert session.streaming
    assert device.warnings == [
        "Live view for Pro Ultra d1 failed: stream/query returned no Signalling URL"
    ]
