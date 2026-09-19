"""Tests for CatLink LogsMixin."""

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.catlink.devices.litterbox import LitterBox
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


class TestLogsMixinLastLog:
    """Tests for LogsMixin _last_log and last_log properties."""

    def test_last_log_empty_when_no_logs(
        self, mock_coordinator, sample_device_data
    ) -> None:
        """Test _last_log and last_log when logs is empty."""
        device = LitterBox(sample_device_data, mock_coordinator)
        device.logs = []
        assert device._last_log == {}
        assert device.last_log is None

    def test_last_log_returns_first_entry(
        self, mock_coordinator, sample_device_data
    ) -> None:
        """Test _last_log returns first log entry."""
        device = LitterBox(sample_device_data, mock_coordinator)
        device.logs = [
            {"time": "2024-01-15 10:00", "event": "Cleaning"},
            {"time": "2024-01-15 09:00", "event": "Idle"},
        ]
        assert device._last_log == {"time": "2024-01-15 10:00", "event": "Cleaning"}

    def test_last_log_formatted_string(
        self, mock_coordinator, sample_device_data
    ) -> None:
        """Test last_log returns formatted string."""
        device = LitterBox(sample_device_data, mock_coordinator)
        device.logs = [{"time": "2024-01-15 10:00", "event": "Cleaning"}]
        assert device.last_log == "2024-01-15 10:00 Cleaning"

    def test_last_log_handles_none_first_entry(
        self, mock_coordinator, sample_device_data
    ) -> None:
        """Test _last_log when first entry is None."""
        device = LitterBox(sample_device_data, mock_coordinator)
        device.logs = [None, {"time": "2024-01-15", "event": "Clean"}]
        assert device._last_log == {}


class TestLogsMixinLastLogAttrs:
    """Tests for LogsMixin last_log_attrs."""

    def test_last_log_attrs_includes_logs(
        self, mock_coordinator, sample_device_data
    ) -> None:
        """Test last_log_attrs includes log data and full logs list."""
        device = LitterBox(sample_device_data, mock_coordinator)
        device.logs = [{"time": "10:00", "event": "Clean", "extra": "val"}]
        attrs = device.last_log_attrs()
        assert attrs["time"] == "10:00"
        assert attrs["event"] == "Clean"
        assert attrs["extra"] == "val"
        assert attrs["logs"] == device.logs


class TestLogsMixinFetchLogs:
    """Tests for LogsMixin _fetch_logs."""

    @pytest.mark.usefixtures("enable_custom_integrations")
    async def test_fetch_logs_success(
        self, mock_coordinator, sample_device_data
    ) -> None:
        """Test _fetch_logs parses API response and updates logs."""
        device = LitterBox(sample_device_data, mock_coordinator)
        device._handle_listeners = MagicMock()
        mock_coordinator.account.request = AsyncMock(
            return_value={
                "data": {
                    "scooperLogTop5": [
                        {"time": "10:00", "event": "Cleaning"},
                    ]
                }
            }
        )

        result = await device._fetch_logs(
            "token/litterbox/stats/log/top5", "scooperLogTop5"
        )

        assert len(result) == 1
        assert result[0]["time"] == "10:00"
        assert result[0]["event"] == "Cleaning"
        assert device.logs == result
        mock_coordinator.account.request.assert_called_once_with(
            "token/litterbox/stats/log/top5", {"deviceId": "dev123"}
        )
        device._handle_listeners.assert_called_once()

    @pytest.mark.usefixtures("enable_custom_integrations")
    async def test_fetch_logs_empty_response(
        self, mock_coordinator, sample_device_data
    ) -> None:
        """Test _fetch_logs handles empty API response."""
        device = LitterBox(sample_device_data, mock_coordinator)
        device._handle_listeners = MagicMock()
        mock_coordinator.account.request = AsyncMock(return_value={"data": {}})

        result = await device._fetch_logs("token/litterbox/log", "scooperLogTop5")

        assert result == []
        assert device.logs == []

    @pytest.mark.usefixtures("enable_custom_integrations")
    async def test_fetch_logs_api_error_sets_empty(
        self, mock_coordinator, sample_device_data
    ) -> None:
        """Test _fetch_logs sets empty list on parse error."""
        device = LitterBox(sample_device_data, mock_coordinator)
        device._handle_listeners = MagicMock()
        mock_coordinator.account.request = AsyncMock(
            return_value={"data": {"scooperLogTop5": "invalid"}}
        )

        result = await device._fetch_logs("token/litterbox/log", "scooperLogTop5")

        assert result == []
        assert device.logs == []


class TestLogsMixinNullableLogs:
    """Tests for LogsMixin _fetch_logs when the CatLink Cloud sends an empty or null log list."""

    @pytest.mark.usefixtures("enable_custom_integrations")
    async def test_empty_list_is_no_recent_activity(
        self, mock_coordinator, sample_device_data
    ) -> None:
        """Test an empty log list from the cloud is valid, not a failure."""
        device = LitterBox(sample_device_data, mock_coordinator)
        mock_coordinator.account.request = AsyncMock(
            return_value={"data": {"scooperLogTop5": []}}
        )

        result = await device._fetch_logs(
            "token/litterbox/stats/log/top5", "scooperLogTop5"
        )

        assert result == []
        assert device.logs == []

    @pytest.mark.usefixtures("enable_custom_integrations")
    async def test_null_list_is_no_recent_activity(
        self, mock_coordinator, sample_device_data
    ) -> None:
        """Test a null log list from the cloud is valid, not a failure."""
        device = LitterBox(sample_device_data, mock_coordinator)
        mock_coordinator.account.request = AsyncMock(
            return_value={"data": {"scooperLogTop5": None}}
        )

        result = await device._fetch_logs(
            "token/litterbox/stats/log/top5", "scooperLogTop5"
        )

        assert result == []
        assert device.logs == []

    @pytest.mark.usefixtures("enable_custom_integrations")
    async def test_empty_list_does_not_log_at_warning_level(
        self, mock_coordinator, sample_device_data, caplog
    ) -> None:
        """Test no recent activity never logs, so it cannot flood the log every poll."""
        device = LitterBox(sample_device_data, mock_coordinator)
        mock_coordinator.account.request = AsyncMock(
            return_value={"data": {"scooperLogTop5": []}}
        )

        with caplog.at_level(logging.DEBUG):
            await device._fetch_logs("token/litterbox/stats/log/top5", "scooperLogTop5")

        assert not [r for r in caplog.records if r.levelno >= logging.WARNING]

    @pytest.mark.usefixtures("enable_custom_integrations")
    async def test_fetch_failure_warns_once_per_device_per_cause(
        self, mock_coordinator, sample_device_data, caplog
    ) -> None:
        """Test a genuine fetch failure warns once per Device per cause, not every poll."""
        device = LitterBox(sample_device_data, mock_coordinator)
        mock_coordinator.account.request = AsyncMock(side_effect=TypeError("boom"))

        with caplog.at_level(logging.WARNING):
            await device._fetch_logs("token/litterbox/stats/log/top5", "scooperLogTop5")
            await device._fetch_logs("token/litterbox/stats/log/top5", "scooperLogTop5")

        assert device.logs == []
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1

    @pytest.mark.usefixtures("enable_custom_integrations")
    async def test_fetch_failure_warns_again_after_recovery(
        self, mock_coordinator, sample_device_data, caplog
    ) -> None:
        """Test the warning re-arms after a successful poll: fail, succeed, fail warns twice."""
        device = LitterBox(sample_device_data, mock_coordinator)
        mock_coordinator.account.request = AsyncMock(
            side_effect=[
                TypeError("boom"),
                {"data": {"scooperLogTop5": []}},
                TypeError("boom again"),
            ]
        )

        with caplog.at_level(logging.WARNING):
            await device._fetch_logs("token/litterbox/stats/log/top5", "scooperLogTop5")
            await device._fetch_logs("token/litterbox/stats/log/top5", "scooperLogTop5")
            await device._fetch_logs("token/litterbox/stats/log/top5", "scooperLogTop5")

        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 2

    @pytest.mark.usefixtures("enable_custom_integrations")
    async def test_fetch_failure_still_warns_once_without_recovery(
        self, mock_coordinator, sample_device_data, caplog
    ) -> None:
        """Test back-to-back failures with no recovery in between still warn only once."""
        device = LitterBox(sample_device_data, mock_coordinator)
        mock_coordinator.account.request = AsyncMock(side_effect=TypeError("boom"))

        with caplog.at_level(logging.WARNING):
            await device._fetch_logs("token/litterbox/stats/log/top5", "scooperLogTop5")
            await device._fetch_logs("token/litterbox/stats/log/top5", "scooperLogTop5")

        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1


class TestScooperProUltraDeviceUpdateLogsNullable:
    """Tests for ScooperProUltraDevice.update_logs when the CatLink Cloud sends an empty list."""

    @pytest.mark.usefixtures("enable_custom_integrations")
    async def test_empty_list_is_no_recent_activity(
        self, mock_coordinator, sample_pro_ultra_data
    ) -> None:
        """Test an empty log list from the cloud is valid, not a failure."""
        device = ScooperProUltraDevice(sample_pro_ultra_data, mock_coordinator)
        mock_coordinator.account.request = AsyncMock(
            return_value={"data": {"records": []}}
        )

        result = await device.update_logs()

        assert result == []
        assert device.logs == []

    @pytest.mark.usefixtures("enable_custom_integrations")
    async def test_empty_list_does_not_log_at_warning_level(
        self, mock_coordinator, sample_pro_ultra_data, caplog
    ) -> None:
        """Test no recent activity never logs, so it cannot flood the log every poll."""
        device = ScooperProUltraDevice(sample_pro_ultra_data, mock_coordinator)
        mock_coordinator.account.request = AsyncMock(
            return_value={"data": {"records": []}}
        )

        with caplog.at_level(logging.DEBUG):
            await device.update_logs()

        assert not [r for r in caplog.records if r.levelno >= logging.WARNING]

    @pytest.mark.usefixtures("enable_custom_integrations")
    async def test_fetch_failure_warns_again_after_recovery(
        self, mock_coordinator, sample_pro_ultra_data, caplog
    ) -> None:
        """Test the warning re-arms after a successful poll: fail, succeed, fail warns twice."""
        device = ScooperProUltraDevice(sample_pro_ultra_data, mock_coordinator)
        mock_coordinator.account.request = AsyncMock(
            side_effect=[
                TypeError("boom"),
                {"data": {"records": []}},
                TypeError("boom again"),
            ]
        )

        with caplog.at_level(logging.WARNING):
            await device.update_logs()
            await device.update_logs()
            await device.update_logs()

        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 2
