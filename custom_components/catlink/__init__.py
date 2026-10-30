"""The component."""

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_DEVICES
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv

from .const import (
    _LOGGER,
    CONF_ACCOUNTS,
    CONF_ACCOUNT_ID,
    CONF_DEVICE_IDS,
    DOMAIN,
    SUPPORTED_DOMAINS,
)
from .helpers import account_uid
from .modules.account import Account
from .modules.devices_coordinator import DevicesCoordinator
from .sensor import async_register_request_api_service

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, hass_config: dict) -> bool:
    """Set up the CatLink component."""
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN].setdefault(CONF_ACCOUNTS, {})
    hass.data[DOMAIN].setdefault(CONF_DEVICES, {})
    hass.data[DOMAIN].setdefault("coordinators", {})
    hass.data[DOMAIN].setdefault("add_entities", {})
    hass.data[DOMAIN].setdefault("config", {})
    hass.data[DOMAIN].setdefault("entry_coordinators", {})
    return True


def _adopt_account_id(hass: HomeAssistant, entry: ConfigEntry, acc: Account) -> None:
    """Move an entry keyed by a phone onto the account id its sign-in returned."""
    account_id = acc.account_id
    if not account_id or entry.unique_id == account_id:
        return
    if hass.config_entries.async_entry_for_domain_unique_id(DOMAIN, account_id):
        _LOGGER.warning(
            "Another config entry already holds account id %s, so entry %s keeps its"
            " current unique id",
            account_id,
            entry.entry_id,
        )
        return
    hass.config_entries.async_update_entry(
        entry,
        unique_id=account_id,
        data={**entry.data, CONF_ACCOUNT_ID: account_id},
    )


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up CatLink from a config entry."""
    hass.data[DOMAIN].setdefault("config", {})
    hass.data[DOMAIN]["config"].setdefault(CONF_DEVICES, [])

    config = {**entry.data, **(entry.options or {})}
    acc = Account(hass, config)
    device_ids = entry.options.get(CONF_DEVICE_IDS) if entry.options else None
    coordinator = DevicesCoordinator(acc, entry.entry_id, device_ids=device_ids)

    await acc.async_check_auth()
    await coordinator.async_refresh()
    _adopt_account_id(hass, entry, acc)

    uid = account_uid(entry.data)
    hass.data[DOMAIN][CONF_ACCOUNTS][uid] = acc
    hass.data[DOMAIN]["coordinators"][f"{DOMAIN}-{uid}-{CONF_DEVICES}"] = coordinator
    hass.data[DOMAIN]["entry_coordinators"][entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, SUPPORTED_DOMAINS)

    await async_register_request_api_service(hass)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    await hass.config_entries.async_unload_platforms(entry, SUPPORTED_DOMAINS)

    uid = account_uid(entry.data)
    if uid in hass.data[DOMAIN][CONF_ACCOUNTS]:
        del hass.data[DOMAIN][CONF_ACCOUNTS][uid]
    coordinator_name = f"{DOMAIN}-{uid}-{CONF_DEVICES}"
    if coordinator_name in hass.data[DOMAIN]["coordinators"]:
        del hass.data[DOMAIN]["coordinators"][coordinator_name]
    if entry.entry_id in hass.data[DOMAIN]["entry_coordinators"]:
        del hass.data[DOMAIN]["entry_coordinators"][entry.entry_id]
    if entry.entry_id in hass.data[DOMAIN].get("add_entities", {}):
        del hass.data[DOMAIN]["add_entities"][entry.entry_id]

    return True
