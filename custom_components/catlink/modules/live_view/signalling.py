"""Signalling: the vendor WebSocket that carries the offer, answer and candidates."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import json

import aiohttp
from yarl import URL

from ...const import _LOGGER
from . import sdp

ENVELOPE_VERSION = "1.0.1"
CLIENT_VERSION = "1.3.2"
MAX_MESSAGE_BYTES = 4096
CONNECT_TIMEOUT = 15.0


class SignallingError(Exception):
    """Signalling failed; the message never carries the signalUrl or a secret."""


class Signalling:
    """One offer and answer exchange over the signalUrl WebSocket."""

    def __init__(self, session: aiohttp.ClientSession, signal_url: str) -> None:
        """Initialize the client; nothing connects until it is opened."""
        self._session = session
        self._url = URL(signal_url, encoded=True)
        self.session_id = self._url.query.get("session", "")
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._receiver: asyncio.Task | None = None
        self._answer: sdp.Answer | None = None
        self._on_candidate: Callable[[sdp.Candidate], None] | None = None
        self._settled = asyncio.Event()

    async def open(self, on_candidate: Callable[[sdp.Candidate], None]) -> None:
        """Connect with the app's Expect: 101 header and start passing candidates on."""
        self._on_candidate = on_candidate
        # aiohttp's error text carries the signalUrl and its token, so it is dropped.
        try:
            async with asyncio.timeout(CONNECT_TIMEOUT):
                self._ws = await self._session.ws_connect(
                    self._url, headers={"Expect": "101"}
                )
        except aiohttp.WSServerHandshakeError as exc:
            raise SignallingError(
                f"Signalling handshake refused with status {exc.status}"
            ) from None
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise SignallingError(
                f"Signalling connect failed: {type(exc).__name__}"
            ) from None
        self._receiver = asyncio.create_task(self._receive())

    async def send_offer(self, offer_sdp: str) -> None:
        """Send the offer SDP."""
        await self._send(
            {**self._params("offerSdp", Version=CLIENT_VERSION), "sdp": offer_sdp}
        )

    async def send_candidate(self, line: str) -> None:
        """Trickle one of our candidate lines."""
        await self._send(
            {
                "sdpMid": "video",
                "sdpMLineIndex": 0,
                "candidate": line,
                **self._params("candidate"),
            }
        )

    async def wait_answer(self, timeout: float) -> sdp.Answer:
        """Return the camera's answer once it arrives."""
        try:
            async with asyncio.timeout(timeout):
                await self._settled.wait()
        except TimeoutError:
            raise SignallingError("The camera did not answer the offer") from None
        if self._answer is None:
            raise SignallingError("Signalling closed before the camera answered")
        if not (self._answer.ufrag and self._answer.pwd):
            raise SignallingError("The camera's answer has no ICE credentials")
        return self._answer

    async def close(self) -> None:
        """Stop reading and close the WebSocket."""
        if self._receiver is not None:
            self._receiver.cancel()
            await asyncio.wait({self._receiver})
        if self._ws is not None:
            await self._ws.close()

    def _params(self, message_type: str, **fields: str) -> dict:
        """Return the fields every message carries, in the app's order."""
        return {
            "iotId": "",
            "clientId": self.session_id,
            "messageType": message_type,
            "clientType": "pc",
            "channelType": "websocket",
            **fields,
            "sessionId": self.session_id,
        }

    async def _send(self, params: dict) -> None:
        """Send one enveloped message, refusing one over the 4 KB limit."""
        text = json.dumps(
            {"version": ENVELOPE_VERSION, "params": params}, separators=(",", ":")
        )
        if len(text.encode()) >= MAX_MESSAGE_BYTES:
            raise SignallingError(
                f"{params['messageType']} exceeds the Signalling message limit"
            )
        try:
            await self._ws.send_str(text)
        except (aiohttp.ClientError, ConnectionError) as exc:
            raise SignallingError(
                f"Signalling send failed: {type(exc).__name__}"
            ) from None

    async def _receive(self) -> None:
        """Read the camera's messages until the WebSocket closes."""
        async for message in self._ws:
            if message.type is aiohttp.WSMsgType.TEXT:
                self._on_text(message.data)
        self._settled.set()

    def _on_text(self, text: str) -> None:
        """Take the answer and candidates; p2pReportToken and the rest are ignored."""
        try:
            params = json.loads(text).get("params") or {}
            message_type = params.get("messageType")
        except (ValueError, AttributeError):
            return
        if message_type == "answerSdp" and self._answer is None:
            answer_sdp = params.get("sdp")
            self._answer = sdp.parse_answer(
                answer_sdp if isinstance(answer_sdp, str) else ""
            )
            _LOGGER.debug(
                "Signalling answer received (version %s, %d camera candidates)",
                params.get("version"),
                len(self._answer.candidates),
            )
            self._settled.set()
        elif message_type == "candidate":
            line = params.get("candidate")
            try:
                self._on_candidate(
                    sdp.parse_candidate(line if isinstance(line, str) else "")
                )
            except ValueError:
                _LOGGER.debug("Ignoring a malformed camera candidate")
