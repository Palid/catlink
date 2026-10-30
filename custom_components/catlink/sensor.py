"""Support for sensor."""

import voluptuous as vol

from homeassistant.components.sensor import DOMAIN as ENTITY_DOMAIN
from homeassistant.core import HomeAssistant, SupportsResponse
from homeassistant.helpers import config_validation as cv, entity_platform

from .const import _LOGGER, DOMAIN
from .entities import CatlinkSensorEntity
from .helpers import Helper, async_setup_domain_platform

async_setup_entry = Helper.async_setup_entry_for(ENTITY_DOMAIN)
async_setup_accounts = Helper.async_setup_accounts

REQUEST_API_SERVICE = "request_api"
REQUEST_API_SCHEMA = {
    vol.Required("api"): cv.string,
    vol.Optional("params", default={}): vol.Any(dict, None),
    vol.Optional("method", default="GET"): cv.string,
    vol.Optional("throw", default=True): cv.boolean,
}


async def _register_sensor_services() -> None:
    """Register sensor-specific entity services (legacy YAML setup path)."""
    platform = entity_platform.async_get_current_platform()
    platform.async_register_entity_service(
        REQUEST_API_SERVICE,
        REQUEST_API_SCHEMA,
        "async_request_api",
        supports_response=SupportsResponse.OPTIONAL,
    )


async def async_register_request_api_service(hass: HomeAssistant) -> None:
    """Register the request_api service from the config-entry setup path.

    async_setup_platform (and the _register_sensor_services call above) is
    the legacy YAML path, which config-entry setup never reaches.
    """
    if hass.services.has_service(DOMAIN, REQUEST_API_SERVICE):
        return
    platform = next(
        (
            p
            for p in entity_platform.async_get_platforms(hass, DOMAIN)
            if p.domain == ENTITY_DOMAIN
        ),
        None,
    )
    if platform is None:
        _LOGGER.warning(
            "No %s sensor platform found; %s.%s was not registered",
            DOMAIN,
            DOMAIN,
            REQUEST_API_SERVICE,
        )
        return
    platform.async_register_entity_service(
        REQUEST_API_SERVICE,
        REQUEST_API_SCHEMA,
        "async_request_api",
        supports_response=SupportsResponse.OPTIONAL,
    )
    platform.async_register_entity_service(
        "add_timing",
        {vol.Required("time"): cv.string},
        "async_add_timing",
    )
    platform.async_register_entity_service(
        "update_timing",
        {vol.Required("timing_id"): cv.string, vol.Required("time"): cv.string},
        "async_update_timing",
    )
    platform.async_register_entity_service(
        "remove_timing",
        {vol.Required("timing_id"): cv.string},
        "async_remove_timing",
    )
    platform.async_register_entity_service(
        "toggle_timing",
        {vol.Required("timing_id"): cv.string, vol.Required("enabled"): cv.boolean},
        "async_toggle_timing",
    )


async def async_setup_platform(
    hass: HomeAssistant, config, async_add_entities, discovery_info=None
):
    """Set up the Catlink sensor platform."""
    await async_setup_domain_platform(
        hass, ENTITY_DOMAIN, async_add_entities, _register_sensor_services
    )
