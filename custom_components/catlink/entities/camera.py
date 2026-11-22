"""Camera entity for CatLink integration."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from homeassistant.components.camera import (
    Camera,
    CameraEntityFeature,
    get_dynamic_camera_stream_settings,
)
from homeassistant.components.ffmpeg import get_ffmpeg_manager
from homeassistant.components.stream import Orientation, Stream, create_stream

from ..const import _LOGGER, CAMERA_CODEC_H264, CONF_CAMERA_CODEC
from ..modules.live_view.session import LiveSession
from .base import CatlinkEntity

if TYPE_CHECKING:
    from ..devices.scooper_pro_ultra import ScooperProUltraDevice

STILL_TIMEOUT = 5
STILL_ARGS = (
    "-f",
    "hevc",
    "-i",
    "pipe:0",
    "-frames:v",
    "1",
    "-f",
    "image2",
    "-c:v",
    "mjpeg",
    "pipe:1",
)


class CatlinkCameraEntity(Camera, CatlinkEntity):
    """A camera's live view, served from its Device's Live Session."""

    _attr_supported_features = CameraEntityFeature.STREAM

    def __init__(self, name, device: ScooperProUltraDevice, option=None) -> None:
        """Initialize the entity."""
        CatlinkEntity.__init__(self, name, device, option)
        Camera.__init__(self)
        self._session = LiveSession(self.account.hass, device)
        self._stream_lock = asyncio.Lock()
        self._still_lock = asyncio.Lock()
        self._still_keyframe: bytes | None = None
        self._still: bytes | None = None

    async def async_added_to_hass(self) -> None:
        """Start the Live Session's RTSP server, then follow its state."""
        await self._session.async_start()
        self.async_on_remove(
            self._session.async_add_listener(self.async_write_ha_state)
        )
        await super().async_added_to_hass()

    async def async_will_remove_from_hass(self) -> None:
        """Stop the Live Session, its RTSP server and the Stream reading from it."""
        await self._session.async_stop()
        if self.stream is not None:
            await self.stream.stop()
            self.stream = None

    @property
    def is_on(self) -> bool:
        """Return whether the camera is switched on."""
        return not self._device.camera_off

    @property
    def is_streaming(self) -> bool:
        """Return whether the Live Session is delivering media."""
        return self._session.streaming

    async def stream_source(self) -> str:
        """Return the RTSP URL, wrapped for H.264 unless go2rtc transcodes it anyway."""
        url = self._session.rtsp_url
        if self.account.get_config(CONF_CAMERA_CODEC) != CAMERA_CODEC_H264:
            return url
        settings = await get_dynamic_camera_stream_settings(self.hass, self.entity_id)
        if settings.orientation is not Orientation.NO_TRANSFORM:
            return url
        return f"ffmpeg:{url}#video=h264#audio=copy"

    async def async_create_stream(self) -> Stream | None:
        """Create the stream component's Stream on the H.265 URL, which PyAV can open."""
        async with self._stream_lock:
            if self.stream is None:
                self.stream = create_stream(
                    self.hass,
                    self._session.rtsp_url,
                    options=self.stream_options,
                    dynamic_stream_settings=await get_dynamic_camera_stream_settings(
                        self.hass, self.entity_id
                    ),
                    stream_label=self.entity_id,
                )
                self.stream.set_update_callback(self.async_write_ha_state)
            return self.stream

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        """Return the last keyframe as a JPEG, caching only successful decodes."""
        if (keyframe := self._session.last_keyframe) is None:
            return None
        async with self._still_lock:
            if keyframe is not self._still_keyframe:
                self._still = await self._async_decode(keyframe)
                self._still_keyframe = keyframe if self._still is not None else None
        return self._still

    async def _async_decode(self, keyframe: bytes) -> bytes | None:
        """Decode an Annex-B H.265 keyframe to a JPEG with Home Assistant's ffmpeg."""
        try:
            process = await asyncio.create_subprocess_exec(
                get_ffmpeg_manager(self.hass).binary,
                *STILL_ARGS,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            try:
                async with asyncio.timeout(STILL_TIMEOUT):
                    jpeg, _ = await process.communicate(keyframe)
            finally:
                if process.returncode is None:
                    process.kill()
        except TimeoutError:
            reason = "ffmpeg timed out"
        except OSError as exc:
            reason = f"ffmpeg did not start: {exc}"
        else:
            if process.returncode == 0 and jpeg:
                return jpeg
            reason = f"ffmpeg exited with {process.returncode}"
        _LOGGER.debug("No still image for %s: %s", self._device.name, reason)
        return None
