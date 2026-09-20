"""Time entity for CatLink integration."""

from datetime import time

from homeassistant.components.time import TimeEntity

from .base import CatlinkEntity


class CatlinkTimeEntity(CatlinkEntity, TimeEntity):
    """Time entity for CatLink."""

    def update(self) -> None:
        """Update the entity."""
        super().update()
        self._attr_native_value = self._attr_state

    @property
    def native_value(self) -> time | None:
        """Return the current time value."""
        return self._attr_native_value

    @property
    def state(self) -> str | None:
        """Return the ISO-formatted state."""
        value = self._attr_native_value
        return value.isoformat() if value is not None else None

    async def async_set_value(self, value: time) -> bool:
        """Change the time."""
        ret = False
        fun = self._option.get("async_set_time")
        if callable(fun):
            ret = await fun(value, entity=self)
        if ret:
            self._attr_native_value = value
        await self._async_after_action(bool(ret), self._option.get("delay_update"))
        return ret
