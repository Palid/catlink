"""Tests for the camera entity on a real entry against a fake CatLink Cloud and Aliyun."""

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

from custom_components.catlink.const import (
    CAMERA_CODEC_H264,
    CONF_ACCOUNT_ID,
    CONF_ALIYUN_CLOUD_KEY,
    CONF_ALIYUN_CLOUD_SECRET,
    CONF_API_BASE,
    CONF_CAMERA_CODEC,
    CONF_PHONE,
    CONF_PHONE_IAC,
    DOMAIN,
)
from custom_components.catlink.modules.live_view import session as live_view
from custom_components.catlink.modules.live_view.session import LiveSession
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.components.camera import (
    CameraEntityFeature,
    CameraState,
    StreamType,
    async_get_image,
    async_get_stream_source,
)
from homeassistant.components.camera.webrtc import (
    CameraWebRTCProvider,
    async_register_webrtc_provider,
)
from homeassistant.components.ffmpeg import get_ffmpeg_manager
from homeassistant.components.stream import Orientation
from homeassistant.const import CONF_PASSWORD, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_platform, entity_registry as er
from homeassistant.setup import async_setup_component

from .live_view_fakes import play, until

CAMERA_ENTITY_ID = "camera.visual_pro_ultra_eeff_camera"
SIGN_IN_ENTITY_ID = "switch.catlink_sign_in"
CREATE_STREAM = "custom_components.catlink.entities.camera.create_stream"
IDLE_TIMEOUT = 0.05
BACKOFF = 0.2
PRO_ULTRA = {
    "id": "pu-1",
    "deviceType": "VISUAL_PRO_ULTRA",
    "mac": "AABBCCDDEEFF",
    "model": "ScooperPROUltra",
    "deviceName": "PU",
}
SCOOPER = {
    "id": "sc-1",
    "deviceType": "SCOOPER",
    "mac": "112233445566",
    "deviceName": "S",
}


def _response(body: dict) -> MagicMock:
    """Wrap a JSON body as an aiohttp response."""
    resp = MagicMock()
    resp.json = AsyncMock(return_value=body)
    return resp


class FakeCloud:
    """A fake CatLink Cloud and Aliyun gateway counting sign-ins and stream/query."""

    def __init__(self) -> None:
        """Accept the stored token; report the camera switched on."""
        self.login_count = 0
        self.valid_tokens = {"tok-1"}
        self.sleep_mode = 0
        self.stream_queries = 0

    async def request(self, method, url, **kwargs):
        """Answer a CatLink Cloud request."""
        pms = kwargs.get("data") or kwargs.get("params") or {}
        if url.endswith("login/password"):
            self.login_count += 1
            self.valid_tokens.add("fresh-tok")
            body = {"returnCode": 0, "data": {"token": "fresh-tok"}}
        elif pms.get("token") not in self.valid_tokens:
            body = {"returnCode": 1002, "msg": "Illegal token"}
        elif url.endswith("token/user/info"):
            body = {"returnCode": 0, "data": {"appUser": {"id": "acc-1"}}}
        elif url.endswith("token/device/union/list/sorted"):
            body = {"returnCode": 0, "data": {"devices": [PRO_ULTRA, SCOOPER]}}
        elif url.endswith("visualScooper/briefInfo"):
            body = {"returnCode": 0, "data": {"deviceInfo": {"iotId": "iot-1"}}}
        elif url.endswith("aliLive/authCode"):
            body = {"returnCode": 0, "data": "auth-code"}
        else:
            body = {"returnCode": 0, "data": {}}
        return _response(body)

    async def post(self, url, **kwargs):
        """Answer an Aliyun gateway request; stream/query has no Signalling URL."""
        if url.endswith("/living/account/region/get"):
            body = {
                "code": 200,
                "data": {
                    "apiGatewayEndpoint": "gateway.invalid",
                    "oaApiGatewayEndpoint": "oa.invalid",
                },
            }
        elif url.endswith("/api/prd/loginbyoauth.json"):
            body = {"data": {"code": 1, "data": {"loginSuccessResult": {"sid": "s"}}}}
        elif url.endswith("/account/createSessionByAuthCode"):
            body = {
                "code": 200,
                "data": {
                    "identityId": "identity",
                    "iotToken": "iot-tok",
                    "iotTokenExpire": 72000,
                    "refreshToken": "refresh-tok",
                    "refreshTokenExpire": 720000,
                },
            }
        elif url.endswith("/thing/properties/get"):
            body = {"code": 200, "data": {"SleepMode": {"value": self.sleep_mode}}}
        elif url.endswith("/vision/customer/stream/query"):
            self.stream_queries += 1
            body = {"code": 200, "data": {}}
        else:
            body = {"code": 200, "data": {}}
        return _response(body)


@pytest.fixture
def fake_cloud():
    """Patch the Account's HTTP session onto a fake cloud and return it."""
    fake = FakeCloud()
    session = MagicMock(request=fake.request, post=fake.post)
    with patch(
        "custom_components.catlink.modules.account.aiohttp_client."
        "async_create_clientsession",
        return_value=session,
    ):
        yield fake


@pytest.fixture
async def setup_entry(
    hass: HomeAssistant,
    enable_custom_integrations,
    hass_storage,
    fake_cloud,
    socket_enabled,
):
    """Set up an entry signed in with a stored token; unload it afterwards."""
    hass_storage["catlink/auth-acc-1.json"] = {
        "version": 1,
        "data": {"phone": "13812345678", "token": "tok-1"},
    }
    entries: list[MockConfigEntry] = []

    async def _setup(**options) -> MockConfigEntry:
        """Set up the entry with these options."""
        entry = MockConfigEntry(
            domain=DOMAIN,
            data={
                CONF_PHONE_IAC: "86",
                CONF_PHONE: "13812345678",
                CONF_API_BASE: "https://app.catlinks.cn/api/",
                CONF_PASSWORD: "secret",
                CONF_ACCOUNT_ID: "acc-1",
                CONF_ALIYUN_CLOUD_KEY: "key",
                CONF_ALIYUN_CLOUD_SECRET: "secret",
            },
            options=options,
            unique_id="acc-1",
            entry_id="camera-entry",
        )
        entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        entries.append(entry)
        return entry

    yield _setup
    for entry in entries:
        await hass.config_entries.async_unload(entry.entry_id)


def camera_entity(hass: HomeAssistant):
    """Return the Pro Ultra's camera entity object."""
    return next(
        e
        for p in entity_platform.async_get_platforms(hass, DOMAIN)
        for e in p.entities.values()
        if e.entity_id == CAMERA_ENTITY_ID
    )


def live_session(hass: HomeAssistant) -> LiveSession:
    """Return the Live Session the Pro Ultra's camera entity started."""
    return camera_entity(hass)._session


async def test_one_camera_per_pro_ultra(hass: HomeAssistant, setup_entry) -> None:
    """Test the Pro Ultra gets exactly one camera and the Scooper none."""
    entry = await setup_entry()

    cameras = [
        (e.entity_id, e.unique_id)
        for e in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
        if e.domain == "camera"
    ]
    assert cameras == [(CAMERA_ENTITY_ID, "VISUAL_PRO_ULTRA_AABBCCDDEEFF-camera")]
    state = hass.states.get(CAMERA_ENTITY_ID)
    assert state.state == CameraState.IDLE
    assert state.attributes["supported_features"] == CameraEntityFeature.STREAM


async def test_stream_source_is_the_rtsp_url_without_opening_a_session(
    hass: HomeAssistant, setup_entry, fake_cloud
) -> None:
    """Test the default stream source is the loopback RTSP URL, taken at once."""
    await setup_entry()
    session = live_session(hass)

    source = await async_get_stream_source(hass, CAMERA_ENTITY_ID)

    assert source == session.rtsp_url
    assert source.startswith("rtsp://127.0.0.1:")
    await hass.async_block_till_done()
    assert not session.streaming
    assert fake_cloud.stream_queries == 0


async def test_h264_option_hands_go2rtc_an_ffmpeg_source(
    hass: HomeAssistant, setup_entry
) -> None:
    """Test the H.264 option wraps the RTSP URL for go2rtc's ffmpeg."""
    await setup_entry(**{CONF_CAMERA_CODEC: CAMERA_CODEC_H264})
    session = live_session(hass)

    source = await async_get_stream_source(hass, CAMERA_ENTITY_ID)

    assert source == f"ffmpeg:{session.rtsp_url}#video=h264#audio=copy"


async def test_h264_option_leaves_the_stream_component_on_h265(
    hass: HomeAssistant, setup_entry, hass_ws_client
) -> None:
    """Test the HLS fallback's Stream opens the plain RTSP URL in H.264 mode."""
    await setup_entry(**{CONF_CAMERA_CODEC: CAMERA_CODEC_H264})
    session = live_session(hass)
    client = await hass_ws_client(hass)

    with patch(CREATE_STREAM) as create_stream:
        create_stream.return_value.start = AsyncMock()
        create_stream.return_value.stop = AsyncMock()
        create_stream.return_value.endpoint_url.return_value = "/api/hls/t/m.m3u8"
        await client.send_json_auto_id(
            {"type": "camera/stream", "entity_id": CAMERA_ENTITY_ID}
        )
        msg = await client.receive_json()

    assert msg["success"]
    assert msg["result"] == {"url": "/api/hls/t/m.m3u8"}
    create_stream.assert_called_once()
    assert create_stream.call_args.args == (hass, session.rtsp_url)
    assert (
        await async_get_stream_source(hass, CAMERA_ENTITY_ID)
        == f"ffmpeg:{session.rtsp_url}#video=h264#audio=copy"
    )


async def test_h264_option_leaves_an_orientation_to_go2rtc(
    hass: HomeAssistant, setup_entry, hass_ws_client
) -> None:
    """Test H.264 mode hands go2rtc the plain URL once an orientation transcodes it."""
    await setup_entry(**{CONF_CAMERA_CODEC: CAMERA_CODEC_H264})
    session = live_session(hass)
    client = await hass_ws_client(hass)

    await client.send_json_auto_id(
        {
            "type": "camera/update_prefs",
            "entity_id": CAMERA_ENTITY_ID,
            "orientation": Orientation.ROTATE_RIGHT,
        }
    )
    assert (await client.receive_json())["success"]

    assert await async_get_stream_source(hass, CAMERA_ENTITY_ID) == session.rtsp_url


async def test_switched_off_camera_refuses_to_stream(
    hass: HomeAssistant, setup_entry, fake_cloud, hass_ws_client
) -> None:
    """Test SleepMode 1 turns the camera off; Home Assistant refuses its streams."""
    fake_cloud.sleep_mode = 1
    entry = await setup_entry()
    entity = camera_entity(hass)
    client = await hass_ws_client(hass)

    assert not entity.is_on
    assert (
        hass.states.get(CAMERA_ENTITY_ID).attributes["supported_features"]
        == CameraEntityFeature.STREAM
    )
    with pytest.raises(HomeAssistantError, match="Camera is off"):
        await async_get_stream_source(hass, CAMERA_ENTITY_ID)
    with patch(CREATE_STREAM) as create_stream:
        await client.send_json_auto_id(
            {"type": "camera/stream", "entity_id": CAMERA_ENTITY_ID}
        )
        msg = await client.receive_json()
    assert msg["error"] == {"code": "start_stream_failed", "message": "Camera is off"}
    create_stream.assert_not_called()
    assert fake_cloud.stream_queries == 0

    fake_cloud.sleep_mode = 0
    await hass.data[DOMAIN]["entry_coordinators"][entry.entry_id].async_refresh()

    assert entity.is_on
    assert (
        await async_get_stream_source(hass, CAMERA_ENTITY_ID)
        == live_session(hass).rtsp_url
    )


class FakeWebRtcProvider(CameraWebRTCProvider):
    """A WebRTC provider, like go2rtc, that records the sources it is asked about."""

    def __init__(self) -> None:
        """Start with no sources seen."""
        self.sources: list[str] = []

    @property
    def domain(self) -> str:
        """Return the provider's domain."""
        return "test"

    def async_is_supported(self, stream_source: str) -> bool:
        """Record the source and accept it."""
        self.sources.append(stream_source)
        return True

    async def async_handle_async_webrtc_offer(self, *args) -> None:
        """Ignore offers."""

    async def async_on_webrtc_candidate(self, *args) -> None:
        """Ignore candidates."""


async def test_camera_switched_off_at_startup_still_loads_under_webrtc(
    hass: HomeAssistant, setup_entry, fake_cloud
) -> None:
    """Test a camera asleep at startup is added, and waking it flips no feature."""
    assert await async_setup_component(hass, "camera", {})
    provider = FakeWebRtcProvider()
    async_register_webrtc_provider(hass, provider)
    await hass.async_block_till_done()
    fake_cloud.sleep_mode = 1

    entry = await setup_entry()
    entity = camera_entity(hass)
    url = live_session(hass).rtsp_url

    assert hass.states.get(CAMERA_ENTITY_ID).state == CameraState.IDLE
    assert not entity.is_on
    assert len(provider.sources) == 1
    assert provider.sources[0].startswith("rtsp://")
    assert entity.camera_capabilities.frontend_stream_types == {
        StreamType.HLS,
        StreamType.WEB_RTC,
    }

    fake_cloud.sleep_mode = 0
    await hass.data[DOMAIN]["entry_coordinators"][entry.entry_id].async_refresh()
    await hass.async_block_till_done()

    assert entity.is_on
    assert entity.supported_features == CameraEntityFeature.STREAM
    assert len(provider.sources) == 1
    assert await async_get_stream_source(hass, CAMERA_ENTITY_ID) == url
    assert fake_cloud.stream_queries == 0


async def test_still_before_any_keyframe_opens_no_session(
    hass: HomeAssistant, setup_entry, fake_cloud
) -> None:
    """Test a still request before the first keyframe has no image and no session."""
    await setup_entry()
    session = live_session(hass)

    with (
        patch("asyncio.create_subprocess_exec") as spawn,
        pytest.raises(HomeAssistantError, match="Unable to get image"),
    ):
        await async_get_image(hass, CAMERA_ENTITY_ID)
    await hass.async_block_till_done()

    spawn.assert_not_called()
    assert not session.streaming
    assert fake_cloud.stream_queries == 0


async def test_still_decodes_each_keyframe_once(
    hass: HomeAssistant, setup_entry, fake_cloud
) -> None:
    """Test the last keyframe is decoded to a JPEG once and then served from cache."""
    await setup_entry()
    entity = camera_entity(hass)
    session = live_session(hass)
    process = MagicMock(returncode=0)
    process.communicate = AsyncMock(return_value=(b"jpeg-1", b""))

    with (
        patch.object(
            LiveSession, "last_keyframe", new_callable=PropertyMock
        ) as keyframe,
        patch(
            "asyncio.create_subprocess_exec", AsyncMock(return_value=process)
        ) as spawn,
    ):
        keyframe.return_value = b"keyframe-1"
        first = await async_get_image(hass, CAMERA_ENTITY_ID)
        again = await entity.async_camera_image()
        keyframe.return_value = b"keyframe-2"
        process.communicate.return_value = (b"jpeg-2", b"")
        second = await entity.async_camera_image()

    assert (first.content, again, second) == (b"jpeg-1", b"jpeg-1", b"jpeg-2")
    assert spawn.await_count == 2
    assert spawn.await_args.args == (
        get_ffmpeg_manager(hass).binary,
        "-f",
        "hevc",
        "-i",
        "pipe:0",
        "-frames:v",
        "1",
        "-f",
        "image2",
        "-c:v",
        "mjpeg",
        "pipe:1",
    )
    assert [c.args for c in process.communicate.await_args_list] == [
        (b"keyframe-1",),
        (b"keyframe-2",),
    ]
    await hass.async_block_till_done()
    assert session._runner is None
    assert not session.streaming
    assert fake_cloud.stream_queries == 0


async def test_failed_still_decode_is_retried_on_the_next_request(
    hass: HomeAssistant, setup_entry, caplog: pytest.LogCaptureFixture
) -> None:
    """Test a failed decode is not cached: the next request retries, at debug."""
    await setup_entry()
    entity = camera_entity(hass)
    process = MagicMock(returncode=1)
    process.communicate = AsyncMock(return_value=(b"", b""))

    with (
        patch.object(
            LiveSession,
            "last_keyframe",
            new_callable=PropertyMock,
            return_value=b"keyframe-1",
        ),
        patch(
            "asyncio.create_subprocess_exec", AsyncMock(return_value=process)
        ) as spawn,
        caplog.at_level(logging.DEBUG),
    ):
        failed = [await entity.async_camera_image() for _ in range(2)]
        process.returncode = 0
        process.communicate.return_value = (b"jpeg-1", b"")
        recovered = [await entity.async_camera_image() for _ in range(2)]

    assert failed == [None, None]
    assert recovered == [b"jpeg-1", b"jpeg-1"]
    assert spawn.await_count == 3
    still = [r for r in caplog.records if r.getMessage().startswith("No still image")]
    assert [r.levelno for r in still] == [logging.DEBUG] * 2


async def test_live_view_uses_the_current_session_without_signing_in(
    hass: HomeAssistant, setup_entry, fake_cloud, connect
) -> None:
    """Test a failed open queries once, not after the reader left, and never signs in."""
    await setup_entry()
    session = live_session(hass)

    with (
        patch.object(live_view, "IDLE_TIMEOUT", IDLE_TIMEOUT),
        patch.object(live_view, "RECONNECT_BACKOFF", BACKOFF),
    ):
        reader = await play(connect, session.rtsp_url)
        await until(lambda: fake_cloud.stream_queries)
        reader.writer.close()
        await asyncio.sleep(2 * BACKOFF)

    assert fake_cloud.stream_queries == 1
    assert fake_cloud.login_count == 0


async def test_renaming_the_entity_restarts_the_live_view_on_a_new_url(
    hass: HomeAssistant, setup_entry, fake_cloud, connect
) -> None:
    """Test the re-added camera listens on a new URL and a reader there opens a session."""
    await setup_entry()
    entity = camera_entity(hass)
    session = entity._session
    old_url = session.rtsp_url
    old_reader = await play(connect, old_url)

    er.async_get(hass).async_update_entity(
        CAMERA_ENTITY_ID, new_entity_id="camera.renamed"
    )
    await hass.async_block_till_done()

    assert entity._session is session
    assert session.rtsp_url != old_url
    assert await asyncio.wait_for(old_reader.reader.read(), 2) == b""
    with pytest.raises(OSError):
        await connect(old_url)
    assert await async_get_stream_source(hass, "camera.renamed") == session.rtsp_url
    queries = fake_cloud.stream_queries
    with (
        patch.object(live_view, "IDLE_TIMEOUT", IDLE_TIMEOUT),
        patch.object(live_view, "RECONNECT_BACKOFF", BACKOFF),
    ):
        reader = await play(connect, session.rtsp_url)
        await until(lambda: fake_cloud.stream_queries > queries)
        reader.writer.close()
        await asyncio.sleep(2 * BACKOFF)


async def test_paused_and_ended_session_makes_the_camera_unavailable(
    hass: HomeAssistant, setup_entry, fake_cloud, connect, caplog
) -> None:
    """Test a paused, ended Session fails live view and the camera, signing in never."""
    entry = await setup_entry()
    session = live_session(hass)
    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": SIGN_IN_ENTITY_ID}, blocking=True
    )
    fake_cloud.valid_tokens.clear()
    account = hass.data[DOMAIN]["accounts"]["acc-1"]
    await (await account.async_aliyun_channel()).async_close()

    reader = await play(connect, session.rtsp_url)
    await until(lambda: "Live view for" in caplog.text)
    reader.writer.close()
    assert "failed: CatLink sign-in is paused\n" in caplog.text
    await hass.data[DOMAIN]["entry_coordinators"][entry.entry_id].async_refresh()

    assert hass.states.get(CAMERA_ENTITY_ID).state == STATE_UNAVAILABLE
    assert fake_cloud.stream_queries == 0
    assert fake_cloud.login_count == 0


async def test_unloading_the_entry_stops_the_live_view(
    hass: HomeAssistant, setup_entry, connect
) -> None:
    """Test unloading the entry closes the Live Session, its server and its readers."""
    entry = await setup_entry()
    session = live_session(hass)
    reader = await play(connect, session.rtsp_url)

    assert await hass.config_entries.async_unload(entry.entry_id)

    assert await asyncio.wait_for(reader.reader.read(), 2) == b""
    with pytest.raises(OSError):
        await connect(session.rtsp_url)
    assert session._runner is None


async def test_failed_server_start_does_not_break_the_unload(
    hass: HomeAssistant, setup_entry, caplog: pytest.LogCaptureFixture
) -> None:
    """Test a camera whose RTSP server cannot start is not added, and unload is clean."""
    with patch(
        "custom_components.catlink.modules.live_view.rtsp.asyncio.start_server",
        side_effect=OSError("no ports"),
    ):
        entry = await setup_entry()
    assert "Error adding entity camera" in caplog.text
    assert hass.states.get(CAMERA_ENTITY_ID) is None
    caplog.clear()

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    assert "Error while removing entity" not in caplog.text
