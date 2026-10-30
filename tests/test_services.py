"""Tests for the catlink.request_api service."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import voluptuous as vol
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.catlink.const import (
    CONF_ACCOUNTS,
    CONF_PHONE,
    CONF_PHONE_IAC,
    DOMAIN,
)
from custom_components.catlink.devices.base import Device
from custom_components.catlink.entities.sensor import CatlinkSensorEntity

from homeassistant.components import persistent_notification
from homeassistant.const import CONF_DEVICES
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_platform


def _make_config_entry(phone: str, entry_id: str) -> MockConfigEntry:
    """Build a config entry for one Account."""
    return MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_PHONE_IAC: "86",
            CONF_PHONE: phone,
            "api_base": "https://app.catlinks.cn/api/",
            "password": "testpass",
        },
        options={},
        entry_id=entry_id,
    )


@pytest.fixture
def hass_domain_data(hass: HomeAssistant):
    """Set up hass.data structure required by async_setup_entry."""
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN].setdefault(CONF_ACCOUNTS, {})
    hass.data[DOMAIN].setdefault(CONF_DEVICES, {})
    hass.data[DOMAIN].setdefault("coordinators", {})
    hass.data[DOMAIN].setdefault("add_entities", {})
    hass.data[DOMAIN].setdefault("config", {})
    hass.data[DOMAIN].setdefault("entry_coordinators", {})
    return hass.data[DOMAIN]


async def _setup_entry(hass: HomeAssistant, phone: str, entry_id: str) -> MockConfigEntry:
    """Set up one config entry with a faked Account and DevicesCoordinator (no network)."""
    with (
        patch("custom_components.catlink.Account") as mock_account_cls,
        patch("custom_components.catlink.DevicesCoordinator") as mock_coordinator_cls,
    ):
        account = mock_account_cls.return_value
        account.uid = f"86-{phone}"
        account.async_check_auth = AsyncMock()

        coordinator = mock_coordinator_cls.return_value
        coordinator.name = f"{DOMAIN}-86-{phone}-devices"
        coordinator.async_refresh = AsyncMock()
        coordinator.data = {}

        entry = _make_config_entry(phone, entry_id)
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    return entry


@pytest.fixture
def fake_device():
    """A fake CatLink Device whose Account.request is faked; no network."""
    fake_account = MagicMock()
    fake_account.request = AsyncMock(
        return_value={"returnCode": 0, "data": {"foo": "bar"}}
    )
    fake_coordinator = MagicMock()
    fake_coordinator.account = fake_account

    device = MagicMock(spec=Device)
    device.id = "dev1"
    device.mac = "AABBCCDDEEFF"
    device.type = "LITTER_BOX_599"
    device.name = "Test Litter Box"
    device.model = "LB599"
    device.detail = {}
    device.listeners = {}
    device.coordinator = fake_coordinator
    return device


async def _add_sensor_entity(
    hass: HomeAssistant, device, entry_id: str | None = None
) -> CatlinkSensorEntity:
    """Add a real CatlinkSensorEntity to a running sensor EntityPlatform.

    With entry_id, adds it to that config entry's own sensor platform
    instance rather than just the first one found.
    """
    entity = CatlinkSensorEntity("state", device)
    entity.coordinator = device.coordinator

    platforms = entity_platform.async_get_platforms(hass, DOMAIN)
    sensor_platform = next(
        p
        for p in platforms
        if p.domain == "sensor"
        and (entry_id is None or (p.config_entry and p.config_entry.entry_id == entry_id))
    )
    await sensor_platform.async_add_entities([entity])
    await hass.async_block_till_done()
    return entity


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_request_api_registered_after_config_entry_setup(
    hass: HomeAssistant, hass_domain_data
) -> None:
    """The service does not exist until a config entry has been set up."""
    assert not hass.services.has_service(DOMAIN, "request_api")

    await _setup_entry(hass, "13800000001", "entry-1")

    assert hass.services.has_service(DOMAIN, "request_api")


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_request_api_registered_exactly_once_with_two_entries(
    hass: HomeAssistant, hass_domain_data
) -> None:
    """Two Accounts (two config entries) still register the service once."""
    with patch(
        "homeassistant.core.ServiceRegistry.async_register",
        wraps=hass.services.async_register,
    ) as spy_register:
        await _setup_entry(hass, "13800000001", "entry-1")
        await _setup_entry(hass, "13800000002", "entry-2")

        request_api_registrations = [
            call
            for call in spy_register.call_args_list
            if call.args[1] == "request_api"
        ]

    assert len(request_api_registrations) == 1
    assert hass.services.has_service(DOMAIN, "request_api")


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_request_api_survives_unloading_one_of_two_entries(
    hass: HomeAssistant, hass_domain_data, fake_device
) -> None:
    """Unloading one Account's entry still lets the service reach the other Account's entity."""
    entry1 = await _setup_entry(hass, "13800000001", "entry-1")
    entry2 = await _setup_entry(hass, "13800000002", "entry-2")

    entity = await _add_sensor_entity(hass, fake_device, entry_id=entry2.entry_id)

    await hass.config_entries.async_unload(entry1.entry_id)
    await hass.async_block_till_done()

    response = await hass.services.async_call(
        DOMAIN,
        "request_api",
        {
            "api": "token/litterbox/aboutDevice",
            "params": {"deviceId": "dev1"},
            "throw": False,
        },
        target={"entity_id": entity.entity_id},
        blocking=True,
        return_response=True,
    )

    assert response == {entity.entity_id: {"returnCode": 0, "data": {"foo": "bar"}}}


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_request_api_call_reaches_account_and_returns_response_data(
    hass: HomeAssistant, hass_domain_data, fake_device
) -> None:
    """Calling the service reaches Account.request and returns its JSON as response data."""
    await _setup_entry(hass, "13800000001", "entry-1")
    entity = await _add_sensor_entity(hass, fake_device)

    response = await hass.services.async_call(
        DOMAIN,
        "request_api",
        {
            "api": "token/litterbox/aboutDevice",
            "params": {"deviceId": "dev1"},
            "method": "POST",
            "throw": False,
        },
        target={"entity_id": entity.entity_id},
        blocking=True,
        return_response=True,
    )

    fake_device.coordinator.account.request.assert_called_once_with(
        "token/litterbox/aboutDevice", {"deviceId": "dev1"}, "POST"
    )
    assert response == {entity.entity_id: {"returnCode": 0, "data": {"foo": "bar"}}}


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_request_api_throw_true_creates_notification(
    hass: HomeAssistant, hass_domain_data, fake_device
) -> None:
    """throw: true still surfaces the response as a persistent notification."""
    await _setup_entry(hass, "13800000001", "entry-1")
    entity = await _add_sensor_entity(hass, fake_device)

    with patch.object(persistent_notification, "async_create") as mock_notify:
        await hass.services.async_call(
            DOMAIN,
            "request_api",
            {
                "api": "token/litterbox/aboutDevice",
                "params": {"deviceId": "dev1"},
                "throw": True,
            },
            target={"entity_id": entity.entity_id},
            blocking=True,
        )

    mock_notify.assert_called_once()


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_request_api_rejects_string_params(
    hass: HomeAssistant, hass_domain_data, fake_device
) -> None:
    """A string for params is rejected by the service schema."""
    await _setup_entry(hass, "13800000001", "entry-1")
    entity = await _add_sensor_entity(hass, fake_device)

    with pytest.raises(vol.Invalid):
        await hass.services.async_call(
            DOMAIN,
            "request_api",
            {"api": "token/litterbox/aboutDevice", "params": "not-a-dict"},
            target={"entity_id": entity.entity_id},
            blocking=True,
        )

    fake_device.coordinator.account.request.assert_not_called()
