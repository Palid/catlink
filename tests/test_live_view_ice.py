"""Tests for the live view ICE agent, against a fake camera on loopback."""

from __future__ import annotations

import asyncio
import logging
import socket
from unittest.mock import patch

import pytest

from custom_components.catlink.modules.live_view import ice, sdp, stun
from custom_components.catlink.modules.live_view.ice import IceAgent, IceError

from .live_view_fakes import (
    CAMERA_PWD,
    CAMERA_UFRAG,
    HOLD_OFF,
    LOOPBACK,
    OUR_PWD,
    OUR_UFRAG,
    PING_SEQ,
    PUBLIC,
    SESSION,
    FakeCamera,
    compact,
    logged,
    loopback_ice,
    probe_rtp,
    response,
)

LOSS_TIMEOUT = 0.25
KEEPALIVE = 0.1


def _request(message_type: str, seq: int) -> str:
    """Return the request body we expect for a stop command."""
    return compact(
        {
            "version": "2.0",
            "type": "request",
            "seq_num": seq,
            "params": {"messageType": message_type, "sessionId": SESSION},
        }
    )


START_STREAMING = compact(
    {
        "version": "2.0",
        "type": "request",
        "seq_num": 2,
        "params": {
            "messageType": "startStreaming",
            "streamType": 0,
            "sessionId": SESSION,
        },
    }
)


@pytest.fixture
def fast_loopback(socket_enabled: None):
    """Shorten every interval and admit loopback as LAN for the fake camera."""
    with loopback_ice(LOSS_TIMEOUT):
        yield


@pytest.fixture
def keepalive_ratio(fast_loopback: None):
    """Scale keepalive, its retry and loss down from 2.5, 0.5 and 5 s alike."""
    with (
        patch.object(ice, "KEEPALIVE_INTERVAL", KEEPALIVE),
        patch.object(ice, "KEEPALIVE_RETRY_INTERVAL", KEEPALIVE / 5),
        patch.object(ice, "LOSS_TIMEOUT", 2 * KEEPALIVE),
    ):
        yield


class Harness:
    """An agent, its fake camera and what the agent's callbacks received."""

    def __init__(self, camera: FakeCamera) -> None:
        """Build an agent wired to record RTP and loss."""
        self.camera = camera
        self.rtp: list[bytes] = []
        self.lost = asyncio.Event()
        self.agent = IceAgent(
            local_ufrag=OUR_UFRAG,
            local_pwd=OUR_PWD,
            session_id=SESSION,
            local_addresses=[LOOPBACK],
            on_rtp=self.rtp.append,
            on_lost=self.lost.set,
        )


@pytest.fixture
async def harness(fast_loopback: None, request: pytest.FixtureRequest):
    """Yield a bound agent and a fake camera; stop and close both afterwards."""
    ack_start_on = getattr(request, "param", 3)
    loop = asyncio.get_running_loop()
    _, camera = await loop.create_datagram_endpoint(
        lambda: FakeCamera(ack_start_on=ack_start_on), local_addr=(LOOPBACK, 0)
    )
    rig = Harness(camera)
    await rig.agent.bind()
    yield rig
    await rig.agent.stop()
    camera.transport.close()


async def _connect(rig: Harness, *extra: sdp.Candidate) -> None:
    """Connect the agent to the fake camera."""
    await rig.agent.connect(
        CAMERA_UFRAG, CAMERA_PWD, [*extra, rig.camera.candidate()], timeout=3
    )


async def test_session_end_to_end(harness: Harness) -> None:
    """We send first, connect after the hold-off, stream, answer and stop cleanly."""
    loop = asyncio.get_running_loop()
    (line,) = harness.agent.local_candidates()
    assert sdp.parse_candidate(line).ip == LOOPBACK
    assert sdp.parse_candidate(line).port > 0
    assert f"ufrag {OUR_UFRAG} " in line

    await _connect(harness)
    assert loop.time() - harness.camera.started >= HOLD_OFF
    assert harness.camera.held > 1

    assert await harness.agent.start_streaming()
    async with asyncio.timeout(2):
        while len(harness.rtp) < 3 or harness.camera.answers_ok < 2:
            await asyncio.sleep(0.01)
    await harness.agent.stop()

    assert harness.rtp == [probe_rtp(0), probe_rtp(1), probe_rtp(2)]
    assert harness.camera.software == [
        START_STREAMING,
        START_STREAMING,
        START_STREAMING,
        response(PING_SEQ, "cameraPing"),
        _request("stopStreaming", 20),
        _request("stopP2p", 20000),
    ]
    assert harness.camera.problems == []
    assert not harness.lost.is_set()
    assert harness.agent._acks == {}


@pytest.mark.parametrize("harness", [None], indirect=True)
async def test_start_streaming_retransmits_at_most_three_times(
    harness: Harness,
) -> None:
    """Without an acknowledgement startStreaming goes out four times, then gives up."""
    await _connect(harness)

    assert not await harness.agent.start_streaming()
    await asyncio.sleep(0.05)
    assert harness.camera.software == [START_STREAMING] * 4
    assert harness.agent._acks == {}


async def test_one_unanswered_keepalive_is_resent(
    harness: Harness, keepalive_ratio: None
) -> None:
    """A keepalive whose answer is lost goes out again before loss is declared."""
    await _connect(harness)
    await asyncio.sleep(KEEPALIVE / 2)
    checks = harness.camera.checks
    harness.camera.unanswered = 1

    await asyncio.sleep(4 * KEEPALIVE)

    assert harness.camera.unanswered == 0
    assert harness.camera.checks - checks >= 4
    assert not harness.lost.is_set()


@pytest.mark.parametrize("sign_of_life", ["rtp", "camera check"])
async def test_media_or_camera_checks_keep_the_pair_alive(
    harness: Harness, keepalive_ratio: None, sign_of_life: str
) -> None:
    """Without keepalive answers, RTP or the camera's own checks still prove life."""
    loop = asyncio.get_running_loop()
    await _connect(harness)
    camera = harness.camera
    camera.answering = False

    async def live() -> None:
        """Send a sign of life from the camera's address every 20 ms."""
        agent = (LOOPBACK, harness.agent._port)
        for seq in range(1000):
            if sign_of_life == "rtp":
                camera.transport.sendto(probe_rtp(seq), agent)
            else:
                camera._send_check(response(seq, "cameraPing"))
            await asyncio.sleep(0.02)

    sender = asyncio.create_task(live())
    await asyncio.sleep(4 * KEEPALIVE)
    assert not harness.lost.is_set()

    sender.cancel()
    silenced = loop.time()
    async with asyncio.timeout(2):
        await harness.lost.wait()
    assert loop.time() - silenced <= 2 * KEEPALIVE + 0.1


async def test_keepalive_holds_then_loss_fires(
    harness: Harness, caplog: pytest.LogCaptureFixture
) -> None:
    """Answered keepalives keep the session; silence ends it after LOSS_TIMEOUT."""
    loop = asyncio.get_running_loop()
    await _connect(harness)
    checks = harness.camera.checks

    await asyncio.sleep(3 * LOSS_TIMEOUT)
    assert not harness.lost.is_set()
    assert harness.camera.checks - checks >= 5

    harness.camera.answering = False
    silenced = loop.time()
    with caplog.at_level(logging.DEBUG):
        async with asyncio.timeout(2):
            await harness.lost.wait()
    assert LOSS_TIMEOUT - 0.06 <= loop.time() - silenced <= LOSS_TIMEOUT + 0.2
    assert "stopped answering" in logged(caplog)

    checks = harness.camera.checks
    await harness.agent.stop()
    assert harness.camera.checks == checks


async def test_camera_without_media_is_lost_and_told_to_stop(
    harness: Harness, caplog: pytest.LogCaptureFixture
) -> None:
    """A camera that answers checks but sends no RTP for RTP_TIMEOUT is lost."""
    loop = asyncio.get_running_loop()
    with patch.object(ice, "RTP_TIMEOUT", 0.3), caplog.at_level(logging.DEBUG):
        await _connect(harness)
        assert await harness.agent.start_streaming()
        async with asyncio.timeout(2):
            while len(harness.rtp) < 3:
                await asyncio.sleep(0.01)
        silenced = loop.time()
        async with asyncio.timeout(2):
            await harness.lost.wait()
    assert 0.3 - 0.06 <= loop.time() - silenced <= 0.3 + 0.2
    assert "stopped sending media" in logged(caplog)

    await harness.agent.stop()
    assert harness.camera.software[-2:] == [
        _request("stopStreaming", 20),
        _request("stopP2p", 20000),
    ]


async def test_cancelling_stop_still_closes_the_socket(harness: Harness) -> None:
    """A stop() cancelled while waiting on the camera closes the UDP socket anyway."""
    await _connect(harness)
    assert await harness.agent.start_streaming()
    harness.camera.answering = False

    stopping = asyncio.create_task(harness.agent.stop())
    await asyncio.sleep(0.05)
    assert not stopping.done()
    stopping.cancel()
    await asyncio.wait({stopping})

    assert harness.agent._transport.is_closing()


async def test_pause_before_start_streaming_does_not_lose_the_camera(
    harness: Harness,
) -> None:
    """The no-media deadline starts at startStreaming, not at ICE connect."""
    with patch.object(ice, "RTP_TIMEOUT", 0.3):
        await _connect(harness)
        await asyncio.sleep(0.45)
        assert not harness.lost.is_set()
        assert await harness.agent.start_streaming()
        async with asyncio.timeout(2):
            await harness.lost.wait()
    await harness.agent.stop()


async def test_public_candidate_is_never_checked(
    harness: Harness, caplog: pytest.LogCaptureFixture
) -> None:
    """Checks go to the LAN candidate only; the public one is logged, unnamed, once."""
    destinations: list[str] = []
    sendto = socket.socket.sendto

    def spy(sock: socket.socket, data: bytes, address: tuple[str, int]) -> int:
        destinations.append(address[0])
        if address[0] != LOOPBACK:
            return len(data)
        return sendto(sock, data, address)

    public = sdp.Candidate("udp", PUBLIC, 5000)
    with patch.object(socket.socket, "sendto", spy), caplog.at_level(logging.DEBUG):
        await _connect(harness, public)
        await harness.agent.start_streaming()
        await harness.agent.stop()

    assert destinations
    assert PUBLIC not in destinations
    text = logged(caplog)
    assert text.count("non-LAN camera candidate") == 1
    for secret in (PUBLIC, OUR_UFRAG, OUR_PWD, CAMERA_UFRAG, CAMERA_PWD, SESSION):
        assert secret not in text


async def test_only_public_candidates_fail_without_a_check(harness: Harness) -> None:
    """Without a LAN candidate by the timeout connect raises, having sent nothing."""
    public = sdp.Candidate("udp", PUBLIC, 5000)

    with pytest.raises(IceError, match="no LAN candidate"):
        await harness.agent.connect(CAMERA_UFRAG, CAMERA_PWD, [public], timeout=0.1)
    assert harness.camera.checks == 0


async def test_candidates_added_while_connecting_are_checked(harness: Harness) -> None:
    """A candidate trickled after connect starts is checked, and connects."""
    connecting = asyncio.create_task(
        harness.agent.connect(CAMERA_UFRAG, CAMERA_PWD, [], timeout=3)
    )
    await asyncio.sleep(0.02)
    harness.agent.add_remote_candidate(harness.camera.candidate())

    await connecting
    assert await harness.agent.start_streaming()
    assert harness.camera.problems == []


async def test_an_answer_from_elsewhere_selects_nothing(harness: Harness) -> None:
    """A verified answer from an address other than the one checked is ignored."""
    loop = asyncio.get_running_loop()
    elsewhere, _ = await loop.create_datagram_endpoint(
        asyncio.DatagramProtocol, local_addr=(LOOPBACK, 0)
    )
    own, harness.camera.transport = harness.camera.transport, elsewhere
    try:
        with pytest.raises(IceError, match="did not answer"):
            await harness.agent.connect(
                CAMERA_UFRAG, CAMERA_PWD, [harness.camera.candidate()], timeout=0.4
            )
    finally:
        harness.camera.transport = own
        elsewhere.close()
    assert harness.camera.viewer is not None


@pytest.mark.parametrize(
    "body",
    [
        {"type": "response", "seq_num": [2], "params": {"messageType": "x"}},
        {"type": "response", "seq_num": 2, "params": {"messageType": {}}},
        {"type": "request", "seq_num": {}, "params": {"messageType": "cameraPing"}},
        {"type": "request", "seq_num": 2, "params": {"messageType": None}},
        {"type": "request", "seq_num": 2, "params": "cameraPing"},
        [2],
    ],
)
async def test_malformed_commands_are_dropped(harness: Harness, body) -> None:
    """A verified camera check whose command has the wrong shape is ignored."""
    await _connect(harness)
    check = stun.encode(
        stun.BINDING_REQUEST,
        b"T" * 12,
        [
            (stun.USERNAME, f"{OUR_UFRAG}:{CAMERA_UFRAG}".encode()),
            (stun.SOFTWARE, compact(body).encode()),
        ],
        key=OUR_PWD.encode(),
    )

    harness.agent.datagram_received(check, (LOOPBACK, harness.camera.port))
    await asyncio.sleep(0.05)

    assert harness.camera.answers_ok == 1
    assert harness.camera.software == []
    assert harness.camera.problems == []


async def test_connect_times_out(harness: Harness) -> None:
    """A camera that never answers fails connect with IceError."""
    harness.camera.answering = False

    with pytest.raises(IceError, match="did not answer"):
        await harness.agent.connect(
            CAMERA_UFRAG, CAMERA_PWD, [harness.camera.candidate()], timeout=0.3
        )
    assert harness.camera.checks > 1


async def _send_from_stranger(rig: Harness, *datagrams: bytes) -> None:
    """Send datagrams to the agent's port from a second loopback socket."""
    loop = asyncio.get_running_loop()
    transport, _ = await loop.create_datagram_endpoint(
        asyncio.DatagramProtocol, local_addr=(LOOPBACK, 0)
    )
    for datagram in datagrams:
        transport.sendto(datagram, (LOOPBACK, rig.agent._port))
    await asyncio.sleep(0.05)
    transport.close()


async def test_rtp_from_another_source_is_dropped(harness: Harness) -> None:
    """Only the selected camera address feeds on_rtp."""
    await _connect(harness)

    await _send_from_stranger(harness, probe_rtp(50), probe_rtp(51))
    assert harness.rtp == []

    assert await harness.agent.start_streaming()
    async with asyncio.timeout(2):
        while len(harness.rtp) < 3:
            await asyncio.sleep(0.01)
    assert harness.rtp == [probe_rtp(0), probe_rtp(1), probe_rtp(2)]


async def test_rtp_before_connect_is_dropped(harness: Harness) -> None:
    """RTP that arrives before a pair is selected never reaches on_rtp."""
    port = harness.agent._port
    harness.camera.transport.sendto(probe_rtp(60), (LOOPBACK, port))
    await _send_from_stranger(harness, probe_rtp(61))

    assert harness.rtp == []


@pytest.mark.parametrize(
    ("address", "lan"),
    [
        ("10.1.2.3", True),
        ("172.16.0.1", True),
        ("172.31.255.255", True),
        ("192.168.1.1", True),
        ("169.254.10.10", True),
        ("fe80::1", True),
        ("fd12:3456::1", True),
        ("172.32.0.1", False),
        ("100.64.0.1", False),
        ("8.8.8.8", False),
        ("127.0.0.1", False),
        ("::1", False),
        ("192.0.2.1", False),
        ("198.18.0.5", False),
        ("0.0.0.1", False),
        ("255.255.255.255", False),
        ("2001:db8::1", False),
        ("2001:4860:4860::8888", False),
        ("camera.local", False),
    ],
)
def test_is_lan_address(address: str, lan: bool) -> None:
    """Only RFC 1918, link-local and unique-local are LAN; special-use ranges are not."""
    assert ice.is_lan_address(address) is lan
