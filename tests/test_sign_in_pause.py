"""Pausing sign-in: no Session is started while the switch is off.

These drive the real Account and a real config entry against a fake CatLink
Cloud that evicts on demand, and count sign-ins, reauth flows and repairs.
"""

from unittest.mock import AsyncMock, MagicMock, patch

from custom_components.catlink.const import (
    CONF_ACCOUNT_ID,
    CONF_ALIYUN_CLOUD_KEY,
    CONF_ALIYUN_CLOUD_SECRET,
    CONF_API_BASE,
    CONF_PHONE,
    CONF_PHONE_IAC,
    DOMAIN,
)
from custom_components.catlink.helpers import SignInPausedError
from custom_components.catlink.modules.account import Account
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import (
    CONF_PASSWORD,
    CONF_TOKEN,
    STATE_OFF,
    STATE_ON,
    EntityCategory,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er, issue_registry as ir
from homeassistant.helpers.update_coordinator import UpdateFailed

SIGN_IN_ENTITY_ID = "switch.catlink_sign_in"
PAUSE_STORE_KEY = "catlink/sign_in-pause-entry.json"
PAUSED = {"version": 1, "data": {"paused": True}}


class FakeCloud:
    """A fake CatLink Cloud that counts sign-ins and evicts stale tokens."""

    def __init__(self) -> None:
        """Initialise the fake cloud."""
        self.login_count = 0
        self.valid_tokens: set[str] = set()

    async def request(self, method, url, **kwargs):
        """Answer a signed request."""
        pms = kwargs.get("data") or kwargs.get("params") or {}
        token = pms.get("token")
        if url.endswith("login/password"):
            self.login_count += 1
            self.valid_tokens.add("fresh-tok")
            body = {"returnCode": 0, "data": {"token": "fresh-tok"}}
        elif token not in self.valid_tokens:
            body = {"returnCode": 1002, "msg": "Illegal token"}
        elif url.endswith("token/user/info"):
            body = {"returnCode": 0, "data": {"appUser": {"id": "acc-1"}}}
        elif url.endswith("token/device/union/list/sorted"):
            body = {"returnCode": 0, "data": {"devices": []}}
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
        entry_id="pause-entry",
    )
    entry.add_to_hass(hass)
    return entry


def paused_account(hass: HomeAssistant, token: str = "stale-tok") -> Account:
    """Build a paused Account whose token the cloud rejects."""
    acc = Account(
        hass,
        {
            CONF_API_BASE: "https://app.catlinks.cn/api/",
            CONF_PHONE_IAC: "86",
            CONF_PHONE: "13812345678",
            CONF_PASSWORD: "secret",
            CONF_ACCOUNT_ID: "acc-1",
            CONF_TOKEN: token,
        },
    )
    acc.sign_in_paused = True
    return acc


async def setup_entry(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Set up the entry and settle."""
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def test_paused_1002_does_not_sign_in_or_reauth(
    hass: HomeAssistant, enable_custom_integrations, hass_storage, fake_cloud
) -> None:
    """Test a 1002 while paused starts no Session, reauth or repair."""
    entry = add_entry(hass)
    acc = paused_account(hass)

    with patch(
        "homeassistant.config_entries.ConfigEntry.async_start_reauth"
    ) as mock_reauth:
        for _ in range(3):
            with pytest.raises(SignInPausedError):
                await acc.get_devices()

    assert fake_cloud.login_count == 0
    mock_reauth.assert_not_called()
    issue_id = f"repeated_eviction_{entry.entry_id}"
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None


async def test_paused_without_token_does_not_sign_in(
    hass: HomeAssistant, enable_custom_integrations, hass_storage, fake_cloud
) -> None:
    """Test the getters do not sign in on a missing token while paused."""
    acc = paused_account(hass, token="")

    with pytest.raises(SignInPausedError):
        await acc.get_devices()
    with pytest.raises(SignInPausedError):
        await acc.get_cats()
    assert await acc.async_check_auth() == {}

    assert fake_cloud.login_count == 0


async def test_paused_coordinator_update_fails(
    hass: HomeAssistant, enable_custom_integrations, hass_storage, fake_cloud
) -> None:
    """Test the coordinator update fails with a clear message while paused."""
    entry = add_entry(hass)
    hass_storage[PAUSE_STORE_KEY] = PAUSED
    await setup_entry(hass, entry)
    coordinator = hass.data[DOMAIN]["entry_coordinators"][entry.entry_id]

    with pytest.raises(UpdateFailed, match="sign-in is paused"):
        await coordinator._async_update_data()  # noqa: SLF001

    assert coordinator.last_update_success is False
    assert fake_cloud.login_count == 0


async def test_paused_startup_without_token_sets_up_switch(
    hass: HomeAssistant, enable_custom_integrations, hass_storage, fake_cloud
) -> None:
    """Test setup completes with zero sign-ins and the switch loads, off."""
    entry = add_entry(hass)
    hass_storage[PAUSE_STORE_KEY] = PAUSED

    await setup_entry(hass, entry)

    assert entry.state is ConfigEntryState.LOADED
    assert fake_cloud.login_count == 0
    assert hass.states.get(SIGN_IN_ENTITY_ID).state == STATE_OFF


async def test_switch_registration(
    hass: HomeAssistant, enable_custom_integrations, hass_storage, fake_cloud
) -> None:
    """Test the switch is a hidden config entity with no device."""
    entry = add_entry(hass)
    await setup_entry(hass, entry)

    item = er.async_get(hass).async_get(SIGN_IN_ENTITY_ID)
    assert item.unique_id == "acc-1-sign_in"
    assert item.entity_category is EntityCategory.CONFIG
    assert item.hidden_by is er.RegistryEntryHider.INTEGRATION
    assert item.disabled_by is None
    assert item.device_id is None
    assert hass.states.get(SIGN_IN_ENTITY_ID).state == STATE_ON


async def test_paused_state_survives_reload(
    hass: HomeAssistant, enable_custom_integrations, hass_storage, fake_cloud
) -> None:
    """Test turning the switch off persists across a reload without a sign-in."""
    entry = add_entry(hass)
    await setup_entry(hass, entry)
    logins = fake_cloud.login_count

    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": SIGN_IN_ENTITY_ID}, blocking=True
    )
    assert hass_storage[PAUSE_STORE_KEY]["data"] == {"paused": True}
    assert entry.state is ConfigEntryState.LOADED

    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    assert hass.states.get(SIGN_IN_ENTITY_ID).state == STATE_OFF
    assert fake_cloud.login_count == logins


async def test_toggling_does_not_reload_entry(
    hass: HomeAssistant, enable_custom_integrations, hass_storage, fake_cloud
) -> None:
    """Test the switch changes the flag without reloading the entry."""
    entry = add_entry(hass)
    await setup_entry(hass, entry)
    acc = hass.data[DOMAIN]["accounts"]["acc-1"]

    with patch.object(
        hass.config_entries, "async_reload", new=AsyncMock()
    ) as mock_reload:
        await hass.services.async_call(
            "switch", "turn_off", {"entity_id": SIGN_IN_ENTITY_ID}, blocking=True
        )
        assert acc.sign_in_paused is True
        await hass.services.async_call(
            "switch", "turn_on", {"entity_id": SIGN_IN_ENTITY_ID}, blocking=True
        )

    mock_reload.assert_not_called()
    assert acc.sign_in_paused is False


async def test_resume_signs_in_once_then_refreshes(
    hass: HomeAssistant, enable_custom_integrations, hass_storage, fake_cloud
) -> None:
    """Test turning the switch on starts exactly one Session and refreshes."""
    entry = add_entry(hass)
    hass_storage[PAUSE_STORE_KEY] = PAUSED
    await setup_entry(hass, entry)
    coordinator = hass.data[DOMAIN]["entry_coordinators"][entry.entry_id]
    assert coordinator.last_update_success is False

    with patch.object(
        coordinator, "async_request_refresh", wraps=coordinator.async_refresh
    ) as mock_refresh:
        await hass.services.async_call(
            "switch", "turn_on", {"entity_id": SIGN_IN_ENTITY_ID}, blocking=True
        )
    await hass.async_block_till_done()

    mock_refresh.assert_called_once()
    assert fake_cloud.login_count == 1
    assert coordinator.last_update_success is True
    assert hass_storage[PAUSE_STORE_KEY]["data"] == {"paused": False}
    assert hass.states.get(SIGN_IN_ENTITY_ID).state == STATE_ON


async def test_request_api_while_paused_and_evicted_raises(
    hass: HomeAssistant, enable_custom_integrations, hass_storage, fake_cloud
) -> None:
    """Test a raw API request while paused and evicted raises HomeAssistantError."""
    acc = paused_account(hass)

    with pytest.raises(HomeAssistantError, match="sign-in is paused"):
        await acc.request("token/device/union/list/sorted", {"type": "NONE"})

    assert fake_cloud.login_count == 0


async def test_aliyun_auth_code_failure_while_paused_is_quiet(
    hass: HomeAssistant, enable_custom_integrations, hass_storage, fake_cloud
) -> None:
    """Test a lost CatLink Session while paused is not an Aliyun auth failure."""
    add_entry(hass)
    acc = paused_account(hass)
    acc._config[CONF_ALIYUN_CLOUD_KEY] = "key"  # noqa: SLF001
    acc._config[CONF_ALIYUN_CLOUD_SECRET] = "secret"  # noqa: SLF001
    channel = await acc.async_aliyun_channel()

    with patch(
        "homeassistant.config_entries.ConfigEntry.async_start_reauth"
    ) as mock_reauth, pytest.raises(SignInPausedError):
        await channel.async_open()

    mock_reauth.assert_not_called()
    assert fake_cloud.login_count == 0
