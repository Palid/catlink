"""Tests for the CatLink Cat class and its platform setup."""

from datetime import timedelta
from unittest.mock import MagicMock

import pytest

from custom_components.catlink.cats import Cat
from custom_components.catlink.const import DOMAIN
from custom_components.catlink.helpers import Helper
from custom_components.catlink.modules.account import Account
from custom_components.catlink.modules.devices_coordinator import DevicesCoordinator
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.const import UnitOfMass

CAT_DATA = {
    "id": "169004",
    "petName": "Zulu",
    "breedName": "STREET CAT",
    "gender": "3",
    "weight": 4.7,
    "year": 2,
    "month": 5,
    "birthday": 1609459200000,
    "avatar": "https://example.com/zulu.jpg",
}
SUMMARY = {
    "statusDescription": "Data collection in progress",
    "toilet": {"times": 2, "weightAvg": 4.7, "peed": 1, "pood": 1},
    "drink": {"times": 1},
    "diet": {"times": 3, "intakes": "12.5"},
    "sport": {"activeDuration": 12},
}


@pytest.fixture
def cat():
    return Cat(CAT_DATA, SUMMARY, MagicMock())


class TestCat:
    """Tests for Cat."""

    def test_properties(self, cat) -> None:
        assert cat.pet_id == "169004"
        assert cat.name == "Zulu"
        assert cat.model == "STREET CAT"
        assert cat.weight == 4.7
        assert cat.age_years == 2
        assert cat.age_months == 5
        assert cat.breed == "STREET CAT"
        assert cat.gender_label == "Neutered male"
        assert cat.birthday == "2021-01-01"
        assert cat.avatar_url == "https://example.com/zulu.jpg"
        assert cat.avatar is None
        assert cat.status == "Data collection in progress"
        assert cat.toilet_times == 2
        assert cat.toilet_weight_avg == 4.7
        assert cat.pee_times == 1
        assert cat.poo_times == 1
        assert cat.drink_times == 1
        assert cat.diet_times == 3
        assert cat.diet_intakes == 12.5
        assert cat.sport_active_duration == 12

    def test_defaults_without_data(self) -> None:
        cat = Cat({"id": "7"}, {}, MagicMock())
        assert cat.name == "Cat 7"
        assert cat.model == "Cat"
        assert cat.status is None
        assert cat.birthday is None
        assert cat.gender_label is None
        assert cat.diet_intakes is None

    def test_hass_sensor_keys(self, cat) -> None:
        sensors = cat.hass_sensor
        assert list(sensors) == [
            "status",
            "weight",
            "age_years",
            "age_months",
            "gender_label",
            "breed",
            "birthday",
            "avatar",
            "toilet_times",
            "toilet_weight_avg",
            "pee_times",
            "poo_times",
            "drink_times",
            "diet_times",
            "diet_intakes",
            "sport_active_duration",
        ]
        assert sensors["weight"]["class"] == SensorDeviceClass.WEIGHT
        assert sensors["weight"]["unit"] == UnitOfMass.KILOGRAMS
        assert sensors["avatar"]["entity_picture"] == "https://example.com/zulu.jpg"
        assert sensors["status"]["state_attrs"]()["pet_id"] == "169004"

    def test_not_a_device(self, cat) -> None:
        assert not hasattr(cat, "type")
        assert not hasattr(cat, "mac")
        assert not hasattr(cat, "detail")

    def test_update_replaces_data_and_notifies(self, cat) -> None:
        listener = MagicMock()
        cat.listeners["x"] = listener
        cat.update({**CAT_DATA, "weight": 5.0}, {})
        assert cat.weight == 5.0
        assert cat.status is None
        listener.assert_called_once()

    def test_registry_strings(self, cat) -> None:
        assert cat.registry_id == "CAT_cat-169004"
        assert cat.object_id_prefix == "CAT_9004"
        assert cat.device_info["identifiers"] == {(DOMAIN, "CAT_cat-169004")}


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_platform_setup_adds_cat_entities(hass) -> None:
    """Test loading a platform adds entities for the cats already fetched."""
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN]["config"] = {"devices": []}
    account = MagicMock(spec=Account)
    account.hass = hass
    account.uid = "86-13812345678"
    account.update_interval = timedelta(minutes=1)
    coordinator = DevicesCoordinator(account, "entry-1")
    coordinator.data = {}
    coordinator.cats["169004"] = Cat(CAT_DATA, SUMMARY, coordinator)
    hass.data[DOMAIN]["entry_coordinators"] = {"entry-1": coordinator}
    add = MagicMock()

    await Helper.async_setup_entry_for("sensor")(
        hass, MagicMock(entry_id="entry-1"), add
    )

    added = add.call_args_list
    assert len(added) == 16
    assert {c.args[0][0].unique_id for c in added} >= {"CAT_cat-169004-weight"}
