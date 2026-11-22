"""Tests for the live view Signalling client, against a scripted loopback server."""

from __future__ import annotations

import asyncio
import json
import logging

import aiohttp
import pytest

from custom_components.catlink.modules.live_view.signalling import (
    Signalling,
    SignallingError,
)

from .live_view_fakes import (
    ANSWER_SDP,
    CAMERA_PWD,
    CANDIDATE,
    PUBLIC_CANDIDATE,
    REPORT_SECRET,
    SESSION,
    TOKEN,
    FakeSignallingServer,
    logged,
    message,
    until,
)

OUR_CANDIDATE = "candidate:1 1 udp 2 192.168.1.5 5 typ host"
LATE_CANDIDATE = "candidate:3 1 udp 2122260223 10.0.0.21 50021 typ host"


@pytest.fixture
async def http(socket_enabled: None):
    """Yield a real aiohttp client session for loopback connections."""
    async with aiohttp.ClientSession() as session:
        yield session


def _ignore(candidate) -> None:
    """Drop a camera candidate."""


async def _start(answer_sdp: str | None = ANSWER_SDP) -> FakeSignallingServer:
    """Start a fake signalling server on loopback."""
    fake = FakeSignallingServer(answer_sdp)
    await fake.server.start_server()
    return fake


async def test_exchange_passes_early_candidates_on_and_ignores_noise(
    http: aiohttp.ClientSession, caplog: pytest.LogCaptureFixture
) -> None:
    """Candidates before the answer reach the callback, and the rest is ignored."""
    fake = await _start()
    signalling = Signalling(http, fake.url())
    heard = []
    try:
        with caplog.at_level(logging.DEBUG):
            await signalling.open(heard.append)
            await signalling.send_offer("v=0\r\n")
            await signalling.send_candidate(OUR_CANDIDATE)
            answer = await signalling.wait_answer(5)
            await signalling.close()
    finally:
        await fake.server.close()

    assert signalling.session_id == SESSION
    assert answer.ufrag == "Cam12"
    assert answer.pwd == CAMERA_PWD
    assert [(c.ip, c.port) for c in answer.candidates] == [("10.0.0.20", 50020)]
    assert [(c.transport, c.ip, c.port) for c in heard] == [
        ("udp", "10.0.0.20", 50020),
        ("udp", "203.0.113.9", 50000),
    ]
    assert fake.expect_headers == ["101"]
    assert fake.query_strings == [f"token={TOKEN}&session={SESSION}"]
    text = logged(caplog)
    assert "Signalling answer received" in text
    for secret in (TOKEN, SESSION, CAMERA_PWD, "Cam12", REPORT_SECRET):
        assert secret not in text


async def test_offer_and_candidate_envelopes(http: aiohttp.ClientSession) -> None:
    """The offer and candidate carry the app's envelope, fields and order."""
    fake = await _start()
    signalling = Signalling(http, fake.url())
    try:
        await signalling.open(_ignore)
        await signalling.send_offer("v=0\r\n")
        await signalling.send_candidate(OUR_CANDIDATE)
        await signalling.wait_answer(5)
        await signalling.close()
    finally:
        await fake.server.close()

    offer, candidate = (json.loads(text) for text in fake.received)
    assert list(offer) == ["version", "params"]
    assert offer["version"] == "1.0.1"
    assert list(offer["params"].items()) == [
        ("iotId", ""),
        ("clientId", SESSION),
        ("messageType", "offerSdp"),
        ("clientType", "pc"),
        ("channelType", "websocket"),
        ("Version", "1.3.2"),
        ("sessionId", SESSION),
        ("sdp", "v=0\r\n"),
    ]
    assert list(candidate["params"].items()) == [
        ("sdpMid", "video"),
        ("sdpMLineIndex", 0),
        ("candidate", OUR_CANDIDATE),
        ("iotId", ""),
        ("clientId", SESSION),
        ("messageType", "candidate"),
        ("clientType", "pc"),
        ("channelType", "websocket"),
        ("sessionId", SESSION),
    ]
    assert all(len(text.encode()) < 4096 for text in fake.received)


async def test_messages_stay_under_4_kb(http: aiohttp.ClientSession) -> None:
    """A message of 4096 bytes is refused before sending; 4095 goes out."""
    fake = await _start()
    signalling = Signalling(http, fake.url())
    try:
        await signalling.open(_ignore)
        empty = json.dumps(
            {
                "version": "1.0.1",
                "params": {
                    "iotId": "",
                    "clientId": SESSION,
                    "messageType": "offerSdp",
                    "clientType": "pc",
                    "channelType": "websocket",
                    "Version": "1.3.2",
                    "sessionId": SESSION,
                    "sdp": "",
                },
            },
            separators=(",", ":"),
        )
        with pytest.raises(SignallingError):
            await signalling.send_offer("x" * (4096 - len(empty)))
        await signalling.send_offer("x" * (4095 - len(empty)))
        await signalling.wait_answer(5)
        await signalling.close()
    finally:
        await fake.server.close()

    assert [len(text.encode()) for text in fake.received] == [4095]


async def test_websocket_closes_once_asked(http: aiohttp.ClientSession) -> None:
    """close() ends the WebSocket on the server side and is safe to repeat."""
    fake = await _start()
    signalling = Signalling(http, fake.url())
    try:
        await signalling.open(_ignore)
        await signalling.send_offer("v=0\r\n")
        await signalling.wait_answer(5)
        assert not fake.closed.is_set()
        await signalling.close()
        await asyncio.wait_for(fake.closed.wait(), 5)
        await signalling.close()
    finally:
        await fake.server.close()


async def test_close_before_answer_fails(http: aiohttp.ClientSession) -> None:
    """A WebSocket the server closes before answering fails the wait at once."""
    fake = await _start(None)
    signalling = Signalling(http, fake.url())
    try:
        await signalling.open(_ignore)
        await signalling.send_offer("v=0\r\n")
        with pytest.raises(SignallingError, match="closed before"):
            await signalling.wait_answer(5)
        await signalling.close()
    finally:
        await fake.server.close()


async def test_answer_timeout(http: aiohttp.ClientSession) -> None:
    """No answer within the timeout raises SignallingError."""
    fake = await _start()
    signalling = Signalling(http, fake.url())
    try:
        await signalling.open(_ignore)
        with pytest.raises(SignallingError, match="did not answer"):
            await signalling.wait_answer(0.05)
        await signalling.close()
    finally:
        await fake.server.close()


async def test_refused_handshake_hides_the_url(http: aiohttp.ClientSession) -> None:
    """A refused handshake raises SignallingError without the token or session."""
    fake = await _start()
    signalling = Signalling(http, fake.url("/elsewhere"))
    try:
        with pytest.raises(SignallingError) as err:
            await signalling.open(_ignore)
    finally:
        await fake.server.close()

    assert str(err.value).startswith("Signalling handshake refused with status 4")
    assert err.value.__cause__ is None
    assert TOKEN not in str(err.value)
    assert SESSION not in str(err.value)


class LateCandidates(FakeSignallingServer):
    """Signalling that answers first and trickles candidates 20 ms later."""

    async def replies(self, offer_sdp: str) -> list[str | float]:
        """Return the answer, a pause, then new, repeated and public candidates."""
        return [
            message({"messageType": "answerSdp", "sdp": ANSWER_SDP}),
            0.02,
            message({"messageType": "candidate", "candidate": LATE_CANDIDATE}),
            message({"messageType": "candidate", "candidate": CANDIDATE}),
            message({"messageType": "candidate", "candidate": LATE_CANDIDATE}),
            message({"messageType": "candidate", "candidate": PUBLIC_CANDIDATE}),
        ]


class OddShapes(FakeSignallingServer):
    """Signalling whose candidate and answer carry values that are not text."""

    def __init__(self, answer_sdp: object) -> None:
        """Answer with this sdp value after a candidate that is a number."""
        super().__init__()
        self.odd_sdp = answer_sdp

    async def replies(self, offer_sdp: str) -> list[str | float]:
        """Return a numeric candidate, then the answer."""
        return [
            message({"messageType": "candidate", "candidate": 7}),
            message({"messageType": "candidate", "candidate": ["x"]}),
            message({"messageType": "answerSdp", "sdp": self.odd_sdp}),
        ]


async def test_candidates_after_the_answer_reach_the_callback(
    http: aiohttp.ClientSession,
) -> None:
    """Candidates trickled after the answer go to the callback as they come."""
    fake = LateCandidates()
    await fake.server.start_server()
    signalling = Signalling(http, fake.url())
    heard = []
    try:
        await signalling.open(heard.append)
        await signalling.send_offer("v=0\r\n")
        answer = await signalling.wait_answer(5)
        await until(lambda: len(heard) == 4)
        await signalling.close()
    finally:
        await fake.server.close()

    assert [(c.ip, c.port) for c in answer.candidates] == [("10.0.0.20", 50020)]
    assert [(c.ip, c.port) for c in heard] == [
        ("10.0.0.21", 50021),
        ("10.0.0.20", 50020),
        ("10.0.0.21", 50021),
        ("203.0.113.9", 50000),
    ]


async def test_odd_candidate_is_ignored(http: aiohttp.ClientSession) -> None:
    """A candidate that is not text is skipped and the answer still arrives."""
    fake = OddShapes(ANSWER_SDP)
    await fake.server.start_server()
    signalling = Signalling(http, fake.url())
    try:
        await signalling.open(_ignore)
        await signalling.send_offer("v=0\r\n")
        answer = await signalling.wait_answer(1)
        await signalling.close()
    finally:
        await fake.server.close()

    assert answer.pwd == CAMERA_PWD
    assert [(c.ip, c.port) for c in answer.candidates] == [("10.0.0.20", 50020)]


@pytest.mark.parametrize("answer_sdp", [{"sdp": "v=0"}, 5, ["v=0"]])
async def test_odd_answer_fails_at_once(
    http: aiohttp.ClientSession, answer_sdp: object
) -> None:
    """An answer whose sdp is not text fails the wait with a fixed reason."""
    fake = OddShapes(answer_sdp)
    await fake.server.start_server()
    signalling = Signalling(http, fake.url())
    try:
        await signalling.open(_ignore)
        await signalling.send_offer("v=0\r\n")
        with pytest.raises(SignallingError, match="^The camera's answer has no ICE"):
            await signalling.wait_answer(1)
        await signalling.close()
    finally:
        await fake.server.close()


async def test_answer_without_credentials_fails(http: aiohttp.ClientSession) -> None:
    """An answer that parses without ICE credentials is rejected."""
    fake = await _start("v=0\r\nm=video 9 RTP/AVPF 96\r\na=sendonly\r\n")
    signalling = Signalling(http, fake.url())
    try:
        await signalling.open(_ignore)
        await signalling.send_offer("v=0\r\n")
        with pytest.raises(SignallingError, match="no ICE credentials"):
            await signalling.wait_answer(5)
        await signalling.close()
    finally:
        await fake.server.close()
