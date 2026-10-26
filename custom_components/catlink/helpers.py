"""Helper functions for the CatLink integration."""

from collections.abc import Mapping
from datetime import timedelta
import re
from typing import TYPE_CHECKING, Any

import phonenumbers
from phonenumbers import NumberParseException

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (
    API_SERVERS,
    CONF_ACCOUNT_ID,
    CONF_API_BASE,
    CONF_EMAIL,
    CONF_PASSWORD,
    CONF_PHONE,
    CONF_PHONE_IAC,
    DOMAIN,
)

if TYPE_CHECKING:
    from .devices.base import Device
    from .modules.devices_coordinator import DevicesCoordinator


async def async_setup_domain_platform(
    hass: HomeAssistant,
    domain: str,
    async_add_entities,
    extra_setup=None,
) -> None:
    """Set up a domain platform (sensor, switch, select, etc.) via discovery.

    Used when loading via async_load_platform; for config entries use
    async_setup_entry_for instead.
    """
    hass.data[DOMAIN]["add_entities"].setdefault("discovery", {})[domain] = (
        async_add_entities
    )
    await Helper.async_setup_accounts(hass, domain)
    if extra_setup is not None:
        await extra_setup()


def account_uid(config: Mapping[str, Any]) -> str:
    """Build the key an account's stored token and registry entry live under.

    The account id once it is known, otherwise the legacy phone-based key.
    """
    account_id = config.get(CONF_ACCOUNT_ID)
    if account_id:
        return account_id
    phone = config.get(CONF_PHONE)
    if phone:
        return f"{config.get(CONF_PHONE_IAC)}-{phone}"
    return ""


def parse_phone_number(phone: str) -> tuple[str, str]:
    """Parse a full phone number into country code and national number.

    Accepts formats like +447911123456, 447911123456, or 07911123456.
    Returns (phone_iac, phone_number) for CatLink API.
    """
    cleaned = re.sub(r"[\s\-\.\(\)]", "", str(phone).strip())
    if not cleaned.startswith("+"):
        cleaned = "+" + cleaned.lstrip("0")
    try:
        parsed = phonenumbers.parse(cleaned, None)
        return (
            str(parsed.country_code),
            str(parsed.national_number),
        )
    except NumberParseException:
        pass
    digits = re.sub(r"\D", "", str(phone))
    if len(digits) >= 10:
        for length in (3, 2, 1):
            if len(digits) > length:
                return (digits[:length], digits[length:])
    return ("86", digits or "0")


async def discover_region(
    hass: HomeAssistant,
    phone_iac: str | None = None,
    phone_number: str | None = None,
    password: str | None = None,
    *,
    email: str | None = None,
) -> tuple[str | None, str | None]:
    """Try each API region until sign-in succeeds.

    Returns (region key, Account id) on success, or (None, None) if no region
    signs in. The Account id is None when the profile did not carry one. The
    successful sign-in has stored the token under the Account's key, so a caller
    that seeds the Account id into its own config reads that token back instead
    of signing in again.
    """
    from .modules.account import Account

    credentials = {CONF_PASSWORD: password}
    if email:
        credentials[CONF_EMAIL] = email
    else:
        credentials[CONF_PHONE] = phone_number
        credentials[CONF_PHONE_IAC] = phone_iac

    for region in ("global", "china", "usa", "singapore"):
        api_base = API_SERVERS.get(region)
        if not api_base:
            continue
        account = Account(hass, {CONF_API_BASE: api_base, **credentials})
        if await account.async_login():
            return region, account.account_id or None
    return None, None


def optional_int(
    value: Any, device: "Device | None" = None, cause: str | None = None
) -> int | None:
    """Convert a CatLink Cloud value to int, treating null or absent as unknown.

    The CatLink Cloud sends null, omits the key entirely, or sends an empty
    or whitespace-only string, for numeric fields it has no value for;
    every one of those returns None so a sensor reports unknown instead of
    a misleading 0. When device and cause are given, a value that is
    present but not int-parseable is logged once per Device per cause at
    warning level, rather than on every poll.
    """
    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        if device is not None and cause is not None:
            device.warn_once(
                cause, "Unexpected value for %s on %s: %r", cause, device.name, value
            )
        return None


def optional_float(
    value: Any, device: "Device | None" = None, cause: str | None = None
) -> float | None:
    """Convert a CatLink Cloud value to float, treating null or absent as unknown.

    Same rules as optional_int, for a value that is a float rather than a
    count.
    """
    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        if device is not None and cause is not None:
            device.warn_once(
                cause, "Unexpected value for %s on %s: %r", cause, device.name, value
            )
        return None


def format_api_error(rdt: dict) -> str:
    """Build a user-friendly error message from CatLink API response.

    Extracts the 'msg' field when present; otherwise returns a string
    representation of the full response for debugging.
    """
    msg = rdt.get("msg") or rdt.get("message")
    code = rdt.get("returnCode")
    if msg:
        return f"{msg} (returnCode: {code})" if code else str(msg)
    return str(rdt)


class Helper:
    """Helper class for the CatLink integration."""

    @classmethod
    def calculate_update_interval(
        cls, update_interval: str | timedelta | int | float | None
    ) -> timedelta:
        """Calculate the update interval as a timedelta object.

        Args:
            update_interval: A timedelta, seconds (int/float), or "HH:MM:SS" string.

        Returns:
            timedelta: The update interval as a timedelta object.
        """
        if isinstance(update_interval, timedelta):
            return update_interval
        if isinstance(update_interval, (int, float)) and update_interval > 0:
            return timedelta(seconds=int(update_interval))
        if isinstance(update_interval, str) and re.match(
            r"^\d{2}:\d{2}:\d{2}$", update_interval
        ):
            return timedelta(
                hours=int(update_interval[:2]),
                minutes=int(update_interval[3:5]),
                seconds=int(update_interval[6:8]),
            )
        return timedelta(minutes=1)

    @classmethod
    async def async_setup_accounts(cls, hass: HomeAssistant, domain: str) -> None:
        """Set up entities for all coordinators (discovery path only)."""
        coordinators: list[DevicesCoordinator] = list(
            hass.data[DOMAIN]["coordinators"].values()
        )
        for coordinator in coordinators:
            if coordinator.data is not None:
                for sta in coordinator.data.values():
                    await coordinator.update_hass_entities(domain, sta)

    @staticmethod
    def async_setup_entry_for(domain: str):
        """Return async_setup_entry bound to the given platform domain."""

        async def _async_setup_entry(
            hass: HomeAssistant,
            config_entry: ConfigEntry,
            async_add_entities,
        ) -> None:
            """Set up the Catlink platform for a config entry."""
            hass.data[DOMAIN].setdefault("add_entities", {})
            hass.data[DOMAIN]["add_entities"].setdefault(config_entry.entry_id, {})[
                domain
            ] = async_add_entities

            coordinator = hass.data[DOMAIN]["entry_coordinators"].get(
                config_entry.entry_id
            )
            if coordinator is not None and coordinator.data is not None:
                for sta in coordinator.data.values():
                    await coordinator.update_hass_entities(domain, sta)

        return _async_setup_entry
