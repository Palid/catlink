"""Scooper Pro Ultra device module for CatLink integration."""

from __future__ import annotations

import datetime
from datetime import time
from functools import partial
from typing import TYPE_CHECKING

from ..const import _LOGGER
from ..helpers import format_api_error, optional_int
from ..models.additional_cfg import AdditionalDeviceConfig
from ..models.api.device import LitterDeviceInfo
from ..models.api.logs import LogEntry
from ..models.api.parse import parse_response
from .litter_device import LitterDevice

if TYPE_CHECKING:
    from ..modules.devices_coordinator import DevicesCoordinator

API_BRIEF_INFO = "token/visualScooper/briefInfo"
API_ABOUT_DEVICE = "token/visualScooper/aboutDevice"
API_QUIET_MODE = "token/visualScooper/quietMode"
API_TIMINGS = "token/visualScooper/timings"
API_ADD_TIMING = "token/visualScooper/addTiming"
API_UPD_TIMING = "token/visualScooper/updTiming"
API_DEL_TIMING = "token/visualScooper/delTiming"
API_TIMING_TOGGLE = "token/visualScooper/timingToggle"
API_ALL_TIMING_TOGGLE = "token/visualScooper/allTimingToggle"
API_LITTER_RESET = "token/visualScooper/litter/reset"
API_DEODORANT_RESET = "token/visualScooper/deodorant/reset"
API_PET_WEIGHT_AUTO_UPDATE = "token/visualScooper/pet/weight/autoUpdate"

DEFAULT_QUIET_START = time(23, 0)
DEFAULT_QUIET_END = time(7, 0)


class ScooperProUltraDevice(LitterDevice):
    """Scooper Pro Ultra device class (CatLink Cloud settings)."""

    def __init__(
        self,
        dat: dict,
        coordinator: "DevicesCoordinator",
        additional_config: AdditionalDeviceConfig | None = None,
    ) -> None:
        """Initialize the device."""
        super().__init__(dat, coordinator, additional_config)
        self._timings: list = []
        self._about: dict = {}

    async def async_init(self) -> None:
        """Initialize the device, its schedule list and about info."""
        await super().async_init()
        await self.update_timings()
        await self.update_about()

    @property
    def litter_remaining_days(self) -> int | None:
        """Return the litter remaining days, or unknown if the cloud has none."""
        return optional_int(self.detail.get("litterCountdown"), self, "litterCountdown")

    @property
    def total_clean_time(self) -> int | None:
        """Return total clean time from briefInfo, or unknown if the cloud has none."""
        return optional_int(self.detail.get("totalCleanTimes"), self, "totalCleanTimes")

    @property
    def quiet_mode(self) -> bool:
        """Return the quiet mode setting, treating null as off."""
        return bool(self.detail.get("quietEnable"))

    @property
    def quiet_start(self) -> time | None:
        """Return the quiet window start, or unknown when not configured."""
        return self._quiet_half(0)

    @property
    def quiet_end(self) -> time | None:
        """Return the quiet window end, or unknown when not configured."""
        return self._quiet_half(1)

    @property
    def all_timings(self) -> bool:
        """Return whether all timed cleanings are enabled, treating null as off."""
        return bool(self.detail.get("allTimingToggle"))

    @property
    def auto_update_pet_weight(self) -> bool:
        """Return the cat-weight auto-update setting, treating null as off."""
        return bool(self.detail.get("autoUpdatePetWeight"))

    @property
    def is_owner(self) -> bool | None:
        """Return whether this account owns the device, or unknown when absent."""
        master = self.detail.get("master")
        if master is None:
            return None
        return master == 1

    @property
    def schedules(self) -> int:
        """Return the number of timed cleaning schedules."""
        return len(self._timings)

    @property
    def about(self) -> str | None:
        """Return the model reported by aboutDevice."""
        return (self._about or {}).get("model")

    @property
    def hass_sensor(self) -> dict:
        """Return the device sensors."""
        return {
            "last_log": {
                "icon": "mdi:message",
                "state_attrs": self.last_log_attrs,
            },
            "error": {
                "icon": "mdi:alert-circle",
            },
            "litter_remaining_days": {
                "icon": "mdi:calendar",
                "unit": "days",
            },
            "deodorant_countdown": {
                "icon": "mdi:timer",
                "unit": "days",
            },
            "total_clean_time": {
                "icon": "mdi:history",
                "unit": "times",
            },
            "schedules": {
                "icon": "mdi:calendar-clock",
                "state_attrs": self.schedules_attrs,
            },
            "about": {
                "icon": "mdi:information",
                "state_attrs": self.about_attrs,
            },
        }

    @property
    def hass_select(self) -> dict:
        """Return the device selects.

        Work mode is deferred to issue #8: its current state is an Aliyun Thing
        Property, so the CatLink Cloud cannot back a select here.
        """
        return {}

    @property
    def hass_switch(self) -> dict:
        """Return the device switches."""
        return {
            "quiet_mode": {
                "icon": "mdi:volume-off",
                "async_turn_on": partial(self.async_set_quiet_mode, True),
                "async_turn_off": partial(self.async_set_quiet_mode, False),
            },
            "all_timings": {
                "icon": "mdi:calendar-multiple-check",
                "async_turn_on": partial(self.async_set_all_timings, True),
                "async_turn_off": partial(self.async_set_all_timings, False),
            },
            "auto_update_pet_weight": {
                "icon": "mdi:scale",
                "async_turn_on": partial(self.async_set_auto_update_pet_weight, True),
                "async_turn_off": partial(self.async_set_auto_update_pet_weight, False),
            },
        }

    @property
    def hass_button(self) -> dict:
        """Return the device buttons."""
        return {
            "reset_litter": {
                "icon": "mdi:shaker-outline",
                "name": "Reset litter",
                "async_press": self.async_reset_litter,
            },
            "reset_deodorant": {
                "icon": "mdi:spray-bottle",
                "name": "Reset deodorant",
                "async_press": self.async_reset_deodorant,
            },
        }

    @property
    def hass_time(self) -> dict:
        """Return the device time entities."""
        return {
            "quiet_start": {
                "icon": "mdi:weather-night",
                "async_set_time": self.async_set_quiet_start,
            },
            "quiet_end": {
                "icon": "mdi:weather-sunny",
                "async_set_time": self.async_set_quiet_end,
            },
        }

    def schedules_attrs(self) -> dict:
        """Return the schedule list as an attribute."""
        return {"schedules": self._timings}

    def about_attrs(self) -> dict:
        """Return the read-only about and briefInfo values that are populated."""
        about = self._about or {}
        return {
            "model": about.get("model"),
            "mac": about.get("mac") or self.mac,
            "device_name": about.get("deviceName") or self.name,
            "care_start": about.get("careStart"),
            "care_end": about.get("careEnd"),
            "owner": self.is_owner,
            "timezone_id": self.detail.get("timezoneId"),
            "quiet_times": self.detail.get("quietTimes"),
        }

    async def async_set_quiet_mode(self, enable: bool, **kwargs) -> bool:
        """Enable or disable quiet mode, re-sending the current window."""
        start, end = self._quiet_range()
        return await self._write_quiet_mode(enable, start, end)

    async def async_set_quiet_start(self, value: time, **kwargs) -> bool:
        """Set the quiet window start, keeping the current end and enable state."""
        _, end = self._quiet_range()
        return await self._write_quiet_mode(self.quiet_mode, value, end)

    async def async_set_quiet_end(self, value: time, **kwargs) -> bool:
        """Set the quiet window end, keeping the current start and enable state."""
        start, _ = self._quiet_range()
        return await self._write_quiet_mode(self.quiet_mode, start, value)

    async def async_set_all_timings(self, enable: bool, **kwargs) -> bool:
        """Enable or disable all timed cleanings."""
        pms = {"deviceId": self.id, "enable": self._wire_bool(enable)}
        return await self._send_setting(
            API_ALL_TIMING_TOGGLE, pms, "All timings", self.update_device_detail
        )

    async def async_set_auto_update_pet_weight(self, enable: bool, **kwargs) -> bool:
        """Enable or disable cat-weight auto-update."""
        pms = {"deviceId": self.id, "enable": self._wire_bool(enable)}
        return await self._send_setting(
            API_PET_WEIGHT_AUTO_UPDATE,
            pms,
            "Auto update pet weight",
            self.update_device_detail,
        )

    async def async_reset_litter(self) -> bool:
        """Reset the litter counter."""
        return await self._send_setting(
            API_LITTER_RESET, {"deviceId": self.id}, "Reset litter", self.update_device_detail
        )

    async def async_reset_deodorant(self) -> bool:
        """Reset the deodorant counter."""
        return await self._send_setting(
            API_DEODORANT_RESET,
            {"deviceId": self.id},
            "Reset deodorant",
            self.update_device_detail,
        )

    async def async_add_timing(self, timing_setting: str) -> bool:
        """Add a timed cleaning at the given HH:mm."""
        pms = {"deviceId": self.id, "timingSetting": timing_setting}
        return await self._send_setting(
            API_ADD_TIMING, pms, "Add timing", self.update_timings
        )

    async def async_update_timing(self, timing_id: str, timing_setting: str) -> bool:
        """Change a timed cleaning to the given HH:mm."""
        pms = {
            "deviceId": self.id,
            "timingSetting": timing_setting,
            "timingId": timing_id,
        }
        return await self._send_setting(
            API_UPD_TIMING, pms, "Update timing", self.update_timings
        )

    async def async_remove_timing(self, timing_id: str) -> bool:
        """Remove a timed cleaning."""
        pms = {"deviceId": self.id, "timingId": timing_id}
        return await self._send_setting(
            API_DEL_TIMING, pms, "Remove timing", self.update_timings
        )

    async def async_toggle_timing(self, timing_id: str, enabled: bool) -> bool:
        """Enable or disable a single timed cleaning."""
        pms = {
            "deviceId": self.id,
            "timingId": timing_id,
            "status": "0" if enabled else "1",
        }
        return await self._send_setting(
            API_TIMING_TOGGLE, pms, "Toggle timing", self.update_timings
        )

    async def update_device_detail(self) -> dict:
        """Update device detail from visualScooper briefInfo."""
        pms = {"deviceId": self.id}
        rsp = None
        try:
            rsp = await self.account.request(API_BRIEF_INFO, pms)
            data = rsp.get("data", {})
            raw = data.get("deviceInfo")
            parsed = parse_response(data, "deviceInfo", LitterDeviceInfo, {})
            rdt = (
                parsed.model_dump(by_alias=True)
                if hasattr(parsed, "model_dump")
                else (parsed or {})
            )
            if not rdt and raw:
                rdt = raw
                _LOGGER.debug(
                    "Using raw deviceInfo for %s because model parsing failed",
                    self.name,
                )
        except (TypeError, ValueError) as exc:
            rdt = {}
            _LOGGER.error("Got device detail for %s failed: %s", self.name, exc)
        if not rdt:
            _LOGGER.warning("Got device detail for %s failed: %s", self.name, rsp)
        self.detail = rdt
        self._action_error = None
        self._handle_listeners()
        return rdt

    async def update_timings(self) -> list:
        """Update the timed cleaning schedule list."""
        pms = {"deviceId": self.id}
        rdt = []
        try:
            rsp = await self.account.request(API_TIMINGS, pms, "POST")
            data = rsp.get("data", {})
            rdt = data.get("timings") or []
        except (TypeError, ValueError) as exc:
            self.warn_once(
                "timings_fetch_failed",
                "Got schedules for %s failed: %s",
                self.name,
                exc,
            )
        else:
            self.clear_warning("timings_fetch_failed")
        self._timings = rdt
        self._handle_listeners()
        return rdt

    async def update_about(self) -> dict:
        """Update the device about info from visualScooper aboutDevice."""
        pms = {"deviceId": self.id}
        rdt = {}
        try:
            rsp = await self.account.request(API_ABOUT_DEVICE, pms)
            rdt = rsp.get("data", {}).get("info", {}) or {}
        except (TypeError, ValueError) as exc:
            self.warn_once(
                "about_fetch_failed", "Got about info for %s failed: %s", self.name, exc
            )
        else:
            self.clear_warning("about_fetch_failed")
        self._about = rdt
        self._handle_listeners()
        return rdt

    async def update_logs(self) -> list:
        """Update device logs using timeline/v2 endpoint."""
        api = "token/litterbox/stats/log/timeline/v2"
        today = datetime.date.today().isoformat()
        pms = {
            "deviceId": self.id,
            "date": today,
            "pageNumber": 1,
            "pageSize": 10,
            "type": 0,
            "subType": 0,
        }
        rdt = []
        try:
            rsp = await self.account.request(api, pms)
            data = rsp.get("data", {})
            parsed = parse_response(data, "records", LogEntry, [])
            if isinstance(parsed, list) and parsed and hasattr(parsed[0], "model_dump"):
                rdt = [p.model_dump() for p in parsed]
            elif isinstance(parsed, list):
                rdt = parsed
            else:
                rdt = data.get("records") or []
            self.clear_warning("logs_fetch_failed")
        except (TypeError, ValueError) as exc:
            self.warn_once(
                "logs_fetch_failed", "Got device logs for %s failed: %s", self.name, exc
            )
        self.logs = rdt
        self._handle_listeners()
        return rdt

    def _quiet_half(self, index: int) -> time | None:
        """Return one half of the quiet window, split on the first hyphen."""
        raw = self.detail.get("quietTimes")
        if not raw or "-" not in raw:
            return None
        try:
            return time.fromisoformat(raw.split("-", 1)[index])
        except (ValueError, IndexError):
            return None

    def _quiet_range(self) -> tuple[time, time]:
        """Return the current quiet window, falling back to the app defaults."""
        start = self.quiet_start or DEFAULT_QUIET_START
        end = self.quiet_end or DEFAULT_QUIET_END
        return start, end

    async def _write_quiet_mode(self, enable: bool, start: time, end: time) -> bool:
        """Send the quiet mode endpoint with enable and the HH:mm-HH:mm window."""
        pms = {
            "deviceId": self.id,
            "enable": self._wire_bool(enable),
            "times": f"{start.strftime('%H:%M')}-{end.strftime('%H:%M')}",
        }
        return await self._send_setting(
            API_QUIET_MODE, pms, "Quiet mode", self.update_device_detail
        )

    async def _send_setting(self, api: str, pms: dict, name: str, refresh) -> bool:
        """Post a setting write and refresh its state source, or record the error."""
        rdt = await self.account.request(api, pms, "POST")
        eno = rdt.get("returnCode", 0)
        if eno:
            err_msg = format_api_error(rdt)
            _LOGGER.error("%s failed: %s", name, err_msg)
            self._set_action_error(err_msg)
            return False
        await refresh()
        _LOGGER.info("%s: %s", name, [rdt, pms])
        return rdt

    @staticmethod
    def _wire_bool(value: bool) -> str:
        """Return the CatLink Cloud string boolean for a flag."""
        return "true" if value else "false"
