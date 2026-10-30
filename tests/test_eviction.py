"""Eviction accounting: the sliding-window repair and failed-reclaim reauth.

These drive the real Account against a fake CatLink Cloud that evicts on demand,
and assert on the repair issue it raises and the re-authentication a failed
reclaim starts.
"""

from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from freezegun import freeze_time
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.const import CONF_PASSWORD, CONF_TOKEN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from custom_components.catlink.const import (
    CONF_ACCOUNT_ID,
    CONF_API_BASE,
    CONF_PHONE,
    CONF_PHONE_IAC,
    DOMAIN,
)
from custom_components.catlink.modules.account import Account


class FakeCloud:
    """A fake CatLink Cloud that evicts device-list requests on demand."""

    def __init__(self, *, login_ok: bool = True) -> None:
        """Initialise the fake cloud."""
        self.login_ok = login_ok
        self.evict = True
        self.login_count = 0

    async def request(self, method, url, **kwargs):
        """Answer a request, evicting the device list while ``evict`` is set."""
        resp = MagicMock()
        if url.endswith("login/password") or url.endswith("login/email/v2"):
            self.login_count += 1
            body = (
                {"returnCode": 0, "data": {"token": "fresh-tok"}}
                if self.login_ok
                else {"returnCode": 12002, "msg": "Wrong password"}
            )
        elif url.endswith("token/user/info"):
            body = {
                "returnCode": 0,
                "data": {"appUser": {"id": "acc-1", "globalId": None}},
            }
        elif url.endswith("token/device/union/list/sorted"):
            body = (
                {"returnCode": 1002, "msg": "Illegal token"}
                if self.evict
                else {"returnCode": 0, "data": {"devices": [{"id": "d1"}]}}
            )
        else:
            body = {"returnCode": 0, "data": {}}
        resp.json = AsyncMock(return_value=body)
        return resp


def make_account(hass: HomeAssistant, fake: FakeCloud) -> Account:
    """Build an Account whose HTTP session is the fake cloud."""
    session = MagicMock()
    session.request = fake.request
    with patch(
        "custom_components.catlink.modules.account.aiohttp_client."
        "async_create_clientsession",
        return_value=session,
    ):
        return Account(
            hass,
            {
                CONF_API_BASE: "https://app.catlinks.cn/api/",
                CONF_PHONE_IAC: "86",
                CONF_PHONE: "13812345678",
                CONF_PASSWORD: "secret",
                CONF_ACCOUNT_ID: "acc-1",
                CONF_TOKEN: "stale-tok",
            },
        )


def add_entry(hass: HomeAssistant) -> MockConfigEntry:
    """Register a config entry keyed by the Account's cloud id."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_PHONE_IAC: "86",
            CONF_PHONE: "13812345678",
            CONF_API_BASE: "https://app.catlinks.cn/api/",
            CONF_PASSWORD: "secret",
            CONF_ACCOUNT_ID: "acc-1",
        },
        options={},
        unique_id="acc-1",
    )
    entry.add_to_hass(hass)
    return entry


def issue_id(entry: MockConfigEntry) -> str:
    """Build the repair issue id for the entry."""
    return f"repeated_eviction_{entry.entry_id}"


async def test_single_eviction_raises_no_repair(
    hass: HomeAssistant, enable_custom_integrations, hass_storage
) -> None:
    """Test one Eviction is below the threshold and raises no repair."""
    entry = add_entry(hass)
    acc = make_account(hass, FakeCloud())

    await acc.get_devices()

    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id(entry)) is None


async def test_repeated_evictions_raise_repair(
    hass: HomeAssistant, enable_custom_integrations, hass_storage
) -> None:
    """Test the repair appears once Evictions reach the threshold in the window."""
    entry = add_entry(hass)
    acc = make_account(hass, FakeCloud())

    for _ in range(3):
        await acc.get_devices()

    issue = ir.async_get(hass).async_get_issue(DOMAIN, issue_id(entry))
    assert issue is not None
    assert issue.severity == ir.IssueSeverity.WARNING
    assert issue.translation_key == "repeated_eviction"
    assert issue.is_fixable is False


async def test_evictions_outside_the_window_do_not_stack(
    hass: HomeAssistant, enable_custom_integrations, hass_storage
) -> None:
    """Test Evictions older than the window fall out before the threshold trips."""
    entry = add_entry(hass)
    with freeze_time("2026-09-20 10:00:00") as frozen:
        acc = make_account(hass, FakeCloud())

        await acc.get_devices()
        await acc.get_devices()
        frozen.tick(timedelta(minutes=61))
        await acc.get_devices()

        assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id(entry)) is None


async def test_repair_clears_after_quiet_period(
    hass: HomeAssistant, enable_custom_integrations, hass_storage
) -> None:
    """Test the repair clears itself once 24 hours pass without an Eviction."""
    entry = add_entry(hass)
    fake = FakeCloud()
    with freeze_time("2026-09-20 10:00:00") as frozen:
        acc = make_account(hass, fake)

        for _ in range(3):
            await acc.get_devices()
        assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id(entry)) is not None

        fake.evict = False
        frozen.tick(timedelta(hours=24, minutes=1))
        await acc.get_devices()

        assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id(entry)) is None


async def test_failed_reclaim_starts_reauth(
    hass: HomeAssistant, enable_custom_integrations, hass_storage
) -> None:
    """Test a reclaim that cannot sign in starts re-authentication for the entry."""
    add_entry(hass)
    acc = make_account(hass, FakeCloud(login_ok=False))

    with patch(
        "homeassistant.config_entries.ConfigEntry.async_start_reauth"
    ) as mock_reauth:
        await acc.get_devices()

    mock_reauth.assert_called_once()
