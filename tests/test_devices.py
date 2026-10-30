"""Tests for CatLink device classes."""

from datetime import time
from unittest.mock import AsyncMock, MagicMock

from custom_components.catlink.devices.base import Device
from custom_components.catlink.devices.cat import CatDevice
from custom_components.catlink.devices.c08 import C08Device
from custom_components.catlink.devices.feeder import FeederDevice
from custom_components.catlink.devices.litterbox import LitterBox
from custom_components.catlink.devices.registry import create_device
from custom_components.catlink.devices.scooper import ScooperDevice
from custom_components.catlink.devices.scooper_pro_ultra import ScooperProUltraDevice
from custom_components.catlink.devices.purepro import PureProDevice
from custom_components.catlink.models.additional_cfg import AdditionalDeviceConfig
from custom_components.catlink.modules.aliyun_channel import (
    AliyunAuthFailed,
    AliyunRequestFailed,
)
import pytest

from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError


@pytest.fixture
def mock_coordinator():
    """Create a mock DevicesCoordinator."""
    coordinator = MagicMock()
    coordinator.account = MagicMock()
    coordinator.account.uid = "86-13812345678"
    return coordinator


@pytest.fixture
def sample_device_data():
    """Sample device data from API."""
    return {
        "id": "dev123",
        "mac": "AA:BB:CC:DD:EE:FF",
        "model": "LB599",
        "deviceName": "Living Room Litter",
        "deviceType": "LITTER_BOX_599",
    }


@pytest.fixture
def sample_feeder_data():
    """Sample feeder device data."""
    return {
        "id": "feeder1",
        "mac": "11:22:33:44:55:66",
        "model": "Feeder Pro",
        "deviceName": "Kitchen Feeder",
        "deviceType": "FEEDER",
    }


@pytest.fixture
def sample_scooper_data():
    """Sample scooper device data."""
    return {
        "id": "scooper1",
        "mac": "FF:EE:DD:CC:BB:AA",
        "model": "Scooper C1",
        "deviceName": "Basement Scooper",
        "deviceType": "SCOOPER",
    }


@pytest.fixture
def sample_c08_data():
    """Sample C08 device data."""
    return {
        "id": "c08-1",
        "mac": "01:23:45:67:89:AB",
        "model": "Open-X",
        "deviceName": "Bedroom C08",
        "deviceType": "C08",
    }


@pytest.fixture
def sample_pro_ultra_data():
    """Sample Scooper Pro Ultra device data."""
    return {
        "id": "ultra1",
        "mac": "44:72:AC:0D:89:37",
        "model": "ScooperPROUltra",
        "deviceName": "ScooperPROUltra",
        "deviceType": "VISUAL_PRO_ULTRA",
    }


@pytest.fixture
def pro_ultra_brief_info_body():
    """Recorded briefInfo deviceInfo body (2026-09-19 live capture, redacted).

    quietEnable/quietTimes come back null on a device that has never had quiet
    mode configured; they must not render as the app's 23:00/07:00 defaults.
    """
    return {
        "allTimingToggle": True,
        "autoUpdatePetWeight": True,
        "catLitterType": None,
        "currentMessage": None,
        "defaultStatus": None,
        "deodorantCountdown": 30,
        "deviceId": "ultra1",
        "deviceModel": "00",
        "deviceName": "ScooperPROUltra",
        "deviceType": None,
        "litterCountdown": 14,
        "mac": "44:72:AC:0D:89:37",
        "master": 1,
        "qcFlag": False,
        "quietEnable": None,
        "quietTimes": None,
        "secretLock": False,
        "snEnable": True,
        "timezoneId": "Europe/Oslo",
        "totalCleanTimes": "1",
    }


@pytest.fixture
def pro_ultra_timings_body():
    """Recorded timings list body (2026-09-19 live capture, redacted).

    All four schedules have status 0, which means enabled.
    """
    return [
        {"id": "t1", "repeat": None, "status": 0, "timingHour": "08", "timingMin": "00"},
        {"id": "t2", "repeat": None, "status": 0, "timingHour": "12", "timingMin": "00"},
        {"id": "t3", "repeat": None, "status": 0, "timingHour": "16", "timingMin": "00"},
        {"id": "t4", "repeat": None, "status": 0, "timingHour": "20", "timingMin": "00"},
    ]


@pytest.fixture
def pro_ultra_about_body():
    """Recorded aboutDevice info body (2026-09-19 live capture, redacted).

    firmwareVersion/sn are null and wifiSingle is not scalar: they are read from
    the Aliyun Channel by the app and are deferred to issue #8.
    """
    return {
        "careEnd": 1790000000,
        "careStart": 1758000000,
        "deviceName": "ScooperPROUltra",
        "firmwareVersion": None,
        "mac": "44:72:AC:0D:89:37",
        "model": "Standard",
        "sn": None,
    }


@pytest.fixture
def pro_ultra_thing_properties():
    """Recorded Aliyun Thing Property snapshot, flattened as the channel returns it.

    Values are placeholders for the wire shapes documented in the design brief;
    the concrete snapshot becomes a fixture once the owner runs a live read.
    """
    return {
        "RunStatus": "runStatus=0&biz=0",
        "AbnormalState": "0",
        "GarbageBoxStatus": "garbageBox=1&fullLevel=0",
        "CatLitterBalance": "62",
        "DeviceWeihgt": "4.8",
        "WiFiRSSI": "-51",
        "FirmwareVersion": "1.4.2",
        "ChildLock": "1",
        "ContinuousClean": "0",
        "PromptSoundSwitch": "1",
        "SoftStoolMode": "0",
        "SleepMode": "0",
        "CurrentModel": "0",
        "IndicatorLight": "2",
        "DayNightMode": "1",
        "CatLitterType": "1",
        "DelayTimeOfClean": "3",
        "LimitWhenFull": "10",
        "CatLitterFlatTime": "13",
    }


@pytest.fixture
def sample_purepro_data():
    """Sample PurePro device data."""
    return {
        "id": "purepro1",
        "mac": "22:33:44:55:66:77",
        "model": "Pure Pro",
        "deviceName": "Cat Fountain",
        "deviceType": "PUREPRO",
    }


@pytest.fixture
def sample_cat_data():
    """Sample cat device data."""
    return {
        "id": "cat-169004",
        "pet_id": "169004",
        "petName": "Zulu",
        "deviceName": "Zulu",
        "deviceType": "CAT",
        "mac": "cat-169004",
        "model": "STREET CAT",
        "gender": 3,
        "weight": 4.7,
        "summary_simple": {
            "statusDescription": "Data collection in progress",
            "toilet": {"times": 2, "weightAvg": 4.7, "peed": 1, "pood": 1},
            "drink": {"times": 1},
            "diet": {"times": 0, "intakes": "0"},
            "sport": {"activeDuration": 12},
        },
    }


class TestDevice:
    """Tests for base Device class."""

    def test_device_properties(self, mock_coordinator, sample_device_data) -> None:
        """Test Device basic properties from data."""
        device = Device(sample_device_data, mock_coordinator)
        assert device.id == "dev123"
        assert device.mac == "AA:BB:CC:DD:EE:FF"
        assert device.model == "LB599"
        assert device.name == "Living Room Litter"
        assert device.type == "LITTER_BOX_599"

    def test_device_error_from_detail(
        self, mock_coordinator, sample_device_data
    ) -> None:
        """Test Device error property from detail."""
        device = Device(sample_device_data, mock_coordinator)
        device.detail = {"currentMessage": "Device offline"}
        assert device.error == "Device offline"

    def test_device_error_action_error_takes_precedence(
        self, mock_coordinator, sample_device_data
    ) -> None:
        """Test _action_error overrides detail error."""
        device = Device(sample_device_data, mock_coordinator)
        device.detail = {"currentMessage": "Old error"}
        device._set_action_error("Protection is temporarily paused.")  # noqa: SLF001
        assert device.error == "Protection is temporarily paused."

    def test_device_update_data(self, mock_coordinator, sample_device_data) -> None:
        """Test update_data updates device data."""
        device = Device(sample_device_data, mock_coordinator)
        new_data = {**sample_device_data, "deviceName": "Updated Name"}
        device.update_data(new_data)
        assert device.data == new_data
        assert device.name == "Updated Name"


class TestDeviceRegistry:
    """Tests for device registry create_device."""

    def test_create_litterbox(self, mock_coordinator, sample_device_data) -> None:
        """Test create_device returns LitterBox for LITTER_BOX_599."""
        device = create_device(sample_device_data, mock_coordinator)
        assert isinstance(device, LitterBox)
        assert device.id == "dev123"

    def test_create_feeder(self, mock_coordinator, sample_feeder_data) -> None:
        """Test create_device returns FeederDevice for FEEDER."""
        device = create_device(sample_feeder_data, mock_coordinator)
        assert isinstance(device, FeederDevice)
        assert device.name == "Kitchen Feeder"

    def test_create_scooper(self, mock_coordinator, sample_scooper_data) -> None:
        """Test create_device returns ScooperDevice for SCOOPER."""
        device = create_device(sample_scooper_data, mock_coordinator)
        assert isinstance(device, ScooperDevice)
        assert device.name == "Basement Scooper"

    def test_create_c08(self, mock_coordinator, sample_c08_data) -> None:
        """Test create_device returns C08Device for C08."""
        device = create_device(sample_c08_data, mock_coordinator)
        assert isinstance(device, C08Device)
        assert device.name == "Bedroom C08"

    def test_create_scooper_pro_ultra(
        self, mock_coordinator, sample_pro_ultra_data
    ) -> None:
        """Test create_device returns ScooperProUltraDevice for VISUAL_PRO_ULTRA."""
        device = create_device(sample_pro_ultra_data, mock_coordinator)
        assert isinstance(device, ScooperProUltraDevice)

    def test_create_cat_device(
        self, mock_coordinator, sample_cat_data
    ) -> None:
        """Test create_device returns CatDevice for CAT."""
        device = create_device(sample_cat_data, mock_coordinator)
        assert isinstance(device, CatDevice)
        assert device.name == "Zulu"

    def test_create_unknown_type_falls_back_to_base(self, mock_coordinator) -> None:
        """Test unknown device type uses base Device class."""
        data = {
            "id": "unknown1",
            "deviceType": "UNKNOWN_TYPE",
            "deviceName": "Unknown",
        }
        device = create_device(data, mock_coordinator)
        assert isinstance(device, Device)
        assert not isinstance(device, LitterBox)
        assert device.type == "UNKNOWN_TYPE"

    def test_create_with_additional_config(
        self, mock_coordinator, sample_device_data
    ) -> None:
        """Test create_device with AdditionalDeviceConfig."""
        config = AdditionalDeviceConfig(
            mac="AA:BB:CC:DD:EE:FF",
            empty_weight=1.5,
            max_samples_litter=12,
        )
        device = create_device(
            sample_device_data, mock_coordinator, additional_config=config
        )
        assert device.additional_config.mac == "AA:BB:CC:DD:EE:FF"
        assert device.additional_config.empty_weight == 1.5


class TestLitterBox:
    """Tests for LitterBox device."""

    def test_modes_property(self, mock_coordinator, sample_device_data) -> None:
        """Test LitterBox modes."""
        device = LitterBox(sample_device_data, mock_coordinator)
        modes = device.modes
        assert modes["00"] == "auto"
        assert modes["01"] == "manual"
        assert modes["02"] == "time"

    def test_actions_property(self, mock_coordinator, sample_device_data) -> None:
        """Test LitterBox actions."""
        device = LitterBox(sample_device_data, mock_coordinator)
        actions = device.actions
        assert "01" in actions
        assert "00" in actions
        assert "Cleaning" in actions.values()
        assert "Pause" in actions.values()

    def test_box_full_levels(self, mock_coordinator, sample_device_data) -> None:
        """Test LitterBox box full sensitivity levels."""
        device = LitterBox(sample_device_data, mock_coordinator)
        levels = device.box_full_levels
        assert "LEVEL_01" in levels
        assert levels["LEVEL_01"] == "Level 1"

    def test_error_default_normal_operation(
        self, mock_coordinator, sample_device_data
    ) -> None:
        """Test LitterBox error defaults to Normal Operation."""
        device = LitterBox(sample_device_data, mock_coordinator)
        device.detail = {}
        assert device.error == "Normal Operation"

    def test_error_from_detail(self, mock_coordinator, sample_device_data) -> None:
        """Test LitterBox error from detail."""
        device = LitterBox(sample_device_data, mock_coordinator)
        device.detail = {"currentError": "Litter full"}
        assert device.error == "Litter full"

    def test_litter_remaining_days(self, mock_coordinator, sample_device_data) -> None:
        """Test LitterBox litter_remaining_days."""
        device = LitterBox(sample_device_data, mock_coordinator)
        device.detail = {"litterCountdown": 5}
        assert device.litter_remaining_days == 5

    def test_knob_status_cleaning_mode(
        self, mock_coordinator, sample_device_data
    ) -> None:
        """Test LitterBox knob_status when no knob error."""
        device = LitterBox(sample_device_data, mock_coordinator)
        device.detail = {"deviceErrorList": []}
        assert device.knob_status == "Cleaning Mode"

    def test_knob_status_empty_mode(self, mock_coordinator, sample_device_data) -> None:
        """Test LitterBox knob_status when left_knob_abnormal."""
        device = LitterBox(sample_device_data, mock_coordinator)
        device.detail = {"deviceErrorList": [{"errkey": "left_knob_abnormal"}]}
        assert device.knob_status == "Empty Mode"

    def test_garbage_actions(self, mock_coordinator, sample_device_data) -> None:
        """Test LitterBox garbage_actions property."""
        device = LitterBox(sample_device_data, mock_coordinator)
        actions = device.garbage_actions
        assert actions["00"] == "Change Bag"
        assert actions["01"] == "Reset"

    def test_last_sync_with_timestamp(
        self, mock_coordinator, sample_device_data
    ) -> None:
        """Test LitterBox last_sync formats timestamp."""
        device = LitterBox(sample_device_data, mock_coordinator)
        device.detail = {"lastHeartBeatTimestamp": 1700000000000}
        result = device.last_sync
        assert result is not None
        assert "2023" in result or "2024" in result

    def test_last_sync_no_timestamp(self, mock_coordinator, sample_device_data) -> None:
        """Test LitterBox last_sync returns None when no timestamp."""
        device = LitterBox(sample_device_data, mock_coordinator)
        device.detail = {}
        assert device.last_sync is None

    def test_garbage_tobe_status_normal(
        self, mock_coordinator, sample_device_data
    ) -> None:
        """Test LitterBox garbage_tobe_status when no full error."""
        device = LitterBox(sample_device_data, mock_coordinator)
        device.detail = {"deviceErrorList": []}
        assert device.garbage_tobe_status == "Normal"

    def test_garbage_tobe_status_full(
        self, mock_coordinator, sample_device_data
    ) -> None:
        """Test LitterBox garbage_tobe_status when garbage full."""
        device = LitterBox(sample_device_data, mock_coordinator)
        device.detail = {"deviceErrorList": [{"errkey": "garbage_tobe_full_abnormal"}]}
        assert device.garbage_tobe_status == "Full"

    def test_garbage_attrs(self, mock_coordinator, sample_device_data) -> None:
        """Test LitterBox garbage_attrs maps garbageStatus."""
        device = LitterBox(sample_device_data, mock_coordinator)
        device.detail = {"garbageStatus": "00"}
        assert device.garbage_attrs()["status"] == "Normal"

        device.detail = {"garbageStatus": "02"}
        assert device.garbage_attrs()["status"] == "Movement Started"

        device.detail = {"garbageStatus": "03"}
        assert device.garbage_attrs()["status"] == "Moving"

    def test_error_attrs(self, mock_coordinator, sample_device_data) -> None:
        """Test LitterBox error_attrs returns deviceErrorList."""
        device = LitterBox(sample_device_data, mock_coordinator)
        device.detail = {"deviceErrorList": [{"errkey": "test"}]}
        attrs = device.error_attrs()
        assert "errors" in attrs
        assert attrs["errors"] == [{"errkey": "test"}]

    def test_box_full_sensitivity_mapped(
        self, mock_coordinator, sample_device_data
    ) -> None:
        """Test LitterBox box_full_sensitivity maps level to label."""
        device = LitterBox(sample_device_data, mock_coordinator)
        device.detail = {"boxFullSensitivity": "LEVEL_01"}
        assert device.box_full_sensitivity == "Level 1"

    def test_box_full_sensitivity_attrs(
        self, mock_coordinator, sample_device_data
    ) -> None:
        """Test LitterBox box_full_sensitivity_attrs."""
        device = LitterBox(sample_device_data, mock_coordinator)
        device.detail = {"boxFullSensitivity": "LEVEL_02"}
        attrs = device.box_full_sensitivity_attrs()
        assert attrs["raw_level"] == "LEVEL_02"

    def test_hass_sensor_structure(self, mock_coordinator, sample_device_data) -> None:
        """Test LitterBox hass_sensor contains expected keys."""
        device = LitterBox(sample_device_data, mock_coordinator)
        device.detail = {"currentError": "Normal"}
        device.logs = []
        sensor = device.hass_sensor
        assert "state" in sensor
        assert "error" in sensor
        assert "last_log" in sensor
        assert "garbage_tobe_status" in sensor
        assert "litter_weight" in sensor
        assert "litter_remaining_days" in sensor

    def test_hass_select_structure(self, mock_coordinator, sample_device_data) -> None:
        """Test LitterBox hass_select contains expected keys."""
        device = LitterBox(sample_device_data, mock_coordinator)
        select = device.hass_select
        assert "mode" in select
        assert "action" in select
        assert "garbage" in select
        assert "box_full_sensitivity" in select
        assert select["mode"]["options"] == ["auto", "manual", "time"]


class TestLitterBoxAsyncMethods:
    """Tests for LitterBox async methods."""

    @pytest.mark.usefixtures("enable_custom_integrations")
    async def test_select_mode_success(
        self, mock_coordinator, sample_device_data
    ) -> None:
        """Test select_mode succeeds and updates device detail."""
        device = LitterBox(sample_device_data, mock_coordinator)
        device.detail = {}
        mock_coordinator.account.request = AsyncMock(
            return_value={"returnCode": 0, "data": {}}
        )
        device.update_device_detail = AsyncMock(return_value={})

        result = await device.select_mode("auto")

        assert result is not False
        mock_coordinator.account.request.assert_called_once()
        call_args = mock_coordinator.account.request.call_args
        assert call_args[0][0] == "token/litterbox/changeMode"
        assert call_args[0][1]["workModel"] == "00"
        assert call_args[0][1]["deviceId"] == "dev123"
        device.update_device_detail.assert_called_once()

    @pytest.mark.usefixtures("enable_custom_integrations")
    async def test_select_mode_invalid_returns_false(
        self, mock_coordinator, sample_device_data
    ) -> None:
        """Test select_mode returns False for invalid mode."""
        device = LitterBox(sample_device_data, mock_coordinator)
        result = await device.select_mode("invalid_mode")
        assert result is False

    @pytest.mark.usefixtures("enable_custom_integrations")
    async def test_select_mode_api_error_sets_action_error(
        self, mock_coordinator, sample_device_data
    ) -> None:
        """Test select_mode sets action error on API failure."""
        device = LitterBox(sample_device_data, mock_coordinator)
        mock_coordinator.account.request = AsyncMock(
            return_value={"returnCode": 4007, "msg": "Device busy"}
        )

        result = await device.select_mode("auto")

        assert result is False
        assert "Device busy" in device.error

    @pytest.mark.usefixtures("enable_custom_integrations")
    async def test_select_action_success(
        self, mock_coordinator, sample_device_data
    ) -> None:
        """Test select_action succeeds for Cleaning."""
        device = LitterBox(sample_device_data, mock_coordinator)
        mock_coordinator.account.request = AsyncMock(return_value={"returnCode": 0})
        device.update_device_detail = AsyncMock(return_value={})

        result = await device.select_action("Cleaning")

        assert result is not False
        mock_coordinator.account.request.assert_called_once()
        assert mock_coordinator.account.request.call_args[0][1]["cmd"] == "01"

    @pytest.mark.usefixtures("enable_custom_integrations")
    async def test_select_action_garbage_bag_calls_change_bag(
        self, mock_coordinator, sample_device_data
    ) -> None:
        """Test select_action with Garbage Bag delegates to changeBag."""
        device = LitterBox(sample_device_data, mock_coordinator)
        device.changeBag = AsyncMock(return_value=True)

        result = await device.select_action("Change Garbage Bag")

        assert result is True
        device.changeBag.assert_called_once()

    @pytest.mark.usefixtures("enable_custom_integrations")
    async def test_change_bag_success(
        self, mock_coordinator, sample_device_data
    ) -> None:
        """Test changeBag succeeds with Change Bag mode."""
        device = LitterBox(sample_device_data, mock_coordinator)
        mock_coordinator.account.request = AsyncMock(return_value={"returnCode": 0})
        device.update_device_detail = AsyncMock(return_value={})

        result = await device.changeBag("Change Bag")

        assert result is not False
        mock_coordinator.account.request.assert_called_once()
        assert mock_coordinator.account.request.call_args[0][1]["enable"] == "1"

    @pytest.mark.usefixtures("enable_custom_integrations")
    async def test_select_box_full_sensitivity_success(
        self, mock_coordinator, sample_device_data
    ) -> None:
        """Test select_box_full_sensitivity succeeds."""
        device = LitterBox(sample_device_data, mock_coordinator)
        mock_coordinator.account.request = AsyncMock(return_value={"returnCode": 0})
        device.update_device_detail = AsyncMock(return_value={})

        result = await device.select_box_full_sensitivity("Level 1")

        assert result is not False
        mock_coordinator.account.request.assert_called_once()
        assert mock_coordinator.account.request.call_args[0][1]["level"] == "LEVEL_01"

    @pytest.mark.usefixtures("enable_custom_integrations")
    async def test_select_box_full_sensitivity_invalid_returns_false(
        self, mock_coordinator, sample_device_data
    ) -> None:
        """Test select_box_full_sensitivity returns False for invalid level."""
        device = LitterBox(sample_device_data, mock_coordinator)
        result = await device.select_box_full_sensitivity("Invalid Level")
        assert result is False

    @pytest.mark.usefixtures("enable_custom_integrations")
    async def test_reset_litter_success(
        self, mock_coordinator, sample_device_data
    ) -> None:
        """Test async_reset_litter calls consumableReset with CAT_LITTER."""
        device = LitterBox(sample_device_data, mock_coordinator)
        mock_coordinator.account.request = AsyncMock(return_value={"returnCode": 0})
        device.update_device_detail = AsyncMock(return_value={})

        result = await device.async_reset_litter()

        assert result is not False
        mock_coordinator.account.request.assert_called_once()
        call_args = mock_coordinator.account.request.call_args
        assert call_args[0][0] == "token/device/union/consumableReset"
        assert call_args[0][1]["consumablesType"] == "CAT_LITTER"
        assert call_args[0][1]["deviceId"] == "dev123"
        assert call_args[0][1]["deviceType"] == "LITTER_BOX_599"

    @pytest.mark.usefixtures("enable_custom_integrations")
    async def test_reset_deodorant_success(
        self, mock_coordinator, sample_device_data
    ) -> None:
        """Test async_reset_deodorant calls consumableReset with DEODORIZER_02."""
        device = LitterBox(sample_device_data, mock_coordinator)
        mock_coordinator.account.request = AsyncMock(return_value={"returnCode": 0})
        device.update_device_detail = AsyncMock(return_value={})

        result = await device.async_reset_deodorant()

        assert result is not False
        call_args = mock_coordinator.account.request.call_args
        assert call_args[0][1]["consumablesType"] == "DEODORIZER_02"

    def test_hass_button_structure(self, mock_coordinator, sample_device_data) -> None:
        """Test LitterBox hass_button contains reset_litter and reset_deodorant."""
        device = LitterBox(sample_device_data, mock_coordinator)
        buttons = device.hass_button
        assert "reset_litter" in buttons
        assert "reset_deodorant" in buttons
        assert buttons["reset_litter"]["name"] == "Reset litter"
        assert buttons["reset_deodorant"]["name"] == "Reset deodorant"


class TestC08Device:
    """Tests for C08Device."""

    def test_modes_property(self, mock_coordinator, sample_c08_data) -> None:
        """Test C08Device modes."""
        device = C08Device(sample_c08_data, mock_coordinator)
        modes = device.modes
        assert modes["00"] == "auto"
        assert modes["01"] == "manual"
        assert modes["02"] == "scheduled"

    def test_litter_type(self, mock_coordinator, sample_c08_data) -> None:
        """Test C08Device litter type mapping."""
        device = C08Device(sample_c08_data, mock_coordinator)
        device.detail = {"litterType": "00"}
        assert device.litter_type == "Bentonite"

    def test_safe_time(self, mock_coordinator, sample_c08_data) -> None:
        """Test C08Device safe time mapping."""
        device = C08Device(sample_c08_data, mock_coordinator)
        device.detail = {"safeTime": "5"}
        assert device.safe_time == "5 min"

    def test_notice_switch_mapping(self, mock_coordinator, sample_c08_data) -> None:
        """Test notice switches map to notice configs."""
        device = C08Device(sample_c08_data, mock_coordinator)
        device.set_notice_configs(
            [{"noticeItem": "LITTERBOX_599_CAT_CAME", "noticeSwitch": True}]
        )
        assert device.notice_cat_came is True
        assert device.notice_box_full is False

    def test_hass_switch_contains_notice(self, mock_coordinator, sample_c08_data) -> None:
        """Test C08Device hass_switch includes notice entries."""
        device = C08Device(sample_c08_data, mock_coordinator)
        switches = device.hass_switch
        assert "notice_cat_came" in switches
        assert switches["notice_cat_came"]["name"] == "Notice: Cat came"

    def test_hass_select_structure(self, mock_coordinator, sample_c08_data) -> None:
        """Test C08Device hass_select contains expected keys."""
        device = C08Device(sample_c08_data, mock_coordinator)
        selects = device.hass_select
        assert "mode" in selects
        assert "action" in selects
        assert "litter_type" in selects
        assert "safe_time" in selects


class TestC08DeviceAsyncMethods:
    """Tests for C08Device async methods."""

    @pytest.mark.usefixtures("enable_custom_integrations")
    async def test_update_device_detail_c08_endpoint(
        self, mock_coordinator, sample_c08_data
    ) -> None:
        """Test update_device_detail uses C08 info endpoint."""
        device = C08Device(sample_c08_data, mock_coordinator)
        mock_coordinator.account.request = AsyncMock(
            return_value={"data": {"deviceInfo": {"workStatus": "00"}}}
        )
        device.async_refresh_c08_extras = AsyncMock()

        result = await device.update_device_detail()

        assert result["workStatus"] == "00"
        mock_coordinator.account.request.assert_called_once_with(
            "token/litterbox/info/c08", {"deviceId": "c08-1"}
        )
        device.async_refresh_c08_extras.assert_called_once()

    @pytest.mark.usefixtures("enable_custom_integrations")
    async def test_select_action_v3(
        self, mock_coordinator, sample_c08_data
    ) -> None:
        """Test select_action uses v3 command payload."""
        device = C08Device(sample_c08_data, mock_coordinator)
        mock_coordinator.account.request = AsyncMock(return_value={"returnCode": 0})
        device.update_device_detail = AsyncMock(return_value={})

        result = await device.select_action("Clean: start")

        assert result is True
        call_args = mock_coordinator.account.request.call_args
        assert call_args[0][0] == "token/litterbox/actionCmd/v3"
        assert call_args[0][1]["action"] == "RUN"
        assert call_args[0][1]["behavior"] == "CLEAN"

    @pytest.mark.usefixtures("enable_custom_integrations")
    async def test_select_litter_type(
        self, mock_coordinator, sample_c08_data
    ) -> None:
        """Test select_litter_type sends litterType setting."""
        device = C08Device(sample_c08_data, mock_coordinator)
        mock_coordinator.account.request = AsyncMock(return_value={"returnCode": 0})
        device.update_device_detail = AsyncMock(return_value={})

        result = await device.select_litter_type("Bentonite")

        assert result is True
        call_args = mock_coordinator.account.request.call_args
        assert call_args[0][0] == "token/litterbox/catLitterSetting"
        assert call_args[0][1]["litterType"] == "00"


class TestFeederDevice:
    """Tests for FeederDevice."""

    def test_feeder_properties(self, mock_coordinator, sample_feeder_data) -> None:
        """Test FeederDevice basic properties."""
        device = FeederDevice(sample_feeder_data, mock_coordinator)
        assert device.id == "feeder1"
        assert device.name == "Kitchen Feeder"
        assert device.type == "FEEDER"

    def test_feeder_weight(self, mock_coordinator, sample_feeder_data) -> None:
        """Test FeederDevice weight property."""
        device = FeederDevice(sample_feeder_data, mock_coordinator)
        device.detail = {"weight": 250}
        assert device.weight == 250

    def test_feeder_error(self, mock_coordinator, sample_feeder_data) -> None:
        """Test FeederDevice error from detail."""
        device = FeederDevice(sample_feeder_data, mock_coordinator)
        device.detail = {"error": "Low food level"}
        assert device.error == "Low food level"

    def test_feeder_error_attrs(self, mock_coordinator, sample_feeder_data) -> None:
        """Test FeederDevice error_attrs."""
        device = FeederDevice(sample_feeder_data, mock_coordinator)
        device.detail = {
            "currentErrorMessage": "Check sensor",
            "currentErrorType": "sensor",
        }
        attrs = device.error_attrs()
        assert attrs["currentErrorMessage"] == "Check sensor"
        assert attrs["currentErrorType"] == "sensor"

    def test_feeder_state(self, mock_coordinator, sample_feeder_data) -> None:
        """Test FeederDevice state from foodOutStatus."""
        device = FeederDevice(sample_feeder_data, mock_coordinator)
        device.detail = {"foodOutStatus": "idle"}
        assert device.state == "idle"

    def test_feeder_state_attrs(self, mock_coordinator, sample_feeder_data) -> None:
        """Test FeederDevice state_attrs."""
        device = FeederDevice(sample_feeder_data, mock_coordinator)
        device.detail = {
            "foodOutStatus": "feeding",
            "autoFillStatus": "ok",
            "keyLockStatus": "unlocked",
        }
        attrs = device.state_attrs()
        assert attrs["work_status"] == "feeding"
        assert attrs["auto_fill_status"] == "ok"
        assert attrs["key_lock_status"] == "unlocked"

    def test_feeder_hass_sensor(self, mock_coordinator, sample_feeder_data) -> None:
        """Test FeederDevice hass_sensor structure."""
        device = FeederDevice(sample_feeder_data, mock_coordinator)
        device.logs = []
        sensor = device.hass_sensor
        assert "state" in sensor
        assert "weight" in sensor
        assert "error" in sensor
        assert "last_log" in sensor

    def test_feeder_hass_button(self, mock_coordinator, sample_feeder_data) -> None:
        """Test FeederDevice hass_button has feed action."""
        device = FeederDevice(sample_feeder_data, mock_coordinator)
        button = device.hass_button
        assert "feed" in button
        assert button["feed"]["async_press"] == device.food_out


class TestFeederDeviceAsyncMethods:
    """Tests for FeederDevice async methods."""

    @pytest.mark.usefixtures("enable_custom_integrations")
    async def test_food_out_success(self, mock_coordinator, sample_feeder_data) -> None:
        """Test food_out succeeds and updates device detail."""
        device = FeederDevice(sample_feeder_data, mock_coordinator)
        mock_coordinator.account.request = AsyncMock(return_value={"returnCode": 0})
        device.update_device_detail = AsyncMock(return_value={})

        result = await device.food_out()

        assert result is not False
        mock_coordinator.account.request.assert_called_once()
        call_args = mock_coordinator.account.request.call_args
        assert call_args[0][0] == "token/device/feeder/foodOut"
        assert call_args[0][1]["footOutNum"] == 1
        assert call_args[0][1]["deviceId"] == "feeder1"
        device.update_device_detail.assert_called_once()

    @pytest.mark.usefixtures("enable_custom_integrations")
    async def test_food_out_custom_portions(self, mock_coordinator, sample_feeder_data) -> None:
        """Test food_out uses custom portion value."""
        device = FeederDevice(sample_feeder_data, mock_coordinator)
        device.portions = 3
        mock_coordinator.account.request = AsyncMock(return_value={"returnCode": 0})
        device.update_device_detail = AsyncMock(return_value={})

        await device.food_out()

        call_args = mock_coordinator.account.request.call_args
        assert call_args[0][1]["footOutNum"] == 3

    @pytest.mark.usefixtures("enable_custom_integrations")
    async def test_food_out_api_error_sets_action_error(
        self, mock_coordinator, sample_feeder_data
    ) -> None:
        """Test food_out sets action error on API failure."""
        device = FeederDevice(sample_feeder_data, mock_coordinator)
        mock_coordinator.account.request = AsyncMock(
            return_value={"returnCode": 500, "msg": "Device busy"}
        )

        result = await device.food_out()

        assert result is False
        assert "Device busy" in (device._action_error or "")  # noqa: SLF001

    @pytest.mark.usefixtures("enable_custom_integrations")
    async def test_update_device_detail_success(
        self, mock_coordinator, sample_feeder_data
    ) -> None:
        """Test FeederDevice update_device_detail parses response."""
        device = FeederDevice(sample_feeder_data, mock_coordinator)
        device._handle_listeners = MagicMock()  # noqa: SLF001
        mock_coordinator.account.request = AsyncMock(
            return_value={
                "data": {
                    "deviceInfo": {
                        "foodOutStatus": "idle",
                        "weight": 250,
                    }
                }
            }
        )

        result = await device.update_device_detail()

        assert result["foodOutStatus"] == "idle"
        assert result["weight"] == 250
        assert device.detail == result
        device._handle_listeners.assert_called_once()  # noqa: SLF001


class TestScooperDeviceAsyncMethods:
    """Tests for ScooperDevice async methods."""

    @pytest.mark.usefixtures("enable_custom_integrations")
    async def test_update_logs_fetches_from_api(
        self, mock_coordinator, sample_scooper_data
    ) -> None:
        """Test ScooperDevice update_logs calls correct API."""
        device = ScooperDevice(sample_scooper_data, mock_coordinator)
        device._handle_listeners = MagicMock()  # noqa: SLF001
        mock_coordinator.account.request = AsyncMock(
            return_value={
                "data": {
                    "scooperLogTop5": [
                        {"time": "10:00", "event": "Cleaning"},
                    ]
                }
            }
        )

        result = await device.update_logs()

        assert len(result) == 1
        mock_coordinator.account.request.assert_called_once_with(
            "token/device/scooper/stats/log/top5", {"deviceId": "scooper1"}
        )


class TestScooperProUltraDevice:
    """Tests for ScooperProUltraDevice."""

    def test_name_has_no_limited_support_suffix(
        self, mock_coordinator, sample_pro_ultra_data
    ) -> None:
        """Test ScooperProUltraDevice name no longer appends a support suffix."""
        device = ScooperProUltraDevice(sample_pro_ultra_data, mock_coordinator)
        assert device.name == "ScooperPROUltra"
        assert not device.name.endswith(" (Limited Support)")

    @pytest.mark.usefixtures("enable_custom_integrations")
    async def test_update_device_detail_brief_info(
        self, mock_coordinator, sample_pro_ultra_data
    ) -> None:
        """Test update_device_detail uses visualScooper briefInfo."""
        device = ScooperProUltraDevice(sample_pro_ultra_data, mock_coordinator)
        mock_coordinator.account.request = AsyncMock(
            return_value={
                "data": {
                    "deviceInfo": {
                        "litterCountdown": 14,
                        "deodorantCountdown": 0,
                        "totalCleanTimes": "3",
                    }
                }
            }
        )

        result = await device.update_device_detail()

        assert result["litterCountdown"] == 14
        assert result["totalCleanTimes"] == "3"
        mock_coordinator.account.request.assert_called_once()
        assert (
            mock_coordinator.account.request.call_args[0][0]
            == "token/visualScooper/briefInfo"
        )

    @pytest.mark.usefixtures("enable_custom_integrations")
    async def test_update_device_detail_keeps_null_quiet_fields(
        self, mock_coordinator, sample_pro_ultra_data, pro_ultra_brief_info_body
    ) -> None:
        """Test the recorded briefInfo body (quietTimes null) does not wipe detail."""
        device = ScooperProUltraDevice(sample_pro_ultra_data, mock_coordinator)
        mock_coordinator.account.request = AsyncMock(
            return_value={"data": {"deviceInfo": pro_ultra_brief_info_body}}
        )

        result = await device.update_device_detail()

        assert result["litterCountdown"] == 14
        assert result["quietEnable"] is None
        assert result["quietTimes"] is None
        assert result["autoUpdatePetWeight"] is True

    @pytest.mark.usefixtures("enable_custom_integrations")
    async def test_update_logs_timeline_v2(
        self, mock_coordinator, sample_pro_ultra_data
    ) -> None:
        """Test update_logs uses timeline/v2 endpoint."""
        device = ScooperProUltraDevice(sample_pro_ultra_data, mock_coordinator)
        mock_coordinator.account.request = AsyncMock(
            return_value={
                "data": {
                    "records": [
                        {"time": "11:05", "event": "Auto-clean"},
                    ]
                }
            }
        )

        result = await device.update_logs()

        assert result[0]["event"] == "Auto-clean"
        assert (
            mock_coordinator.account.request.call_args[0][0]
            == "token/litterbox/stats/log/timeline/v2"
        )

    def test_quiet_mode_from_brief_info(
        self, mock_coordinator, sample_pro_ultra_data
    ) -> None:
        """Test quiet_mode reads quietEnable, treating null as off."""
        device = ScooperProUltraDevice(sample_pro_ultra_data, mock_coordinator)
        device.detail = {"quietEnable": True}
        assert device.quiet_mode is True
        device.detail = {"quietEnable": None}
        assert device.quiet_mode is False

    def test_quiet_hours_from_brief_info(
        self, mock_coordinator, sample_pro_ultra_data
    ) -> None:
        """Test quiet_start/quiet_end split quietTimes on the first hyphen."""
        device = ScooperProUltraDevice(sample_pro_ultra_data, mock_coordinator)
        device.detail = {"quietTimes": "23:00-07:00"}
        assert device.quiet_start == time(23, 0)
        assert device.quiet_end == time(7, 0)

    def test_quiet_hours_none_when_not_configured(
        self, mock_coordinator, sample_pro_ultra_data
    ) -> None:
        """Test quiet hours are unknown, not the app defaults, when quietTimes is null."""
        device = ScooperProUltraDevice(sample_pro_ultra_data, mock_coordinator)
        device.detail = {"quietTimes": None}
        assert device.quiet_start is None
        assert device.quiet_end is None

    def test_all_timings_and_auto_update_from_brief_info(
        self, mock_coordinator, sample_pro_ultra_data
    ) -> None:
        """Test all_timings and auto_update_pet_weight read their briefInfo flags."""
        device = ScooperProUltraDevice(sample_pro_ultra_data, mock_coordinator)
        device.detail = {"allTimingToggle": True, "autoUpdatePetWeight": False}
        assert device.all_timings is True
        assert device.auto_update_pet_weight is False

    def test_schedules_from_timings_fixture(
        self, mock_coordinator, sample_pro_ultra_data, pro_ultra_timings_body
    ) -> None:
        """Test schedules count and attribute derive from the recorded timings list."""
        device = ScooperProUltraDevice(sample_pro_ultra_data, mock_coordinator)
        device._timings = pro_ultra_timings_body  # noqa: SLF001
        assert device.schedules == 4
        assert device.schedules_attrs()["schedules"] == pro_ultra_timings_body

    def test_about_from_about_device_fixture(
        self, mock_coordinator, sample_pro_ultra_data, pro_ultra_about_body
    ) -> None:
        """Test about state and attributes derive from the recorded aboutDevice body."""
        device = ScooperProUltraDevice(sample_pro_ultra_data, mock_coordinator)
        device._about = pro_ultra_about_body  # noqa: SLF001
        assert device.about == "Standard"
        attrs = device.about_attrs()
        assert attrs["model"] == "Standard"
        assert attrs["care_start"] == 1758000000
        assert attrs["care_end"] == 1790000000
        assert "firmware_version" not in attrs
        assert "wifi_signal" not in attrs

    def test_hass_select_exposes_work_mode_and_aliyun_selects(
        self, mock_coordinator, sample_pro_ultra_data
    ) -> None:
        """Test the work-mode select deferred from #7 is now wired, with the Aliyun selects."""
        device = ScooperProUltraDevice(sample_pro_ultra_data, mock_coordinator)
        selects = device.hass_select
        assert set(selects) == {
            "work_mode",
            "indicator_light",
            "fill_light",
            "litter_type",
        }
        assert selects["work_mode"]["options"] == ["induction", "manual", "timing"]

    def test_hass_switch_structure(
        self, mock_coordinator, sample_pro_ultra_data
    ) -> None:
        """Test hass_switch keeps the CatLink Cloud switches and adds the Aliyun ones."""
        device = ScooperProUltraDevice(sample_pro_ultra_data, mock_coordinator)
        switches = device.hass_switch
        assert set(switches) == {
            "quiet_mode",
            "all_timings",
            "auto_update_pet_weight",
            "child_lock",
            "continuous_clean",
            "prompt_sounds",
            "soft_stool",
            "camera_sleep",
        }

    def test_hass_number_structure(
        self, mock_coordinator, sample_pro_ultra_data
    ) -> None:
        """Test hass_number exposes the Aliyun-backed numbers."""
        device = ScooperProUltraDevice(sample_pro_ultra_data, mock_coordinator)
        assert set(device.hass_number) == {
            "clean_delay",
            "full_bin_threshold",
            "levelling_duration",
        }

    def test_hass_binary_sensor_structure(
        self, mock_coordinator, sample_pro_ultra_data
    ) -> None:
        """Test hass_binary_sensor exposes the problem and bin sensors."""
        device = ScooperProUltraDevice(sample_pro_ultra_data, mock_coordinator)
        assert set(device.hass_binary_sensor) == {
            "problem",
            "bin_full",
            "bin_installed",
        }

    def test_hass_button_and_time_structure(
        self, mock_coordinator, sample_pro_ultra_data
    ) -> None:
        """Test hass_button adds the cleaning actions and hass_time keeps the quiet window."""
        device = ScooperProUltraDevice(sample_pro_ultra_data, mock_coordinator)
        assert set(device.hass_button) == {
            "reset_litter",
            "reset_deodorant",
            "clean",
            "empty",
            "level",
            "pause",
            "resume",
            "cancel",
        }
        assert set(device.hass_time) == {"quiet_start", "quiet_end"}

    @pytest.mark.usefixtures("enable_custom_integrations")
    async def test_set_quiet_mode_sends_expected_params(
        self, mock_coordinator, sample_pro_ultra_data
    ) -> None:
        """Test async_set_quiet_mode posts quietMode with enable and times."""
        device = ScooperProUltraDevice(sample_pro_ultra_data, mock_coordinator)
        device.detail = {"quietEnable": False, "quietTimes": "23:00-07:00"}
        mock_coordinator.account.request = AsyncMock(return_value={"returnCode": 0})
        device.update_device_detail = AsyncMock(return_value={})

        result = await device.async_set_quiet_mode(True)

        assert result is not False
        call_args = mock_coordinator.account.request.call_args
        assert call_args[0][0] == "token/visualScooper/quietMode"
        assert call_args[0][1]["deviceId"] == "ultra1"
        assert call_args[0][1]["enable"] == "true"
        assert call_args[0][1]["times"] == "23:00-07:00"
        assert call_args[0][2] == "POST"

    @pytest.mark.usefixtures("enable_custom_integrations")
    async def test_set_quiet_mode_error_surfaces_without_changing_state(
        self, mock_coordinator, sample_pro_ultra_data
    ) -> None:
        """Test a cloud error returns False, records the error and leaves detail intact."""
        device = ScooperProUltraDevice(sample_pro_ultra_data, mock_coordinator)
        device.detail = {"quietEnable": False, "quietTimes": "23:00-07:00"}
        mock_coordinator.account.request = AsyncMock(
            return_value={"returnCode": 1, "msg": "boom"}
        )
        device.update_device_detail = AsyncMock(return_value={})

        result = await device.async_set_quiet_mode(True)

        assert result is False
        assert device._action_error is not None  # noqa: SLF001
        assert device.detail == {"quietEnable": False, "quietTimes": "23:00-07:00"}
        device.update_device_detail.assert_not_called()

    @pytest.mark.usefixtures("enable_custom_integrations")
    async def test_set_quiet_start_resends_window(
        self, mock_coordinator, sample_pro_ultra_data
    ) -> None:
        """Test setting the start time re-sends quietMode with the new start, current end."""
        device = ScooperProUltraDevice(sample_pro_ultra_data, mock_coordinator)
        device.detail = {"quietEnable": True, "quietTimes": "23:00-07:00"}
        mock_coordinator.account.request = AsyncMock(return_value={"returnCode": 0})
        device.update_device_detail = AsyncMock(return_value={})

        await device.async_set_quiet_start(time(22, 30))

        pms = mock_coordinator.account.request.call_args[0][1]
        assert pms["enable"] == "true"
        assert pms["times"] == "22:30-07:00"

    @pytest.mark.usefixtures("enable_custom_integrations")
    async def test_set_all_timings_sends_string_bool(
        self, mock_coordinator, sample_pro_ultra_data
    ) -> None:
        """Test async_set_all_timings posts allTimingToggle with a string boolean."""
        device = ScooperProUltraDevice(sample_pro_ultra_data, mock_coordinator)
        mock_coordinator.account.request = AsyncMock(return_value={"returnCode": 0})
        device.update_device_detail = AsyncMock(return_value={})

        await device.async_set_all_timings(False)

        call_args = mock_coordinator.account.request.call_args
        assert call_args[0][0] == "token/visualScooper/allTimingToggle"
        assert call_args[0][1] == {"deviceId": "ultra1", "enable": "false"}

    @pytest.mark.usefixtures("enable_custom_integrations")
    async def test_set_auto_update_pet_weight_sends_string_bool(
        self, mock_coordinator, sample_pro_ultra_data
    ) -> None:
        """Test async_set_auto_update_pet_weight posts the autoUpdate endpoint."""
        device = ScooperProUltraDevice(sample_pro_ultra_data, mock_coordinator)
        mock_coordinator.account.request = AsyncMock(return_value={"returnCode": 0})
        device.update_device_detail = AsyncMock(return_value={})

        await device.async_set_auto_update_pet_weight(True)

        call_args = mock_coordinator.account.request.call_args
        assert call_args[0][0] == "token/visualScooper/pet/weight/autoUpdate"
        assert call_args[0][1] == {"deviceId": "ultra1", "enable": "true"}

    @pytest.mark.usefixtures("enable_custom_integrations")
    async def test_reset_litter_uses_visual_scooper_endpoint(
        self, mock_coordinator, sample_pro_ultra_data
    ) -> None:
        """Test async_reset_litter posts the Pro Ultra litter reset, deviceId only."""
        device = ScooperProUltraDevice(sample_pro_ultra_data, mock_coordinator)
        mock_coordinator.account.request = AsyncMock(return_value={"returnCode": 0})
        device.update_device_detail = AsyncMock(return_value={})

        await device.async_reset_litter()

        call_args = mock_coordinator.account.request.call_args
        assert call_args[0][0] == "token/visualScooper/litter/reset"
        assert call_args[0][1] == {"deviceId": "ultra1"}
        assert call_args[0][2] == "POST"

    @pytest.mark.usefixtures("enable_custom_integrations")
    async def test_reset_deodorant_uses_visual_scooper_endpoint(
        self, mock_coordinator, sample_pro_ultra_data
    ) -> None:
        """Test async_reset_deodorant posts the Pro Ultra deodorant reset, deviceId only."""
        device = ScooperProUltraDevice(sample_pro_ultra_data, mock_coordinator)
        mock_coordinator.account.request = AsyncMock(return_value={"returnCode": 0})
        device.update_device_detail = AsyncMock(return_value={})

        await device.async_reset_deodorant()

        call_args = mock_coordinator.account.request.call_args
        assert call_args[0][0] == "token/visualScooper/deodorant/reset"
        assert call_args[0][1] == {"deviceId": "ultra1"}

    @pytest.mark.usefixtures("enable_custom_integrations")
    async def test_add_timing_sends_setting_without_timing_id(
        self, mock_coordinator, sample_pro_ultra_data
    ) -> None:
        """Test async_add_timing posts addTiming with timingSetting and no timingId."""
        device = ScooperProUltraDevice(sample_pro_ultra_data, mock_coordinator)
        mock_coordinator.account.request = AsyncMock(return_value={"returnCode": 0})
        device.update_timings = AsyncMock(return_value=[])

        await device.async_add_timing("09:30")

        call_args = mock_coordinator.account.request.call_args
        assert call_args[0][0] == "token/visualScooper/addTiming"
        assert call_args[0][1] == {"deviceId": "ultra1", "timingSetting": "09:30"}
        assert "timingId" not in call_args[0][1]

    @pytest.mark.usefixtures("enable_custom_integrations")
    async def test_update_timing_sends_setting_and_timing_id(
        self, mock_coordinator, sample_pro_ultra_data
    ) -> None:
        """Test async_update_timing posts updTiming with both setting and timingId."""
        device = ScooperProUltraDevice(sample_pro_ultra_data, mock_coordinator)
        mock_coordinator.account.request = AsyncMock(return_value={"returnCode": 0})
        device.update_timings = AsyncMock(return_value=[])

        await device.async_update_timing("t3", "10:15")

        call_args = mock_coordinator.account.request.call_args
        assert call_args[0][0] == "token/visualScooper/updTiming"
        assert call_args[0][1] == {
            "deviceId": "ultra1",
            "timingSetting": "10:15",
            "timingId": "t3",
        }

    @pytest.mark.usefixtures("enable_custom_integrations")
    async def test_remove_timing_sends_timing_id_without_setting(
        self, mock_coordinator, sample_pro_ultra_data
    ) -> None:
        """Test async_remove_timing posts delTiming with timingId and no timingSetting."""
        device = ScooperProUltraDevice(sample_pro_ultra_data, mock_coordinator)
        mock_coordinator.account.request = AsyncMock(return_value={"returnCode": 0})
        device.update_timings = AsyncMock(return_value=[])

        await device.async_remove_timing("t2")

        call_args = mock_coordinator.account.request.call_args
        assert call_args[0][0] == "token/visualScooper/delTiming"
        assert call_args[0][1] == {"deviceId": "ultra1", "timingId": "t2"}
        assert "timingSetting" not in call_args[0][1]

    @pytest.mark.usefixtures("enable_custom_integrations")
    async def test_toggle_timing_status_inverts_enabled(
        self, mock_coordinator, sample_pro_ultra_data
    ) -> None:
        """Test enabled maps to status 0 (on) and disabled to status 1 (off)."""
        device = ScooperProUltraDevice(sample_pro_ultra_data, mock_coordinator)
        mock_coordinator.account.request = AsyncMock(return_value={"returnCode": 0})
        device.update_timings = AsyncMock(return_value=[])

        await device.async_toggle_timing("t1", True)
        assert mock_coordinator.account.request.call_args[0][0] == (
            "token/visualScooper/timingToggle"
        )
        assert mock_coordinator.account.request.call_args[0][1]["status"] == "0"

        await device.async_toggle_timing("t1", False)
        assert mock_coordinator.account.request.call_args[0][1]["status"] == "1"

    @pytest.mark.usefixtures("enable_custom_integrations")
    async def test_add_timing_error_surfaces_without_changing_schedules(
        self, mock_coordinator, sample_pro_ultra_data, pro_ultra_timings_body
    ) -> None:
        """Test a schedule write error returns False and leaves the list untouched."""
        device = ScooperProUltraDevice(sample_pro_ultra_data, mock_coordinator)
        device._timings = list(pro_ultra_timings_body)  # noqa: SLF001
        mock_coordinator.account.request = AsyncMock(
            return_value={"returnCode": 5, "msg": "nope"}
        )
        device.update_timings = AsyncMock()

        result = await device.async_add_timing("09:30")

        assert result is False
        assert device._action_error is not None  # noqa: SLF001
        assert device.schedules == 4
        device.update_timings.assert_not_called()

    @pytest.mark.usefixtures("enable_custom_integrations")
    async def test_update_timings_posts_and_stores_list(
        self, mock_coordinator, sample_pro_ultra_data, pro_ultra_timings_body
    ) -> None:
        """Test update_timings posts to the timings endpoint and stores the list."""
        device = ScooperProUltraDevice(sample_pro_ultra_data, mock_coordinator)
        mock_coordinator.account.request = AsyncMock(
            return_value={"data": {"timings": pro_ultra_timings_body}}
        )

        result = await device.update_timings()

        assert result == pro_ultra_timings_body
        assert device.schedules == 4
        call_args = mock_coordinator.account.request.call_args
        assert call_args[0][0] == "token/visualScooper/timings"
        assert call_args[0][2] == "POST"

    @pytest.mark.usefixtures("enable_custom_integrations")
    async def test_update_about_fetches_about_device(
        self, mock_coordinator, sample_pro_ultra_data, pro_ultra_about_body
    ) -> None:
        """Test update_about fetches aboutDevice and stores data.info."""
        device = ScooperProUltraDevice(sample_pro_ultra_data, mock_coordinator)
        mock_coordinator.account.request = AsyncMock(
            return_value={"data": {"info": pro_ultra_about_body}}
        )

        result = await device.update_about()

        assert result == pro_ultra_about_body
        assert device.about == "Standard"
        assert (
            mock_coordinator.account.request.call_args[0][0]
            == "token/visualScooper/aboutDevice"
        )

    @pytest.mark.usefixtures("enable_custom_integrations")
    async def test_schedules_round_trip_against_fake_cloud(
        self, mock_coordinator, sample_pro_ultra_data
    ) -> None:
        """Test add then list reflects the new schedule against an in-memory fake cloud."""
        device = ScooperProUltraDevice(sample_pro_ultra_data, mock_coordinator)
        store: list[dict] = []

        async def fake_request(api, pms=None, method="GET", **kwargs):
            if api == "token/visualScooper/addTiming":
                store.append(
                    {"id": f"t{len(store) + 1}", "status": 0, "setting": pms["timingSetting"]}
                )
                return {"returnCode": 0}
            if api == "token/visualScooper/timings":
                return {"data": {"timings": list(store)}}
            return {"returnCode": 0}

        mock_coordinator.account.request = AsyncMock(side_effect=fake_request)

        await device.async_add_timing("09:30")
        await device.update_timings()

        assert device.schedules == 1
        assert device._timings[0]["setting"] == "09:30"  # noqa: SLF001


def _fake_channel(properties: dict | None = None):
    """Return a mock Aliyun channel that records writes and echoes properties."""
    channel = MagicMock()
    channel.async_set_properties = AsyncMock()
    channel.async_get_properties = AsyncMock(return_value=dict(properties or {}))
    return channel


def _pro_ultra_ready(
    data, coordinator, properties, iot_id="IOTID26CHAR26CHAR26CHAR26"
):
    """Build a Pro Ultra device with an iotId and a property snapshot in place."""
    device = ScooperProUltraDevice(data, coordinator)
    device.detail = {"iotId": iot_id}
    device.thing_properties = dict(properties)
    channel = _fake_channel(properties)
    coordinator.account.async_aliyun_channel = AsyncMock(return_value=channel)
    return device, channel


class TestScooperProUltraAliyun:
    """Tests for the Scooper Pro Ultra entities backed by the Aliyun Channel."""

    def test_entity_state_from_snapshot(
        self, mock_coordinator, sample_pro_ultra_data, pro_ultra_thing_properties
    ) -> None:
        """Test every read-only entity derives from one recorded property snapshot."""
        device = ScooperProUltraDevice(sample_pro_ultra_data, mock_coordinator)
        device.thing_properties = pro_ultra_thing_properties

        assert device.status == "idle"
        assert device.problem is False
        assert device.bin_full is False
        assert device.bin_installed is True
        assert device.litter_balance == 62
        assert device.weight == "4.8"
        assert device.wifi_signal == "-51"
        assert device.firmware == "1.4.2"
        assert device.child_lock is True
        assert device.continuous_clean is False
        assert device.prompt_sounds is True
        assert device.soft_stool is False
        assert device.camera_sleep is True
        assert device.work_mode == "induction"
        assert device.indicator_light == "auto"
        assert device.fill_light == "on"
        assert device.litter_type == "mixed"
        assert device.clean_delay == 3
        assert device.full_bin_threshold == 3
        assert device.levelling_duration == 3

    def test_problem_and_bin_from_snapshot(
        self, mock_coordinator, sample_pro_ultra_data
    ) -> None:
        """Test the problem reason and bin sensors derive from their properties."""
        device = ScooperProUltraDevice(sample_pro_ultra_data, mock_coordinator)
        device.thing_properties = {
            "AbnormalState": "6",
            "GarbageBoxStatus": "garbageBox=0&fullLevel=2",
        }
        assert device.problem is True
        assert device.problem_attrs()["reason"] == "motor_blocking_protection"
        assert device.bin_full is True
        assert device.bin_installed is False

    @pytest.mark.parametrize(
        ("run_status", "expected"),
        [
            ("runStatus=0&biz=0", "idle"),
            ("runStatus=1&biz=1", "cleaning"),
            ("runStatus=1&biz=2", "emptying"),
            ("runStatus=1&biz=3", "levelling"),
            ("runStatus=2&biz=1", "cancelling"),
            ("runStatus=3&biz=1", "paused"),
            ("runStatus=4&biz=1", "paused"),
        ],
    )
    def test_status_mapping(
        self, mock_coordinator, sample_pro_ultra_data, run_status, expected
    ) -> None:
        """Test the status sensor maps runStatus and biz, treating 4 as paused."""
        device = ScooperProUltraDevice(sample_pro_ultra_data, mock_coordinator)
        device.thing_properties = {"RunStatus": run_status}
        assert device.status == expected

    async def test_clean_writes_run_status(
        self, mock_coordinator, sample_pro_ultra_data
    ) -> None:
        """Test Clean writes runStatus=1&biz=1 when the device is idle and well."""
        device, channel = _pro_ultra_ready(
            sample_pro_ultra_data,
            mock_coordinator,
            {
                "RunStatus": "runStatus=0&biz=0",
                "AbnormalState": "0",
                "GarbageBoxStatus": "g=1&f=0",
            },
        )

        assert await device.async_clean() is True

        channel.async_set_properties.assert_awaited_once_with(
            "IOTID26CHAR26CHAR26CHAR26", {"RunStatus": "runStatus=1&biz=1"}
        )

    async def test_empty_and_level_write_their_biz(
        self, mock_coordinator, sample_pro_ultra_data
    ) -> None:
        """Test Empty writes biz=2 and Level writes biz=3."""
        snapshot = {
            "RunStatus": "runStatus=0&biz=0",
            "AbnormalState": "0",
            "GarbageBoxStatus": "g=1&f=0",
        }
        device, channel = _pro_ultra_ready(
            sample_pro_ultra_data, mock_coordinator, snapshot
        )

        await device.async_empty()
        assert channel.async_set_properties.await_args[0][1] == {
            "RunStatus": "runStatus=1&biz=2"
        }

        await device.async_level()
        assert channel.async_set_properties.await_args[0][1] == {
            "RunStatus": "runStatus=1&biz=3"
        }

    async def test_pause_resume_cancel_reuse_current_biz(
        self, mock_coordinator, sample_pro_ultra_data
    ) -> None:
        """Test pause/resume/cancel reuse the parsed current biz, never a guess."""
        device, channel = _pro_ultra_ready(
            sample_pro_ultra_data, mock_coordinator, {"RunStatus": "runStatus=1&biz=2"}
        )
        await device.async_pause()
        assert channel.async_set_properties.await_args[0][1] == {
            "RunStatus": "runStatus=3&biz=2"
        }

        device.thing_properties = {"RunStatus": "runStatus=3&biz=2"}
        await device.async_resume()
        assert channel.async_set_properties.await_args[0][1] == {
            "RunStatus": "runStatus=1&biz=2"
        }

        device.thing_properties = {"RunStatus": "runStatus=1&biz=3"}
        await device.async_cancel()
        assert channel.async_set_properties.await_args[0][1] == {
            "RunStatus": "runStatus=2&biz=3"
        }

    async def test_clean_blocked_when_running(
        self, mock_coordinator, sample_pro_ultra_data
    ) -> None:
        """Test Clean is refused, with no write, while a cycle runs."""
        device, channel = _pro_ultra_ready(
            sample_pro_ultra_data,
            mock_coordinator,
            {
                "RunStatus": "runStatus=1&biz=1",
                "AbnormalState": "0",
                "GarbageBoxStatus": "g=1&f=0",
            },
        )

        with pytest.raises(HomeAssistantError):
            await device.async_clean()
        channel.async_set_properties.assert_not_awaited()

    async def test_clean_blocked_when_abnormal(
        self, mock_coordinator, sample_pro_ultra_data
    ) -> None:
        """Test Clean is refused when AbnormalState is a known fault."""
        device, channel = _pro_ultra_ready(
            sample_pro_ultra_data,
            mock_coordinator,
            {
                "RunStatus": "runStatus=0&biz=0",
                "AbnormalState": "1",
                "GarbageBoxStatus": "g=1&f=0",
            },
        )

        with pytest.raises(HomeAssistantError):
            await device.async_clean()
        channel.async_set_properties.assert_not_awaited()

    async def test_clean_blocked_when_bin_full(
        self, mock_coordinator, sample_pro_ultra_data
    ) -> None:
        """Test Clean is refused when the waste bin is full."""
        device, channel = _pro_ultra_ready(
            sample_pro_ultra_data,
            mock_coordinator,
            {
                "RunStatus": "runStatus=0&biz=0",
                "AbnormalState": "0",
                "GarbageBoxStatus": "g=1&f=2",
            },
        )

        with pytest.raises(HomeAssistantError):
            await device.async_clean()
        channel.async_set_properties.assert_not_awaited()

    async def test_clean_blocked_without_snapshot(
        self, mock_coordinator, sample_pro_ultra_data
    ) -> None:
        """Test Clean is refused when no property snapshot has been read."""
        device, channel = _pro_ultra_ready(
            sample_pro_ultra_data, mock_coordinator, {}
        )

        with pytest.raises(HomeAssistantError):
            await device.async_clean()
        channel.async_set_properties.assert_not_awaited()

    async def test_setting_blocked_when_running(
        self, mock_coordinator, sample_pro_ultra_data
    ) -> None:
        """Test a setting write is refused while the device is not idle."""
        device, channel = _pro_ultra_ready(
            sample_pro_ultra_data, mock_coordinator, {"RunStatus": "runStatus=1&biz=1"}
        )

        with pytest.raises(HomeAssistantError):
            await device.async_set_child_lock(True)
        channel.async_set_properties.assert_not_awaited()

    async def test_child_lock_write_when_idle(
        self, mock_coordinator, sample_pro_ultra_data
    ) -> None:
        """Test a switch write goes through when idle."""
        device, channel = _pro_ultra_ready(
            sample_pro_ultra_data, mock_coordinator, {"RunStatus": "runStatus=0&biz=0"}
        )

        await device.async_set_child_lock(True)
        channel.async_set_properties.assert_awaited_once_with(
            "IOTID26CHAR26CHAR26CHAR26", {"ChildLock": 1}
        )

    async def test_camera_sleep_write_is_inverted(
        self, mock_coordinator, sample_pro_ultra_data
    ) -> None:
        """Test turning camera sleep on writes SleepMode 0 (awake maps to on)."""
        device, channel = _pro_ultra_ready(
            sample_pro_ultra_data, mock_coordinator, {"RunStatus": "runStatus=0&biz=0"}
        )

        await device.async_set_camera_sleep(True)
        assert channel.async_set_properties.await_args[0][1] == {"SleepMode": 0}

    async def test_number_writes_map_level_to_stored(
        self, mock_coordinator, sample_pro_ultra_data
    ) -> None:
        """Test the full-bin threshold and levelling numbers map a level to stored steps."""
        device, channel = _pro_ultra_ready(
            sample_pro_ultra_data, mock_coordinator, {"RunStatus": "runStatus=0&biz=0"}
        )

        await device.async_set_full_bin_threshold(4)
        assert channel.async_set_properties.await_args[0][1] == {"LimitWhenFull": 15}

        await device.async_set_levelling_duration(2)
        assert channel.async_set_properties.await_args[0][1] == {"CatLitterFlatTime": 11}

    async def test_indicator_light_select_writes_code(
        self, mock_coordinator, sample_pro_ultra_data
    ) -> None:
        """Test the indicator-light select writes the three-state code when idle."""
        device, channel = _pro_ultra_ready(
            sample_pro_ultra_data, mock_coordinator, {"RunStatus": "runStatus=0&biz=0"}
        )

        await device.async_select_indicator_light("auto")
        assert channel.async_set_properties.await_args[0][1] == {"IndicatorLight": 2}

    @pytest.mark.usefixtures("enable_custom_integrations")
    async def test_work_mode_write_uses_catlink_cloud_when_idle(
        self, mock_coordinator, sample_pro_ultra_data
    ) -> None:
        """Test the work-mode select writes over the CatLink Cloud once idle."""
        device = ScooperProUltraDevice(sample_pro_ultra_data, mock_coordinator)
        device.thing_properties = {"RunStatus": "runStatus=0&biz=0"}
        mock_coordinator.account.request = AsyncMock(return_value={"returnCode": 0})
        device.update_device_detail = AsyncMock(return_value={})

        await device.async_select_work_mode("manual")

        call_args = mock_coordinator.account.request.call_args
        assert call_args[0][0] == "token/device/changeMode"
        assert call_args[0][1] == {"workModel": "01", "deviceId": "ultra1"}

    async def test_work_mode_blocked_when_running(
        self, mock_coordinator, sample_pro_ultra_data
    ) -> None:
        """Test the work-mode select is refused while the device runs."""
        device = ScooperProUltraDevice(sample_pro_ultra_data, mock_coordinator)
        device.thing_properties = {"RunStatus": "runStatus=1&biz=1"}
        mock_coordinator.account.request = AsyncMock(return_value={"returnCode": 0})

        with pytest.raises(HomeAssistantError):
            await device.async_select_work_mode("manual")
        mock_coordinator.account.request.assert_not_called()

    async def test_write_auth_failure_starts_reauth(
        self, mock_coordinator, sample_pro_ultra_data
    ) -> None:
        """Test an Aliyun auth failure on a write surfaces ConfigEntryAuthFailed."""
        device, channel = _pro_ultra_ready(
            sample_pro_ultra_data,
            mock_coordinator,
            {
                "RunStatus": "runStatus=0&biz=0",
                "AbnormalState": "0",
                "GarbageBoxStatus": "g=1&f=0",
            },
        )
        channel.async_set_properties = AsyncMock(side_effect=AliyunAuthFailed("expired"))

        with pytest.raises(ConfigEntryAuthFailed):
            await device.async_clean()

    async def test_write_without_aliyun_credentials_starts_reauth(
        self, mock_coordinator, sample_pro_ultra_data
    ) -> None:
        """Test a write with no Aliyun credentials configured starts reauth."""
        device, channel = _pro_ultra_ready(
            sample_pro_ultra_data,
            mock_coordinator,
            {
                "RunStatus": "runStatus=0&biz=0",
                "AbnormalState": "0",
                "GarbageBoxStatus": "g=1&f=0",
            },
        )
        mock_coordinator.account.async_aliyun_channel = AsyncMock(
            side_effect=AliyunAuthFailed("Aliyun credentials are not configured")
        )

        with pytest.raises(ConfigEntryAuthFailed):
            await device.async_clean()
        channel.async_set_properties.assert_not_called()

    @pytest.mark.usefixtures("enable_custom_integrations")
    async def test_update_detail_reads_thing_properties(
        self, mock_coordinator, sample_pro_ultra_data, pro_ultra_thing_properties
    ) -> None:
        """Test update_device_detail reads the Aliyun properties when an iotId is present."""
        device = ScooperProUltraDevice(sample_pro_ultra_data, mock_coordinator)
        mock_coordinator.account.request = AsyncMock(
            return_value={"data": {"deviceInfo": {"iotId": "IOTID26CHAR26CHAR26CHAR26"}}}
        )
        channel = _fake_channel(pro_ultra_thing_properties)
        mock_coordinator.account.async_aliyun_channel = AsyncMock(return_value=channel)

        await device.update_device_detail()

        channel.async_get_properties.assert_awaited_once_with(
            "IOTID26CHAR26CHAR26CHAR26"
        )
        assert device.status == "idle"

    @pytest.mark.usefixtures("enable_custom_integrations")
    async def test_update_detail_survives_unreachable_aliyun(
        self, mock_coordinator, sample_pro_ultra_data
    ) -> None:
        """Test an unreachable Aliyun leaves the CatLink Cloud detail intact and does not raise."""
        device = ScooperProUltraDevice(sample_pro_ultra_data, mock_coordinator)
        mock_coordinator.account.request = AsyncMock(
            return_value={
                "data": {
                    "deviceInfo": {
                        "iotId": "IOTID26CHAR26CHAR26CHAR26",
                        "litterCountdown": 14,
                    }
                }
            }
        )
        channel = MagicMock()
        channel.async_get_properties = AsyncMock(
            side_effect=AliyunRequestFailed("unreachable")
        )
        mock_coordinator.account.async_aliyun_channel = AsyncMock(return_value=channel)

        result = await device.update_device_detail()

        assert result["litterCountdown"] == 14
        assert device.thing_properties == {}

    @pytest.mark.usefixtures("enable_custom_integrations")
    async def test_update_detail_auth_failure_raises(
        self, mock_coordinator, sample_pro_ultra_data
    ) -> None:
        """Test an Aliyun auth failure during the read propagates as ConfigEntryAuthFailed."""
        device = ScooperProUltraDevice(sample_pro_ultra_data, mock_coordinator)
        mock_coordinator.account.request = AsyncMock(
            return_value={"data": {"deviceInfo": {"iotId": "IOTID26CHAR26CHAR26CHAR26"}}}
        )
        channel = MagicMock()
        channel.async_get_properties = AsyncMock(
            side_effect=AliyunAuthFailed("expired")
        )
        mock_coordinator.account.async_aliyun_channel = AsyncMock(return_value=channel)

        with pytest.raises(ConfigEntryAuthFailed):
            await device.update_device_detail()

    @pytest.mark.usefixtures("enable_custom_integrations")
    async def test_pro_ultra_without_iot_id_makes_no_aliyun_call(
        self, mock_coordinator, sample_pro_ultra_data
    ) -> None:
        """Test a Pro Ultra whose briefInfo carries no iotId opens no Aliyun Channel."""
        device = ScooperProUltraDevice(sample_pro_ultra_data, mock_coordinator)
        mock_coordinator.account.request = AsyncMock(
            return_value={"data": {"deviceInfo": {"litterCountdown": 14}}}
        )
        mock_coordinator.account.async_aliyun_channel = AsyncMock()

        await device.update_device_detail()

        mock_coordinator.account.async_aliyun_channel.assert_not_called()

    @pytest.mark.usefixtures("enable_custom_integrations")
    async def test_non_pro_ultra_device_makes_no_aliyun_call(
        self, mock_coordinator, sample_feeder_data
    ) -> None:
        """Test a device that is not a Pro Ultra never opens the Aliyun Channel."""
        device = FeederDevice(sample_feeder_data, mock_coordinator)
        mock_coordinator.account.async_aliyun_channel = AsyncMock()
        mock_coordinator.account.request = AsyncMock(
            return_value={"data": {"deviceInfo": {"foodOutStatus": "00"}}}
        )

        await device.update_device_detail()

        mock_coordinator.account.async_aliyun_channel.assert_not_called()


class TestScooperDevice:
    """Tests for ScooperDevice."""

    def test_scooper_properties(self, mock_coordinator, sample_scooper_data) -> None:
        """Test ScooperDevice basic properties."""
        device = ScooperDevice(sample_scooper_data, mock_coordinator)
        assert device.id == "scooper1"
        assert device.name == "Basement Scooper"
        assert device.type == "SCOOPER"

    def test_scooper_modes(self, mock_coordinator, sample_scooper_data) -> None:
        """Test ScooperDevice modes include empty mode."""
        device = ScooperDevice(sample_scooper_data, mock_coordinator)
        modes = device.modes
        assert modes["00"] == "auto"
        assert modes["01"] == "manual"
        assert modes["02"] == "time"
        assert modes["03"] == "empty"

    def test_scooper_actions(self, mock_coordinator, sample_scooper_data) -> None:
        """Test ScooperDevice actions."""
        device = ScooperDevice(sample_scooper_data, mock_coordinator)
        actions = device.actions
        assert actions["00"] == "pause"
        assert actions["01"] == "start"

    def test_scooper_temperature(self, mock_coordinator, sample_scooper_data) -> None:
        """Test ScooperDevice temperature."""
        device = ScooperDevice(sample_scooper_data, mock_coordinator)
        device.detail = {"temperature": "25"}
        assert device.temperature == "25"

    def test_scooper_temperature_default(
        self, mock_coordinator, sample_scooper_data
    ) -> None:
        """Test ScooperDevice temperature defaults to -."""
        device = ScooperDevice(sample_scooper_data, mock_coordinator)
        device.detail = {}
        assert device.temperature == "-"

    def test_scooper_humidity(self, mock_coordinator, sample_scooper_data) -> None:
        """Test ScooperDevice humidity."""
        device = ScooperDevice(sample_scooper_data, mock_coordinator)
        device.detail = {"humidity": "45"}
        assert device.humidity == "45"

    def test_scooper_error_from_current_message(
        self, mock_coordinator, sample_scooper_data
    ) -> None:
        """Test ScooperDevice error from currentMessage."""
        device = ScooperDevice(sample_scooper_data, mock_coordinator)
        device.detail = {"currentMessage": "Sensor fault"}
        assert device.error == "Sensor fault"

    def test_scooper_error_from_data(
        self, mock_coordinator, sample_scooper_data
    ) -> None:
        """Test ScooperDevice error from data currentErrorMessage."""
        device = ScooperDevice(sample_scooper_data, mock_coordinator)
        device.detail = {}
        device.data = {"currentErrorMessage": "Connection lost"}
        assert device.error == "Connection lost"

    def test_scooper_error_action_takes_precedence(
        self, mock_coordinator, sample_scooper_data
    ) -> None:
        """Test ScooperDevice _action_error overrides."""
        device = ScooperDevice(sample_scooper_data, mock_coordinator)
        device.detail = {"currentMessage": "Old"}
        device._set_action_error("New error")  # noqa: SLF001
        assert device.error == "New error"

    def test_scooper_error_attrs(self, mock_coordinator, sample_scooper_data) -> None:
        """Test ScooperDevice error_attrs contains error_logs."""
        device = ScooperDevice(sample_scooper_data, mock_coordinator)
        attrs = device.error_attrs()
        assert "error_logs" in attrs
        assert isinstance(attrs["error_logs"], list)

    def test_scooper_hass_sensor(self, mock_coordinator, sample_scooper_data) -> None:
        """Test ScooperDevice hass_sensor structure."""
        device = ScooperDevice(sample_scooper_data, mock_coordinator)
        sensor = device.hass_sensor
        assert "state" in sensor
        assert "litter_weight" in sensor
        assert "temperature" in sensor
        assert "humidity" in sensor
        assert "error" in sensor


class TestPureProDevice:
    """Tests for PureProDevice."""

    def test_purepro_properties(self, mock_coordinator, sample_purepro_data) -> None:
        """Test PureProDevice basic properties."""
        device = PureProDevice(sample_purepro_data, mock_coordinator)
        assert device.id == "purepro1"
        assert device.name == "Cat Fountain"
        assert device.type == "PUREPRO"

    def test_purepro_modes(self, mock_coordinator, sample_purepro_data) -> None:
        """Test PureProDevice modes."""
        device = PureProDevice(sample_purepro_data, mock_coordinator)
        modes = device.modes
        assert modes["CONTINUOUS_SPRING"] == "Flowing mode"
        assert modes["INTERMITTENT_SPRING"] == "Eco-mode"
        assert modes["INDUCTION_SPRING"] == "Smart mode"

    def test_purepro_hass_sensor(self, mock_coordinator, sample_purepro_data) -> None:
        """Test PureProDevice hass_sensor structure."""
        device = PureProDevice(sample_purepro_data, mock_coordinator)
        sensor = device.hass_sensor
        assert "state" in sensor
        assert "last_log" in sensor
        assert "online" in sensor


class TestPureProDeviceAsyncMethods:
    """Tests for PureProDevice async methods."""

    @pytest.mark.usefixtures("enable_custom_integrations")
    async def test_update_device_detail_purepro(
        self, mock_coordinator, sample_purepro_data
    ) -> None:
        """Test update_device_detail uses PurePro detail endpoint."""
        device = PureProDevice(sample_purepro_data, mock_coordinator)
        mock_coordinator.account.request = AsyncMock(
            return_value={"data": {"workStatus": "00"}}
        )

        result = await device.update_device_detail()

        assert result["workStatus"] == "00"
        mock_coordinator.account.request.assert_called_once_with(
            "token/device/purepro/detail", {"deviceId": "purepro1"}
        )

    @pytest.mark.usefixtures("enable_custom_integrations")
    async def test_select_mode_purepro(
        self, mock_coordinator, sample_purepro_data
    ) -> None:
        """Test select_mode uses PurePro runMode endpoint."""
        device = PureProDevice(sample_purepro_data, mock_coordinator)
        mock_coordinator.account.request = AsyncMock(return_value={"returnCode": 0})
        device.update_device_detail = AsyncMock(return_value={})

        result = await device.select_mode("Flowing mode")

        assert result is True
        call_args = mock_coordinator.account.request.call_args
        assert call_args[0][0] == "token/device/purepro/runMode"
        assert call_args[0][1]["runMode"] == "CONTINUOUS_SPRING"
        assert call_args[0][1]["deviceId"] == "purepro1"

    @pytest.mark.usefixtures("enable_custom_integrations")
    async def test_update_logs_purepro(
        self, mock_coordinator, sample_purepro_data
    ) -> None:
        """Test update_logs uses PurePro stats/log/top5 endpoint."""
        device = PureProDevice(sample_purepro_data, mock_coordinator)
        mock_coordinator.account.request = AsyncMock(
            return_value={"data": {"pureLogTop5": [{"event": "Cat drank water"}]}}
        )

        result = await device.update_logs()

        assert len(result) == 1
        assert result[0]["event"] == "Cat drank water"
        mock_coordinator.account.request.assert_called_once_with(
            "token/device/purepro/stats/log/top5", {"deviceId": "purepro1"}
        )
