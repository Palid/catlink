"""Tests for CatLink helper functions."""

from datetime import timedelta
import logging
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import urlparse

import pytest

from custom_components.catlink.const import DOMAIN
from custom_components.catlink.helpers import (
    Helper,
    RegionDiscovery,
    async_setup_domain_platform,
    discover_region,
    format_api_error,
    parse_phone_number,
)


class TestParsePhoneNumber:
    """Tests for parse_phone_number."""

    def test_international_format_with_plus(self) -> None:
        """Test parsing international format with plus."""
        assert parse_phone_number("+447911123456") == ("44", "7911123456")

    def test_international_format_without_plus(self) -> None:
        """Test parsing international format without plus."""
        assert parse_phone_number("447911123456") == ("44", "7911123456")

    def test_us_number(self) -> None:
        """Test parsing US number."""
        assert parse_phone_number("+12025551234") == ("1", "2025551234")

    def test_china_number(self) -> None:
        """Test parsing China number."""
        assert parse_phone_number("+8613812345678") == ("86", "13812345678")

    def test_with_spaces_and_dashes(self) -> None:
        """Test parsing number with formatting."""
        assert parse_phone_number("+44 7911-123-456") == ("44", "7911123456")

    def test_singapore_number(self) -> None:
        """Test parsing Singapore number."""
        assert parse_phone_number("+6591234567") == ("65", "91234567")


class TestFormatApiError:
    """Tests for format_api_error."""

    def test_with_msg_and_code(self) -> None:
        """Test formatting with msg and returnCode."""
        rdt = {
            "returnCode": 4007,
            "msg": "Protection is temporarily paused.",
            "success": False,
        }
        assert (
            format_api_error(rdt)
            == "Protection is temporarily paused. (returnCode: 4007)"
        )

    def test_with_msg_only(self) -> None:
        """Test formatting with msg only."""
        rdt = {"msg": "Device offline"}
        assert format_api_error(rdt) == "Device offline"

    def test_with_message_alias(self) -> None:
        """Test formatting with message key."""
        rdt = {"message": "Custom error", "returnCode": 500}
        assert format_api_error(rdt) == "Custom error (returnCode: 500)"

    def test_without_msg(self) -> None:
        """Test formatting when msg is missing."""
        rdt = {"returnCode": 4007, "data": {}}
        assert "4007" in format_api_error(rdt)


class TestCalculateUpdateInterval:
    """Tests for Helper.calculate_update_interval."""

    def test_timedelta_passthrough(self) -> None:
        """Test timedelta is returned as-is."""
        interval = timedelta(minutes=5)
        assert Helper.calculate_update_interval(interval) == interval

    def test_seconds_int(self) -> None:
        """Test seconds as int."""
        assert Helper.calculate_update_interval(60) == timedelta(seconds=60)

    def test_seconds_float(self) -> None:
        """Test seconds as float."""
        assert Helper.calculate_update_interval(90.5) == timedelta(seconds=90)

    def test_hhmmss_string(self) -> None:
        """Test HH:MM:SS format."""
        assert Helper.calculate_update_interval("01:30:00") == timedelta(
            hours=1, minutes=30
        )

    def test_invalid_string_defaults_to_one_minute(self) -> None:
        """Test invalid string returns 1 minute default."""
        assert Helper.calculate_update_interval("invalid") == timedelta(minutes=1)

    def test_none_defaults_to_one_minute(self) -> None:
        """Test None returns 1 minute default."""
        assert Helper.calculate_update_interval(None) == timedelta(minutes=1)

    def test_zero_or_negative_defaults_to_one_minute(self) -> None:
        """Test zero or negative returns 1 minute default."""
        assert Helper.calculate_update_interval(0) == timedelta(minutes=1)
        assert Helper.calculate_update_interval(-10) == timedelta(minutes=1)


SGP = "https://app-sgp.catlinks.cn/api/"
GLOBAL = "https://app.catlinks.cn/api/"
EU = "https://app-eu.catlinks.cn/api/"


class RegionFakeCloud:
    """A fake CatLink Cloud that serves the area table and signs in per host."""

    def __init__(self) -> None:
        """Initialise with an uninformative area table and no accepting hosts."""
        self.area_response: dict = {
            "returnCode": 0,
            "data": {"areaCode": "", "areaList": []},
        }
        self.accepting_hosts: set[str] = set()
        self.profiles: dict[str, dict] = {}
        self.login_hosts: list[str] = []
        self.area_calls = 0
        self._valid_tokens: dict[str, str] = {}

    async def request(self, method, url, **kwargs):
        """Answer a signed request, tracking sign-ins and area-table reads."""
        host = urlparse(url).netloc
        pms = kwargs.get("data") or kwargs.get("params") or {}
        token = pms.get("token")
        if url.endswith("common/areaServerList"):
            self.area_calls += 1
            body = self.area_response
        elif url.endswith("login/password") or url.endswith("login/email/v2"):
            self.login_hosts.append(host)
            if host in self.accepting_hosts:
                tok = f"tok-{host}"
                self._valid_tokens[tok] = host
                body = {"returnCode": 0, "data": {"token": tok}}
            else:
                body = {"returnCode": 2001, "msg": "account_error"}
        elif url.endswith("token/user/info"):
            if token in self._valid_tokens:
                app_user = {"id": "acc-1", "globalId": None}
                app_user.update(self.profiles.get(host, {}))
                body = {"returnCode": 0, "data": {"appUser": app_user}}
            else:
                body = {"returnCode": 1002, "msg": "Illegal token"}
        else:
            body = {"returnCode": 0, "data": {}}
        resp = MagicMock()
        resp.json = AsyncMock(return_value=body)
        return resp

    @property
    def login_count(self) -> int:
        """Return the number of sign-in attempts made."""
        return len(self.login_hosts)


def _area_table(area_code, rows):
    """Build an areaServerList response body from (twoCode, mobileCode, host) rows."""
    return {
        "returnCode": 0,
        "data": {
            "areaCode": area_code,
            "areaList": [
                {"twoCode": two, "mobileCode": iac, "serverRegion": host}
                for two, iac, host in rows
            ],
        },
    }


@pytest.fixture
def region_cloud():
    """Patch the account HTTP session onto a region-aware fake cloud."""
    fake = RegionFakeCloud()
    session = MagicMock()
    session.request = fake.request
    with patch(
        "custom_components.catlink.modules.account.aiohttp_client."
        "async_create_clientsession",
        return_value=session,
    ):
        yield fake


@pytest.mark.usefixtures("enable_custom_integrations", "hass_storage")
class TestDiscoverRegion:
    """Tests for discover_region against a fake cloud."""

    async def test_singapore_account_found_on_first_sign_in(
        self, hass, region_cloud
    ) -> None:
        """Test the detected country picks the host, found in one sign-in."""
        region_cloud.area_response = _area_table(
            "SG", [("SG", "65", "https://app-sgp.catlinks.cn")]
        )
        region_cloud.accepting_hosts = {"app-sgp.catlinks.cn"}

        result = await discover_region(hass, "65", "91234567", "secret")

        assert result.api_base == SGP
        assert result.account_id == "acc-1"
        assert region_cloud.login_hosts == ["app-sgp.catlinks.cn"]
        assert region_cloud.login_count == 1

    async def test_phone_country_code_drives_candidate(
        self, hass, region_cloud
    ) -> None:
        """Test the Phone's country code picks the host when no country is detected."""
        region_cloud.area_response = _area_table(
            "", [("SG", "65", "https://app-sgp.catlinks.cn")]
        )
        region_cloud.accepting_hosts = {"app-sgp.catlinks.cn"}

        result = await discover_region(hass, "65", "91234567", "secret")

        assert result.api_base == SGP
        assert region_cloud.login_hosts == ["app-sgp.catlinks.cn"]

    async def test_detected_country_wins_over_ambiguous_mobile_code(
        self, hass, region_cloud
    ) -> None:
        """Test an ambiguous mobileCode is not resolved when a country is detected."""
        region_cloud.area_response = _area_table(
            "CA",
            [
                ("US", "1", "https://app-usa.catlinks.cn"),
                ("CA", "1", "https://app.catlinks.cn"),
            ],
        )
        region_cloud.accepting_hosts = {"app.catlinks.cn"}

        result = await discover_region(hass, "1", "2025551234", "secret")

        assert result.api_base == GLOBAL
        assert region_cloud.login_hosts[0] == "app.catlinks.cn"
        assert "app-usa.catlinks.cn" not in region_cloud.login_hosts

    async def test_email_path_discovers_region(self, hass, region_cloud) -> None:
        """Test region discovery applies to the Email sign-in path too."""
        region_cloud.area_response = _area_table(
            "SG", [("SG", "65", "https://app-sgp.catlinks.cn")]
        )
        region_cloud.accepting_hosts = {"app-sgp.catlinks.cn"}

        result = await discover_region(
            hass, password="secret", email="cat@example.com"
        )

        assert result.api_base == SGP
        assert result.account_id == "acc-1"
        assert region_cloud.login_count == 1

    async def test_wrong_password_runs_full_fallback(
        self, hass, region_cloud
    ) -> None:
        """Test a wrong password walks the fallback and reports no region."""
        result = await discover_region(hass, "86", "13812345678", "wrongpass")

        assert result.api_base is None
        assert result.account_id is None
        assert region_cloud.login_count <= 4

    @pytest.mark.xfail(
        reason=(
            "Unknown (brief §9.2): a wrong-region sign-in is not yet distinguishable "
            "from a wrong password, so discovery cannot safely stop after the first "
            "failed attempt until a second-region Account confirms the returnCode"
        ),
        strict=False,
    )
    async def test_wrong_password_stops_after_one_attempt(
        self, hass, region_cloud
    ) -> None:
        """Test discovery stops after one failed attempt once the signal is known."""
        await discover_region(hass, "86", "13812345678", "wrongpass")

        assert region_cloud.login_count == 1

    async def test_reauth_signs_in_once_on_stored_base(
        self, hass, region_cloud
    ) -> None:
        """Test a stored base signs in once, skipping the area lookup and fallback."""
        region_cloud.accepting_hosts = {"app-sgp.catlinks.cn"}

        result = await discover_region(
            hass, "65", "91234567", "secret", api_base=SGP
        )

        assert result.api_base == SGP
        assert region_cloud.login_hosts == ["app-sgp.catlinks.cn"]
        assert region_cloud.login_count == 1
        assert region_cloud.area_calls == 0

    async def test_reauth_failure_rediscovers(self, hass, region_cloud) -> None:
        """Test a stored base that fails falls through to a full rediscovery."""
        region_cloud.area_response = _area_table(
            "NO", [("NO", "47", "https://app.catlinks.cn")]
        )
        region_cloud.accepting_hosts = {"app.catlinks.cn"}

        result = await discover_region(
            hass, "47", "40012345", "secret", api_base=SGP
        )

        assert result.api_base == GLOBAL
        assert region_cloud.area_calls == 1
        assert region_cloud.login_hosts[0] == "app-sgp.catlinks.cn"
        assert "app.catlinks.cn" in region_cloud.login_hosts

    async def test_full_fallback_when_lookup_uninformative(
        self, hass, region_cloud
    ) -> None:
        """Test an empty area table falls back through the four known servers in order."""
        region_cloud.accepting_hosts = {"app-sgp.catlinks.cn"}

        result = await discover_region(hass, "86", "13812345678", "secret")

        assert result.api_base == SGP
        assert region_cloud.login_hosts == [
            "app.catlinks.cn",
            "app-sh.catlinks.cn",
            "app-usa.catlinks.cn",
            "app-sgp.catlinks.cn",
        ]

    async def test_full_fallback_when_lookup_errors(
        self, hass, region_cloud
    ) -> None:
        """Test an area table that returns an error code still falls back."""
        region_cloud.area_response = {"returnCode": 500, "msg": "boom"}
        region_cloud.accepting_hosts = {"app.catlinks.cn"}

        result = await discover_region(hass, "86", "13812345678", "secret")

        assert result.api_base == GLOBAL
        assert region_cloud.login_hosts[0] == "app.catlinks.cn"

    async def test_mismatched_client_server_warns_but_keeps_host(
        self, hass, region_cloud, caplog
    ) -> None:
        """Test the owner's clientServer 1 warns but does not move the working host."""
        region_cloud.area_response = _area_table(
            "NO", [("NO", "47", "https://app.catlinks.cn")]
        )
        region_cloud.accepting_hosts = {"app.catlinks.cn"}
        region_cloud.profiles = {
            "app.catlinks.cn": {"clientServer": 1, "countryCode": "NO"}
        }

        with caplog.at_level(logging.WARNING):
            result = await discover_region(hass, "47", "40012345", "secret")

        assert result.api_base == GLOBAL
        assert result.client_server == "1"
        assert result.country_code == "NO"
        assert "clientServer" in caplog.text

    async def test_misspelled_clent_server_is_ignored(
        self, hass, region_cloud
    ) -> None:
        """Test only clientServer is read; the misspelt clentServer is ignored."""
        region_cloud.area_response = _area_table(
            "NO", [("NO", "47", "https://app.catlinks.cn")]
        )
        region_cloud.accepting_hosts = {"app.catlinks.cn"}
        region_cloud.profiles = {
            "app.catlinks.cn": {"clientServer": "3", "clentServer": "0"}
        }

        result = await discover_region(hass, "47", "40012345", "secret")

        assert result.client_server == "3"

    async def test_unknown_host_is_used_verbatim(
        self, hass, region_cloud
    ) -> None:
        """Test a host the table alone knows is used directly rather than a fallback."""
        region_cloud.area_response = _area_table(
            "EU", [("EU", "999", "https://app-eu.catlinks.cn")]
        )
        region_cloud.accepting_hosts = {"app-eu.catlinks.cn"}

        result = await discover_region(hass, "999", "12345", "secret")

        assert result.api_base == EU
        assert region_cloud.login_hosts[0] == "app-eu.catlinks.cn"
        assert isinstance(result, RegionDiscovery)


class TestAsyncSetupDomainPlatform:
    """Tests for async_setup_domain_platform."""

    @pytest.mark.usefixtures("enable_custom_integrations")
    async def test_async_setup_domain_platform_registers_add_entities(
        self, hass
    ) -> None:
        """Test async_setup_domain_platform registers add_entities."""
        hass.data[DOMAIN] = {
            "add_entities": {},
            "coordinators": {},
        }
        add_entities = MagicMock()

        with patch(
            "custom_components.catlink.helpers.Helper.async_setup_accounts",
            new_callable=AsyncMock,
        ) as mock_setup:
            await async_setup_domain_platform(hass, "sensor", add_entities)

            assert "discovery" in hass.data[DOMAIN]["add_entities"]
            assert (
                hass.data[DOMAIN]["add_entities"]["discovery"]["sensor"] == add_entities
            )
            mock_setup.assert_called_once_with(hass, "sensor")

    @pytest.mark.usefixtures("enable_custom_integrations")
    async def test_async_setup_domain_platform_calls_extra_setup(self, hass) -> None:
        """Test async_setup_domain_platform calls extra_setup when provided."""
        hass.data[DOMAIN] = {
            "add_entities": {},
            "coordinators": {},
        }
        extra_setup = AsyncMock()

        with patch(
            "custom_components.catlink.helpers.Helper.async_setup_accounts",
            new_callable=AsyncMock,
        ):
            await async_setup_domain_platform(
                hass, "sensor", MagicMock(), extra_setup=extra_setup
            )

            extra_setup.assert_called_once()
