"""The component."""

import asyncio

from homeassistant.components import persistent_notification
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import slugify

from ..const import _LOGGER, DOMAIN
from ..cats import Cat
from ..devices.base import Device


class CatlinkEntity(CoordinatorEntity):
    """CatlinkEntity."""

    def __init__(self, name, device: Device | Cat, option=None) -> None:
        """Initialize the entity."""
        self.coordinator = device.coordinator
        CoordinatorEntity.__init__(self, self.coordinator)
        self.account = self.coordinator.account
        self._name = name
        self._device = device
        self._option = option or {}
        display_name = self._option.get("name", name)
        self._attr_name = f"{device.name} {display_name}".strip()
        self._attr_device_id = device.registry_id
        self._attr_unique_id = f"{self._attr_device_id}-{name}"
        self._object_id = slugify(f"{device.object_id_prefix}_{name}")
        self._attr_icon = self._option.get("icon")
        self._attr_device_class = self._option.get("class")
        self._attr_native_unit_of_measurement = self._option.get("unit")
        self._attr_state_class = self._option.get("state_class")
        entity_picture = self._option.get("entity_picture")
        if callable(entity_picture):
            self._attr_entity_picture = entity_picture()
        elif entity_picture:
            self._attr_entity_picture = entity_picture
        self._attr_device_info = device.device_info

    def add_to_platform_start(self, hass, platform, parallel_updates) -> None:
        """Start adding the entity, suggesting an id in the platform's domain."""
        super().add_to_platform_start(hass, platform, parallel_updates)
        self.entity_id = f"{platform.domain}.{self._object_id}"

    async def async_added_to_hass(self) -> None:
        """Run when entity about to be added to hass."""
        await super().async_added_to_hass()
        self._device.listeners[self._attr_unique_id] = self._handle_coordinator_update
        self._handle_coordinator_update()

    def _handle_coordinator_update(self):
        self.update()
        self.async_write_ha_state()

    async def _async_after_action(self, success: bool, delay: float | None = None) -> None:
        """Run after an action: write state, optional delay, then coordinator refresh."""
        if success:
            self.async_write_ha_state()
            if delay is not None:
                await asyncio.sleep(delay)
            self._handle_coordinator_update()

    def update(self) -> None:
        """Update the entity."""
        if hasattr(self._device, self._name):
            self._attr_state = getattr(self._device, self._name)
            _LOGGER.debug(
                "Entity update: %s", [self.entity_id, self._name, self._attr_state]
            )
        entity_picture = self._option.get("entity_picture")
        if callable(entity_picture):
            self._attr_entity_picture = entity_picture()

        fun = self._option.get("state_attrs")
        if callable(fun):
            self._attr_extra_state_attributes = fun()

    @property
    def state(self) -> str:
        """Return the state of the entity."""
        return self._attr_state

    async def async_request_api(self, api, params=None, method="GET", **kwargs) -> dict:
        """Request API."""
        throw = kwargs.pop("throw", None)
        rdt = await self.account.request(api, params, method, **kwargs)
        if throw:
            persistent_notification.async_create(
                self.hass,
                f"{rdt}",
                f"Request: {api}",
                f"{DOMAIN}-request",
            )
        return rdt
