"""Tests for the Aliyun Channel client, entirely against a fake aiohttp session."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
from types import SimpleNamespace
from urllib.parse import parse_qsl, urlparse

from aiohttp import ClientError
from custom_components.catlink.modules.aliyun_channel import (
    AliyunAuthFailed,
    AliyunChannel,
    AliyunError,
    AliyunRequestFailed,
)
import pytest

REGION_PATH = "/living/account/region/get"
OA_LOGIN_PATH = "/api/prd/loginbyoauth.json"
CREATE_PATH = "/account/createSessionByAuthCode"
REFRESH_PATH = "/account/checkOrRefreshSession"
GET_PATH = "/thing/properties/get"
SET_PATH = "/living/thing/properties/set"


class FakeResponse:
    """Minimal stand-in for an aiohttp ClientResponse."""

    def __init__(self, payload) -> None:
        """Store the payload to return from json(), or an error to raise."""
        self._payload = payload
        self.status = 200

    async def json(self, **_kwargs) -> dict:
        """Return the recorded payload, or raise a configured decode error."""
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class FakeSession:
    """Records every request and answers from per-path handlers."""

    def __init__(self) -> None:
        """Initialize the request log and handler map."""
        self.requests: list[SimpleNamespace] = []
        self._handlers: dict[str, object] = {}
        self.error: Exception | None = None

    def route(self, path: str, handler) -> None:
        """Register a handler (payload dict or callable) for a path."""
        self._handlers[path] = handler

    def count(self, path: str) -> int:
        """Return how many requests hit a path."""
        return sum(1 for r in self.requests if r.path == path)

    def last(self, path: str) -> SimpleNamespace:
        """Return the most recent request for a path."""
        return next(r for r in reversed(self.requests) if r.path == path)

    async def post(self, url, data=None, headers=None):
        """Record the request and return the routed payload."""
        await asyncio.sleep(0)
        if self.error is not None:
            raise self.error
        path = urlparse(url).path
        if headers["content-type"].startswith("application/x-www-form-urlencoded"):
            body = {key: json.loads(value) for key, value in parse_qsl(data)}
        else:
            body = json.loads(data) if data else {}
        self.requests.append(
            SimpleNamespace(url=url, path=path, headers=headers, body=body, raw=data)
        )
        handler = self._handlers.get(path)
        payload = handler(body) if callable(handler) else handler
        return FakeResponse(payload or {})


def _session_data(iot_expire=86400, refresh_expire=864000, token="tok1", refresh="ref1"):
    """Return a createSession/refresh data body with the given lifetimes."""
    return {
        "code": 200,
        "data": {
            "identityId": "identity-1",
            "iotToken": token,
            "iotTokenExpire": iot_expire,
            "refreshToken": refresh,
            "refreshTokenExpire": refresh_expire,
        },
    }


def _region_ok():
    """Return a region/get success body."""
    return {
        "code": 200,
        "data": {
            "apiGatewayEndpoint": "gw.example.com",
            "oaApiGatewayEndpoint": "oa.example.com",
        },
    }


def _oa_login_ok():
    """Return an OpenAccount loginbyoauth success body."""
    return {
        "data": {
            "code": 1,
            "message": "success",
            "data": {"loginSuccessResult": {"sid": "OASID1", "sidExpireIn": 86400}},
        }
    }


def _oa_signature(request) -> str:
    """Recompute the OpenAccount HmacSHA1 signature over the x-ca headers and form."""
    headers = request.headers
    signed = headers["x-ca-signature-headers"].split(",")
    form = sorted(parse_qsl(request.raw))
    text = "\n".join(
        [
            "POST",
            headers["accept"],
            "",
            headers["content-type"],
            headers["date"],
            *(f"{key}:{headers[key]}" for key in signed),
            f"{request.path}?" + "&".join(f"{k}={v}" for k, v in form),
        ]
    )
    digest = hmac.new(b"test-secret", text.encode(), hashlib.sha1).digest()
    return base64.b64encode(digest).decode()


def make_channel(session, auth_code_fn, country="SG"):
    """Build a channel wired to the fake session with dummy credentials."""
    return AliyunChannel(
        None,
        session,
        country,
        auth_code_fn,
        app_key="test-key",
        app_secret="test-secret",
    )


async def _counting_auth_code(counter, code="AUTHCODE1"):
    """Return an auth-code callable that bumps a mutable counter."""

    async def _fn():
        counter.append(code)
        return code

    return _fn


async def test_opens_session_from_auth_code() -> None:
    """A session opens: region lookup, OpenAccount sign-in, then createSessionByAuthCode."""
    session = FakeSession()
    session.route(REGION_PATH, _region_ok())
    session.route(OA_LOGIN_PATH, _oa_login_ok())
    session.route(CREATE_PATH, _session_data())
    calls: list[str] = []

    async def auth_code():
        calls.append("code")
        return "AUTHCODE1"

    channel = make_channel(session, auth_code)
    await channel.async_open()

    assert calls == ["code"]
    assert [r.path for r in session.requests] == [
        REGION_PATH,
        OA_LOGIN_PATH,
        CREATE_PATH,
    ]
    region = session.last(REGION_PATH)
    assert region.body["params"] == {
        "authCode": "AUTHCODE1",
        "type": "THIRD_AUTHCODE",
        "countryCode": "SG",
    }
    login = session.last(OA_LOGIN_PATH)
    assert urlparse(login.url).hostname == "oa.example.com"
    assert login.body == {
        "loginByOauthRequest": {
            "oauthPlateform": 23,
            "oauthAppKey": "test-key",
            "authCode": "AUTHCODE1",
            "country": "SG",
            "riskControlInfo": {
                "platformName": "android",
                "USE_OA_PWD_ENCRYPT": "true",
                "USE_H5_NC": "true",
                "signType": "RSA",
            },
        }
    }
    assert login.headers["x-ca-key"] == "test-key"
    assert login.headers["x-ca-signature-method"] == "HmacSHA1"
    assert login.headers["x-ca-signature-headers"] == (
        "x-ca-key,x-ca-nonce,x-ca-signature-method,x-ca-timestamp"
    )
    assert login.headers["x-ca-signature"] == _oa_signature(login)
    create = session.last(CREATE_PATH)
    assert urlparse(create.url).hostname == "gw.example.com"
    assert create.body["request"]["apiVer"] == "1.0.4"
    assert create.body["params"]["request"] == {
        "authCode": "OASID1",
        "accountType": "OA_SESSION",
        "appKey": "test-key",
    }
    assert create.headers["x-ca-key"] == "test-key"


async def test_get_properties_for_iot_id() -> None:
    """Reading properties hits /thing/properties/get and flattens data.<id>.value."""
    session = FakeSession()
    session.route(REGION_PATH, _region_ok())
    session.route(OA_LOGIN_PATH, _oa_login_ok())
    session.route(CREATE_PATH, _session_data())
    session.route(
        GET_PATH,
        {
            "code": 200,
            "data": {
                "RunStatus": {"value": "runStatus=0&biz=0"},
                "CatLitterBalance": {"value": "55"},
            },
        },
    )
    channel = make_channel(session, (await _counting_auth_code([])))
    await channel.async_open()

    result = await channel.async_get_properties("IOTID26CHAR")

    assert result == {
        "RunStatus": "runStatus=0&biz=0",
        "CatLitterBalance": "55",
    }
    req = session.last(GET_PATH)
    assert req.body["request"]["apiVer"] == "1.0.0"
    assert req.body["request"]["iotToken"] == "tok1"
    assert req.body["params"] == {"iotId": "IOTID26CHAR"}
    expected_md5 = base64.b64encode(
        hashlib.md5(json.dumps(req.body).encode()).digest()
    ).decode()
    assert req.headers["content-md5"] == expected_md5


async def test_set_properties_for_iot_id() -> None:
    """Writing properties hits /living/thing/properties/set with iotId and items."""
    session = FakeSession()
    session.route(REGION_PATH, _region_ok())
    session.route(OA_LOGIN_PATH, _oa_login_ok())
    session.route(CREATE_PATH, _session_data())
    session.route(SET_PATH, {"code": 200, "data": {}})
    channel = make_channel(session, (await _counting_auth_code([])))
    await channel.async_open()

    await channel.async_set_properties(
        "IOTID26CHAR", {"RunStatus": "runStatus=1&biz=1"}
    )

    req = session.last(SET_PATH)
    assert req.body["request"]["apiVer"] == "1.0.0"
    assert req.body["params"] == {
        "iotId": "IOTID26CHAR",
        "items": {"RunStatus": "runStatus=1&biz=1"},
    }


async def test_refreshes_ahead_of_expiry() -> None:
    """A token inside the hour margin triggers exactly one checkOrRefreshSession."""
    session = FakeSession()
    session.route(REGION_PATH, _region_ok())
    session.route(OA_LOGIN_PATH, _oa_login_ok())
    session.route(CREATE_PATH, _session_data(iot_expire=100, refresh_expire=864000))
    session.route(REFRESH_PATH, _session_data(iot_expire=86400, token="tok2"))
    session.route(GET_PATH, {"code": 200, "data": {}})
    channel = make_channel(session, (await _counting_auth_code([])))
    await channel.async_open()

    await channel.async_get_properties("IOTID26CHAR")

    assert session.count(REFRESH_PATH) == 1
    assert session.count(CREATE_PATH) == 1
    assert session.last(GET_PATH).body["request"]["iotToken"] == "tok2"


async def test_refresh_happens_once_under_concurrency() -> None:
    """Ten concurrent reads refresh the session exactly once."""
    session = FakeSession()
    session.route(REGION_PATH, _region_ok())
    session.route(OA_LOGIN_PATH, _oa_login_ok())
    session.route(CREATE_PATH, _session_data(iot_expire=100, refresh_expire=864000))
    session.route(REFRESH_PATH, _session_data(iot_expire=86400, token="tok2"))
    session.route(GET_PATH, {"code": 200, "data": {}})
    channel = make_channel(session, (await _counting_auth_code([])))
    await channel.async_open()

    await asyncio.gather(
        *(channel.async_get_properties("IOTID26CHAR") for _ in range(10))
    )

    assert session.count(REFRESH_PATH) == 1
    assert session.count(GET_PATH) == 10


async def test_refresh_failure_falls_back_to_new_auth_code() -> None:
    """A rejected refresh (2401) falls back to a fresh auth code and createSession."""
    session = FakeSession()
    session.route(REGION_PATH, _region_ok())
    session.route(OA_LOGIN_PATH, _oa_login_ok())
    session.route(CREATE_PATH, _session_data(iot_expire=100, refresh_expire=864000))
    session.route(REFRESH_PATH, {"code": 2401, "message": "session expired"})
    session.route(GET_PATH, {"code": 200, "data": {}})
    calls: list[str] = []

    async def auth_code():
        calls.append("code")
        return "AUTHCODE1"

    channel = make_channel(session, auth_code)
    await channel.async_open()

    await channel.async_get_properties("IOTID26CHAR")

    assert len(calls) == 2
    assert session.count(OA_LOGIN_PATH) == 2
    assert session.count(CREATE_PATH) == 2
    assert session.count(REFRESH_PATH) == 1


async def test_total_failure_starts_reauth() -> None:
    """When the auth-code callable raises, AliyunAuthFailed propagates."""
    session = FakeSession()

    async def auth_code():
        raise RuntimeError("catlink cloud down")

    channel = make_channel(session, auth_code)

    with pytest.raises(AliyunAuthFailed):
        await channel.async_open()


async def test_transport_error_becomes_request_failed() -> None:
    """An aiohttp transport error surfaces as AliyunRequestFailed, never a raw client error."""
    session = FakeSession()
    session.route(REGION_PATH, _region_ok())
    session.route(OA_LOGIN_PATH, _oa_login_ok())
    session.route(CREATE_PATH, _session_data())
    channel = make_channel(session, (await _counting_auth_code([])))
    await channel.async_open()
    session.error = ClientError("boom")

    with pytest.raises(AliyunRequestFailed):
        await channel.async_get_properties("IOTID26CHAR")


async def test_create_session_failure_is_auth_failed() -> None:
    """A rejected createSessionByAuthCode raises AliyunAuthFailed for reauth."""
    session = FakeSession()
    session.route(REGION_PATH, _region_ok())
    session.route(OA_LOGIN_PATH, _oa_login_ok())
    session.route(CREATE_PATH, {"code": 29020, "message": "bad auth code"})
    channel = make_channel(session, (await _counting_auth_code([])))

    with pytest.raises(AliyunAuthFailed):
        await channel.async_open()


async def test_oa_login_failure_is_auth_failed() -> None:
    """A rejected OpenAccount sign-in raises AliyunAuthFailed and opens no session."""
    session = FakeSession()
    session.route(REGION_PATH, _region_ok())
    session.route(OA_LOGIN_PATH, {"data": {"code": 2, "message": "auth code invalid"}})
    channel = make_channel(session, (await _counting_auth_code([])))

    with pytest.raises(AliyunAuthFailed):
        await channel.async_open()
    assert session.count(CREATE_PATH) == 0


async def test_region_without_oa_endpoint_is_request_failed() -> None:
    """A region answer naming no OpenAccount endpoint stops before signing in."""
    session = FakeSession()
    session.route(
        REGION_PATH, {"code": 200, "data": {"apiGatewayEndpoint": "gw.example.com"}}
    )
    channel = make_channel(session, (await _counting_auth_code([])))

    with pytest.raises(AliyunRequestFailed):
        await channel.async_open()
    assert session.count(OA_LOGIN_PATH) == 0


async def test_non_json_body_becomes_request_failed() -> None:
    """A non-JSON body (a decode error) surfaces as AliyunRequestFailed, not ValueError."""
    session = FakeSession()
    session.route(REGION_PATH, _region_ok())
    session.route(OA_LOGIN_PATH, _oa_login_ok())
    session.route(CREATE_PATH, _session_data())
    session.route(GET_PATH, json.JSONDecodeError("Expecting value", "<html>", 0))
    channel = make_channel(session, (await _counting_auth_code([])))
    await channel.async_open()

    with pytest.raises(AliyunRequestFailed):
        await channel.async_get_properties("IOTID26CHAR")


async def test_incomplete_session_is_request_failed() -> None:
    """A 200 missing a session field raises an AliyunError, never a bare KeyError."""
    session = FakeSession()
    session.route(REGION_PATH, _region_ok())
    session.route(OA_LOGIN_PATH, _oa_login_ok())
    session.route(
        CREATE_PATH,
        {
            "code": 200,
            "data": {
                "identityId": "identity-1",
                "iotToken": "tok1",
                "iotTokenExpire": 86400,
            },
        },
    )
    channel = make_channel(session, (await _counting_auth_code([])))

    with pytest.raises(AliyunError):
        await channel.async_open()
