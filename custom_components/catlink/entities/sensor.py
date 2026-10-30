"""Sensor entity for CatLink integration."""

from homeassistant.components.sensor import SensorEntity
from homeassistant.exceptions import HomeAssistantError

from .base import CatlinkEntity

SCHEDULE_DEVICE_TYPE = "VISUAL_PRO_ULTRA"


class CatlinkSensorEntity(CatlinkEntity, SensorEntity):
    """Sensor entity for CatLink."""

    async def async_add_timing(self, time, **kwargs) -> bool:
        """Add a timed cleaning schedule."""
        return await self._schedule_action("async_add_timing", time)

    async def async_update_timing(self, timing_id, time, **kwargs) -> bool:
        """Update a timed cleaning schedule."""
        return await self._schedule_action("async_update_timing", timing_id, time)

    async def async_remove_timing(self, timing_id, **kwargs) -> bool:
        """Remove a timed cleaning schedule."""
        return await self._schedule_action("async_remove_timing", timing_id)

    async def async_toggle_timing(self, timing_id, enabled, **kwargs) -> bool:
        """Enable or disable a timed cleaning schedule."""
        return await self._schedule_action("async_toggle_timing", timing_id, enabled)

    async def _schedule_action(self, method: str, *args) -> bool:
        """Dispatch a schedule service to the device, refusing other device types."""
        if self._device.type != SCHEDULE_DEVICE_TYPE:
            raise HomeAssistantError(
                f"{method} is only supported on {SCHEDULE_DEVICE_TYPE} devices"
            )
        return await getattr(self._device, method)(*args)
