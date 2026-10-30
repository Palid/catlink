"""Config flow for CatLink integration."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    SOURCE_REAUTH,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.const import CONF_PASSWORD
from homeassistant.core import callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .const import (
    ALIYUN_CHANNEL_DEVICE_TYPES,
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
    DEVICE_SUPPORT_LABELS,
    DOMAIN,
    ERROR_ALIYUN_REQUIRED,
    ERROR_INVALID_AUTH,
    SUPPORTED_DEVICE_TYPES,
)
from .helpers import discover_region, parse_phone_number
from .modules.account import Account


def _device_label(dat: dict, supported: bool) -> str:
    """Build a human-readable label for a device."""
    name = dat.get("deviceName") or dat.get("model") or "Unknown"
    model = dat.get("model", "")
    device_type = dat.get("deviceType", "")
    suffix = DEVICE_SUPPORT_LABELS.get(device_type) or (
        "Supported" if supported else "Limited support"
    )
    if model and model != name:
        return f"{name} ({model}) - {suffix}"
    return f"{name} ({device_type}) - {suffix}"


def _account_identifier(data: Mapping[str, Any]) -> str:
    """Build the account identifier an entry signs in with."""
    email = data.get(CONF_EMAIL)
    if email:
        return email
    return f"+{data.get(CONF_PHONE_IAC, '')}{data.get(CONF_PHONE, '')}"


def _aliyun_fields(stored: Mapping[str, Any]) -> dict:
    """Build the optional Aliyun credential fields, prefilling only the key."""
    return {
        vol.Optional(
            CONF_ALIYUN_CLOUD_KEY, default=stored.get(CONF_ALIYUN_CLOUD_KEY, "")
        ): str,
        vol.Optional(CONF_ALIYUN_CLOUD_SECRET): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD)
        ),
    }


def _aliyun_credentials(
    user_input: Mapping[str, Any], stored: Mapping[str, Any]
) -> dict[str, str]:
    """Return the Aliyun credentials typed in; a blank field keeps the stored value."""
    credentials = {}
    for key in (CONF_ALIYUN_CLOUD_KEY, CONF_ALIYUN_CLOUD_SECRET):
        value = (user_input.get(key) or "").strip() or stored.get(key)
        if value:
            credentials[key] = value
    return credentials


def _lacks_aliyun_credentials(config: Mapping[str, Any], devices: list[dict]) -> bool:
    """Return whether a Device needs the Aliyun Channel but credentials are missing."""
    if config.get(CONF_ALIYUN_CLOUD_KEY) and config.get(CONF_ALIYUN_CLOUD_SECRET):
        return False
    return any(dat.get("deviceType") in ALIYUN_CHANNEL_DEVICE_TYPES for dat in devices)


class CatlinkConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a CatLink config flow."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: ConfigEntry,
    ) -> CatlinkOptionsFlowHandler:
        """Get the options flow for this handler."""
        return CatlinkOptionsFlowHandler()

    def __init__(self) -> None:
        """Initialize the config flow."""
        super().__init__()
        self._account: Account | None = None
        self._config: dict[str, Any] = {}
        self._title: str = ""
        self._configured_entry: ConfigEntry | None = None
        self._discovered_devices: list[dict] = []
        self._device_options: dict[str, str] = {}
        self._supported_ids: list[str] = []

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Route re-authentication to the form matching the entry's sign-in method."""
        if entry_data.get(CONF_EMAIL):
            return await self.async_step_email()
        return await self.async_step_phone()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask which sign-in method the account uses."""
        return self.async_show_menu(step_id="user", menu_options=["email", "phone"])

    async def async_step_email(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle sign-in with an email and password."""
        errors: dict[str, str] = {}

        if user_input is not None:
            email = user_input[CONF_EMAIL].strip()
            password = user_input[CONF_PASSWORD]

            stored: Mapping[str, Any] = {}
            if self.source == SOURCE_REAUTH:
                stored = self._get_reauth_entry().data

            discovery = await discover_region(
                self.hass,
                password=password,
                email=email,
                api_base=stored.get(CONF_API_BASE),
            )
            if discovery.api_base is None:
                errors["base"] = ERROR_INVALID_AUTH
            else:
                config = {
                    CONF_API_BASE: discovery.api_base,
                    CONF_EMAIL: email,
                    CONF_PASSWORD: password,
                }
                if discovery.account_id:
                    config[CONF_ACCOUNT_ID] = discovery.account_id
                if discovery.client_server is not None:
                    config[CONF_CLIENT_SERVER] = discovery.client_server
                if discovery.country_code is not None:
                    config[CONF_COUNTRY_CODE] = discovery.country_code
                config.update(_aliyun_credentials(user_input, stored))
                result = await self._async_signed_in(config, email)
                if result is not None:
                    return result
                errors[CONF_ALIYUN_CLOUD_KEY] = ERROR_ALIYUN_REQUIRED

        reauth_entry = (
            self._get_reauth_entry() if self.source == SOURCE_REAUTH else None
        )
        default_email = reauth_entry.data.get(CONF_EMAIL, "") if reauth_entry else ""

        schema = vol.Schema(
            {
                vol.Required(CONF_EMAIL, default=default_email): str,
                vol.Required(CONF_PASSWORD): str,
                **_aliyun_fields(reauth_entry.data if reauth_entry else {}),
            }
        )

        return self.async_show_form(
            step_id="email",
            data_schema=schema,
            errors=errors,
        )

    async def async_step_phone(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle sign-in with a phone number and password."""
        errors: dict[str, str] = {}

        if user_input is not None:
            phone_raw = user_input[CONF_PHONE].strip()
            password = user_input[CONF_PASSWORD]

            phone_iac, phone_number = parse_phone_number(phone_raw)
            stored: Mapping[str, Any] = {}
            if self.source == SOURCE_REAUTH:
                stored = self._get_reauth_entry().data
            discovery = await discover_region(
                self.hass,
                phone_iac,
                phone_number,
                password,
                api_base=stored.get(CONF_API_BASE),
            )
            if discovery.api_base is None:
                errors["base"] = ERROR_INVALID_AUTH
            else:
                config = {
                    CONF_API_BASE: discovery.api_base,
                    CONF_PHONE: phone_number,
                    CONF_PHONE_IAC: phone_iac,
                    CONF_PASSWORD: password,
                }
                if discovery.account_id:
                    config[CONF_ACCOUNT_ID] = discovery.account_id
                if discovery.client_server is not None:
                    config[CONF_CLIENT_SERVER] = discovery.client_server
                if discovery.country_code is not None:
                    config[CONF_COUNTRY_CODE] = discovery.country_code
                config.update(_aliyun_credentials(user_input, stored))
                result = await self._async_signed_in(
                    config, f"+{phone_iac}{phone_number}"
                )
                if result is not None:
                    return result
                errors[CONF_ALIYUN_CLOUD_KEY] = ERROR_ALIYUN_REQUIRED

        reauth_entry = (
            self._get_reauth_entry() if self.source == SOURCE_REAUTH else None
        )
        default_phone = ""
        if reauth_entry:
            piac = reauth_entry.data.get(CONF_PHONE_IAC, "")
            pnum = reauth_entry.data.get(CONF_PHONE, "")
            default_phone = f"+{piac}{pnum}" if piac and pnum else ""

        schema = vol.Schema(
            {
                vol.Required(CONF_PHONE, default=default_phone): str,
                vol.Required(CONF_PASSWORD): str,
                **_aliyun_fields(reauth_entry.data if reauth_entry else {}),
            }
        )

        return self.async_show_form(
            step_id="phone",
            data_schema=schema,
            errors=errors,
        )

    async def _async_signed_in(
        self, config: dict[str, Any], title: str
    ) -> ConfigFlowResult | None:
        """Settle the entry this sign-in belongs to, then discover its devices.

        Returns None when the Account owns a Scooper Pro Ultra but the Aliyun
        credentials are missing, so the caller shows its form again.
        """
        account = Account(self.hass, config)
        await account.async_check_auth()

        self._account = account
        self._config = config
        self._title = title

        if self.source == SOURCE_REAUTH:
            entry = self._get_reauth_entry()
            known = entry.data.get(CONF_ACCOUNT_ID)
            if known and known != account.account_id:
                return self.async_abort(reason="wrong_account")
            if account.uid:
                other = self.hass.config_entries.async_entry_for_domain_unique_id(
                    DOMAIN, account.uid
                )
                if other is not None and other.entry_id != entry.entry_id:
                    return self.async_abort(reason="already_configured")
            if _lacks_aliyun_credentials(config, await account.get_devices()):
                return None
            return self.async_update_reload_and_abort(
                entry,
                unique_id=account.uid or entry.unique_id,
                title=title,
                data=config,
            )

        configured = (
            self.hass.config_entries.async_entry_for_domain_unique_id(
                DOMAIN, account.uid
            )
            if account.uid
            else None
        )
        if configured is not None:
            if _account_identifier(configured.data) == title:
                return self.async_abort(reason="already_configured")
            self._configured_entry = configured
            return await self.async_step_switch_identifier()

        if account.uid:
            await self.async_set_unique_id(account.uid)
        else:
            await self.async_set_unique_id(title)
            self._abort_if_unique_id_configured()

        devices = await account.get_devices()
        if _lacks_aliyun_credentials(config, devices):
            return None
        if not devices:
            return self.async_create_entry(
                title=title,
                data=config,
                options={CONF_DEVICE_IDS: [], CONF_UPDATE_INTERVAL: 60},
            )

        self._discovered_devices = devices
        self._device_options = {}
        self._supported_ids = []
        for dat in devices:
            did = dat.get("id")
            if not did:
                continue
            supported = dat.get("deviceType", "") in SUPPORTED_DEVICE_TYPES
            self._device_options[did] = _device_label(dat, supported)
            if supported:
                self._supported_ids.append(did)

        return await self.async_step_discovery()

    async def async_step_switch_identifier(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Offer to move the configured entry to the account identifier just used."""
        if user_input is not None:
            if not user_input[CONF_SWITCH_IDENTIFIER]:
                return self.async_abort(reason="already_configured")
            return self.async_update_reload_and_abort(
                self._configured_entry,
                unique_id=self._account.uid,
                title=self._title,
                data={
                    **self._config,
                    **_aliyun_credentials(self._config, self._configured_entry.data),
                },
                reason="identifier_switched",
            )

        return self.async_show_form(
            step_id="switch_identifier",
            data_schema=vol.Schema(
                {vol.Required(CONF_SWITCH_IDENTIFIER, default=True): bool}
            ),
            description_placeholders={
                "current_identifier": _account_identifier(self._configured_entry.data),
                "new_identifier": self._title,
            },
        )

    async def async_step_discovery(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the device discovery step."""
        if user_input is not None:
            selected_ids = user_input.get(CONF_DEVICE_IDS, [])
            update_interval = user_input.get(CONF_UPDATE_INTERVAL, 60)
            return self.async_create_entry(
                title=self._title,
                data=self._config,
                options={
                    CONF_DEVICE_IDS: selected_ids,
                    CONF_UPDATE_INTERVAL: update_interval,
                },
            )

        supported_count = len(self._supported_ids)
        unsupported_count = len(self._device_options) - supported_count

        return self.async_show_form(
            step_id="discovery",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_DEVICE_IDS,
                        default=self._supported_ids,
                    ): cv.multi_select(self._device_options),
                    vol.Optional(
                        CONF_UPDATE_INTERVAL,
                        default=60,
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=30,
                            max=3600,
                            step=30,
                            mode=NumberSelectorMode.BOX,
                            unit_of_measurement="s",
                        )
                    ),
                }
            ),
            description_placeholders={
                "supported_count": str(supported_count),
                "unsupported_count": str(unsupported_count),
                "total_count": str(len(self._device_options)),
            },
        )


class CatlinkOptionsFlowHandler(OptionsFlowWithReload):
    """Handle CatLink options flow."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage device selection and refresh interval."""
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        account = Account(
            self.hass,
            {**dict(self.config_entry.data), **dict(self.config_entry.options or {})},
        )
        await account.async_check_auth()
        devices = await account.get_devices() or []

        device_options: dict[str, str] = {}
        supported_ids: list[str] = []
        for dat in devices:
            did = dat.get("id")
            if not did:
                continue
            supported = dat.get("deviceType", "") in SUPPORTED_DEVICE_TYPES
            device_options[did] = _device_label(dat, supported)
            if supported:
                supported_ids.append(did)

        current_ids = self.config_entry.options.get(CONF_DEVICE_IDS)
        if current_ids is None:
            current_ids = (
                supported_ids if supported_ids else list(device_options.keys())
            )

        current_interval = self.config_entry.options.get(CONF_UPDATE_INTERVAL, 60)

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_DEVICE_IDS,
                        default=current_ids,
                    ): cv.multi_select(device_options),
                    vol.Optional(
                        CONF_UPDATE_INTERVAL,
                        default=current_interval,
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=30,
                            max=3600,
                            step=30,
                            mode=NumberSelectorMode.BOX,
                            unit_of_measurement="s",
                        )
                    ),
                }
            ),
        )
