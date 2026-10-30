"""Device-agnostic Aliyun Channel client for CatLink camera-generation devices.

The flow mirrors PyMammotion's production sequence: a CatLink Cloud authorisation
code opens an Aliyun IoT session (region lookup, OpenAccount sign-in, then
createSessionByAuthCode with the OpenAccount session id), which is refreshed
ahead of expiry and used to read and write a device's Thing Properties. The
request signature is written here rather than pulled from
``alibabacloud-iot-api-gateway`` so the integration stays dependency-free.
"""

from __future__ import annotations

import asyncio
import base64
from collections.abc import Awaitable, Callable
from email.utils import formatdate
import hashlib
import hmac
import json
import time
from typing import Any
from urllib.parse import urlencode
import uuid

import aiohttp
from homeassistant.core import HomeAssistant

from ..const import _LOGGER, ALIYUN_DOMAIN

REFRESH_MARGIN = 3600

_REGION_PATH = "/living/account/region/get"
_OA_LOGIN_PATH = "/api/prd/loginbyoauth.json"
_CREATE_SESSION_PATH = "/account/createSessionByAuthCode"
_REFRESH_SESSION_PATH = "/account/checkOrRefreshSession"
_PROPERTIES_GET_PATH = "/thing/properties/get"
_PROPERTIES_SET_PATH = "/living/thing/properties/set"

_SESSION_EXPIRED_CODES = frozenset({2401, 29003, 460})

_OA_PLATFORM_THIRD_AUTHCODE = 23
_OA_SUCCESS_CODES = frozenset({1, 100})
# The fixed entries of the app's riskControlInfo; its device-specific entries
# (umidToken, utdid, deviceId, model, ...) have no Home Assistant counterpart.
_OA_RISK_CONTROL_INFO = {
    "platformName": "android",
    "USE_OA_PWD_ENCRYPT": "true",
    "USE_H5_NC": "true",
    "signType": "RSA",
}

# Every field createSessionByAuthCode / checkOrRefreshSession must return on a 200.
_SESSION_FIELDS = (
    "identityId",
    "iotToken",
    "iotTokenExpire",
    "refreshToken",
    "refreshTokenExpire",
)

# Headers the gateway signature drops before building its canonical header block.
_MOVE_HEADERS = frozenset(
    {
        "x-ca-signature",
        "x-ca-signature-headers",
        "accept",
        "content-md5",
        "content-type",
        "date",
        "host",
        "token",
        "user-agent",
    }
)


class AliyunError(Exception):
    """Base class for Aliyun Channel errors."""


class AliyunRequestFailed(AliyunError):
    """A request failed for a reason the caller should treat as transient."""

    def __init__(self, message: str, code: int | None = None) -> None:
        """Store the human message and the Aliyun response code."""
        super().__init__(message)
        self.message = message
        self.code = code


class AliyunSessionExpired(AliyunRequestFailed):
    """The IoT session was rejected and must be refreshed or reopened."""


class AliyunAuthFailed(AliyunError):
    """The channel cannot authorise: no credentials, or no usable authorisation code."""


class AliyunChannel:
    """Opens and maintains one Aliyun IoT session for an Account."""

    def __init__(
        self,
        hass: HomeAssistant | None,
        session: aiohttp.ClientSession,
        country: str,
        request_auth_code: Callable[[], Awaitable[str]],
        *,
        app_key: str,
        app_secret: str,
    ) -> None:
        """Initialize the channel; nothing is requested until it is opened."""
        self.hass = hass
        self._session = session
        self._country = country
        self._request_auth_code = request_auth_code
        self._app_key = app_key
        self._app_secret = app_secret
        self._refresh_lock = asyncio.Lock()
        self._endpoint = ALIYUN_DOMAIN
        self._iot_token = ""
        self._identity_id = ""
        self._refresh_token = ""
        self._iot_token_expire = 0
        self._refresh_token_expire = 0
        self._issued_at = 0

    async def async_open(self) -> None:
        """Open a fresh session from a new authorisation code."""
        async with self._refresh_lock:
            await self._async_open_locked()

    async def async_get_properties(self, iot_id: str) -> dict[str, Any]:
        """Return the device's Thing Properties, flattened to {identifier: value}."""
        data = await self._async_request(
            _PROPERTIES_GET_PATH, "1.0.0", {"iotId": iot_id}, authed=True
        )
        return {
            key: value.get("value") if isinstance(value, dict) else value
            for key, value in data.items()
        }

    async def async_set_properties(self, iot_id: str, items: dict[str, Any]) -> None:
        """Write one or more Thing Properties on the device."""
        await self._async_request(
            _PROPERTIES_SET_PATH,
            "1.0.0",
            {"iotId": iot_id, "items": items},
            authed=True,
        )

    async def async_close(self) -> None:
        """Drop the local session so the next call opens a fresh one."""
        self._iot_token = ""
        self._identity_id = ""
        self._refresh_token = ""
        self._iot_token_expire = 0
        self._refresh_token_expire = 0
        self._issued_at = 0

    async def _async_request(
        self, path: str, api_ver: str, params: dict, *, authed: bool
    ) -> dict:
        """Ensure freshness, send a signed request and classify the response."""
        await self._async_ensure_fresh()
        payload = await self._async_send(
            self._endpoint, path, api_ver, params, authed=authed
        )
        code = payload.get("code")
        if code == 200:
            return payload.get("data") or {}
        if code in _SESSION_EXPIRED_CODES:
            raise AliyunSessionExpired(payload.get("message") or "session expired", code)
        raise AliyunRequestFailed(payload.get("message") or "request failed", code)

    async def _async_ensure_fresh(self) -> None:
        """Refresh the IoT token once, ahead of expiry, under concurrency."""
        issued_at = self._issued_at
        async with self._refresh_lock:
            if self._issued_at != issued_at:
                return
            if self._issued_at + self._iot_token_expire > time.time() + REFRESH_MARGIN:
                return
            if self._issued_at + self._refresh_token_expire <= time.time():
                await self._async_open_locked()
                return
            try:
                await self._async_refresh_locked()
            except AliyunSessionExpired:
                await self._async_open_locked()

    async def _async_open_locked(self) -> None:
        """Fetch an authorisation code and exchange it for a new session."""
        try:
            auth_code = await self._request_auth_code()
        except Exception as exc:  # noqa: BLE001 - any failure means reauth
            raise AliyunAuthFailed(f"authorisation code request failed: {exc}") from exc
        if not auth_code:
            raise AliyunAuthFailed("CatLink Cloud returned no authorisation code")
        self._endpoint, oa_endpoint = await self._async_region(auth_code)
        session_id = await self._async_oa_login(oa_endpoint, auth_code)
        await self._async_create_session(session_id)

    async def _async_region(self, auth_code: str) -> tuple[str, str]:
        """Look up the account's API gateway and OpenAccount endpoints."""
        payload = await self._async_send(
            ALIYUN_DOMAIN,
            _REGION_PATH,
            "1.0.2",
            {
                "authCode": auth_code,
                "type": "THIRD_AUTHCODE",
                "countryCode": self._country,
            },
            authed=False,
        )
        if payload.get("code") != 200:
            raise AliyunRequestFailed(
                payload.get("message") or "region lookup failed", payload.get("code")
            )
        data = payload.get("data") or {}
        endpoint = data.get("apiGatewayEndpoint")
        oa_endpoint = data.get("oaApiGatewayEndpoint")
        if not endpoint or not oa_endpoint:
            raise AliyunRequestFailed(
                "region lookup returned no gateway or OpenAccount endpoint"
            )
        return endpoint, oa_endpoint

    async def _async_oa_login(self, host: str, auth_code: str) -> str:
        """Sign in to OpenAccount with the authorisation code; return its session id."""
        form = {
            "loginByOauthRequest": json.dumps(
                {
                    "oauthPlateform": _OA_PLATFORM_THIRD_AUTHCODE,
                    "oauthAppKey": self._app_key,
                    "authCode": auth_code,
                    "country": self._country,
                    "riskControlInfo": _OA_RISK_CONTROL_INFO,
                },
                separators=(",", ":"),
            )
        }
        headers = self._build_oa_headers(host, _OA_LOGIN_PATH, form)
        payload = await self._async_post(host, _OA_LOGIN_PATH, urlencode(form), headers)
        result = payload.get("data") or {}
        login = (result.get("data") or {}).get("loginSuccessResult") or {}
        if result.get("code") not in _OA_SUCCESS_CODES or not login.get("sid"):
            raise AliyunAuthFailed(
                result.get("message") or "OpenAccount loginbyoauth rejected"
            )
        return login["sid"]

    async def _async_create_session(self, session_id: str) -> None:
        """Exchange the OpenAccount session id for an IoT session."""
        payload = await self._async_send(
            self._endpoint,
            _CREATE_SESSION_PATH,
            "1.0.4",
            {
                "request": {
                    "authCode": session_id,
                    "accountType": "OA_SESSION",
                    "appKey": self._app_key,
                }
            },
            authed=False,
        )
        data = payload.get("data") or {}
        if payload.get("code") != 200 or not data.get("identityId"):
            raise AliyunAuthFailed(
                payload.get("message") or "createSessionByAuthCode rejected"
            )
        if not self._has_session_fields(data):
            raise AliyunRequestFailed(
                "createSessionByAuthCode returned an incomplete session"
            )
        self._apply_session(data)

    async def _async_refresh_locked(self) -> None:
        """Refresh the IoT token with the refresh token and identity."""
        payload = await self._async_send(
            self._endpoint,
            _REFRESH_SESSION_PATH,
            "1.0.4",
            {
                "request": {
                    "refreshToken": self._refresh_token,
                    "identityId": self._identity_id,
                }
            },
            authed=False,
        )
        if payload.get("code") != 200:
            raise AliyunSessionExpired(
                payload.get("message") or "refresh rejected", payload.get("code")
            )
        data = payload.get("data") or {}
        if not self._has_session_fields(data):
            raise AliyunRequestFailed("refresh returned an incomplete session")
        self._apply_session(data)

    @staticmethod
    def _has_session_fields(data: dict) -> bool:
        """Return whether a 200 carries every field a session needs."""
        return all(data.get(field) not in (None, "") for field in _SESSION_FIELDS)

    def _apply_session(self, data: dict) -> None:
        """Store a session bundle and stamp its local issue time."""
        self._identity_id = data["identityId"]
        self._iot_token = data["iotToken"]
        self._refresh_token = data["refreshToken"]
        self._iot_token_expire = int(data["iotTokenExpire"])
        self._refresh_token_expire = int(data["refreshTokenExpire"])
        self._issued_at = int(time.time())

    async def _async_send(
        self, host: str, path: str, api_ver: str, params: dict, *, authed: bool
    ) -> dict:
        """Sign and send one gateway request, returning its parsed payload."""
        request: dict[str, Any] = {"apiVer": api_ver, "language": "en-US"}
        if authed:
            request["iotToken"] = self._iot_token
        body = json.dumps(
            {
                "id": uuid.uuid4().hex,
                "version": "1.0",
                "request": request,
                "params": params,
            }
        )
        headers = self._build_headers(host, path, body)
        return await self._async_post(host, path, body, headers)

    async def _async_post(
        self, host: str, path: str, body: str, headers: dict[str, str]
    ) -> dict:
        """Post one signed body, returning its parsed payload."""
        url = f"https://{host}{path}"
        try:
            resp = await self._session.post(url, data=body, headers=headers)
            payload = await resp.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError, ValueError) as exc:
            raise AliyunRequestFailed(f"transport error: {exc}") from exc
        payload = payload or {}
        _LOGGER.debug(
            "Aliyun %s response code: %s",
            path,
            payload.get("code") if isinstance(payload, dict) else None,
        )
        return payload

    def _build_headers(self, host: str, path: str, body: str) -> dict[str, str]:
        """Build the fixed gateway headers and append the signature."""
        content_md5 = (
            base64.b64encode(hashlib.md5(body.encode()).digest()).decode()
            if body
            else ""
        )
        headers = {
            "host": host,
            "date": formatdate(usegmt=True),
            "x-ca-nonce": uuid.uuid4().hex,
            "x-ca-key": self._app_key,
            "x-ca-signaturemethod": "HmacSHA256",
            "accept": "application/json",
            "user-agent": "HomeAssistant-CatLink",
            "content-type": "application/octet-stream",
            "content-md5": content_md5,
        }
        signed = sorted(k for k in headers if k not in _MOVE_HEADERS)
        headers["x-ca-signature"] = self._sign(
            "POST", path, headers, signed, hashlib.sha256
        )
        headers["x-ca-signature-headers"] = ",".join(signed)
        return headers

    def _build_oa_headers(
        self, host: str, path: str, form: dict[str, str]
    ) -> dict[str, str]:
        """Build the OpenAccount gateway headers and append its HmacSHA1 signature."""
        headers = {
            "host": host,
            "date": formatdate(usegmt=True),
            "x-ca-timestamp": str(int(time.time() * 1000)),
            "x-ca-nonce": str(uuid.uuid4()),
            "x-ca-key": self._app_key,
            "x-ca-signature-method": "HmacSHA1",
            "ca_version": "1",
            "accept": "application/json; charset=utf-8",
            "user-agent": "HomeAssistant-CatLink",
            "content-type": "application/x-www-form-urlencoded; charset=utf-8",
        }
        signed = sorted(k for k in headers if k.startswith("x-ca-"))
        resource = f"{path}?" + "&".join(f"{k}={v}" for k, v in sorted(form.items()))
        headers["x-ca-signature"] = self._sign(
            "POST", resource, headers, signed, hashlib.sha1
        )
        headers["x-ca-signature-headers"] = ",".join(signed)
        return headers

    def _sign(
        self,
        method: str,
        resource: str,
        headers: dict[str, str],
        signed: list[str],
        digestmod: Callable[..., Any],
    ) -> str:
        """Return the base64 HMAC gateway signature over the signed headers."""
        header_block = "\n".join(f"{k}:{headers[k]}" for k in signed)
        string_to_sign = "\n".join(
            [
                method,
                headers.get("accept", ""),
                headers.get("content-md5", ""),
                headers.get("content-type", ""),
                headers.get("date", ""),
                header_block,
                resource,
            ]
        )
        digest = hmac.new(
            self._app_secret.encode(), string_to_sign.encode(), digestmod
        ).digest()
        return base64.b64encode(digest).decode()
