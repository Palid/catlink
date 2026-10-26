"""Helper functions for the CatLink integration."""

from collections.abc import Mapping
from datetime import timedelta
import re
from typing import TYPE_CHECKING, Any, NamedTuple

import phonenumbers
from phonenumbers import NumberParseException

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (
    _LOGGER,
    API_SERVERS,
    AREA_SERVER_LIST_API,
    CLIENT_SERVER_REGION,
    CONF_ACCOUNT_ID,
    CONF_API_BASE,
    CONF_EMAIL,
    CONF_PASSWORD,
    CONF_PHONE,
    CONF_PHONE_IAC,
    DEFAULT_API_BASE,
    DOMAIN,
    FALLBACK_REGIONS,
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


class RegionDiscovery(NamedTuple):
    """What discovery learned: the base that signed in and the profile it read."""

    api_base: str | None
    account_id: str | None
    client_server: str | None
    country_code: str | None


def _base_from_server_region(server_region: str | None) -> str | None:
    """Turn a serverRegion host from the area table into an API base URL."""
    if not server_region:
        return None
    return f"{server_region.rstrip('/')}/api/"


async def _area_server_candidate(
    hass: HomeAssistant, phone_iac: str | None
) -> str | None:
    """Ask the unauthenticated area table for the host this caller should use.

    Picks by the country the cloud detects, else by the Phone's country code.
    Costs no sign-in, so no Session and no Eviction. Returns None when the table
    is unreachable or names nothing that matches.
    """
    from .modules.account import Account

    account = Account(hass, {CONF_API_BASE: DEFAULT_API_BASE})
    rsp = await account.request(AREA_SERVER_LIST_API)
    if rsp.get("returnCode") != 0:
        return None
    data = rsp.get("data") or {}
    entries = data.get("areaList") or []
    area_code = data.get("areaCode")
    if area_code:
        for entry in entries:
            if entry.get("twoCode") == area_code:
                return _base_from_server_region(entry.get("serverRegion"))
    if phone_iac:
        for entry in entries:
            if entry.get("mobileCode") == phone_iac:
                return _base_from_server_region(entry.get("serverRegion"))
    return None


def _candidate_order(candidate: str | None) -> list[str]:
    """List the bases to try, candidate first then the fallback, de-duplicated."""
    order: list[str] = []
    if candidate:
        order.append(candidate)
    for region in FALLBACK_REGIONS:
        base = API_SERVERS.get(region)
        if base and base not in order:
            order.append(base)
    return order


def _host_region_class(api_base: str) -> str:
    """Classify a host into a market the way the app's substring tests do."""
    host = api_base.lower()
    if "sgp" in host:
        return "singapore"
    if "usa" in host:
        return "usa"
    return "china"


def _log_region_mismatch(api_base: str, client_server: str | None) -> None:
    """Warn, without switching, when the profile's region disagrees with the host."""
    expected = CLIENT_SERVER_REGION.get(str(client_server or ""))
    if expected is None or expected == _host_region_class(api_base):
        return
    _LOGGER.warning(
        "Account profile clientServer %s points at the %s region but sign-in used"
        " %s; keeping the working host and not switching",
        client_server,
        expected,
        api_base,
    )


def _discovered(account, api_base: str) -> RegionDiscovery:
    """Build the discovery result from a signed-in account, logging any mismatch."""
    _log_region_mismatch(api_base, account.client_server)
    return RegionDiscovery(
        api_base,
        account.account_id or None,
        account.client_server,
        account.country_code,
    )


async def discover_region(
    hass: HomeAssistant,
    phone_iac: str | None = None,
    phone_number: str | None = None,
    password: str | None = None,
    *,
    email: str | None = None,
    api_base: str | None = None,
) -> RegionDiscovery:
    """Find the region an Account signs in on with as few sign-ins as possible.

    When ``api_base`` is given (re-authentication), that stored base is tried
    once first; on success neither the area lookup nor the fallback runs.
    Otherwise the unauthenticated area table names a candidate host, tried before
    the reordered, de-duplicated fallback over the four known servers.

    Returns a RegionDiscovery whose api_base is None when no server signs in. The
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

    tried: set[str] = set()
    if api_base:
        account = Account(hass, {CONF_API_BASE: api_base, **credentials})
        if await account.async_login():
            return _discovered(account, api_base)
        tried.add(api_base)

    candidate = await _area_server_candidate(hass, phone_iac)
    for base in _candidate_order(candidate):
        if base in tried:
            continue
        account = Account(hass, {CONF_API_BASE: base, **credentials})
        if await account.async_login():
            return _discovered(account, base)
    return RegionDiscovery(None, None, None, None)


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
