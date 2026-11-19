"""Sign-in switch for CatLink integration."""

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory

from ..modules.devices_coordinator import DevicesCoordinator


class CatlinkSignInSwitch(SwitchEntity):
    """Pauses or resumes signing in to the CatLink Cloud for one Account."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_translation_key = "sign_in"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_entity_registry_visible_default = False

    def __init__(self, entry: ConfigEntry, coordinator: DevicesCoordinator) -> None:
        """Initialize the switch."""
        self._coordinator = coordinator
        self._account = coordinator.account
        self._attr_unique_id = f"{entry.entry_id}-sign_in"

    @property
    def is_on(self) -> bool:
        """Return whether signing in is allowed."""
        return not self._account.sign_in_paused

    @property
    def icon(self) -> str:
        """Return the icon for the current state."""
        return "mdi:login" if self.is_on else "mdi:account-lock"

    async def async_turn_on(self, **kwargs) -> None:
        """Resume signing in, starting from a refresh."""
        await self._account.async_set_sign_in_paused(False)
        self.async_write_ha_state()
        await self._coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs) -> None:
        """Pause signing in."""
        await self._account.async_set_sign_in_paused(True)
        self.async_write_ha_state()
