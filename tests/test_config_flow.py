"""Tests for CatLink config flow."""

from unittest.mock import AsyncMock, MagicMock, patch

from custom_components.catlink.config_flow import _device_label
from custom_components.catlink.const import (
    CONF_ACCOUNT_ID,
    CONF_ALIYUN_CLOUD_KEY,
    CONF_ALIYUN_CLOUD_SECRET,
    CONF_API_BASE,
    CONF_CLIENT_SERVER,
    CONF_COUNTRY_CODE,
    CONF_DEVICE_IDS,
    CONF_EMAIL,
    CONF_PHONE,
    CONF_PHONE_IAC,
    CONF_SWITCH_IDENTIFIER,
    CONF_UPDATE_INTERVAL,
    DOMAIN,
    ERROR_ALIYUN_INVALID,
    ERROR_ALIYUN_REQUIRED,
    ERROR_CANNOT_CONNECT,
    ERROR_INVALID_AUTH,
)
from custom_components.catlink.helpers import RegionDiscovery
from custom_components.catlink.modules.aliyun_channel import (
    AliyunAuthFailed,
    AliyunRequestFailed,
)
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
import voluptuous as vol

from homeassistant import data_entry_flow
from homeassistant.config_entries import SOURCE_REAUTH, SOURCE_USER
from homeassistant.core import HomeAssistant


class TestDeviceLabel:
    """Tests for _device_label helper."""

    def test_device_label_with_name_and_model(self) -> None:
        """Test label when device has name and model."""
        dat = {
            "deviceName": "Living Room",
            "model": "LB599",
            "deviceType": "LITTER_BOX_599",
        }
        assert _device_label(dat, True) == "Living Room (LB599) - Supported"
        assert _device_label(dat, False) == "Living Room (LB599) - Limited support"

    def test_device_label_name_equals_model(self) -> None:
        """Test label when name equals model uses deviceType."""
        dat = {
            "deviceName": "LB599",
            "model": "LB599",
            "deviceType": "LITTER_BOX_599",
        }
        assert _device_label(dat, True) == "LB599 (LITTER_BOX_599) - Supported"

    def test_device_label_fallback_to_model(self) -> None:
        """Test label falls back to model when deviceName missing."""
        dat = {"model": "LB599", "deviceType": "LITTER_BOX_599"}
        assert _device_label(dat, True) == "LB599 (LITTER_BOX_599) - Supported"

    def test_device_label_fallback_to_unknown(self) -> None:
        """Test label falls back to Unknown when name and model missing."""
        dat = {"deviceType": "UNKNOWN"}
        assert _device_label(dat, True) == "Unknown (UNKNOWN) - Supported"

    def test_device_label_pro_ultra_names_aliyun_credentials(self) -> None:
        """Test the Scooper Pro Ultra label says it needs the Aliyun credentials."""
        dat = {
            "deviceName": "ScooperPROUltra",
            "model": "ScooperPROUltra",
            "deviceType": "VISUAL_PRO_ULTRA",
        }
        assert _device_label(dat, True) == (
            "ScooperPROUltra (VISUAL_PRO_ULTRA) - "
            "Supported; needs the Aliyun credentials"
        )


@pytest.fixture(autouse=True)
def mock_discover_region():
    """Mock discover_region to avoid real API calls."""
    with patch(
        "custom_components.catlink.config_flow.discover_region",
        new_callable=AsyncMock,
        return_value=RegionDiscovery(
            "https://app.catlinks.cn/api/", "acc-1", None, None
        ),
    ):
        yield


@pytest.fixture
def mock_account():
    """Mock Account class."""
    with patch("custom_components.catlink.config_flow.Account") as mock:
        instance = mock.return_value
        instance.uid = "86-13812345678"
        instance.account_id = "acc-1"
        instance.async_check_auth = AsyncMock()
        instance.get_devices = AsyncMock(return_value=[])
        yield mock


async def test_user_step_form(hass: HomeAssistant, enable_custom_integrations) -> None:
    """Test the initial user step shows the sign-in method menu."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] == data_entry_flow.FlowResultType.MENU
    assert result["step_id"] == "user"
    assert set(result["menu_options"]) == {"email", "phone"}


async def test_user_step_success_no_devices(
    hass: HomeAssistant, enable_custom_integrations, mock_account
) -> None:
    """Test successful flow with no devices."""
    mock_account.return_value.get_devices = AsyncMock(return_value=[])

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "phone"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"phone": "+8613812345678", "password": "testpass"},
    )

    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    assert result["title"] == "+8613812345678"
    assert result["data"]["phone_iac"] == "86"
    assert result["data"]["phone"] == "13812345678"
    assert result["data"]["api_base"] == "https://app.catlinks.cn/api/"


async def test_user_step_success_with_devices(
    hass: HomeAssistant, enable_custom_integrations, mock_account
) -> None:
    """Test successful flow with devices proceeds to discovery."""
    mock_account.return_value.get_devices = AsyncMock(
        return_value=[
            {
                "id": "dev1",
                "deviceName": "Litter Box",
                "model": "LB599",
                "deviceType": "LITTER_BOX_599",
            }
        ]
    )

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "phone"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"phone": "+8613812345678", "password": "testpass"},
    )

    assert result["type"] == data_entry_flow.FlowResultType.FORM
    assert result["step_id"] == "discovery"


async def test_user_step_invalid_auth(
    hass: HomeAssistant, enable_custom_integrations
) -> None:
    """Test flow shows error when auth fails."""
    with patch(
        "custom_components.catlink.config_flow.discover_region",
        new_callable=AsyncMock,
        return_value=RegionDiscovery(None, None, None, None),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "phone"}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"phone": "+8613812345678", "password": "wrongpass"},
        )

        assert result["type"] == data_entry_flow.FlowResultType.FORM
        assert result["errors"]["base"] == ERROR_INVALID_AUTH


async def test_discovery_step_form(
    hass: HomeAssistant, enable_custom_integrations, mock_account
) -> None:
    """Test discovery step shows device selection and update interval."""
    mock_account.return_value.get_devices = AsyncMock(
        return_value=[
            {
                "id": "dev1",
                "deviceName": "Litter Box",
                "model": "LB599",
                "deviceType": "LITTER_BOX_599",
            },
            {
                "id": "dev2",
                "deviceName": "Feeder",
                "model": "FD001",
                "deviceType": "FEEDER",
            },
        ]
    )

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "phone"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"phone": "+8613812345678", "password": "testpass"},
    )

    assert result["type"] == data_entry_flow.FlowResultType.FORM
    assert result["step_id"] == "discovery"
    assert CONF_DEVICE_IDS in result["data_schema"].schema
    assert CONF_UPDATE_INTERVAL in result["data_schema"].schema


async def test_discovery_step_create_entry(
    hass: HomeAssistant, enable_custom_integrations, mock_account
) -> None:
    """Test discovery step creates entry with selected devices and interval."""
    mock_account.return_value.get_devices = AsyncMock(
        return_value=[
            {
                "id": "dev1",
                "deviceName": "Litter Box",
                "model": "LB599",
                "deviceType": "LITTER_BOX_599",
            },
        ]
    )

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "phone"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"phone": "+8613812345678", "password": "testpass"},
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_DEVICE_IDS: ["dev1"], CONF_UPDATE_INTERVAL: 120},
    )

    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    assert result["title"] == "+8613812345678"
    assert result["options"][CONF_DEVICE_IDS] == ["dev1"]
    assert result["options"][CONF_UPDATE_INTERVAL] == 120


async def test_reauth_flow(
    hass: HomeAssistant, enable_custom_integrations, mock_account
) -> None:
    """Test reauth flow updates entry with new credentials."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_PHONE_IAC: "86",
            CONF_PHONE: "13812345678",
            "api_base": "https://app.catlinks.cn/api/",
            "password": "oldpass",
        },
        unique_id="86-13812345678",
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_REAUTH, "entry_id": entry.entry_id},
        data=entry.data,
    )

    assert result["type"] == data_entry_flow.FlowResultType.FORM
    assert result["step_id"] == "phone"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"phone": "+8613812345678", "password": "newpass"},
    )

    assert result["type"] == data_entry_flow.FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_PHONE] == "13812345678"
    assert entry.data["password"] == "newpass"


async def test_reauth_flow_invalid_auth(
    hass: HomeAssistant, enable_custom_integrations
) -> None:
    """Test reauth flow shows error when auth fails."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_PHONE_IAC: "86",
            CONF_PHONE: "13812345678",
            "api_base": "https://app.catlinks.cn/api/",
            "password": "oldpass",
        },
        unique_id="86-13812345678",
    )
    entry.add_to_hass(hass)

    with patch(
        "custom_components.catlink.config_flow.discover_region",
        new_callable=AsyncMock,
        return_value=RegionDiscovery(None, None, None, None),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_REAUTH, "entry_id": entry.entry_id},
            data=entry.data,
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"phone": "+8613812345678", "password": "wrongpass"},
        )

    assert result["type"] == data_entry_flow.FlowResultType.FORM
    assert result["errors"]["base"] == ERROR_INVALID_AUTH


async def test_options_flow(
    hass: HomeAssistant, enable_custom_integrations, mock_account
) -> None:
    """Test options flow updates device selection and refresh interval."""
    mock_account.return_value.get_devices = AsyncMock(
        return_value=[
            {
                "id": "dev1",
                "deviceName": "Litter Box",
                "model": "LB599",
                "deviceType": "LITTER_BOX_599",
            },
            {
                "id": "dev2",
                "deviceName": "Feeder",
                "model": "FD001",
                "deviceType": "FEEDER",
            },
        ]
    )

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_PHONE_IAC: "86",
            CONF_PHONE: "13812345678",
            "api_base": "https://app.catlinks.cn/api/",
            "password": "testpass",
        },
        options={CONF_DEVICE_IDS: ["dev1"], CONF_UPDATE_INTERVAL: 60},
        unique_id="86-13812345678",
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)

    assert result["type"] == data_entry_flow.FlowResultType.FORM
    assert result["step_id"] == "init"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_DEVICE_IDS: ["dev1", "dev2"], CONF_UPDATE_INTERVAL: 300},
    )

    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_DEVICE_IDS] == ["dev1", "dev2"]
    assert result["data"][CONF_UPDATE_INTERVAL] == 300


async def _pick_sign_in_method(hass: HomeAssistant, method: str) -> dict:
    """Start a user flow and choose a Sign-in Method from the menu."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    return await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": method}
    )


async def test_email_step_form(
    hass: HomeAssistant, enable_custom_integrations
) -> None:
    """Test the Email step asks for an Email and a password."""
    result = await _pick_sign_in_method(hass, "email")

    assert result["type"] == data_entry_flow.FlowResultType.FORM
    assert result["step_id"] == "email"
    assert CONF_EMAIL in result["data_schema"].schema
    assert "password" in result["data_schema"].schema


async def test_phone_step_form(
    hass: HomeAssistant, enable_custom_integrations
) -> None:
    """Test the Phone step asks for a Phone and a password."""
    result = await _pick_sign_in_method(hass, "phone")

    assert result["type"] == data_entry_flow.FlowResultType.FORM
    assert result["step_id"] == "phone"
    assert CONF_PHONE in result["data_schema"].schema
    assert "password" in result["data_schema"].schema


async def test_email_sign_in_creates_entry_keyed_by_account(
    hass: HomeAssistant, enable_custom_integrations, mock_account
) -> None:
    """Test Email sign-in creates an entry whose unique id is the Account id."""
    mock_account.return_value.uid = "acc-1"
    mock_account.return_value.get_devices = AsyncMock(return_value=[])

    result = await _pick_sign_in_method(hass, "email")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_EMAIL: "cat@example.com", "password": "testpass"},
    )

    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    assert result["title"] == "cat@example.com"
    assert result["data"][CONF_EMAIL] == "cat@example.com"
    assert CONF_PHONE not in result["data"]
    assert result["result"].unique_id == "acc-1"


async def test_email_sign_in_reaches_discovery(
    hass: HomeAssistant, enable_custom_integrations, mock_account
) -> None:
    """Test Email sign-in with devices continues to the discovery step."""
    mock_account.return_value.uid = "acc-1"
    mock_account.return_value.get_devices = AsyncMock(
        return_value=[
            {
                "id": "dev1",
                "deviceName": "Litter Box",
                "model": "LB599",
                "deviceType": "LITTER_BOX_599",
            }
        ]
    )

    result = await _pick_sign_in_method(hass, "email")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_EMAIL: "cat@example.com", "password": "testpass"},
    )

    assert result["step_id"] == "discovery"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_DEVICE_IDS: ["dev1"], CONF_UPDATE_INTERVAL: 120},
    )

    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    assert result["title"] == "cat@example.com"
    assert result["result"].unique_id == "acc-1"


async def test_phone_sign_in_creates_entry_keyed_by_account(
    hass: HomeAssistant, enable_custom_integrations, mock_account
) -> None:
    """Test Phone sign-in is keyed by the Account id and adds no Email key."""
    mock_account.return_value.uid = "acc-1"
    mock_account.return_value.get_devices = AsyncMock(return_value=[])

    result = await _pick_sign_in_method(hass, "phone")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_PHONE: "+8613812345678", "password": "testpass"},
    )

    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    assert result["result"].unique_id == "acc-1"
    assert CONF_EMAIL not in result["data"]


async def test_email_step_invalid_auth_stays_on_email_form(
    hass: HomeAssistant, enable_custom_integrations
) -> None:
    """Test a failed Email sign-in reports on the Email form."""
    with patch(
        "custom_components.catlink.config_flow.discover_region",
        new_callable=AsyncMock,
        return_value=RegionDiscovery(None, None, None, None),
    ):
        result = await _pick_sign_in_method(hass, "email")
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_EMAIL: "cat@example.com", "password": "wrongpass"},
        )

    assert result["type"] == data_entry_flow.FlowResultType.FORM
    assert result["step_id"] == "email"
    assert result["errors"]["base"] == ERROR_INVALID_AUTH


async def test_phone_step_invalid_auth_stays_on_phone_form(
    hass: HomeAssistant, enable_custom_integrations
) -> None:
    """Test a failed Phone sign-in reports on the Phone form."""
    with patch(
        "custom_components.catlink.config_flow.discover_region",
        new_callable=AsyncMock,
        return_value=RegionDiscovery(None, None, None, None),
    ):
        result = await _pick_sign_in_method(hass, "phone")
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_PHONE: "+8613812345678", "password": "wrongpass"},
        )

    assert result["type"] == data_entry_flow.FlowResultType.FORM
    assert result["step_id"] == "phone"
    assert result["errors"]["base"] == ERROR_INVALID_AUTH


async def test_reauth_email_entry_opens_email_form(
    hass: HomeAssistant, enable_custom_integrations, mock_account
) -> None:
    """Test re-authentication of an Email entry skips the menu."""
    mock_account.return_value.uid = "acc-1"
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_EMAIL: "cat@example.com",
            CONF_ACCOUNT_ID: "acc-1",
            "api_base": "https://app.catlinks.cn/api/",
            "password": "oldpass",
        },
        unique_id="acc-1",
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_REAUTH, "entry_id": entry.entry_id},
        data=entry.data,
    )

    assert result["type"] == data_entry_flow.FlowResultType.FORM
    assert result["step_id"] == "email"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_EMAIL: "cat@example.com", "password": "newpass"},
    )

    assert result["type"] == data_entry_flow.FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_EMAIL] == "cat@example.com"
    assert entry.data["password"] == "newpass"


async def test_reauth_aborts_when_signed_in_to_a_different_account(
    hass: HomeAssistant, enable_custom_integrations, mock_account
) -> None:
    """Test re-authentication onto a different Account is refused."""
    mock_account.return_value.uid = "acc-2"
    mock_account.return_value.account_id = "acc-2"
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_EMAIL: "cat@example.com",
            CONF_ACCOUNT_ID: "acc-1",
            "api_base": "https://app.catlinks.cn/api/",
            "password": "oldpass",
        },
        unique_id="acc-1",
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_REAUTH, "entry_id": entry.entry_id},
        data=entry.data,
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_EMAIL: "other@example.com", "password": "newpass"},
    )

    assert result["type"] == data_entry_flow.FlowResultType.ABORT
    assert result["reason"] == "wrong_account"
    assert entry.data[CONF_EMAIL] == "cat@example.com"


async def test_reauth_does_not_collide_with_another_entrys_account(
    hass: HomeAssistant, enable_custom_integrations, mock_account
) -> None:
    """Test re-authenticating a legacy entry onto an id another entry holds aborts."""
    mock_account.return_value.uid = "acc-1"
    mock_account.return_value.account_id = "acc-1"
    email_entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_EMAIL: "cat@example.com",
            CONF_ACCOUNT_ID: "acc-1",
            "api_base": "https://app.catlinks.cn/api/",
            "password": "emailpass",
        },
        unique_id="acc-1",
    )
    email_entry.add_to_hass(hass)
    phone_entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_PHONE_IAC: "86",
            CONF_PHONE: "13812345678",
            "api_base": "https://app.catlinks.cn/api/",
            "password": "oldpass",
        },
        unique_id="86-13812345678",
    )
    phone_entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_REAUTH, "entry_id": phone_entry.entry_id},
        data=phone_entry.data,
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_PHONE: "+8613812345678", "password": "newpass"},
    )

    assert result["type"] == data_entry_flow.FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert phone_entry.unique_id == "86-13812345678"
    unique_ids = [entry.unique_id for entry in hass.config_entries.async_entries(DOMAIN)]
    assert len(unique_ids) == len(set(unique_ids))


async def test_email_sign_in_without_account_id_still_dedupes(
    hass: HomeAssistant, enable_custom_integrations, mock_account
) -> None:
    """Test an Email entry with no Account id is keyed by Email and not duplicated."""
    mock_account.return_value.uid = ""
    mock_account.return_value.account_id = ""
    mock_account.return_value.get_devices = AsyncMock(return_value=[])

    with patch(
        "custom_components.catlink.config_flow.discover_region",
        new_callable=AsyncMock,
        return_value=RegionDiscovery(
            "https://app.catlinks.cn/api/", None, None, None
        ),
    ):
        result = await _pick_sign_in_method(hass, "email")
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_EMAIL: "cat@example.com", "password": "testpass"},
        )
        assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
        assert result["result"].unique_id == "cat@example.com"

        result = await _pick_sign_in_method(hass, "email")
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_EMAIL: "cat@example.com", "password": "testpass"},
        )

    assert result["type"] == data_entry_flow.FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert len(hass.config_entries.async_entries(DOMAIN)) == 1


async def test_legacy_entry_adopts_account_id_then_matches_email_sign_in(
    hass: HomeAssistant, enable_custom_integrations, mock_account
) -> None:
    """Test a Phone-keyed entry adopts the Account id and is then matched by Email."""
    mock_account.return_value.uid = "acc-1"
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_PHONE_IAC: "86",
            CONF_PHONE: "13812345678",
            "api_base": "https://app.catlinks.cn/api/",
            "password": "oldpass",
        },
        unique_id="86-13812345678",
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_REAUTH, "entry_id": entry.entry_id},
        data=entry.data,
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_PHONE: "+8613812345678", "password": "newpass"},
    )

    assert result["type"] == data_entry_flow.FlowResultType.ABORT
    assert entry.unique_id == "acc-1"

    result = await _pick_sign_in_method(hass, "email")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_EMAIL: "cat@example.com", "password": "testpass"},
    )

    assert result["type"] == data_entry_flow.FlowResultType.FORM
    assert result["step_id"] == "switch_identifier"


async def test_switch_identifier_accepted_updates_existing_entry(
    hass: HomeAssistant, enable_custom_integrations, mock_account
) -> None:
    """Test accepting the switch moves the entry to the new Account Identifier."""
    mock_account.return_value.uid = "acc-1"
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_PHONE_IAC: "86",
            CONF_PHONE: "13812345678",
            CONF_ACCOUNT_ID: "acc-1",
            "api_base": "https://app.catlinks.cn/api/",
            "password": "oldpass",
        },
        unique_id="acc-1",
    )
    entry.add_to_hass(hass)

    result = await _pick_sign_in_method(hass, "email")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_EMAIL: "cat@example.com", "password": "testpass"},
    )

    assert result["step_id"] == "switch_identifier"
    assert result["description_placeholders"]["current_identifier"] == "+8613812345678"
    assert result["description_placeholders"]["new_identifier"] == "cat@example.com"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_SWITCH_IDENTIFIER: True}
    )

    assert result["type"] == data_entry_flow.FlowResultType.ABORT
    assert len(hass.config_entries.async_entries(DOMAIN)) == 1
    assert entry.data[CONF_EMAIL] == "cat@example.com"
    assert CONF_PHONE not in entry.data
    assert entry.title == "cat@example.com"


async def test_switch_identifier_declined_aborts_as_already_configured(
    hass: HomeAssistant, enable_custom_integrations, mock_account
) -> None:
    """Test declining the switch leaves a single, unchanged entry."""
    mock_account.return_value.uid = "acc-1"
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_PHONE_IAC: "86",
            CONF_PHONE: "13812345678",
            CONF_ACCOUNT_ID: "acc-1",
            "api_base": "https://app.catlinks.cn/api/",
            "password": "oldpass",
        },
        unique_id="acc-1",
    )
    entry.add_to_hass(hass)

    result = await _pick_sign_in_method(hass, "email")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_EMAIL: "cat@example.com", "password": "testpass"},
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_SWITCH_IDENTIFIER: False}
    )

    assert result["type"] == data_entry_flow.FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert len(hass.config_entries.async_entries(DOMAIN)) == 1
    assert CONF_EMAIL not in entry.data


async def test_same_account_identifier_aborts_as_already_configured(
    hass: HomeAssistant, enable_custom_integrations, mock_account
) -> None:
    """Test re-adding the same Account Identifier does not offer a switch."""
    mock_account.return_value.uid = "acc-1"
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_EMAIL: "cat@example.com",
            CONF_ACCOUNT_ID: "acc-1",
            "api_base": "https://app.catlinks.cn/api/",
            "password": "testpass",
        },
        unique_id="acc-1",
    )
    entry.add_to_hass(hass)

    result = await _pick_sign_in_method(hass, "email")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_EMAIL: "cat@example.com", "password": "testpass"},
    )

    assert result["type"] == data_entry_flow.FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_two_accounts_create_two_entries(
    hass: HomeAssistant, enable_custom_integrations, mock_account
) -> None:
    """Test two distinct Accounts can be configured side by side."""
    mock_account.return_value.get_devices = AsyncMock(return_value=[])

    mock_account.return_value.uid = "acc-1"
    result = await _pick_sign_in_method(hass, "email")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_EMAIL: "cat@example.com", "password": "testpass"},
    )
    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY

    mock_account.return_value.uid = "acc-2"
    result = await _pick_sign_in_method(hass, "phone")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_PHONE: "+8613812345678", "password": "testpass"},
    )
    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY

    entries = hass.config_entries.async_entries(DOMAIN)
    assert {entry.unique_id for entry in entries} == {"acc-1", "acc-2"}


async def test_entry_records_client_server_and_country_without_switching(
    hass: HomeAssistant, enable_custom_integrations, mock_account
) -> None:
    """Test the profile's region is stored on the entry but never switches the host."""
    mock_account.return_value.uid = "acc-1"
    mock_account.return_value.get_devices = AsyncMock(return_value=[])

    with patch(
        "custom_components.catlink.config_flow.discover_region",
        new_callable=AsyncMock,
        return_value=RegionDiscovery(
            "https://app.catlinks.cn/api/", "acc-1", "1", "NO"
        ),
    ):
        result = await _pick_sign_in_method(hass, "email")
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_EMAIL: "cat@example.com", "password": "testpass"},
        )

    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_CLIENT_SERVER] == "1"
    assert result["data"][CONF_COUNTRY_CODE] == "NO"
    assert result["data"][CONF_API_BASE] == "https://app.catlinks.cn/api/"


async def test_reauth_discovers_on_the_stored_base(
    hass: HomeAssistant, enable_custom_integrations, mock_account
) -> None:
    """Test re-authentication hands discovery the stored base to sign in on."""
    mock_account.return_value.uid = "acc-1"
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_PHONE_IAC: "65",
            CONF_PHONE: "91234567",
            CONF_ACCOUNT_ID: "acc-1",
            CONF_API_BASE: "https://app-sgp.catlinks.cn/api/",
            "password": "oldpass",
        },
        unique_id="acc-1",
    )
    entry.add_to_hass(hass)

    with patch(
        "custom_components.catlink.config_flow.discover_region",
        new_callable=AsyncMock,
        return_value=RegionDiscovery(
            "https://app-sgp.catlinks.cn/api/", "acc-1", None, None
        ),
    ) as mock_disc:
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_REAUTH, "entry_id": entry.entry_id},
            data=entry.data,
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_PHONE: "+6591234567", "password": "newpass"},
        )

    assert result["type"] == data_entry_flow.FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    mock_disc.assert_awaited_once()
    assert (
        mock_disc.call_args.kwargs["api_base"] == "https://app-sgp.catlinks.cn/api/"
    )


PRO_ULTRA = {
    "id": "dev-ultra",
    "deviceName": "ScooperPROUltra",
    "model": "ScooperPROUltra",
    "deviceType": "VISUAL_PRO_ULTRA",
}


def _schema_defaults(result: dict) -> dict:
    """Return the prefilled value of every form field that has one."""
    return {
        str(key): key.default()
        for key in result["data_schema"].schema
        if key.default is not vol.UNDEFINED
    }


async def test_setup_without_pro_ultra_stores_no_aliyun_credentials(
    hass: HomeAssistant, enable_custom_integrations, mock_account
) -> None:
    """Test an Account without a Scooper Pro Ultra needs no Aliyun credentials."""
    mock_account.return_value.uid = "acc-1"
    mock_account.return_value.get_devices = AsyncMock(
        return_value=[
            {
                "id": "dev1",
                "deviceName": "Litter Box",
                "model": "LB599",
                "deviceType": "LITTER_BOX_599",
            }
        ]
    )

    result = await _pick_sign_in_method(hass, "email")
    assert CONF_ALIYUN_CLOUD_KEY in result["data_schema"].schema
    assert CONF_ALIYUN_CLOUD_SECRET in result["data_schema"].schema

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_EMAIL: "cat@example.com", "password": "testpass"},
    )
    assert result["step_id"] == "discovery"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_DEVICE_IDS: ["dev1"], CONF_UPDATE_INTERVAL: 60},
    )

    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    assert CONF_ALIYUN_CLOUD_KEY not in result["data"]
    assert CONF_ALIYUN_CLOUD_SECRET not in result["data"]


async def test_setup_with_pro_ultra_requires_aliyun_credentials(
    hass: HomeAssistant, enable_custom_integrations, mock_account
) -> None:
    """Test an Account owning a Scooper Pro Ultra cannot finish without them."""
    mock_account.return_value.uid = "acc-1"
    mock_account.return_value.get_devices = AsyncMock(return_value=[PRO_ULTRA])

    result = await _pick_sign_in_method(hass, "email")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_EMAIL: "cat@example.com",
            "password": "testpass",
            CONF_ALIYUN_CLOUD_KEY: "  ",
        },
    )

    assert result["type"] == data_entry_flow.FlowResultType.FORM
    assert result["step_id"] == "email"
    assert result["errors"] == {CONF_ALIYUN_CLOUD_KEY: ERROR_ALIYUN_REQUIRED}
    assert not hass.config_entries.async_entries(DOMAIN)


async def test_setup_with_pro_ultra_stores_aliyun_credentials(
    hass: HomeAssistant, enable_custom_integrations, mock_account
) -> None:
    """Test the Aliyun credentials given for a Scooper Pro Ultra land in the entry."""
    mock_account.return_value.uid = "acc-1"
    mock_account.return_value.get_devices = AsyncMock(return_value=[PRO_ULTRA])

    result = await _pick_sign_in_method(hass, "phone")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_PHONE: "+8613812345678",
            "password": "testpass",
            CONF_ALIYUN_CLOUD_KEY: " test-aliyun-key ",
            CONF_ALIYUN_CLOUD_SECRET: "test-aliyun-secret",
        },
    )
    assert result["step_id"] == "discovery"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_DEVICE_IDS: ["dev-ultra"], CONF_UPDATE_INTERVAL: 60},
    )

    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_ALIYUN_CLOUD_KEY] == "test-aliyun-key"
    assert result["data"][CONF_ALIYUN_CLOUD_SECRET] == "test-aliyun-secret"


async def test_reauth_adds_aliyun_credentials_for_pro_ultra(
    hass: HomeAssistant, enable_custom_integrations, mock_account
) -> None:
    """Test re-authentication offers the Aliyun fields and stores them."""
    mock_account.return_value.uid = "acc-1"
    mock_account.return_value.get_devices = AsyncMock(return_value=[PRO_ULTRA])
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_EMAIL: "cat@example.com",
            CONF_ACCOUNT_ID: "acc-1",
            CONF_API_BASE: "https://app.catlinks.cn/api/",
            "password": "oldpass",
        },
        unique_id="acc-1",
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_REAUTH, "entry_id": entry.entry_id},
        data=entry.data,
    )
    assert CONF_ALIYUN_CLOUD_KEY in result["data_schema"].schema
    assert CONF_ALIYUN_CLOUD_SECRET in result["data_schema"].schema

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_EMAIL: "cat@example.com", "password": "newpass"},
    )
    assert result["type"] == data_entry_flow.FlowResultType.FORM
    assert result["errors"] == {CONF_ALIYUN_CLOUD_KEY: ERROR_ALIYUN_REQUIRED}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_EMAIL: "cat@example.com",
            "password": "newpass",
            CONF_ALIYUN_CLOUD_KEY: "test-aliyun-key",
            CONF_ALIYUN_CLOUD_SECRET: "test-aliyun-secret",
        },
    )

    assert result["type"] == data_entry_flow.FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_ALIYUN_CLOUD_KEY] == "test-aliyun-key"
    assert entry.data[CONF_ALIYUN_CLOUD_SECRET] == "test-aliyun-secret"


async def test_reauth_prefills_the_key_and_keeps_a_blank_secret(
    hass: HomeAssistant, enable_custom_integrations, mock_account
) -> None:
    """Test re-authentication never shows the secret and keeps it when left blank."""
    mock_account.return_value.uid = "acc-1"
    mock_account.return_value.get_devices = AsyncMock(return_value=[PRO_ULTRA])
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_EMAIL: "cat@example.com",
            CONF_ACCOUNT_ID: "acc-1",
            CONF_API_BASE: "https://app.catlinks.cn/api/",
            "password": "oldpass",
            CONF_ALIYUN_CLOUD_KEY: "test-aliyun-key",
            CONF_ALIYUN_CLOUD_SECRET: "test-aliyun-secret",
        },
        unique_id="acc-1",
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_REAUTH, "entry_id": entry.entry_id},
        data=entry.data,
    )
    defaults = _schema_defaults(result)
    assert defaults[CONF_ALIYUN_CLOUD_KEY] == "test-aliyun-key"
    assert CONF_ALIYUN_CLOUD_SECRET not in defaults

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_EMAIL: "cat@example.com", "password": "newpass"},
    )

    assert result["type"] == data_entry_flow.FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data["password"] == "newpass"
    assert entry.data[CONF_ALIYUN_CLOUD_KEY] == "test-aliyun-key"
    assert entry.data[CONF_ALIYUN_CLOUD_SECRET] == "test-aliyun-secret"


async def test_switch_identifier_keeps_the_aliyun_credentials(
    hass: HomeAssistant, enable_custom_integrations, mock_account
) -> None:
    """Test moving an entry to a new Account Identifier keeps its Aliyun credentials."""
    mock_account.return_value.uid = "acc-1"
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_PHONE_IAC: "86",
            CONF_PHONE: "13812345678",
            CONF_ACCOUNT_ID: "acc-1",
            CONF_API_BASE: "https://app.catlinks.cn/api/",
            "password": "oldpass",
            CONF_ALIYUN_CLOUD_KEY: "test-aliyun-key",
            CONF_ALIYUN_CLOUD_SECRET: "test-aliyun-secret",
        },
        unique_id="acc-1",
    )
    entry.add_to_hass(hass)

    result = await _pick_sign_in_method(hass, "email")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_EMAIL: "cat@example.com", "password": "testpass"},
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_SWITCH_IDENTIFIER: True}
    )

    assert result["reason"] == "identifier_switched"
    assert entry.data[CONF_EMAIL] == "cat@example.com"
    assert entry.data[CONF_ALIYUN_CLOUD_KEY] == "test-aliyun-key"
    assert entry.data[CONF_ALIYUN_CLOUD_SECRET] == "test-aliyun-secret"


async def test_switch_identifier_requires_aliyun_credentials_for_pro_ultra(
    hass: HomeAssistant, enable_custom_integrations, mock_account
) -> None:
    """Test switching an entry with no Aliyun credentials asks for them for a Pro Ultra."""
    mock_account.return_value.uid = "acc-1"
    mock_account.return_value.get_devices = AsyncMock(return_value=[PRO_ULTRA])
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_PHONE_IAC: "86",
            CONF_PHONE: "13812345678",
            CONF_ACCOUNT_ID: "acc-1",
            CONF_API_BASE: "https://app.catlinks.cn/api/",
            "password": "oldpass",
        },
        unique_id="acc-1",
    )
    entry.add_to_hass(hass)

    result = await _pick_sign_in_method(hass, "email")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_EMAIL: "cat@example.com", "password": "testpass"},
    )

    assert result["step_id"] == "email"
    assert result["errors"] == {CONF_ALIYUN_CLOUD_KEY: ERROR_ALIYUN_REQUIRED}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_EMAIL: "cat@example.com",
            "password": "testpass",
            CONF_ALIYUN_CLOUD_KEY: "test-aliyun-key",
            CONF_ALIYUN_CLOUD_SECRET: "test-aliyun-secret",
        },
    )
    assert result["step_id"] == "switch_identifier"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_SWITCH_IDENTIFIER: True}
    )

    assert result["reason"] == "identifier_switched"
    assert entry.data[CONF_ALIYUN_CLOUD_KEY] == "test-aliyun-key"
    assert entry.data[CONF_ALIYUN_CLOUD_SECRET] == "test-aliyun-secret"


def _email_entry(hass: HomeAssistant, **aliyun: str) -> MockConfigEntry:
    """Add a configured Email entry holding the given Aliyun credentials, if any."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_EMAIL: "cat@example.com",
            CONF_ACCOUNT_ID: "acc-1",
            CONF_API_BASE: "https://app.catlinks.cn/api/",
            "password": "testpass",
            **aliyun,
        },
        unique_id="acc-1",
    )
    entry.add_to_hass(hass)
    return entry


def _running_account(
    hass: HomeAssistant, entry: MockConfigEntry, devices: list[dict]
) -> MagicMock:
    """Register a running Account for the entry the way async_setup_entry does."""
    account = MagicMock()
    account.token = "test-token"
    account.get_devices = AsyncMock(return_value=devices)
    account.async_check_aliyun_credentials = AsyncMock()
    hass.data.setdefault(DOMAIN, {}).setdefault("entry_coordinators", {})[
        entry.entry_id
    ] = MagicMock(account=account)
    return account


@pytest.fixture
def mock_setup_entry():
    """Stand in for entry setup so the reload after a reconfigure stays offline."""
    with patch(
        "custom_components.catlink.async_setup_entry", return_value=True
    ) as mock:
        yield mock


async def test_reconfigure_adds_aliyun_credentials_after_trying_them(
    hass: HomeAssistant, enable_custom_integrations, mock_setup_entry
) -> None:
    """Test reconfigure tries the credentials on the running Account, then stores them."""
    entry = _email_entry(hass)
    account = _running_account(hass, entry, [PRO_ULTRA])

    result = await entry.start_reconfigure_flow(hass)

    assert result["type"] == data_entry_flow.FlowResultType.FORM
    assert result["step_id"] == "reconfigure"
    assert {str(key) for key in result["data_schema"].schema} == {
        CONF_ALIYUN_CLOUD_KEY,
        CONF_ALIYUN_CLOUD_SECRET,
    }

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_ALIYUN_CLOUD_KEY: " test-aliyun-key ",
            CONF_ALIYUN_CLOUD_SECRET: "test-aliyun-secret",
        },
    )
    await hass.async_block_till_done()

    assert result["type"] == data_entry_flow.FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    account.async_check_aliyun_credentials.assert_awaited_once_with(
        "test-aliyun-key", "test-aliyun-secret"
    )
    assert entry.data[CONF_ALIYUN_CLOUD_KEY] == "test-aliyun-key"
    assert entry.data[CONF_ALIYUN_CLOUD_SECRET] == "test-aliyun-secret"
    assert entry.data["password"] == "testpass"
    mock_setup_entry.assert_awaited_once()


async def test_reconfigure_keeps_the_stored_secret_when_left_blank(
    hass: HomeAssistant, enable_custom_integrations, mock_setup_entry
) -> None:
    """Test reconfigure prefills only the key and keeps the stored secret when blank."""
    entry = _email_entry(
        hass,
        **{
            CONF_ALIYUN_CLOUD_KEY: "test-aliyun-key",
            CONF_ALIYUN_CLOUD_SECRET: "test-aliyun-secret",
        },
    )
    account = _running_account(hass, entry, [PRO_ULTRA])

    result = await entry.start_reconfigure_flow(hass)
    defaults = _schema_defaults(result)
    assert defaults[CONF_ALIYUN_CLOUD_KEY] == "test-aliyun-key"
    assert CONF_ALIYUN_CLOUD_SECRET not in defaults

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ALIYUN_CLOUD_KEY: "test-rotated-key"}
    )

    assert result["reason"] == "reconfigure_successful"
    account.async_check_aliyun_credentials.assert_awaited_once_with(
        "test-rotated-key", "test-aliyun-secret"
    )
    assert entry.data[CONF_ALIYUN_CLOUD_KEY] == "test-rotated-key"
    assert entry.data[CONF_ALIYUN_CLOUD_SECRET] == "test-aliyun-secret"


async def test_reconfigure_requires_aliyun_credentials_for_pro_ultra(
    hass: HomeAssistant, enable_custom_integrations, mock_setup_entry
) -> None:
    """Test reconfigure will not save empty credentials for a Scooper Pro Ultra."""
    entry = _email_entry(hass)
    account = _running_account(hass, entry, [PRO_ULTRA])

    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ALIYUN_CLOUD_KEY: "  "}
    )

    assert result["type"] == data_entry_flow.FlowResultType.FORM
    assert result["step_id"] == "reconfigure"
    assert result["errors"] == {CONF_ALIYUN_CLOUD_KEY: ERROR_ALIYUN_REQUIRED}
    account.async_check_aliyun_credentials.assert_not_awaited()
    assert CONF_ALIYUN_CLOUD_KEY not in entry.data


@pytest.mark.parametrize(
    ("failure", "errors"),
    [
        (
            AliyunAuthFailed("OpenAccount loginbyoauth rejected"),
            {CONF_ALIYUN_CLOUD_KEY: ERROR_ALIYUN_INVALID},
        ),
        (AliyunRequestFailed("transport error"), {"base": ERROR_CANNOT_CONNECT}),
    ],
)
async def test_reconfigure_reports_a_failed_aliyun_check(
    hass: HomeAssistant,
    enable_custom_integrations,
    mock_setup_entry,
    failure: Exception,
    errors: dict,
) -> None:
    """Test credentials Aliyun rejects, or cannot be tried, are not stored."""
    entry = _email_entry(hass)
    account = _running_account(hass, entry, [PRO_ULTRA])
    account.async_check_aliyun_credentials.side_effect = failure

    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_ALIYUN_CLOUD_KEY: "test-aliyun-key",
            CONF_ALIYUN_CLOUD_SECRET: "test-wrong-secret",
        },
    )

    assert result["type"] == data_entry_flow.FlowResultType.FORM
    assert result["errors"] == errors
    assert CONF_ALIYUN_CLOUD_KEY not in entry.data
    mock_setup_entry.assert_not_awaited()


async def test_reconfigure_without_pro_ultra_leaves_the_fields_optional(
    hass: HomeAssistant, enable_custom_integrations, mock_setup_entry
) -> None:
    """Test an Account without a Scooper Pro Ultra can save blank credentials."""
    entry = _email_entry(hass)
    account = _running_account(
        hass,
        entry,
        [{"id": "dev1", "deviceName": "Litter Box", "deviceType": "LITTER_BOX_599"}],
    )

    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})

    assert result["reason"] == "reconfigure_successful"
    account.async_check_aliyun_credentials.assert_not_awaited()
    assert CONF_ALIYUN_CLOUD_KEY not in entry.data
    assert CONF_ALIYUN_CLOUD_SECRET not in entry.data


async def test_reconfigure_of_an_entry_not_loaded_signs_in_and_stores_untried(
    hass: HomeAssistant, enable_custom_integrations, mock_account, mock_setup_entry
) -> None:
    """Test an entry that is not running signs in to list Devices and skips the check."""
    mock_account.return_value.token = "test-token"
    mock_account.return_value.get_devices = AsyncMock(return_value=[PRO_ULTRA])
    mock_account.return_value.async_check_aliyun_credentials = AsyncMock()
    entry = _email_entry(hass)

    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_ALIYUN_CLOUD_KEY: "test-aliyun-key",
            CONF_ALIYUN_CLOUD_SECRET: "test-aliyun-secret",
        },
    )

    assert result["reason"] == "reconfigure_successful"
    assert mock_account.call_args.args[1][CONF_EMAIL] == "cat@example.com"
    mock_account.return_value.async_check_auth.assert_awaited_once()
    mock_account.return_value.async_check_aliyun_credentials.assert_not_awaited()
    assert entry.data[CONF_ALIYUN_CLOUD_KEY] == "test-aliyun-key"
    assert entry.data[CONF_ALIYUN_CLOUD_SECRET] == "test-aliyun-secret"
