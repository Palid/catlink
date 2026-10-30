"""Scooper Pro Ultra device module for CatLink integration."""

from __future__ import annotations

import datetime
from datetime import time
from functools import partial
from typing import TYPE_CHECKING

from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError

from ..const import _LOGGER
from ..helpers import format_api_error, optional_int
from ..models.additional_cfg import AdditionalDeviceConfig
from ..models.api.device import LitterDeviceInfo
from ..models.api.logs import LogEntry
from ..models.api.parse import parse_response
from ..modules.aliyun_channel import AliyunAuthFailed, AliyunRequestFailed
from .litter_device import LitterDevice

if TYPE_CHECKING:
    from ..modules.devices_coordinator import DevicesCoordinator

API_BRIEF_INFO = "token/visualScooper/briefInfo"
API_CHANGE_MODE = "token/device/changeMode"
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

WORK_MODE_CODES = {"induction": "00", "manual": "01", "timing": "02"}
WORK_MODE_LABELS = {0: "induction", 1: "manual", 2: "timing"}
LIGHT_LABELS = {0: "off", 1: "on", 2: "auto"}
LIGHT_CODES = {"off": 0, "on": 1, "auto": 2}
LITTER_TYPE_LABELS = {0: "clay", 1: "mixed"}
LITTER_TYPE_CODES = {"clay": 0, "mixed": 1}
ABNORMAL_STATES = {
    1: "anti_pinch_protection",
    6: "motor_blocking_protection",
    7: "weight_protection",
}
CLEANING_BIZ = {"clean": 1, "empty": 2, "level": 3}
STATUS_BY_BIZ = {1: "cleaning", 2: "emptying", 3: "levelling"}


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
        self.thing_properties: dict = {}

    async def async_init(self) -> None:
        """Initialize the device, its schedule list and about info."""
        await super().async_init()
        await self.update_timings()
        await self.update_about()

    @property
    def iot_id(self) -> str | None:
        """Return the Aliyun device id carried on the briefInfo payload."""
        return self.detail.get("iotId")

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
    def status(self) -> str | None:
        """Return the cleaning status, from runStatus and biz."""
        run = self._run_status()
        if run is None:
            return None
        if run == 0:
            return "idle"
        if run == 1:
            return STATUS_BY_BIZ.get(self._current_biz(), "running")
        if run == 2:
            return "cancelling"
        if run in (3, 4):
            return "paused"
        return "unknown"

    @property
    def problem(self) -> bool:
        """Return whether the device reports a known abnormal state."""
        return self._abnormal_state() in ABNORMAL_STATES

    @property
    def bin_full(self) -> bool:
        """Return whether the waste bin is full (the busy-check hard block)."""
        values = self._garbage_values()
        return bool(values) and values[-1] == "2"

    @property
    def bin_installed(self) -> bool:
        """Return whether a waste bin is detected."""
        values = self._garbage_values()
        return bool(values) and values[0] != "0"

    @property
    def litter_balance(self) -> int | None:
        """Return the remaining litter as a percentage."""
        return self._prop_int("CatLitterBalance")

    @property
    def weight(self) -> str | None:
        """Return the measured weight (vendor identifier DeviceWeihgt)."""
        return self.thing_properties.get("DeviceWeihgt")

    @property
    def wifi_signal(self) -> str | None:
        """Return the Wi-Fi RSSI."""
        return self.thing_properties.get("WiFiRSSI")

    @property
    def firmware(self) -> str | None:
        """Return the firmware version reported over the Aliyun Channel."""
        return self.thing_properties.get("FirmwareVersion")

    @property
    def child_lock(self) -> bool:
        """Return whether the child lock is on."""
        return self._prop_int("ChildLock") == 1

    @property
    def continuous_clean(self) -> bool:
        """Return whether continuous clean is on."""
        return self._prop_int("ContinuousClean") == 1

    @property
    def prompt_sounds(self) -> bool:
        """Return whether prompt sounds are on."""
        return self._prop_int("PromptSoundSwitch") == 1

    @property
    def soft_stool(self) -> bool:
        """Return whether soft-stool mode is on."""
        return self._prop_int("SoftStoolMode") == 1

    @property
    def camera_sleep(self) -> bool:
        """Return whether the camera is asleep (SleepMode is inverted: 0 = awake)."""
        return self._prop_int("SleepMode") == 0

    @property
    def work_mode(self) -> str | None:
        """Return the work mode from the Aliyun CurrentModel property."""
        return WORK_MODE_LABELS.get(self._prop_int("CurrentModel"))

    @property
    def indicator_light(self) -> str | None:
        """Return the indicator light mode."""
        return LIGHT_LABELS.get(self._prop_int("IndicatorLight"))

    @property
    def fill_light(self) -> str | None:
        """Return the fill light mode (DayNightMode)."""
        return LIGHT_LABELS.get(self._prop_int("DayNightMode"))

    @property
    def litter_type(self) -> str | None:
        """Return the configured litter type."""
        return LITTER_TYPE_LABELS.get(self._prop_int("CatLitterType"))

    @property
    def clean_delay(self) -> int | None:
        """Return the clean delay in minutes."""
        return self._prop_int("DelayTimeOfClean")

    @property
    def full_bin_threshold(self) -> int | None:
        """Return the full-bin threshold as a 1-4 level."""
        stored = self._prop_int("LimitWhenFull")
        return None if stored is None else stored // 5 + 1

    @property
    def levelling_duration(self) -> int | None:
        """Return the levelling duration as a 1-4 level."""
        stored = self._prop_int("CatLitterFlatTime")
        if stored is None:
            return None
        if stored <= 9:
            return 1
        if stored <= 11:
            return 2
        if stored <= 13:
            return 3
        return 4

    def problem_attrs(self) -> dict:
        """Return the abnormal-state reason as an attribute."""
        return {"reason": ABNORMAL_STATES.get(self._abnormal_state())}

    def _prop_int(self, identifier: str) -> int | None:
        """Return a Thing Property as an int, or unknown when absent or unparseable."""
        return optional_int(self.thing_properties.get(identifier))

    def _abnormal_state(self) -> int | None:
        """Return the AbnormalState value as an int."""
        return self._prop_int("AbnormalState")

    def _run_status_pairs(self) -> dict:
        """Return the parsed key=value pairs of the RunStatus property."""
        return self._parse_pairs(self.thing_properties.get("RunStatus"))

    def _run_status(self) -> int | None:
        """Return the current runStatus value."""
        return optional_int(self._run_status_pairs().get("runStatus"))

    def _current_biz(self) -> int | None:
        """Return the current biz value, reused for pause/resume/cancel."""
        return optional_int(self._run_status_pairs().get("biz"))

    def _garbage_values(self) -> list:
        """Return the parsed values of GarbageBoxStatus in wire order.

        The app slices this compound string by fixed offset; its key names are
        not in the source, so the values are read positionally: the first is the
        bin-detected flag, the last is the fill level. Confirm the literal keys
        during live verification.
        """
        return list(self._parse_pairs(self.thing_properties.get("GarbageBoxStatus")).values())

    @staticmethod
    def _parse_pairs(raw) -> dict:
        """Parse a key=value&key=value Thing Property string into a dict."""
        result: dict[str, str] = {}
        if not isinstance(raw, str):
            return result
        for part in raw.split("&"):
            if "=" in part:
                key, value = part.split("=", 1)
                result[key] = value
        return result

    def _require_snapshot(self) -> None:
        """Refuse an action when no property snapshot has been read."""
        if not self.thing_properties:
            raise HomeAssistantError(f"No live status for {self.name} yet")

    def _require_idle(self) -> None:
        """Refuse a setting or work-mode write while the device is not idle."""
        self._require_snapshot()
        if self._run_status() != 0:
            raise HomeAssistantError(f"{self.name} is running; wait until it is idle")

    def _check_cleaning(self) -> None:
        """Refuse a cleaning action unless the device is idle, well and not full."""
        self._require_snapshot()
        if self._run_status() != 0:
            raise HomeAssistantError(f"{self.name} is busy; wait until it is idle")
        if self._abnormal_state() in ABNORMAL_STATES:
            raise HomeAssistantError(f"{self.name} reports a problem; clear it first")
        if self.bin_full:
            raise HomeAssistantError(f"{self.name} waste bin is full; empty it first")

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
            "status": {
                "icon": "mdi:robot-vacuum",
            },
            "litter_balance": {
                "icon": "mdi:gauge",
                "unit": "%",
            },
            "weight": {
                "icon": "mdi:scale",
            },
            "wifi_signal": {
                "icon": "mdi:wifi",
                "class": "signal_strength",
                "unit": "dBm",
            },
            "firmware": {
                "icon": "mdi:chip",
            },
        }

    @property
    def hass_binary_sensor(self) -> dict:
        """Return the device binary sensors."""
        return {
            "problem": {
                "icon": "mdi:alert",
                "class": "problem",
                "state_attrs": self.problem_attrs,
            },
            "bin_full": {
                "icon": "mdi:delete-alert",
                "class": "problem",
            },
            "bin_installed": {
                "icon": "mdi:delete-empty",
            },
        }

    @property
    def hass_select(self) -> dict:
        """Return the device selects."""
        return {
            "work_mode": {
                "icon": "mdi:menu",
                "options": list(WORK_MODE_CODES),
                "async_select": self.async_select_work_mode,
            },
            "indicator_light": {
                "icon": "mdi:lightbulb",
                "options": list(LIGHT_CODES),
                "async_select": self.async_select_indicator_light,
            },
            "fill_light": {
                "icon": "mdi:light-flood-down",
                "options": list(LIGHT_CODES),
                "async_select": self.async_select_fill_light,
            },
            "litter_type": {
                "icon": "mdi:shaker-outline",
                "options": list(LITTER_TYPE_CODES),
                "async_select": self.async_select_litter_type,
            },
        }

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
            "child_lock": {
                "icon": "mdi:lock",
                "async_turn_on": partial(self.async_set_child_lock, True),
                "async_turn_off": partial(self.async_set_child_lock, False),
            },
            "continuous_clean": {
                "icon": "mdi:replay",
                "async_turn_on": partial(self.async_set_continuous_clean, True),
                "async_turn_off": partial(self.async_set_continuous_clean, False),
            },
            "prompt_sounds": {
                "icon": "mdi:volume-high",
                "async_turn_on": partial(self.async_set_prompt_sounds, True),
                "async_turn_off": partial(self.async_set_prompt_sounds, False),
            },
            "soft_stool": {
                "icon": "mdi:emoticon-poop",
                "async_turn_on": partial(self.async_set_soft_stool, True),
                "async_turn_off": partial(self.async_set_soft_stool, False),
            },
            "camera_sleep": {
                "icon": "mdi:camera-off",
                "async_turn_on": partial(self.async_set_camera_sleep, True),
                "async_turn_off": partial(self.async_set_camera_sleep, False),
            },
        }

    @property
    def hass_number(self) -> dict:
        """Return the device numbers."""
        return {
            "clean_delay": {
                "icon": "mdi:timer-sand",
                "unit": "min",
                "min": 1,
                "max": 60,
                "async_set": self.async_set_clean_delay,
            },
            "full_bin_threshold": {
                "icon": "mdi:delete-clock",
                "min": 1,
                "max": 4,
                "async_set": self.async_set_full_bin_threshold,
            },
            "levelling_duration": {
                "icon": "mdi:timer-cog",
                "min": 1,
                "max": 4,
                "async_set": self.async_set_levelling_duration,
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
            "clean": {
                "icon": "mdi:broom",
                "name": "Clean",
                "async_press": self.async_clean,
            },
            "empty": {
                "icon": "mdi:delete-empty",
                "name": "Empty",
                "async_press": self.async_empty,
            },
            "level": {
                "icon": "mdi:format-align-middle",
                "name": "Level litter",
                "async_press": self.async_level,
            },
            "pause": {
                "icon": "mdi:pause",
                "name": "Pause",
                "async_press": self.async_pause,
            },
            "resume": {
                "icon": "mdi:play",
                "name": "Resume",
                "async_press": self.async_resume,
            },
            "cancel": {
                "icon": "mdi:stop",
                "name": "Cancel",
                "async_press": self.async_cancel,
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

    async def async_clean(self) -> bool:
        """Start a cleaning cycle."""
        return await self._start_cleaning("clean")

    async def async_empty(self) -> bool:
        """Empty the waste bin."""
        return await self._start_cleaning("empty")

    async def async_level(self) -> bool:
        """Level the litter."""
        return await self._start_cleaning("level")

    async def async_pause(self) -> bool:
        """Pause a running cycle, reusing the current biz."""
        self._require_snapshot()
        if self._run_status() != 1:
            raise HomeAssistantError(f"{self.name} is not running")
        return await self._write_run_status(3, self._current_biz())

    async def async_resume(self) -> bool:
        """Resume a paused cycle, reusing the current biz."""
        self._require_snapshot()
        if self._run_status() not in (3, 4):
            raise HomeAssistantError(f"{self.name} is not paused")
        return await self._write_run_status(1, self._current_biz())

    async def async_cancel(self) -> bool:
        """Cancel a running or paused cycle, reusing the current biz."""
        self._require_snapshot()
        if self._run_status() not in (1, 3, 4):
            raise HomeAssistantError(f"{self.name} has nothing to cancel")
        return await self._write_run_status(2, self._current_biz())

    async def async_set_child_lock(self, enable: bool, **kwargs) -> bool:
        """Enable or disable the child lock."""
        self._require_idle()
        return await self._write_thing_property("ChildLock", 1 if enable else 0)

    async def async_set_continuous_clean(self, enable: bool, **kwargs) -> bool:
        """Enable or disable continuous clean."""
        self._require_idle()
        return await self._write_thing_property("ContinuousClean", 1 if enable else 0)

    async def async_set_prompt_sounds(self, enable: bool, **kwargs) -> bool:
        """Enable or disable prompt sounds."""
        self._require_idle()
        return await self._write_thing_property("PromptSoundSwitch", 1 if enable else 0)

    async def async_set_soft_stool(self, enable: bool, **kwargs) -> bool:
        """Enable or disable soft-stool mode."""
        self._require_idle()
        return await self._write_thing_property("SoftStoolMode", 1 if enable else 0)

    async def async_set_camera_sleep(self, enable: bool, **kwargs) -> bool:
        """Put the camera to sleep or wake it (SleepMode is inverted)."""
        self._require_idle()
        return await self._write_thing_property("SleepMode", 0 if enable else 1)

    async def async_select_indicator_light(self, option: str, **kwargs) -> bool:
        """Set the indicator light mode."""
        return await self._write_light("IndicatorLight", option)

    async def async_select_fill_light(self, option: str, **kwargs) -> bool:
        """Set the fill light mode (DayNightMode)."""
        return await self._write_light("DayNightMode", option)

    async def async_select_litter_type(self, option: str, **kwargs) -> bool:
        """Set the litter type."""
        code = LITTER_TYPE_CODES.get(option)
        if code is None:
            return False
        self._require_idle()
        return await self._write_thing_property("CatLitterType", code)

    async def async_select_work_mode(self, option: str, **kwargs) -> bool:
        """Change the work mode over the CatLink Cloud once the device is idle."""
        code = WORK_MODE_CODES.get(option)
        if code is None:
            return False
        self._require_idle()
        return await self._send_setting(
            API_CHANGE_MODE,
            {"workModel": code, "deviceId": self.id},
            "Work mode",
            self.update_device_detail,
        )

    async def async_set_clean_delay(self, value: float, **kwargs) -> bool:
        """Set the clean delay in minutes."""
        self._require_idle()
        return await self._write_thing_property("DelayTimeOfClean", int(value))

    async def async_set_full_bin_threshold(self, value: float, **kwargs) -> bool:
        """Set the full-bin threshold from a 1-4 level."""
        self._require_idle()
        return await self._write_thing_property("LimitWhenFull", (int(value) - 1) * 5)

    async def async_set_levelling_duration(self, value: float, **kwargs) -> bool:
        """Set the levelling duration from a 1-4 level."""
        self._require_idle()
        return await self._write_thing_property(
            "CatLitterFlatTime", (int(value) - 1) * 2 + 9
        )

    async def _start_cleaning(self, action: str) -> bool:
        """Run the busy-check, then write the cleaning RunStatus."""
        self._check_cleaning()
        return await self._write_run_status(1, CLEANING_BIZ[action])

    async def _write_run_status(self, run: int, biz: int | None) -> bool:
        """Write the RunStatus Thing Property as runStatus=<d>&biz=<d>."""
        return await self._write_thing_property(
            "RunStatus", f"runStatus={run}&biz={biz if biz is not None else 0}"
        )

    async def _write_light(self, identifier: str, option: str) -> bool:
        """Write one of the three-state light selects when idle."""
        code = LIGHT_CODES.get(option)
        if code is None:
            return False
        self._require_idle()
        return await self._write_thing_property(identifier, code)

    async def _write_thing_property(self, identifier: str, value) -> bool:
        """Write one Thing Property over the Aliyun Channel and re-read it."""
        iot_id = self.iot_id
        if not iot_id:
            raise HomeAssistantError(f"{self.name} has no Aliyun device id")
        try:
            channel = await self.account.async_aliyun_channel()
            await channel.async_set_properties(iot_id, {identifier: value})
        except AliyunAuthFailed as exc:
            raise ConfigEntryAuthFailed(str(exc)) from exc
        except AliyunRequestFailed as exc:
            raise HomeAssistantError(f"Aliyun write failed for {self.name}: {exc}") from exc
        await self._async_update_thing_properties()
        self._handle_listeners()
        return True

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
        await self._async_update_thing_properties()
        self._handle_listeners()
        return rdt

    async def _async_update_thing_properties(self) -> None:
        """Read the Aliyun Thing Properties, leaving other devices unaffected on error."""
        iot_id = self.iot_id
        if not iot_id:
            self.warn_once(
                "no_iot_id",
                "No iotId on briefInfo for %s; skipping Aliyun Channel",
                self.name,
            )
            return
        try:
            channel = await self.account.async_aliyun_channel()
            self.thing_properties = await channel.async_get_properties(iot_id)
        except AliyunAuthFailed as exc:
            raise ConfigEntryAuthFailed(str(exc)) from exc
        except AliyunRequestFailed as exc:
            self.warn_once(
                "aliyun_unavailable",
                "Aliyun Channel unavailable for %s: %s",
                self.name,
                exc,
            )
        else:
            self.clear_warning("aliyun_unavailable")

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
