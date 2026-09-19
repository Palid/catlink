"""Tests for CatLink platform setup (sensor, switch, binary_sensor, select, button)."""

from unittest.mock import AsyncMock, MagicMock, patch

from custom_components.catlink import binary_sensor, button, select, sensor, switch
from custom_components.catlink.const import (
    CONF_ACCOUNTS,
    CONF_DEVICE_IDS,
    CONF_PHONE,
    CONF_PHONE_IAC,
    CONF_UPDATE_INTERVAL,
    DOMAIN,
    SUPPORTED_DOMAINS,
)
from custom_components.catlink.devices.litterbox import LitterBox
from custom_components.catlink.entities.registry import DOMAIN_ENTITY_CLASSES
from custom_components.catlink.entities.sensor import CatlinkSensorEntity
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_DEVICES
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

DEVICE_UNIQUE_ID = "LITTER_BOX_599_AABBCCDDEEFF-error"


@pytest.fixture
def mock_account():
    """Mock Account to avoid real API calls."""
    with patch("custom_components.catlink.Account") as mock:
        instance = mock.return_value
        instance.uid = "86-13812345678"
        instance.hass = None
        instance.async_check_auth = AsyncMock()
        instance.get_devices = AsyncMock(return_value=[])
        instance.update_interval = __import__("datetime").timedelta(minutes=1)
        instance.get_config = MagicMock(return_value=None)
        yield mock


@pytest.fixture
def mock_coordinator():
    """Mock DevicesCoordinator class."""
    with patch("custom_components.catlink.DevicesCoordinator") as mock:
        instance = mock.return_value
        instance.name = f"{DOMAIN}-86-13812345678-devices"
        instance.async_refresh = AsyncMock()
        instance.data = {}
        yield mock


@pytest.fixture
def mock_config_entry():
    """Return a mock config entry."""
    return MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_PHONE_IAC: "86",
            CONF_PHONE: "13812345678",
            "api_base": "https://app.catlinks.cn/api/",
            "password": "testpass",
        },
        options={CONF_DEVICE_IDS: [], CONF_UPDATE_INTERVAL: 60},
        unique_id="86-13812345678",
    )


@pytest.fixture
async def init_integration(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_account,
    mock_coordinator,
) -> MockConfigEntry:
    """Set up the CatLink integration for platform tests."""
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN].setdefault(CONF_ACCOUNTS, {})
    hass.data[DOMAIN].setdefault(CONF_DEVICES, {})
    hass.data[DOMAIN].setdefault("coordinators", {})
    hass.data[DOMAIN].setdefault("add_entities", {})
    hass.data[DOMAIN].setdefault("config", {CONF_DEVICES: []})
    hass.data[DOMAIN].setdefault("entry_coordinators", {})

    mock_account.return_value.hass = hass

    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    return mock_config_entry


@pytest.fixture
def catlink_device():
    """Return a Device whose entities can be added to a real platform."""
    coordinator = MagicMock()
    coordinator.account = MagicMock()
    device = LitterBox(
        {
            "id": "dev123",
            "mac": "AABBCCDDEEFF",
            "model": "LB599",
            "deviceName": "Test Litter Box",
            "deviceType": "LITTER_BOX_599",
        },
        coordinator,
    )
    device.detail = {"firmwareVersion": "1.0.0", "currentError": "Normal"}
    return device


async def _add_entity(
    hass: HomeAssistant, entry: MockConfigEntry, domain: str, entity
) -> None:
    """Add an entity through the add_entities callback the platform registered."""
    hass.data[DOMAIN]["add_entities"][entry.entry_id][domain]([entity])
    await hass.async_block_till_done()


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_platforms_loaded(init_integration: MockConfigEntry) -> None:
    """Test all supported platforms are loaded."""
    assert init_integration.state is ConfigEntryState.LOADED
    for domain in SUPPORTED_DOMAINS:
        assert domain in ["sensor", "binary_sensor", "switch", "select", "button"]


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_add_entities_registered(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
) -> None:
    """Test add_entities callbacks are registered for each platform."""
    add_entities = hass.data[DOMAIN].get("add_entities", {})
    entry_add = add_entities.get(init_integration.entry_id, {})
    for domain in SUPPORTED_DOMAINS:
        assert domain in entry_add
        assert callable(entry_add[domain])


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_sensor_platform_setup_entry(
    hass: HomeAssistant,
) -> None:
    """Test sensor platform async_setup_entry registers callback and updates entities."""
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN].setdefault("add_entities", {})
    hass.data[DOMAIN].setdefault("entry_coordinators", {})

    mock_add_entities = MagicMock()
    mock_config_entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_PHONE_IAC: "86", CONF_PHONE: "13812345678"},
        entry_id="test-sensor-entry",
    )
    mock_config_entry.add_to_hass(hass)

    await sensor.async_setup_entry(hass, mock_config_entry, mock_add_entities)

    assert mock_config_entry.entry_id in hass.data[DOMAIN]["add_entities"]
    assert "sensor" in hass.data[DOMAIN]["add_entities"][mock_config_entry.entry_id]
    assert (
        hass.data[DOMAIN]["add_entities"][mock_config_entry.entry_id]["sensor"]
        is mock_add_entities
    )


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_switch_platform_setup_entry(
    hass: HomeAssistant,
) -> None:
    """Test switch platform async_setup_entry registers callback."""
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN].setdefault("add_entities", {})
    hass.data[DOMAIN].setdefault("entry_coordinators", {})

    mock_add_entities = MagicMock()
    mock_config_entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_PHONE_IAC: "86", CONF_PHONE: "13812345678"},
        entry_id="test-switch-entry",
    )
    mock_config_entry.add_to_hass(hass)

    await switch.async_setup_entry(hass, mock_config_entry, mock_add_entities)

    assert "switch" in hass.data[DOMAIN]["add_entities"][mock_config_entry.entry_id]


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_all_platforms_have_setup_entry(
    hass: HomeAssistant,
) -> None:
    """Test all supported domains have async_setup_entry."""
    for _domain, module in [
        ("sensor", sensor),
        ("switch", switch),
        ("binary_sensor", binary_sensor),
        ("select", select),
        ("button", button),
    ]:
        assert hasattr(module, "async_setup_entry")
        assert callable(module.async_setup_entry)


@pytest.mark.usefixtures("enable_custom_integrations")
@pytest.mark.parametrize("platform_domain", SUPPORTED_DOMAINS)
async def test_entity_id_uses_platform_domain(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    catlink_device,
    platform_domain: str,
) -> None:
    """Test a newly added entity lands in its platform domain with the documented object id."""
    entity = DOMAIN_ENTITY_CLASSES[platform_domain](
        "error", catlink_device, {"options": ["Normal"]}
    )

    await _add_entity(hass, init_integration, platform_domain, entity)

    assert entity.entity_id == f"{platform_domain}.litter_box_599_eeff_error"


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_registered_entity_keeps_its_entity_id(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    catlink_device,
) -> None:
    """Test an entity already in the registry keeps the entity ID stored there."""
    er.async_get(hass).async_get_or_create(
        "sensor",
        DOMAIN,
        DEVICE_UNIQUE_ID,
        suggested_object_id="renamed_by_the_person",
        config_entry=init_integration,
    )
    entity = CatlinkSensorEntity("error", catlink_device)

    await _add_entity(hass, init_integration, "sensor", entity)

    assert entity.entity_id == "sensor.renamed_by_the_person"


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_no_wrong_domain_warning_logged(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    catlink_device,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Test adding entities logs no wrong-domain deprecation warning."""
    for platform_domain in SUPPORTED_DOMAINS:
        entity = DOMAIN_ENTITY_CLASSES[platform_domain](
            "error", catlink_device, {"options": ["Normal"]}
        )
        await _add_entity(hass, init_integration, platform_domain, entity)

    assert "entity ID with wrong domain" not in caplog.text


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_listeners_fire_after_entity_id_rewrite(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    catlink_device,
) -> None:
    """Test a Device notifies an entity whose ID the registry rewrote."""
    er.async_get(hass).async_get_or_create(
        "sensor",
        DOMAIN,
        DEVICE_UNIQUE_ID,
        suggested_object_id="renamed_by_the_person",
        config_entry=init_integration,
    )
    entity = CatlinkSensorEntity("error", catlink_device)
    await _add_entity(hass, init_integration, "sensor", entity)

    catlink_device.detail = {"currentError": "Litter box full"}
    catlink_device._handle_listeners()  # noqa: SLF001
    await hass.async_block_till_done()

    assert hass.states.get("sensor.renamed_by_the_person").state == "Litter box full"
