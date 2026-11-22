"""Tests for the live view offer builder, candidate lines and answer parser."""

from __future__ import annotations

import hashlib
import hmac
import re

import pytest

from custom_components.catlink.modules.live_view import sdp

ANSWER = "\r\n".join(
    [
        "v=0",
        "o=- 1 2 IN IP4 127.0.0.1",
        "s=-",
        "t=0 0",
        "m=video 50020 RTP/AVPF 96 97",
        "c=IN IP4 10.0.0.20",
        "a=candidate:2063896965 1 udp 2122260223 10.0.0.20 50020 typ host "
        "generation 0 ufrag Cam12",
        "a=candidate:broken",
        "a=ice-ufrag:Cam12",
        "a=ice-pwd:mediaLevelPassword0123456789",
        "a=sendonly",
        "a=ssrc-group:FID 1 2",
        "a=extdata:0123456789abcdef0123456789abcdef01234567_0.8.5",
        "",
    ]
)


def _ssrcs(offer: sdp.Offer) -> tuple[str, str]:
    """Return the video and rtx SSRCs from the offer's ssrc-group line."""
    return re.search(r"a=ssrc-group:FID (\d+) (\d+)\r\n", offer.sdp).groups()


def test_offer_structure() -> None:
    """The offer is the app's single bundled video section, line for line."""
    offer = sdp.build_offer()
    ssrc, rtx_ssrc = _ssrcs(offer)
    lines = offer.sdp.split("\r\n")

    assert lines[-1] == ""
    lines = lines[:-1]
    assert not any("\n" in line for line in lines)
    assert re.fullmatch(r"o=- \d+ 2 IN IP4 127\.0\.0\.1", lines[1])
    cname = lines[24].removeprefix(f"a=ssrc:{ssrc} cname:")
    assert re.fullmatch(r"[A-Za-z0-9]{16}", cname)
    assert lines[:1] + lines[2:-1] == [
        "v=0",
        "s=-",
        "t=0 0",
        "a=group:BUNDLE video",
        "a=msid-semantic: WMS stream_label0",
        "m=video 9 RTP/AVPF 96 97",
        "c=IN IP4 0.0.0.0",
        "a=rtcp:9 IN IP4 0.0.0.0",
        f"a=ice-ufrag:{offer.ufrag}",
        f"a=ice-pwd:{offer.pwd}",
        "a=mid:video",
        "a=sendrecv",
        "a=rtcp-mux",
        "a=rtcp-rsize",
        "a=rtpmap:96 H264/90000",
        "a=rtcp-fb:96 ccm fir",
        "a=rtcp-fb:96 nack",
        "a=fmtp:96 level-asymmetry-allowed=1;packetization-mode=1;"
        "profile-level-id=42e01f",
        "a=rtpmap:97 rtx/90000",
        "a=rtcp-fb:97 ccm fir",
        "a=rtcp-fb:97 nack",
        "a=fmtp:97 apt=96",
        f"a=ssrc-group:FID {ssrc} {rtx_ssrc}",
        f"a=ssrc:{ssrc} cname:{cname}",
        f"a=ssrc:{ssrc} msid:stream_label0 video_label0",
        f"a=ssrc:{ssrc} mslabel:stream_label0",
        f"a=ssrc:{ssrc} label:video_label0",
        f"a=ssrc:{rtx_ssrc} cname:{cname}",
        f"a=ssrc:{rtx_ssrc} msid:stream_label0 video_label0",
        f"a=ssrc:{rtx_ssrc} mslabel:stream_label0",
        f"a=ssrc:{rtx_ssrc} label:video_label0",
    ]


def test_offer_credentials() -> None:
    """The ufrag is A plus 4 characters, the pwd 24, and the SSRCs differ."""
    offer = sdp.build_offer()
    ssrc, rtx_ssrc = _ssrcs(offer)

    assert re.fullmatch(r"A[A-Za-z0-9+/]{4}", offer.ufrag)
    assert re.fullmatch(r"[A-Za-z0-9+/]{24}", offer.pwd)
    assert ssrc != rtx_ssrc
    assert sdp.build_offer().pwd != offer.pwd


def test_offer_ends_with_extdata_hmac() -> None:
    """The last line is HMAC-SHA1 of the ssrc-group value, keyed with our pwd."""
    offer = sdp.build_offer()
    expected = hmac.new(
        offer.pwd.encode(), "FID {} {}".format(*_ssrcs(offer)).encode(), hashlib.sha1
    ).hexdigest()

    assert offer.sdp.endswith(f"\r\na=extdata:{expected}_0.8.5\r\n")


def test_candidate_line() -> None:
    """Host candidates take the app's form and parse back."""
    line = sdp.candidate_line("192.168.1.10", 50000, "AbC+1")

    assert re.fullmatch(
        r"candidate:\d+ 1 udp 2122260223 192\.168\.1\.10 50000 typ host "
        r"generation 0 ufrag AbC\+1 network-id 1 network-cost 10",
        line,
    )
    assert sdp.parse_candidate(line) == sdp.Candidate("udp", "192.168.1.10", 50000)
    assert sdp.candidate_line("192.168.1.11", 50000, "AbC+1") != line


def test_parse_candidate_rejects_malformed_lines() -> None:
    """A line that is not a candidate, or lacks typ, raises ValueError."""
    with pytest.raises(ValueError):
        sdp.parse_candidate("a=mid:video")
    with pytest.raises(ValueError):
        sdp.parse_candidate("candidate:1 1 udp 1 192.168.1.1 5 host")


def test_parse_answer_with_candidate_and_extdata() -> None:
    """A broken candidate and extdata are ignored."""
    answer = sdp.parse_answer(ANSWER)

    assert answer == sdp.Answer(
        ufrag="Cam12",
        pwd="mediaLevelPassword0123456789",
        candidates=[sdp.Candidate("udp", "10.0.0.20", 50020)],
    )


def test_parse_answer_media_level_wins_over_session_level() -> None:
    """Media-level ICE values win when the answer also carries session-level ones."""
    text = ANSWER.replace(
        "m=video", "a=ice-ufrag:SESS1\r\na=ice-pwd:sessionLevelPasswordXxxxx\r\nm=video"
    )

    answer = sdp.parse_answer(text)

    assert answer.ufrag == "Cam12"
    assert answer.pwd == "mediaLevelPassword0123456789"


def test_parse_answer_falls_back_to_session_level() -> None:
    """Without media-level ICE values the session-level ones are used."""
    text = ANSWER.replace("m=video", "a=ice-ufrag:SESS1\r\na=ice-pwd:sessionLevelPasswordXxxxx\r\nm=video")
    text = text.replace("a=ice-ufrag:Cam12\r\n", "").replace(
        "a=ice-pwd:mediaLevelPassword0123456789\r\n", ""
    )

    answer = sdp.parse_answer(text)

    assert answer.ufrag == "SESS1"
    assert answer.pwd == "sessionLevelPasswordXxxxx"


def test_parse_answer_without_credentials() -> None:
    """An answer without ICE values parses to None for the caller to reject."""
    answer = sdp.parse_answer("v=0\r\nm=video 9 RTP/AVPF 96\r\n")

    assert (answer.ufrag, answer.pwd, answer.candidates) == (None, None, [])
