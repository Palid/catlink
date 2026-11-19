"""Tests pinning the identity Home Assistant stores for CatLink entities."""

from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.catlink.const import DOMAIN
from custom_components.catlink.modules.account import Account
from custom_components.catlink.modules.devices_coordinator import DevicesCoordinator

CAT = {"id": 169004, "petName": "Zulu", "breedName": "STREET CAT", "weight": 4.7}
DEVICE = {
    "id": "dev123",
    "mac": "AA:BB:CC:DD:EE:FF",
    "model": "LB599",
    "deviceName": "Living Room Litter",
    "deviceType": "LITTER_BOX_599",
}


@pytest.fixture
async def added(hass):
    """Run one refresh and return the entities added per platform domain."""
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN]["config"] = {"devices": []}
    hass.data[DOMAIN]["devices"] = {}
    account = MagicMock(spec=Account)
    account.hass = hass
    account.uid = "86-13812345678"
    account.update_interval = timedelta(minutes=1)
    account.get_devices = AsyncMock(return_value=[DEVICE])
    account.get_cats = AsyncMock(return_value=[CAT])
    account.get_cat_summary_simple = AsyncMock(return_value={})
    account.request = AsyncMock(
        return_value={"data": {"deviceInfo": {"firmwareVersion": "1.2.3"}}}
    )
    coordinator = DevicesCoordinator(account, "entry-1")
    entities = {}

    def adder_for(domain):
        def add(new):
            entities.setdefault(domain, []).extend(new)

        return add

    hass.data[DOMAIN]["add_entities"] = {
        "entry-1": {d: adder_for(d) for d in ("sensor", "binary_sensor", "switch")}
    }
    await coordinator._async_update_data()
    return entities


def _by_key(entities, key, unique_id_prefix):
    return next(
        e
        for e in entities
        if e.unique_id.startswith(unique_id_prefix) and e.unique_id.endswith(f"-{key}")
    )


def _entity_id(entity, hass, domain):
    entity.add_to_platform_start(hass, MagicMock(domain=domain), None)
    return entity.entity_id


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_cat_identity(hass, added) -> None:
    entity = _by_key(added["sensor"], "weight", "CAT_")
    assert entity.unique_id == "CAT_cat-169004-weight"
    assert _entity_id(entity, hass, "sensor") == "sensor.cat_9004_weight"
    info = entity.device_info
    assert info["identifiers"] == {("catlink", "CAT_cat-169004")}
    assert info["name"] == "Zulu"
    assert info["model"] == "STREET CAT"
    assert info["manufacturer"] == "CatLink"
    assert info["sw_version"] is None
    assert not info.get("via_device")


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_device_identity(hass, added) -> None:
    entity = _by_key(added["sensor"], "error", "LITTER_BOX_599_")
    assert entity.unique_id == "LITTER_BOX_599_AA:BB:CC:DD:EE:FF-error"
    assert _entity_id(entity, hass, "sensor") == "sensor.litter_box_599_e_ff_error"
    info = entity.device_info
    assert info["identifiers"] == {("catlink", "LITTER_BOX_599_AA:BB:CC:DD:EE:FF")}
    assert info["name"] == "Living Room Litter"
    assert info["model"] == "LB599"
    assert info["manufacturer"] == "CatLink"
    assert info["sw_version"] == "1.2.3"
    assert not info.get("via_device")


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_short_pet_id_identity(hass) -> None:
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN]["config"] = {"devices": []}
    hass.data[DOMAIN]["devices"] = {}
    account = MagicMock(spec=Account)
    account.hass = hass
    account.uid = "86-13812345678"
    account.update_interval = timedelta(minutes=1)
    account.get_devices = AsyncMock(return_value=[])
    account.get_cats = AsyncMock(return_value=[{"id": 7}])
    account.get_cat_summary_simple = AsyncMock(return_value={})
    coordinator = DevicesCoordinator(account, "entry-1")
    entities = []
    hass.data[DOMAIN]["add_entities"] = {"entry-1": {"sensor": entities.extend}}
    await coordinator._async_update_data()

    entity = _by_key(entities, "weight", "CAT_")
    assert entity.unique_id == "CAT_cat-7-weight"
    assert _entity_id(entity, hass, "sensor") == "sensor.cat_at_7_weight"
    assert entity.name == "Cat 7 weight"
    assert entity.device_info["name"] == "Cat 7"
    assert entity.device_info["model"] == "Cat"
