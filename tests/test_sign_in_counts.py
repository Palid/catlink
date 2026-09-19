"""Sign-in count proofs: fresh setup and legacy-entry restart against a fake cloud.

These drive the real Account and real discover_region so the number of Sessions
started (each an Eviction of the phone app) is measured, not mocked.
"""

from unittest.mock import AsyncMock, MagicMock, patch

from custom_components.catlink import _adopt_account_id
from custom_components.catlink.const import (
    CONF_ACCOUNT_ID,
    CONF_EMAIL,
    CONF_PHONE,
    CONF_PHONE_IAC,
    DOMAIN,
)
from custom_components.catlink.modules.account import Account
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant import data_entry_flow
from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant


class FakeCloud:
    """A fake CatLink Cloud that counts sign-ins and rejects stale tokens."""

    def __init__(self, account_id: str = "acc-1") -> None:
        """Initialise the fake cloud."""
        self.account_id = account_id
        self.fresh_token = "fresh-tok"
        self.valid_tokens: set[str] = set()
        self.login_count = 0

    async def request(self, method, url, **kwargs):
        """Answer a signed request, counting sign-ins and checking the token."""
        pms = kwargs.get("data") or kwargs.get("params") or {}
        token = pms.get("token")
        if url.endswith("login/email/v2") or url.endswith("login/password"):
            self.login_count += 1
            self.valid_tokens.add(self.fresh_token)
            body = {"returnCode": 0, "data": {"token": self.fresh_token}}
        elif url.endswith("token/user/info"):
            if token in self.valid_tokens:
                body = {
                    "returnCode": 0,
                    "data": {"appUser": {"id": self.account_id, "globalId": None}},
                }
            else:
                body = {"returnCode": 1002, "msg": "Illegal token"}
        elif url.endswith("token/device/union/list/sorted"):
            if token in self.valid_tokens:
                body = {"returnCode": 0, "data": {"devices": []}}
            else:
                body = {"returnCode": 1002, "msg": "Illegal token"}
        else:
            body = {"returnCode": 0, "data": {}}
        resp = MagicMock()
        resp.json = AsyncMock(return_value=body)
        return resp


@pytest.fixture
def fake_cloud():
    """Patch the account HTTP session onto a fake cloud and return it."""
    fake = FakeCloud()
    session = MagicMock()
    session.request = fake.request
    with patch(
        "custom_components.catlink.modules.account.aiohttp_client."
        "async_create_clientsession",
        return_value=session,
    ):
        yield fake


async def test_fresh_phone_setup_signs_in_once(
    hass: HomeAssistant, enable_custom_integrations, hass_storage, fake_cloud
) -> None:
    """Test a fresh Phone setup starts exactly one Session."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "phone"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_PHONE: "+8613812345678", "password": "secretpass"},
    )

    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_ACCOUNT_ID] == "acc-1"
    assert result["result"].unique_id == "acc-1"
    assert fake_cloud.login_count == 1


async def test_fresh_email_setup_signs_in_once(
    hass: HomeAssistant, enable_custom_integrations, hass_storage, fake_cloud
) -> None:
    """Test a fresh Email setup starts exactly one Session."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "email"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_EMAIL: "cat@example.com", "password": "secretpass"},
    )

    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_ACCOUNT_ID] == "acc-1"
    assert result["result"].unique_id == "acc-1"
    assert fake_cloud.login_count == 1


async def test_legacy_entry_restart_signs_in_once_then_zero(
    hass: HomeAssistant, enable_custom_integrations, hass_storage, fake_cloud
) -> None:
    """Test an evicted legacy entry re-logs in once, adopts, then stops evicting."""
    hass_storage["catlink/auth-86-13812345678.json"] = {
        "version": 1,
        "data": {"phone": "13812345678", "token": "stale-tok"},
    }
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_PHONE_IAC: "86",
            CONF_PHONE: "13812345678",
            "api_base": "https://app.catlinks.cn/api/",
            "password": "secretpass",
        },
        options={},
        unique_id="86-13812345678",
    )
    entry.add_to_hass(hass)

    acc = Account(hass, {**entry.data})
    await acc.async_check_auth()
    await acc.get_devices()
    _adopt_account_id(hass, entry, acc)

    assert fake_cloud.login_count == 1
    assert acc.account_id == "acc-1"
    assert entry.unique_id == "acc-1"
    assert entry.data[CONF_ACCOUNT_ID] == "acc-1"

    acc2 = Account(hass, {**entry.data})
    await acc2.async_check_auth()
    await acc2.get_devices()

    assert fake_cloud.login_count == 1
