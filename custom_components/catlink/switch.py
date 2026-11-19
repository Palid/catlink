"""Support for switch."""

from homeassistant.components.switch import DOMAIN as ENTITY_DOMAIN
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .entities import CatlinkSwitchEntity
from .entities.sign_in import CatlinkSignInSwitch
from .helpers import Helper, async_setup_domain_platform

_async_setup_device_switches = Helper.async_setup_entry_for(ENTITY_DOMAIN)


async def async_setup_entry(hass: HomeAssistant, config_entry, async_add_entities):
    """Set up the Device switches and the Account's sign-in switch."""
    await _async_setup_device_switches(hass, config_entry, async_add_entities)
    coordinator = hass.data[DOMAIN]["entry_coordinators"][config_entry.entry_id]
    async_add_entities([CatlinkSignInSwitch(config_entry, coordinator)])


async def async_setup_platform(
    hass: HomeAssistant, config, async_add_entities, discovery_info=None
):
    """Set up the Catlink switch platform."""
    await async_setup_domain_platform(hass, ENTITY_DOMAIN, async_add_entities)
