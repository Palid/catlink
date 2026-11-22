"""Live Sessions: one per camera Device, shared by its RTSP readers."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import TYPE_CHECKING

from homeassistant.components import network
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from ...const import _LOGGER, DOMAIN
from ..aliyun_channel import AliyunError
from . import ice, sdp
from .depacketiser import AudioChunk, Depacketiser, VideoFrame
from .ice import IceAgent, IceError
from .rtsp import RtspServer
from .signalling import Signalling, SignallingError

if TYPE_CHECKING:
    from ...devices.scooper_pro_ultra import ScooperProUltraDevice

STREAM_QUERY_PATH = "/vision/customer/stream/query"
STREAM_QUERY_API_VERSION = "2.2.0"

IDLE_TIMEOUT = 10
QUERY_TIMEOUT = 15
ANSWER_TIMEOUT = 10
# The camera holds ICE off for about 1.5 s after it answers.
ICE_TIMEOUT = 5
MEDIA_TIMEOUT = 5
RECONNECT_BACKOFF = 1
RECONNECT_BACKOFF_MAX = 60

_FAILURES = (AliyunError, HomeAssistantError, IceError, OSError, SignallingError)


class LiveViewError(HomeAssistantError):
    """A Live Session cannot deliver media; the message is the reason to show."""


class LiveSession:
    """One Device's on-demand Live Session, shared by the readers of its RTSP server."""

    def __init__(self, hass: HomeAssistant, device: ScooperProUltraDevice) -> None:
        """Create the Device's RTSP server; nothing opens until the first reader."""
        self._hass = hass
        self._device = device
        self._rtsp = RtspServer(self._on_demand)
        self._readers = False
        self._runner: asyncio.Task | None = None
        self._stopping: asyncio.Task | None = None
        self._backoff: asyncio.Event | None = None
        self._idle: asyncio.TimerHandle | None = None
        self._streaming = False
        self._keyframe: bytes | None = None
        self._listeners: list[Callable[[], None]] = []

    @property
    def streaming(self) -> bool:
        """Return whether the camera's media is flowing into Home Assistant."""
        return self._streaming

    @property
    def last_keyframe(self) -> bytes | None:
        """Return the latest keyframe access unit, Annex-B with VPS, SPS and PPS."""
        return self._keyframe

    @property
    def rtsp_url(self) -> str:
        """Return the loopback RTSP URL, whose path is a secret token."""
        return self._rtsp.url

    async def async_start(self) -> None:
        """Start the RTSP server, so that rtsp_url can be handed out."""
        await self._rtsp.start()

    async def async_stop(self) -> None:
        """Stop the RTSP server first, so no reader can reopen it, then close."""
        await self._rtsp.stop()
        runner = self._end()
        if runner is not None:
            runner.cancel()
        if tasks := {task for task in (runner, self._stopping) if task is not None}:
            await asyncio.wait(tasks)

    @callback
    def async_add_listener(self, update: Callable[[], None]) -> Callable[[], None]:
        """Call update whenever streaming changes; return its remover."""
        self._listeners.append(update)

        @callback
        def remove() -> None:
            """Stop calling update."""
            self._listeners.remove(update)

        return remove

    @callback
    def _on_demand(self, readers: bool) -> None:
        """Open on the first reader, cut a backoff short, idle once the last one goes."""
        self._readers = readers
        if self._backoff is not None:
            self._backoff.set()
        if readers:
            if self._idle is not None:
                self._idle.cancel()
                self._idle = None
            if self._runner is None:
                self._runner = self._hass.async_create_background_task(
                    self._run(self._stopping),
                    f"{DOMAIN} live session",
                    eager_start=False,
                )
        elif self._runner is not None and self._idle is None:
            self._idle = self._hass.loop.call_later(IDLE_TIMEOUT, self._idle_expired)

    @callback
    def _idle_expired(self) -> None:
        """Close the Live Session after IDLE_TIMEOUT without a reader."""
        self._idle = None
        self._stopping = self._end()
        self._stopping.cancel()

    def _end(self) -> asyncio.Task | None:
        """Forget the runner and the idle timer, and return the runner."""
        if self._idle is not None:
            self._idle.cancel()
            self._idle = None
        runner, self._runner = self._runner, None
        return runner

    async def _run(self, previous: asyncio.Task | None) -> None:
        """Keep media flowing while there are readers, reconnecting with backoff."""
        if previous is not None:
            await asyncio.wait({previous})
        delay = RECONNECT_BACKOFF
        try:
            while True:
                lost = asyncio.Event()
                try:
                    agent = await self._connect(lost)
                except _FAILURES as exc:
                    self._failed(self._reason(exc))
                    if not self._readers:
                        break
                    woken = await self._back_off(delay)
                    if not self._readers:
                        break
                    delay = (
                        RECONNECT_BACKOFF
                        if woken
                        else min(2 * delay, RECONNECT_BACKOFF_MAX)
                    )
                    continue
                delay = RECONNECT_BACKOFF
                try:
                    self._device.clear_warning("live_view")
                    self._set_state(True)
                    await lost.wait()
                    _LOGGER.debug("Live view for %s lost the camera", self._device.name)
                finally:
                    await agent.stop()
                self._set_state(False)
                self._rtsp.discontinuity()
                if not self._readers:
                    break
        finally:
            self._set_state(False)
            if self._runner is asyncio.current_task():
                self._end()

    async def _back_off(self, delay: float) -> bool:
        """Wait out the delay; return True when a change in demand cut it short."""
        self._backoff = asyncio.Event()
        try:
            async with asyncio.timeout(delay):
                await self._backoff.wait()
        except TimeoutError:
            return False
        finally:
            self._backoff = None
        return True

    async def _connect(self, lost: asyncio.Event) -> IceAgent:
        """Open one connection: stream/query, Signalling, ICE, the first keyframe."""
        if self._device.camera_off:
            raise LiveViewError("The camera is switched off")
        signal_url = await self._stream_query()
        local_addresses = await self._local_addresses()
        offer = sdp.build_offer()
        signalling = Signalling(async_get_clientsession(self._hass), signal_url)
        flowing = asyncio.Event()
        # on_rtp only runs once ICE has selected the camera, after depacketiser exists.
        agent = IceAgent(
            local_ufrag=offer.ufrag,
            local_pwd=offer.pwd,
            session_id=signalling.session_id,
            local_addresses=local_addresses,
            on_rtp=lambda datagram: self._on_rtp(depacketiser, flowing, datagram),
            on_lost=lost.set,
        )
        try:
            try:
                await signalling.open(agent.add_remote_candidate)
                await agent.bind()
                await signalling.send_offer(offer.sdp)
                for line in agent.local_candidates():
                    await signalling.send_candidate(line)
                answer = await signalling.wait_answer(ANSWER_TIMEOUT)
                key = answer.pwd[:16].encode()
                if len(key) != 16:
                    raise LiveViewError("The camera's answer carries no media key")
                depacketiser = Depacketiser(key)
                await agent.connect(
                    answer.ufrag, answer.pwd, answer.candidates, ICE_TIMEOUT
                )
                acknowledged = await agent.start_streaming()
            finally:
                await signalling.close()
            if not acknowledged:
                _LOGGER.debug(
                    "%s did not acknowledge startStreaming", self._device.name
                )
            try:
                async with asyncio.timeout(MEDIA_TIMEOUT):
                    await flowing.wait()
            except TimeoutError:
                raise LiveViewError("The camera sent no video") from None
        except BaseException:
            await agent.stop()
            raise
        return agent

    async def _stream_query(self) -> str:
        """Ask the cloud for a Signalling URL for the Device's main stream."""
        iot_id = self._device.iot_id
        if not iot_id:
            raise LiveViewError("The Device has no Aliyun device id")
        channel = await self._device.account.async_aliyun_channel()
        try:
            async with asyncio.timeout(QUERY_TIMEOUT):
                data = await channel.async_request(
                    STREAM_QUERY_PATH,
                    STREAM_QUERY_API_VERSION,
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
        except TimeoutError:
            raise LiveViewError("stream/query timed out") from None
        p2p_info = data.get("p2pInfo") if isinstance(data, dict) else None
        signal_url = p2p_info.get("signalUrl") if isinstance(p2p_info, dict) else None
        if not (isinstance(signal_url, str) and signal_url):
            raise LiveViewError("stream/query returned no Signalling URL")
        return signal_url

    async def _local_addresses(self) -> list[str]:
        """Return the enabled IPv4 LAN addresses from the network integration."""
        addresses = [
            str(address)
            for address in await network.async_get_enabled_source_ips(self._hass)
            if address.version == 4 and ice.is_lan_address(str(address))
        ]
        if not addresses:
            raise LiveViewError("Home Assistant has no enabled LAN IPv4 address")
        return addresses

    def _on_rtp(
        self, depacketiser: Depacketiser, flowing: asyncio.Event, datagram: bytes
    ) -> None:
        """Forward media to the RTSP stream; keep the last keyframe."""
        for event in depacketiser.feed(datagram):
            if isinstance(event, VideoFrame) and event.clock is not None:
                self._rtsp.push_video(event.data, event.clock, event.keyframe)
                if event.keyframe:
                    self._keyframe = event.data
                    flowing.set()
            elif isinstance(event, AudioChunk) and event.clock is not None:
                self._rtsp.push_audio(event.data, event.clock)

    def _reason(self, exc: Exception) -> str:
        """Return the reason to show for a failed attempt, free of any secret."""
        if isinstance(exc, AliyunError):
            return f"stream/query failed: {exc}"
        return str(exc)

    def _failed(self, reason: str) -> None:
        """Log why an attempt failed, as a warning once until media flows again."""
        self._device.warn_once(
            "live_view", "Live view for %s failed: %s", self._device.name, reason
        )
        _LOGGER.debug("Live view for %s failed: %s", self._device.name, reason)

    def _set_state(self, streaming: bool) -> None:
        """Store whether media flows and tell the listeners when it changes."""
        if streaming == self._streaming:
            return
        self._streaming = streaming
        for update in list(self._listeners):
            update()
