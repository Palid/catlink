"""Tests for CatLink LitterDevice base class."""

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.catlink.devices.c08 import C08Device
from custom_components.catlink.devices.litter_device import LitterDevice
from custom_components.catlink.devices.litterbox import LitterBox
from custom_components.catlink.devices.scooper import ScooperDevice
from custom_components.catlink.devices.scooper_pro_ultra import ScooperProUltraDevice


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
def sample_scooper_data():
    """Sample Scooper device data."""
    return {
        "id": "scooper1",
        "mac": "FF:EE:DD:CC:BB:AA",
        "model": "Scooper C1",
        "deviceName": "Basement Scooper",
        "deviceType": "SCOOPER",
    }


class TestLitterDeviceState:
    """Tests for LitterDevice state property."""

    def test_state_idle(self, mock_coordinator, sample_device_data) -> None:
        """Test state maps workStatus 00 to idle."""
        device = LitterBox(sample_device_data, mock_coordinator)
        device.detail = {"workStatus": "00"}
        assert device.state == "idle"

    def test_state_running(self, mock_coordinator, sample_device_data) -> None:
        """Test state maps workStatus 01 to running."""
        device = LitterBox(sample_device_data, mock_coordinator)
        device.detail = {"workStatus": "01"}
        assert device.state == "running"

    def test_state_need_reset(self, mock_coordinator, sample_device_data) -> None:
        """Test state maps workStatus 02 to need_reset."""
        device = LitterBox(sample_device_data, mock_coordinator)
        device.detail = {"workStatus": "02"}
        assert device.state == "need_reset"

    def test_state_unknown_fallback(self, mock_coordinator, sample_device_data) -> None:
        """Test state returns raw value for unknown workStatus."""
        device = LitterBox(sample_device_data, mock_coordinator)
        device.detail = {"workStatus": "99"}
        assert device.state == "99"


class TestLitterDeviceLitterWeight:
    """Tests for LitterDevice litter_weight property."""

    def test_litter_weight_calculated(
        self, mock_coordinator, sample_device_data
    ) -> None:
        """Test litter_weight subtracts empty weight from catLitterWeight."""
        device = LitterBox(sample_device_data, mock_coordinator)
        device.empty_litter_box_weight = 1.0
        device.detail = {"catLitterWeight": 3.5}
        assert device.litter_weight == 2.5

    def test_litter_weight_unknown_when_absent(
        self, mock_coordinator, sample_device_data
    ) -> None:
        """Test litter_weight is unknown, not a false 0.0, when the cloud omits catLitterWeight."""
        device = LitterBox(sample_device_data, mock_coordinator)
        device.empty_litter_box_weight = 1.0
        device.detail = {}
        assert device.litter_weight is None

    def test_litter_weight_unknown_when_null(
        self, mock_coordinator, sample_device_data
    ) -> None:
        """Test litter_weight is unknown when the cloud sends null."""
        device = LitterBox(sample_device_data, mock_coordinator)
        device.detail = {"catLitterWeight": None}
        assert device.litter_weight is None

    def test_litter_weight_no_error_log_when_null(
        self, mock_coordinator, sample_device_data, caplog
    ) -> None:
        """Test a null catLitterWeight never raises and never logs at error level."""
        device = LitterBox(sample_device_data, mock_coordinator)
        device.detail = {"catLitterWeight": None}
        with caplog.at_level(logging.DEBUG):
            assert device.litter_weight is None
        assert not [r for r in caplog.records if r.levelno >= logging.ERROR]

    def test_litter_weight_does_not_record_sample_when_unknown(
        self, mock_coordinator, sample_device_data
    ) -> None:
        """Test a null catLitterWeight is not recorded in the occupied-detection deque."""
        device = LitterBox(sample_device_data, mock_coordinator)
        device._litter_weight_during_day.clear()
        device.detail = {"catLitterWeight": None}
        assert device.litter_weight is None
        assert len(device._litter_weight_during_day) == 0

    def test_litter_weight_unknown_when_empty_string_no_warning(
        self, mock_coordinator, sample_device_data, caplog
    ) -> None:
        """Test an empty-string catLitterWeight (the cloud's other no-value marker) is silent."""
        device = LitterBox(sample_device_data, mock_coordinator)
        device.detail = {"catLitterWeight": "  "}
        with caplog.at_level(logging.WARNING):
            assert device.litter_weight is None
        assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


class TestLitterDeviceCleanTimes:
    """Tests for LitterDevice total_clean_time and manual_clean_time."""

    def test_total_clean_time(self, mock_coordinator, sample_device_data) -> None:
        """Test total_clean_time sums induction and manual times."""
        device = LitterBox(sample_device_data, mock_coordinator)
        device.detail = {"inductionTimes": 10, "manualTimes": 5}
        assert device.total_clean_time == 15

    def test_manual_clean_time(self, mock_coordinator, sample_device_data) -> None:
        """Test manual_clean_time from manualTimes."""
        device = LitterBox(sample_device_data, mock_coordinator)
        device.detail = {"manualTimes": 7}
        assert device.manual_clean_time == 7

    def test_deodorant_countdown(self, mock_coordinator, sample_device_data) -> None:
        """Test deodorant_countdown from detail."""
        device = LitterBox(sample_device_data, mock_coordinator)
        device.detail = {"deodorantCountdown": 30}
        assert device.deodorant_countdown == 30


class TestLitterDeviceOccupied:
    """Tests for LitterDevice occupied property."""

    def test_occupied_true_when_weight_increases(
        self, mock_coordinator, sample_device_data
    ) -> None:
        """Test occupied True when litter weight increases during day."""
        device = LitterBox(sample_device_data, mock_coordinator)
        device._litter_weight_during_day.clear()
        device._litter_weight_during_day.extend([1.0, 1.5, 2.0])
        assert device.occupied is True

    def test_occupied_false_when_weight_decreases(
        self, mock_coordinator, sample_device_data
    ) -> None:
        """Test occupied False when litter weight only decreases."""
        device = LitterBox(sample_device_data, mock_coordinator)
        device._litter_weight_during_day.clear()
        device._litter_weight_during_day.extend([3.0, 2.5, 2.0])
        assert device.occupied is False

    def test_occupied_false_when_empty(
        self, mock_coordinator, sample_device_data
    ) -> None:
        """Test occupied False when deque has fewer than 2 entries."""
        device = LitterBox(sample_device_data, mock_coordinator)
        device._litter_weight_during_day.clear()
        device._litter_weight_during_day.append(1.0)
        assert device.occupied is False


class TestLitterDeviceOnline:
    """Tests for LitterDevice online property."""

    def test_online_true(self, mock_coordinator, sample_device_data) -> None:
        """Test online returns True when detail has online."""
        device = LitterBox(sample_device_data, mock_coordinator)
        device.detail = {"online": True}
        assert device.online is True

    def test_online_false(self, mock_coordinator, sample_device_data) -> None:
        """Test online returns False when detail has online False."""
        device = LitterBox(sample_device_data, mock_coordinator)
        device.detail = {"online": False}
        assert device.online is False


class TestLitterDeviceBaseStateAttrs:
    """Tests for LitterDevice _base_state_attrs."""

    def test_base_state_attrs(self, mock_coordinator, sample_device_data) -> None:
        """Test _base_state_attrs returns expected keys."""
        device = LitterBox(sample_device_data, mock_coordinator)
        device.detail = {
            "workStatus": "00",
            "alarmStatus": "ok",
            "weight": 2500,
            "catLitterWeight": 3.5,
            "inductionTimes": 10,
            "manualTimes": 5,
            "keyLock": "0",
            "safeTime": 30,
            "catLitterPaveSecond": 60,
            "deodorantCountdown": 30,
            "litterCountdown": 5,
        }
        attrs = device._base_state_attrs()
        assert attrs["mac"] == "AA:BB:CC:DD:EE:FF"
        assert attrs["work_status"] == "00"
        assert attrs["alarm_status"] == "ok"
        assert attrs["weight"] == 2500
        assert attrs["litter_weight_kg"] == 3.5
        assert attrs["total_clean_times"] == 15
        assert attrs["manual_clean_times"] == 5
        assert attrs["deodorant_countdown"] == 30
        assert attrs["litter_countdown"] == 5


class TestLitterDeviceNullableNumericFields:
    """Tests for LitterDevice numeric fields when the CatLink Cloud sends null."""

    def test_total_clean_time_unknown_when_null(
        self, mock_coordinator, sample_device_data
    ) -> None:
        """Test total_clean_time is unknown when both cloud fields are null."""
        device = LitterBox(sample_device_data, mock_coordinator)
        device.detail = {"inductionTimes": None, "manualTimes": None}
        assert device.total_clean_time is None

    def test_total_clean_time_unknown_when_absent(
        self, mock_coordinator, sample_device_data
    ) -> None:
        """Test total_clean_time is unknown when the cloud omits both fields."""
        device = LitterBox(sample_device_data, mock_coordinator)
        device.detail = {}
        assert device.total_clean_time is None

    def test_total_clean_time_unknown_when_one_field_null(
        self, mock_coordinator, sample_device_data
    ) -> None:
        """Test total_clean_time is unknown, not a partial sum, when one field is null."""
        device = LitterBox(sample_device_data, mock_coordinator)
        device.detail = {"inductionTimes": 10, "manualTimes": None}
        assert device.total_clean_time is None

    def test_manual_clean_time_unknown_when_null(
        self, mock_coordinator, sample_device_data
    ) -> None:
        """Test manual_clean_time is unknown when the cloud sends null."""
        device = LitterBox(sample_device_data, mock_coordinator)
        device.detail = {"manualTimes": None}
        assert device.manual_clean_time is None

    def test_manual_clean_time_unknown_when_absent(
        self, mock_coordinator, sample_device_data
    ) -> None:
        """Test manual_clean_time is unknown when the cloud omits the field."""
        device = LitterBox(sample_device_data, mock_coordinator)
        device.detail = {}
        assert device.manual_clean_time is None

    def test_deodorant_countdown_unknown_when_null(
        self, mock_coordinator, sample_device_data
    ) -> None:
        """Test deodorant_countdown is unknown when the cloud sends null."""
        device = LitterBox(sample_device_data, mock_coordinator)
        device.detail = {"deodorantCountdown": None}
        assert device.deodorant_countdown is None

    def test_deodorant_countdown_unknown_when_absent(
        self, mock_coordinator, sample_device_data
    ) -> None:
        """Test deodorant_countdown is unknown when the cloud omits the field."""
        device = LitterBox(sample_device_data, mock_coordinator)
        device.detail = {}
        assert device.deodorant_countdown is None

    def test_deodorant_countdown_no_error_log_when_null(
        self, mock_coordinator, sample_device_data, caplog
    ) -> None:
        """Test a null deodorant countdown never raises and never logs at error level."""
        device = LitterBox(sample_device_data, mock_coordinator)
        device.detail = {"deodorantCountdown": None}
        with caplog.at_level(logging.DEBUG):
            assert device.deodorant_countdown is None
        assert not [r for r in caplog.records if r.levelno >= logging.ERROR]

    def test_deodorant_countdown_reflects_value_that_appears_later(
        self, mock_coordinator, sample_device_data
    ) -> None:
        """Test a value that appears on a later poll is reflected in state."""
        device = LitterBox(sample_device_data, mock_coordinator)
        device.detail = {"deodorantCountdown": None}
        assert device.deodorant_countdown is None
        device.detail = {"deodorantCountdown": 12}
        assert device.deodorant_countdown == 12

    def test_deodorant_countdown_unexpected_value_warns_once_per_cause(
        self, mock_coordinator, sample_device_data, caplog
    ) -> None:
        """Test an unparseable value warns once per Device per cause, not on every poll."""
        device = LitterBox(sample_device_data, mock_coordinator)
        device.detail = {"deodorantCountdown": "not-a-number"}
        with caplog.at_level(logging.WARNING):
            assert device.deodorant_countdown is None
            assert device.deodorant_countdown is None
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1

    def test_deodorant_countdown_unknown_when_empty_string_no_warning(
        self, mock_coordinator, sample_device_data, caplog
    ) -> None:
        """Test an empty-string deodorantCountdown (the cloud's other no-value marker) is silent."""
        device = LitterBox(sample_device_data, mock_coordinator)
        device.detail = {"deodorantCountdown": "  "}
        with caplog.at_level(logging.WARNING):
            assert device.deodorant_countdown is None
        assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


class TestLitterDeviceBaseStateAttrsNullable:
    """Tests for LitterDevice _base_state_attrs when cloud fields are null."""

    def test_total_clean_times_unknown_when_null(
        self, mock_coordinator, sample_device_data
    ) -> None:
        """Test total_clean_times attr is unknown, not 0, when both fields are null."""
        device = LitterBox(sample_device_data, mock_coordinator)
        device.detail = {"inductionTimes": None, "manualTimes": None}
        attrs = device._base_state_attrs()
        assert attrs["total_clean_times"] is None


class TestLitterDeviceBaseLitterRemainingDaysNullable:
    """Tests for LitterDevice's own litter_remaining_days (used by C08Device, ScooperDevice)."""

    def test_unknown_when_null(self, mock_coordinator, sample_device_data) -> None:
        """Test litter_remaining_days is unknown when the cloud sends null."""
        device = LitterDevice(sample_device_data, mock_coordinator)
        device.detail = {"litterCountdown": None}
        assert device.litter_remaining_days is None

    def test_unknown_when_absent(self, mock_coordinator, sample_device_data) -> None:
        """Test litter_remaining_days is unknown when the cloud omits the field."""
        device = LitterDevice(sample_device_data, mock_coordinator)
        device.detail = {}
        assert device.litter_remaining_days is None

    def test_returns_value_when_present(
        self, mock_coordinator, sample_device_data
    ) -> None:
        """Test litter_remaining_days returns the value when the cloud sends one."""
        device = LitterDevice(sample_device_data, mock_coordinator)
        device.detail = {"litterCountdown": 5}
        assert device.litter_remaining_days == 5


class TestLitterBoxLitterRemainingDaysNullable:
    """Tests for LitterBox's own litter_remaining_days override."""

    def test_unknown_when_null(self, mock_coordinator, sample_device_data) -> None:
        """Test litter_remaining_days is unknown when the cloud sends null."""
        device = LitterBox(sample_device_data, mock_coordinator)
        device.detail = {"litterCountdown": None}
        assert device.litter_remaining_days is None

    def test_unknown_when_absent(self, mock_coordinator, sample_device_data) -> None:
        """Test litter_remaining_days is unknown when the cloud omits the field."""
        device = LitterBox(sample_device_data, mock_coordinator)
        device.detail = {}
        assert device.litter_remaining_days is None

    def test_no_error_log_when_null(
        self, mock_coordinator, sample_device_data, caplog
    ) -> None:
        """Test a null litter countdown never raises and never logs at error level."""
        device = LitterBox(sample_device_data, mock_coordinator)
        device.detail = {"litterCountdown": None}
        with caplog.at_level(logging.DEBUG):
            assert device.litter_remaining_days is None
        assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


class TestScooperProUltraDeviceNullableNumericFields:
    """Tests for ScooperProUltraDevice numeric fields when the CatLink Cloud sends null."""

    def test_litter_remaining_days_unknown_when_null(
        self, mock_coordinator, sample_pro_ultra_data
    ) -> None:
        """Test litter_remaining_days is unknown when the cloud sends null."""
        device = ScooperProUltraDevice(sample_pro_ultra_data, mock_coordinator)
        device.detail = {"litterCountdown": None}
        assert device.litter_remaining_days is None

    def test_total_clean_time_unknown_when_absent(
        self, mock_coordinator, sample_pro_ultra_data
    ) -> None:
        """Test total_clean_time is unknown when the cloud omits totalCleanTimes."""
        device = ScooperProUltraDevice(sample_pro_ultra_data, mock_coordinator)
        device.detail = {}
        assert device.total_clean_time is None

    def test_no_error_log_when_both_fields_null(
        self, mock_coordinator, sample_pro_ultra_data, caplog
    ) -> None:
        """Test null numeric fields never raise and never log at error level."""
        device = ScooperProUltraDevice(sample_pro_ultra_data, mock_coordinator)
        device.detail = {"litterCountdown": None, "totalCleanTimes": None}
        with caplog.at_level(logging.DEBUG):
            assert device.litter_remaining_days is None
            assert device.total_clean_time is None
        assert not [r for r in caplog.records if r.levelno >= logging.ERROR]

    @pytest.mark.usefixtures("enable_custom_integrations")
    async def test_update_device_detail_empty_string_litter_countdown_keeps_other_fields(
        self, mock_coordinator, sample_pro_ultra_data
    ) -> None:
        """Test an empty-string litterCountdown doesn't wipe the rest of deviceInfo."""
        device = ScooperProUltraDevice(sample_pro_ultra_data, mock_coordinator)
        mock_coordinator.account.request = AsyncMock(
            return_value={
                "data": {
                    "deviceInfo": {
                        "workStatus": "01",
                        "litterCountdown": "",
                        "totalCleanTimes": 5,
                    }
                }
            }
        )

        result = await device.update_device_detail()

        assert result["workStatus"] == "01"
        assert result["totalCleanTimes"] == 5
        assert device.litter_remaining_days is None


class TestC08DeviceLitterWeightNullable:
    """Tests for C08Device litter_weight (inherited from LitterDevice) when the cloud sends null."""

    def test_unknown_when_null(self, mock_coordinator, sample_c08_data) -> None:
        """Test litter_weight is unknown when the cloud sends null."""
        device = C08Device(sample_c08_data, mock_coordinator)
        device.detail = {"catLitterWeight": None}
        assert device.litter_weight is None

    def test_unknown_when_absent(self, mock_coordinator, sample_c08_data) -> None:
        """Test litter_weight is unknown when the cloud omits catLitterWeight."""
        device = C08Device(sample_c08_data, mock_coordinator)
        device.detail = {}
        assert device.litter_weight is None

    def test_no_error_log_when_null(
        self, mock_coordinator, sample_c08_data, caplog
    ) -> None:
        """Test a null catLitterWeight never raises and never logs at error level."""
        device = C08Device(sample_c08_data, mock_coordinator)
        device.detail = {"catLitterWeight": None}
        with caplog.at_level(logging.DEBUG):
            assert device.litter_weight is None
        assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


class TestC08DeviceNullableNumericFields:
    """Tests for C08Device numeric fields (inherited from LitterDevice) when the cloud sends null."""

    def test_deodorant_countdown_unknown_when_null(
        self, mock_coordinator, sample_c08_data
    ) -> None:
        """Test deodorant_countdown is unknown when the cloud sends null."""
        device = C08Device(sample_c08_data, mock_coordinator)
        device.detail = {"deodorantCountdown": None}
        assert device.deodorant_countdown is None

    def test_litter_remaining_days_unknown_when_absent(
        self, mock_coordinator, sample_c08_data
    ) -> None:
        """Test litter_remaining_days is unknown when the cloud omits litterCountdown."""
        device = C08Device(sample_c08_data, mock_coordinator)
        device.detail = {}
        assert device.litter_remaining_days is None

    def test_no_error_log_when_fields_null(
        self, mock_coordinator, sample_c08_data, caplog
    ) -> None:
        """Test null numeric fields never raise and never log at error level."""
        device = C08Device(sample_c08_data, mock_coordinator)
        device.detail = {
            "deodorantCountdown": None,
            "litterCountdown": None,
            "inductionTimes": None,
            "manualTimes": None,
        }
        with caplog.at_level(logging.DEBUG):
            assert device.deodorant_countdown is None
            assert device.litter_remaining_days is None
            assert device.total_clean_time is None
            assert device.manual_clean_time is None
        assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


class TestScooperDeviceNullableNumericFields:
    """Tests for ScooperDevice numeric fields (inherited from LitterDevice) when the cloud sends null."""

    def test_deodorant_countdown_unknown_when_absent(
        self, mock_coordinator, sample_scooper_data
    ) -> None:
        """Test deodorant_countdown is unknown when the cloud omits the field."""
        device = ScooperDevice(sample_scooper_data, mock_coordinator)
        device.detail = {}
        assert device.deodorant_countdown is None

    def test_litter_remaining_days_unknown_when_null(
        self, mock_coordinator, sample_scooper_data
    ) -> None:
        """Test litter_remaining_days is unknown when the cloud sends null."""
        device = ScooperDevice(sample_scooper_data, mock_coordinator)
        device.detail = {"litterCountdown": None}
        assert device.litter_remaining_days is None

    def test_no_error_log_when_fields_null(
        self, mock_coordinator, sample_scooper_data, caplog
    ) -> None:
        """Test null numeric fields never raise and never log at error level."""
        device = ScooperDevice(sample_scooper_data, mock_coordinator)
        device.detail = {
            "deodorantCountdown": None,
            "litterCountdown": None,
            "inductionTimes": None,
            "manualTimes": None,
        }
        with caplog.at_level(logging.DEBUG):
            assert device.deodorant_countdown is None
            assert device.litter_remaining_days is None
            assert device.total_clean_time is None
            assert device.manual_clean_time is None
        assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
