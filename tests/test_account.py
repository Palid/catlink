"""Tests for CatLink Account module."""

from datetime import timedelta
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from homeassistant.const import CONF_DEVICES, CONF_PASSWORD, CONF_TOKEN

from custom_components.catlink.const import (
    CONF_ACCOUNT_ID,
    CONF_ALIYUN_CLOUD_KEY,
    CONF_ALIYUN_CLOUD_SECRET,
    CONF_API_BASE,
    CONF_EMAIL,
    CONF_LANGUAGE,
    CONF_PHONE,
    CONF_PHONE_IAC,
    CONF_SCAN_INTERVAL,
    CONF_UPDATE_INTERVAL,
    DEFAULT_API_BASE,
    DOMAIN,
    SCAN_INTERVAL,
)
from custom_components.catlink.modules.account import Account, _redact_for_log

ALIYUN_CREDENTIALS = {
    CONF_ALIYUN_CLOUD_KEY: "test-aliyun-key",
    CONF_ALIYUN_CLOUD_SECRET: "test-aliyun-secret",
}


@pytest.fixture
def account_config():
    """Sample account config."""
    return {
        CONF_PHONE_IAC: "86",
        CONF_PHONE: "13812345678",
        CONF_PASSWORD: "testpass",
        CONF_TOKEN: "existing-token",
    }


@pytest.fixture
def mock_http_session():
    """Mock aiohttp client session."""
    with patch(
        "custom_components.catlink.modules.account.aiohttp_client.async_create_clientsession"
    ) as mock_create:
        session = MagicMock()
        mock_create.return_value = session
        yield session


@pytest.fixture
def account(hass, account_config, mock_http_session):
    """Create Account instance with mocked HTTP."""
    return Account(hass, account_config)


def fake_cloud(account, responses):
    """Answer requests from a canned map of api path to body, recording each call."""
    calls = []

    async def _request(method, url, **kwargs):
        pms = kwargs.get("data") or kwargs.get("params") or {}
        calls.append((method, url, pms))
        body = next(
            (rsp for api, rsp in responses.items() if url.endswith(api)),
            {"returnCode": 0, "data": {}},
        )
        resp = MagicMock()
        resp.json = AsyncMock(return_value=body)
        return resp

    account.http.request = _request
    return calls


def fake_profile(account_id):
    """Answer token/user/info the way the cloud does: nested, globalId null."""
    return {
        "returnCode": 0,
        "data": {"appUser": {"id": account_id, "globalId": None}},
    }


class TestAccountProperties:
    """Tests for Account properties."""

    def test_phone(self, account) -> None:
        """Test phone property."""
        assert account.phone == "13812345678"

    def test_uid(self, account) -> None:
        """Test uid property."""
        assert account.uid == "86-13812345678"

    def test_token(self, account) -> None:
        """Test token property."""
        assert account.token == "existing-token"

    def test_token_empty_when_missing(self, hass, mock_http_session) -> None:
        """Test token returns empty string when not in config."""
        config = {CONF_PHONE_IAC: "86", CONF_PHONE: "13812345678", CONF_PASSWORD: "pwd"}
        acc = Account(hass, config)
        assert acc.token == ""


class TestAccountGetConfig:
    """Tests for Account get_config."""

    def test_get_config_from_account(self, account) -> None:
        """Test get_config returns value from account config."""
        assert account.get_config(CONF_PHONE) == "13812345678"

    def test_get_config_from_global(self, hass, mock_http_session) -> None:
        """Test get_config falls back to global config."""
        hass.data[DOMAIN] = {"config": {CONF_LANGUAGE: "en_US"}}
        config = {CONF_PHONE_IAC: "86", CONF_PHONE: "13812345678", CONF_PASSWORD: "pwd"}
        acc = Account(hass, config)
        assert acc.get_config(CONF_LANGUAGE) == "en_US"

    def test_get_config_default(self, account) -> None:
        """Test get_config returns default when key missing."""
        assert account.get_config("missing_key", "default") == "default"


class TestAccountUpdateInterval:
    """Tests for Account update_interval."""

    def test_update_interval_from_config(self, hass, mock_http_session) -> None:
        """Test update_interval from account config."""
        config = {
            CONF_PHONE_IAC: "86",
            CONF_PHONE: "13812345678",
            CONF_PASSWORD: "pwd",
            CONF_UPDATE_INTERVAL: 120,
        }
        acc = Account(hass, config)
        assert acc.update_interval == timedelta(seconds=120)

    def test_update_interval_from_global(self, hass, mock_http_session) -> None:
        """Test update_interval falls back to global config."""
        hass.data[DOMAIN] = {"config": {CONF_UPDATE_INTERVAL: 90}}
        config = {CONF_PHONE_IAC: "86", CONF_PHONE: "13812345678", CONF_PASSWORD: "pwd"}
        acc = Account(hass, config)
        assert acc.update_interval == timedelta(seconds=90)

    def test_update_interval_default(self, account) -> None:
        """Test update_interval defaults to SCAN_INTERVAL."""
        assert account.update_interval == SCAN_INTERVAL


class TestAccountApiUrl:
    """Tests for Account api_url."""

    def test_api_url_relative_path(self, account) -> None:
        """Test api_url builds URL from base and path."""
        url = account.api_url("token/device/list")
        assert url == f"{DEFAULT_API_BASE.rstrip('/')}/token/device/list"

    def test_api_url_absolute_https(self, account) -> None:
        """Test api_url returns full URL as-is for https."""
        url = account.api_url("https://other.api/endpoint")
        assert url == "https://other.api/endpoint"

    def test_api_url_absolute_http(self, account) -> None:
        """Test api_url returns full URL as-is for http."""
        url = account.api_url("http://other.api/endpoint")
        assert url == "http://other.api/endpoint"

    def test_api_url_custom_base(self, hass, mock_http_session) -> None:
        """Test api_url uses custom API base from config."""
        config = {
            CONF_PHONE_IAC: "86",
            CONF_PHONE: "13812345678",
            CONF_PASSWORD: "pwd",
            CONF_API_BASE: "https://custom.api/v1/",
        }
        acc = Account(hass, config)
        url = acc.api_url("login")
        assert url == "https://custom.api/v1/login"


class TestAccountParamsSign:
    """Tests for Account.params_sign static method."""

    def test_params_sign_deterministic(self) -> None:
        """Test params_sign produces consistent output."""
        pms = {"a": "1", "b": "2"}
        sig1 = Account.params_sign(pms)
        sig2 = Account.params_sign(pms)
        assert sig1 == sig2
        assert len(sig1) == 32
        assert sig1.isupper()

    def test_params_sign_different_inputs_different_output(self) -> None:
        """Test params_sign differs for different params."""
        sig1 = Account.params_sign({"a": "1"})
        sig2 = Account.params_sign({"a": "2"})
        assert sig1 != sig2


class TestAccountEncryptPassword:
    """Tests for Account.encrypt_password static method."""

    def test_encrypt_password_returns_base64(self) -> None:
        """Test encrypt_password returns base64 string."""
        result = Account.encrypt_password("short")
        assert isinstance(result, str)
        assert len(result) > 0
        try:
            import base64

            base64.b64decode(result)
        except Exception:
            pytest.fail("Result should be valid base64")

    def test_encrypt_password_same_length_for_same_input(self) -> None:
        """Test encrypt_password produces consistent output length for same input."""
        r1 = Account.encrypt_password("test123")
        r2 = Account.encrypt_password("test123")
        assert len(r1) == len(r2)
        assert len(r1) > 50


class TestAccountPassword:
    """Tests for Account password property."""

    def test_short_password_gets_encrypted(self, hass, mock_http_session) -> None:
        """Test password <= 16 chars is encrypted."""
        config = {
            CONF_PHONE_IAC: "86",
            CONF_PHONE: "13812345678",
            CONF_PASSWORD: "short",
        }
        acc = Account(hass, config)
        pwd = acc.password
        assert pwd != "short"
        assert len(pwd) > 16

    def test_password_over_16_chars_gets_encrypted(
        self, hass, mock_http_session
    ) -> None:
        """Test a 17 character password is encrypted, not sent as typed."""
        config = {
            CONF_PHONE_IAC: "86",
            CONF_PHONE: "13812345678",
            CONF_PASSWORD: "a" * 17,
        }
        acc = Account(hass, config)
        pwd = acc.password
        assert pwd != "a" * 17
        assert len(pwd) > 50


class TestRedactForLog:
    """Tests for the debug-log redaction helper."""

    def test_blanks_token_email_and_mobile_keeps_account_id(self) -> None:
        """Test credential and identifier fields are blanked, cloud id kept."""
        result = {
            "returnCode": 0,
            "data": {
                "token": "secret-token",
                "appUser": {"id": "acc-1", "email": "cat@example.com", "mobile": "138"},
                "devices": [{"phone": "138", "deviceName": "Litter Box"}],
            },
        }

        redacted = _redact_for_log(result)

        assert redacted["data"]["token"] == "***"
        assert redacted["data"]["appUser"]["email"] == "***"
        assert redacted["data"]["appUser"]["mobile"] == "***"
        assert redacted["data"]["devices"][0]["phone"] == "***"
        assert redacted["data"]["appUser"]["id"] == "acc-1"
        assert redacted["data"]["devices"][0]["deviceName"] == "Litter Box"
        assert result["data"]["token"] == "secret-token"


class TestAccountRequest:
    """Tests for Account request method."""

    @pytest.mark.usefixtures("enable_custom_integrations")
    async def test_request_get_returns_json(self, account) -> None:
        """Test request GET returns parsed JSON."""
        request_kwargs = {}

        async def mock_request(method, url, **kwargs):
            request_kwargs.clear()
            request_kwargs.update(kwargs)
            resp = MagicMock()
            resp.json = AsyncMock(return_value={"returnCode": 0, "data": {}})
            return resp

        account.http.request = mock_request

        result = await account.request("token/device/list", {"type": "NONE"})

        assert result == {"returnCode": 0, "data": {}}
        params = request_kwargs.get("params") or {}
        assert "noncestr" in params

    @pytest.mark.usefixtures("enable_custom_integrations")
    async def test_request_includes_token_in_params(self, account) -> None:
        """Test request includes token in params when present."""
        call_params = {}

        async def mock_request(method, url, **kwargs):
            call_params.update(kwargs)
            resp = MagicMock()
            resp.json = AsyncMock(return_value={})
            return resp

        account.http.request = mock_request
        await account.request("api", {"key": "val"})

        params = call_params.get("params") or call_params.get("data") or {}
        assert CONF_TOKEN in params
        assert params[CONF_TOKEN] == "existing-token"

    @pytest.mark.usefixtures("enable_custom_integrations")
    async def test_request_retries_once_on_1002_without_leaking_flag(
        self, account
    ) -> None:
        """Test a 1002 retries once and never passes _retried to the transport."""
        responses = [{"returnCode": 1002}, {"returnCode": 0, "data": {"ok": 1}}]

        async def mock_request(method, url, **kwargs):
            assert "_retried" not in kwargs
            resp = MagicMock()
            resp.json = AsyncMock(return_value=responses.pop(0))
            return resp

        account.http.request = mock_request

        with patch.object(
            account, "async_login", new_callable=AsyncMock, return_value=True
        ):
            result = await account.request("token/device/list")

        assert result == {"returnCode": 0, "data": {"ok": 1}}

    @pytest.mark.usefixtures("enable_custom_integrations")
    async def test_profile_fetch_does_not_re_enter_login(self, account) -> None:
        """Test a 1002 on the profile fetch does not recurse back into sign-in."""
        logins = 0

        async def mock_request(method, url, **kwargs):
            nonlocal logins
            resp = MagicMock()
            if url.endswith("login/password"):
                logins += 1
                body = {"returnCode": 0, "data": {"token": "tok"}}
            elif url.endswith("token/user/info"):
                body = {"returnCode": 1002, "msg": "Illegal token"}
            else:
                body = {"returnCode": 0, "data": {}}
            resp.json = AsyncMock(return_value=body)
            return resp

        account.http.request = mock_request

        with patch.object(account, "async_check_auth", new_callable=AsyncMock):
            assert await account.async_login() is True

        assert logins == 1
        assert account.account_id == ""


class TestAccountAsyncLogin:
    """Tests for Account async_login."""

    @pytest.mark.usefixtures("enable_custom_integrations")
    async def test_login_success(self, account) -> None:
        """Test async_login succeeds with valid token in response."""
        with patch.object(account, "request", new_callable=AsyncMock) as mock_request:
            with patch.object(
                account, "async_check_auth", new_callable=AsyncMock
            ) as mock_check:
                mock_request.return_value = {
                    "data": {"token": "new-token"},
                    "returnCode": 0,
                }
                result = await account.async_login()

                assert result is True
                assert account.token == "new-token"
                mock_check.assert_called_once_with(True)

    @pytest.mark.usefixtures("enable_custom_integrations")
    async def test_login_fails_no_token(self, account) -> None:
        """Test async_login returns False when no token in response."""
        with patch.object(account, "request", new_callable=AsyncMock) as mock_request:
            mock_request.return_value = {"data": {}, "returnCode": 0}
            result = await account.async_login()

            assert result is False
            assert account.token == ""


class TestAccountSignInMethod:
    """Tests for the Sign-in Method the account uses."""

    @pytest.mark.usefixtures("enable_custom_integrations")
    async def test_email_account_signs_in_through_email_endpoint(
        self, hass, mock_http_session
    ) -> None:
        """Test an Email account posts to the Email endpoint with the right fields."""
        acc = Account(hass, {CONF_EMAIL: "cat@example.com", CONF_PASSWORD: "testpass"})
        calls = fake_cloud(
            acc,
            {
                "login/email/v2": {"returnCode": 0, "data": {"token": "email-token"}},
                "token/user/info": fake_profile("acc-1"),
            },
        )

        assert await acc.async_login() is True

        method, url, pms = calls[0]
        assert method == "POST"
        assert url.endswith("login/email/v2")
        assert pms["email"] == "cat@example.com"
        assert pms["platform"] == "ANDROID"
        assert pms["os"]
        assert pms["password"] != "testpass"
        assert "sign" in pms

    @pytest.mark.usefixtures("enable_custom_integrations")
    async def test_phone_account_signs_in_through_phone_endpoint(
        self, account
    ) -> None:
        """Test a Phone account still posts to the Phone endpoint."""
        calls = fake_cloud(
            account,
            {
                "login/password": {"returnCode": 0, "data": {"token": "phone-token"}},
                "token/user/info": fake_profile("acc-1"),
            },
        )

        assert await account.async_login() is True

        method, url, pms = calls[0]
        assert method == "POST"
        assert url.endswith("login/password")
        assert pms["mobile"] == "13812345678"
        assert pms["internationalCode"] == "86"
        assert pms["password"] != "testpass"

    @pytest.mark.usefixtures("enable_custom_integrations")
    async def test_sign_in_with_long_password_encrypts_it(
        self, hass, mock_http_session
    ) -> None:
        """Test a 17 character password reaches the cloud encrypted."""
        acc = Account(
            hass,
            {CONF_EMAIL: "cat@example.com", CONF_PASSWORD: "correcthorsebattery"},
        )
        calls = fake_cloud(
            acc, {"login/email/v2": {"returnCode": 0, "data": {"token": "tok"}}}
        )

        await acc.async_login()

        assert calls[0][2]["password"] != "correcthorsebattery"


class TestAccountId:
    """Tests for the Account's stable cloud id."""

    @pytest.mark.usefixtures("enable_custom_integrations")
    async def test_sign_in_reads_account_id_from_profile(self, account) -> None:
        """Test a successful sign-in adopts the Account id as the unique id."""
        fake_cloud(
            account,
            {
                "login/password": {"returnCode": 0, "data": {"token": "phone-token"}},
                "token/user/info": fake_profile("acc-1"),
            },
        )

        assert await account.async_login() is True
        assert account.account_id == "acc-1"
        assert account.uid == "acc-1"

    @pytest.mark.usefixtures("enable_custom_integrations")
    async def test_account_id_comes_from_id_when_global_id_is_null(
        self, account
    ) -> None:
        """Test the Account id is read from id, which globalId is null alongside."""
        fake_cloud(
            account,
            {
                "login/password": {"returnCode": 0, "data": {"token": "phone-token"}},
                "token/user/info": {
                    "returnCode": 0,
                    "data": {"appUser": {"id": "123456", "globalId": None}},
                },
            },
        )

        await account.async_login()

        assert account.account_id == "123456"

    @pytest.mark.usefixtures("enable_custom_integrations")
    async def test_sign_in_stores_token_under_the_account_id(
        self, account, hass_storage
    ) -> None:
        """Test the token lands under the new key, so the next start need not sign in."""
        fake_cloud(
            account,
            {
                "login/password": {"returnCode": 0, "data": {"token": "phone-token"}},
                "token/user/info": fake_profile("acc-1"),
            },
        )

        await account.async_login()

        assert f"{DOMAIN}/auth-acc-1.json" in hass_storage
        assert f"{DOMAIN}/auth-86-13812345678.json" not in hass_storage

    @pytest.mark.usefixtures("enable_custom_integrations")
    async def test_account_id_unknown_when_profile_has_none(self, account) -> None:
        """Test a profile without an id leaves the legacy Phone-based key in place."""
        fake_cloud(
            account,
            {
                "login/password": {"returnCode": 0, "data": {"token": "phone-token"}},
                "token/user/info": {"returnCode": 0, "data": {"appUser": {}}},
            },
        )

        assert await account.async_login() is True
        assert account.account_id == ""
        assert account.uid == "86-13812345678"

    def test_uid_falls_back_to_phone_key(self, account) -> None:
        """Test a legacy entry without an Account id keeps its Phone-based key."""
        assert account.account_id == ""
        assert account.uid == "86-13812345678"

    def test_uid_prefers_account_id(self, hass, mock_http_session) -> None:
        """Test a stored Account id wins over the Phone-based key."""
        acc = Account(
            hass,
            {
                CONF_PHONE_IAC: "86",
                CONF_PHONE: "13812345678",
                CONF_PASSWORD: "pwd",
                CONF_ACCOUNT_ID: "acc-1",
            },
        )
        assert acc.uid == "acc-1"


class TestAccountGetDevices:
    """Tests for Account get_devices."""

    @pytest.mark.usefixtures("enable_custom_integrations")
    async def test_get_devices_returns_list(self, account) -> None:
        """Test get_devices returns device list from API."""
        with patch.object(account, "request", new_callable=AsyncMock) as mock_request:
            mock_request.return_value = {
                "returnCode": 0,
                "data": {
                    CONF_DEVICES: [
                        {"id": "dev1", "deviceName": "Litter Box"},
                    ]
                },
            }
            devices = await account.get_devices()

            assert devices == [{"id": "dev1", "deviceName": "Litter Box"}]
            mock_request.assert_called_once()

    @pytest.mark.usefixtures("enable_custom_integrations")
    async def test_get_devices_login_when_no_token(
        self, hass, mock_http_session
    ) -> None:
        """Test get_devices calls async_login when token is empty."""
        config = {
            CONF_PHONE_IAC: "86",
            CONF_PHONE: "13812345678",
            CONF_PASSWORD: "short",
        }
        acc = Account(hass, config)
        acc._config[CONF_TOKEN] = None

        with patch.object(acc, "async_login", new_callable=AsyncMock) as mock_login:
            mock_login.return_value = False
            devices = await acc.get_devices()

            assert devices == []
            mock_login.assert_called_once()


class TestAccountGetCats:
    """Tests for Account get_cats."""

    @pytest.mark.usefixtures("enable_custom_integrations")
    async def test_get_cats_returns_list(self, account) -> None:
        """Test get_cats returns cats list from API."""
        with patch.object(account, "request", new_callable=AsyncMock) as mock_request:
            mock_request.return_value = {
                "returnCode": 0,
                "data": {"cats": [{"id": "cat1", "petName": "Zulu"}]},
            }
            cats = await account.get_cats("Europe/Belgrade")

            assert cats == [{"id": "cat1", "petName": "Zulu"}]
            mock_request.assert_called_once()

    @pytest.mark.usefixtures("enable_custom_integrations")
    async def test_get_cats_login_when_no_token(
        self, hass, mock_http_session
    ) -> None:
        """Test get_cats calls async_login when token is empty."""
        config = {
            CONF_PHONE_IAC: "86",
            CONF_PHONE: "13812345678",
            CONF_PASSWORD: "short",
        }
        acc = Account(hass, config)
        acc._config[CONF_TOKEN] = None

        with patch.object(acc, "async_login", new_callable=AsyncMock) as mock_login:
            mock_login.return_value = False
            cats = await acc.get_cats("Europe/Belgrade")

            assert cats == []
            mock_login.assert_called_once()


class TestAccountGetCatSummarySimple:
    """Tests for Account get_cat_summary_simple."""

    @pytest.mark.usefixtures("enable_custom_integrations")
    async def test_get_cat_summary_simple_returns_data(self, account) -> None:
        """Test get_cat_summary_simple returns summary data."""
        with patch.object(account, "request", new_callable=AsyncMock) as mock_request:
            mock_request.return_value = {
                "returnCode": 0,
                "data": {"statusDescription": "Good"},
            }
            summary = await account.get_cat_summary_simple(
                "169004", "2026-02-06", "Europe/Belgrade"
            )

            assert summary == {"statusDescription": "Good"}
            mock_request.assert_called_once()

class TestAccountAsyncCheckAuth:
    """Tests for Account async_check_auth."""

    @pytest.mark.usefixtures("enable_custom_integrations")
    async def test_check_auth_save_stores_token(self, account) -> None:
        """Test async_check_auth with save=True stores token to Store."""
        with patch(
            "custom_components.catlink.modules.account.Store"
        ) as mock_store_cls:
            mock_store = MagicMock()
            mock_store.async_load = AsyncMock(return_value={})
            mock_store.async_save = AsyncMock()
            mock_store_cls.return_value = mock_store

            result = await account.async_check_auth(save=True)

            assert result[CONF_PHONE] == "13812345678"
            assert result[CONF_TOKEN] == "existing-token"
            mock_store.async_save.assert_called_once()
            saved = mock_store.async_save.call_args[0][0]
            assert saved[CONF_TOKEN] == "existing-token"

    @pytest.mark.usefixtures("enable_custom_integrations")
    async def test_check_auth_load_restores_token_from_store(
        self, account
    ) -> None:
        """Test async_check_auth loads token from Store when present."""
        with patch(
            "custom_components.catlink.modules.account.Store"
        ) as mock_store_cls:
            mock_store = MagicMock()
            mock_store.async_load = AsyncMock(
                return_value={CONF_PHONE: "13812345678", CONF_TOKEN: "stored-token"}
            )
            mock_store_cls.return_value = mock_store

            with patch.object(
                account, "async_login", new_callable=AsyncMock
            ) as mock_login:
                result = await account.async_check_auth(save=False)

                assert account.token == "stored-token"
                mock_login.assert_not_called()
                assert result[CONF_TOKEN] == "stored-token"

    @pytest.mark.usefixtures("enable_custom_integrations")
    async def test_check_auth_calls_login_when_store_empty(
        self, account
    ) -> None:
        """Test async_check_auth calls async_login when Store has no token."""
        with patch(
            "custom_components.catlink.modules.account.Store"
        ) as mock_store_cls:
            mock_store = MagicMock()
            mock_store.async_load = AsyncMock(return_value={})
            mock_store_cls.return_value = mock_store

            with patch.object(
                account, "async_login", new_callable=AsyncMock
            ) as mock_login:
                mock_login.return_value = True
                await account.async_check_auth(save=False)

                mock_login.assert_called_once()


class TestAccountRequestErrors:
    """Tests for Account request error handling."""

    @pytest.mark.usefixtures("enable_custom_integrations")
    async def test_request_returns_empty_on_connector_error(self, account) -> None:
        """Test request returns empty dict on ClientConnectorError."""
        from aiohttp import ClientConnectorError

        async def mock_request_fail(*args, **kwargs):
            raise ClientConnectorError(MagicMock(), OSError("Connection refused"))

        account.http.request = mock_request_fail

        result = await account.request("token/device/list")

        assert result == {}

    @pytest.mark.usefixtures("enable_custom_integrations")
    async def test_request_returns_empty_on_timeout(self, account) -> None:
        """Test request returns empty dict on TimeoutError."""
        async def mock_request_timeout(*args, **kwargs):
            raise TimeoutError("Request timed out")

        account.http.request = mock_request_timeout

        result = await account.request("token/device/list")

        assert result == {}

    @pytest.mark.usefixtures("enable_custom_integrations")
    async def test_request_returns_empty_when_json_is_none(self, account) -> None:
        """Test request returns empty dict when response json is None."""
        async def mock_request_json_none(*args, **kwargs):
            resp = MagicMock()
            resp.json = AsyncMock(return_value=None)
            return resp

        account.http.request = mock_request_json_none

        result = await account.request("token/device/list")

        assert result == {}


class TestAccountAliyunChannel:
    """Tests for the lazily-opened Aliyun Channel wiring on Account."""

    @pytest.mark.parametrize(
        ("api_base", "expected"),
        [
            ("https://app-sgp.catlinks.cn/api/", "SG"),
            ("https://app-usa.catlinks.cn/api/", "US"),
            ("https://app.catlinks.cn/api/", "CN"),
        ],
    )
    def test_aliyun_country_from_api_base(
        self, hass, mock_http_session, api_base, expected
    ) -> None:
        """Test the country is derived from the CatLink Cloud host, as the app does."""
        account = Account(
            hass,
            {CONF_PHONE_IAC: "86", CONF_PHONE: "13812345678", CONF_API_BASE: api_base},
        )
        assert account._aliyun_country() == expected  # noqa: SLF001

    @pytest.mark.usefixtures("enable_custom_integrations")
    async def test_aliyun_channel_is_created_once(
        self, hass, account_config, mock_http_session
    ) -> None:
        """Test the channel is built lazily and the same instance is returned after."""
        from custom_components.catlink.modules.aliyun_channel import AliyunChannel

        account = Account(hass, {**account_config, **ALIYUN_CREDENTIALS})
        first = await account.async_aliyun_channel()
        second = await account.async_aliyun_channel()

        assert isinstance(first, AliyunChannel)
        assert first is second

    @pytest.mark.usefixtures("enable_custom_integrations")
    async def test_aliyun_channel_takes_the_entry_credentials(
        self, hass, account_config, mock_http_session
    ) -> None:
        """Test the channel is built with the Aliyun credentials the entry holds."""
        account = Account(hass, {**account_config, **ALIYUN_CREDENTIALS})

        with patch(
            "custom_components.catlink.modules.account.AliyunChannel"
        ) as channel_cls:
            await account.async_aliyun_channel()

        assert channel_cls.call_args.kwargs == {
            "app_key": "test-aliyun-key",
            "app_secret": "test-aliyun-secret",
        }

    @pytest.mark.usefixtures("enable_custom_integrations")
    @pytest.mark.parametrize(
        "missing", [CONF_ALIYUN_CLOUD_KEY, CONF_ALIYUN_CLOUD_SECRET]
    )
    async def test_aliyun_channel_without_credentials_fails_auth(
        self, hass, account_config, mock_http_session, missing
    ) -> None:
        """Test a missing Aliyun credential fails auth instead of opening a channel."""
        from custom_components.catlink.modules.aliyun_channel import AliyunAuthFailed

        config = {**account_config, **ALIYUN_CREDENTIALS}
        del config[missing]
        account = Account(hass, config)

        with pytest.raises(AliyunAuthFailed, match="not configured"):
            await account.async_aliyun_channel()
        assert account._aliyun is None  # noqa: SLF001

    @pytest.mark.usefixtures("enable_custom_integrations")
    async def test_auth_code_request_returns_data_string(self, account) -> None:
        """Test the auth-code request posts the aliLive endpoint and returns data."""
        account.request = AsyncMock(return_value={"data": "AUTHCODE1"})

        code = await account._async_aliyun_auth_code()  # noqa: SLF001

        assert code == "AUTHCODE1"
        call_args = account.request.call_args
        assert call_args[0][0] == "token/user/auth/aliLive/authCode"
        assert call_args[0][2] == "POST"

    @pytest.mark.usefixtures("enable_custom_integrations")
    async def test_auth_code_request_returns_empty_on_failure(self, account) -> None:
        """Test the auth-code request returns an empty string when the cloud has none."""
        account.request = AsyncMock(return_value={"returnCode": 1002})

        code = await account._async_aliyun_auth_code()  # noqa: SLF001

        assert code == ""

    @pytest.mark.usefixtures("enable_custom_integrations")
    async def test_auth_code_response_is_not_logged_in_clear(
        self, account, caplog
    ) -> None:
        """Test the auth-code value is kept out of the shared debug line."""

        async def fake_http_request(method, url, **kws):
            assert "_redact_response" not in kws
            resp = MagicMock()
            resp.json = AsyncMock(return_value={"data": "SECRETAUTHCODE"})
            return resp

        account.http.request = fake_http_request

        with caplog.at_level(logging.DEBUG, logger="custom_components.catlink.const"):
            code = await account._async_aliyun_auth_code()  # noqa: SLF001

        assert code == "SECRETAUTHCODE"
        assert "SECRETAUTHCODE" not in caplog.text
        assert "<redacted>" in caplog.text

    @pytest.mark.usefixtures("enable_custom_integrations")
    async def test_aliyun_credential_check_opens_a_throwaway_session(
        self, hass, account_config, mock_http_session
    ) -> None:
        """Test the check opens and drops its own channel with the given credentials."""
        account = Account(hass, {**account_config, **ALIYUN_CREDENTIALS})
        account.request = AsyncMock(return_value={"data": "AUTHCODE1"})

        with patch(
            "custom_components.catlink.modules.account.AliyunChannel"
        ) as channel_cls:
            channel = channel_cls.return_value
            channel.async_open = AsyncMock()
            channel.async_close = AsyncMock()
            await account.async_check_aliyun_credentials(
                "test-new-key", "test-new-secret"
            )

        args = channel_cls.call_args
        assert args.args[2] == "CN"
        assert await args.args[3]() == "AUTHCODE1"
        assert args.kwargs == {
            "app_key": "test-new-key",
            "app_secret": "test-new-secret",
        }
        channel.async_open.assert_awaited_once()
        channel.async_close.assert_awaited_once()
        assert account._aliyun is None  # noqa: SLF001

    @pytest.mark.usefixtures("enable_custom_integrations")
    async def test_aliyun_credential_check_drops_a_rejected_session(
        self, account
    ) -> None:
        """Test rejected credentials propagate and the throwaway session is still dropped."""
        from custom_components.catlink.modules.aliyun_channel import AliyunAuthFailed

        account.request = AsyncMock(return_value={"data": "AUTHCODE1"})

        with patch(
            "custom_components.catlink.modules.account.AliyunChannel"
        ) as channel_cls:
            channel = channel_cls.return_value
            channel.async_open = AsyncMock(side_effect=AliyunAuthFailed("rejected"))
            channel.async_close = AsyncMock()
            with pytest.raises(AliyunAuthFailed):
                await account.async_check_aliyun_credentials(
                    "test-new-key", "test-wrong-secret"
                )

        channel.async_close.assert_awaited_once()

    @pytest.mark.usefixtures("enable_custom_integrations")
    async def test_aliyun_credential_check_without_auth_code_fails_the_request(
        self, account
    ) -> None:
        """Test a CatLink Cloud with no auth code is not taken for rejected credentials."""
        from custom_components.catlink.modules.aliyun_channel import (
            AliyunRequestFailed,
        )

        account.request = AsyncMock(return_value={"returnCode": 1002})

        with patch(
            "custom_components.catlink.modules.account.AliyunChannel"
        ) as channel_cls:
            with pytest.raises(AliyunRequestFailed, match="no authorisation code"):
                await account.async_check_aliyun_credentials(
                    "test-new-key", "test-new-secret"
                )

        channel_cls.assert_not_called()


class TestAccountEvictionRecovery:
    """Tests for the single-place Eviction recovery in request()."""

    @pytest.mark.usefixtures("enable_custom_integrations")
    async def test_request_succeeds_after_one_reclaim(self, account) -> None:
        """Test a 1002 reclaims the Session once and the request then succeeds."""
        responses = [
            {"returnCode": 1002, "msg": "Illegal token"},
            {"returnCode": 0, "data": {"ok": 1}},
        ]

        async def mock_request(method, url, **kwargs):
            assert "_retried" not in kwargs
            resp = MagicMock()
            resp.json = AsyncMock(return_value=responses.pop(0))
            return resp

        account.http.request = mock_request

        with patch.object(
            account, "async_login", new_callable=AsyncMock, return_value=True
        ) as mock_login:
            result = await account.request(
                "token/device/union/list/sorted", {"type": "NONE"}
            )

        assert result == {"returnCode": 0, "data": {"ok": 1}}
        mock_login.assert_called_once()

    @pytest.mark.usefixtures("enable_custom_integrations")
    async def test_retry_after_eviction_carries_fresh_signature_and_token(
        self, account
    ) -> None:
        """Test the retried request re-signs clean params and carries the new token."""
        captured: list[dict] = []
        responses = [
            {"returnCode": 1002, "msg": "Illegal token"},
            {"returnCode": 0, "data": {"ok": 1}},
        ]

        async def mock_request(method, url, **kwargs):
            assert "_retried" not in kwargs
            pms = kwargs.get("params") or kwargs.get("data") or {}
            captured.append(dict(pms))
            resp = MagicMock()
            resp.json = AsyncMock(return_value=responses.pop(0))
            return resp

        account.http.request = mock_request

        async def reclaim() -> bool:
            account._config[CONF_TOKEN] = "fresh-token"
            return True

        with patch.object(account, "async_login", side_effect=reclaim):
            result = await account.request(
                "token/device/union/list/sorted", {"type": "NONE"}
            )

        assert result == {"returnCode": 0, "data": {"ok": 1}}
        assert len(captured) == 2
        retried = captured[1]
        assert retried[CONF_TOKEN] == "fresh-token"
        signed_params = {k: v for k, v in retried.items() if k != "sign"}
        assert retried["sign"] == Account.params_sign(signed_params)
        assert retried["sign"] != captured[0]["sign"]

    @pytest.mark.usefixtures("enable_custom_integrations")
    async def test_get_devices_recovers_through_the_single_retry(
        self, account
    ) -> None:
        """Test get_devices leans on request()'s single retry: one reclaim only."""
        responses = [
            {"returnCode": 1002, "msg": "Illegal token"},
            {"returnCode": 0, "data": {CONF_DEVICES: [{"id": "d1"}]}},
        ]

        async def mock_request(method, url, **kwargs):
            resp = MagicMock()
            resp.json = AsyncMock(return_value=responses.pop(0))
            return resp

        account.http.request = mock_request

        with patch.object(
            account, "async_login", new_callable=AsyncMock, return_value=True
        ) as mock_login:
            devices = await account.get_devices()

        assert devices == [{"id": "d1"}]
        mock_login.assert_called_once()
        assert responses == []
