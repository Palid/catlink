"""Tests for the camera RTP depacketiser, on packets built in the test."""

from __future__ import annotations

from custom_components.catlink.modules.live_view.depacketiser import (
    MAX_MISORDER,
    AudioChunk,
    Depacketiser,
    VideoFrame,
)
import pytest

from .live_view_fakes import KEY, encrypted, packet

START = b"\x00\x00\x00\x01\x02\x01"


def run(packets, key=KEY):
    """Feed every packet and collect the events."""
    d = Depacketiser(key)
    out = []
    for p in packets:
        out.extend(d.feed(p))
    return out


def of(events, kind):
    """Return the events of one type."""
    return [e for e in events if isinstance(e, kind)]


def single(seq):
    """Return a one-packet keyframe at a sequence number, its timestamp following it."""
    seq &= 0xFFFF
    return packet(seq, seq * 3960, START + b"x", b"\x0b\x00", marker=True)


def feed(d, seqs):
    """Feed one-packet keyframes at each sequence number and collect the events."""
    return [e for seq in seqs for e in d.feed(single(seq))]


class TestPFrame:
    """Plain P frames."""

    def test_p_frame_over_several_packets(self):
        """P frame over several packets."""
        events = run([
            packet(10, 5000, START + b"AAA", b"\x0a\x00", clock=111),
            packet(11, 5000, b"BBB", b"\x08\x00"),
            packet(12, 5000, b"CCC", b"\x08\x00", marker=True),
        ])
        (frame,) = of(events, VideoFrame)
        assert frame.data == START + b"AAABBBCCC"
        assert frame.rtp_timestamp == 5000
        assert frame.clock == 111
        assert not frame.keyframe

    def test_single_packet_frame(self):
        """Single packet frame."""
        (frame,) = of(run([packet(1, 9, START, b"\x0a\x00", marker=True)]), VideoFrame)
        assert frame.data == START

    def test_two_frames_back_to_back(self):
        """Two frames back to back."""
        events = run([
            packet(1, 100, START + b"1", b"\x0a\x00", marker=True),
            packet(2, 200, START + b"2", b"\x0a\x00", marker=True),
        ])
        assert [f.data for f in of(events, VideoFrame)] == [START + b"1", START + b"2"]


class TestDecrypt:
    """Keyframe heads decrypted with the key."""

    def keyframe(self, first_seq=1, ts=7000, key=KEY):
        """Return an encrypted keyframe of three head packets and a clear tail."""
        return [
            encrypted(first_seq, ts, b"\x00\x00\x00\x01\x40\x01" + b"v" * 33, b"\x0b\x00", key=key, clock=900),
            encrypted(first_seq + 1, ts, b"s" * 40, b"\x09\x00", key=key, clock=900),
            encrypted(first_seq + 2, ts, b"p" * 31, b"\x09\x00", key=key, clock=900),
            packet(first_seq + 3, ts, b"TAIL", b"\x09\x00", clock=900, marker=True),
        ]

    def test_keyframe_decrypted_into_one_frame(self):
        """Keyframe decrypted into one frame."""
        (frame,) = of(run(self.keyframe()), VideoFrame)
        assert frame.keyframe
        assert frame.clock == 900
        assert frame.data == b"\x00\x00\x00\x01\x40\x01" + b"v" * 33 + b"s" * 40 + b"p" * 31 + b"TAIL"

    def test_lost_head_packet_drops_keyframe(self):
        """A head packet lost mid keyframe drops the whole keyframe."""
        first, _, third, tail = self.keyframe()
        assert of(run([first, third, tail]), VideoFrame) == []

    def test_trailing_partial_block_stays_clear(self):
        """The bytes after the last whole block arrive in the clear and reach the frame as sent."""
        payload = b"p" * 18 + b"clear tail 13"
        pkt = encrypted(1, 1, payload, b"\x0b\x00", marker=True)
        assert (len(pkt) - 16) % 16 == 13
        assert pkt.endswith(b"clear tail 13")
        assert b"p" * 18 not in pkt
        (frame,) = of(run([pkt]), VideoFrame)
        assert frame.data == payload

    def test_p_frame_after_keyframe_still_works(self):
        """P frame after keyframe still works."""
        events = run(self.keyframe() + [packet(5, 8000, START, b"\x0a\x00", marker=True)])
        frames = of(events, VideoFrame)
        assert [f.keyframe for f in frames] == [True, False]

    def test_short_encrypted_packet_ignored(self):
        """Short encrypted packet ignored."""
        assert Depacketiser(KEY).feed(encrypted(1, 1, b"", b"")) == []

    def test_wrong_key_does_not_emit_frame(self):
        """Wrong key does not emit frame."""
        events = run(self.keyframe(), key=b"X" * 16)
        assert of(events, VideoFrame) == []

    def test_clock_read_from_decrypted_head_without_clear_tail(self):
        """A keyframe made only of encrypted packets takes its clock from the decrypted first one."""
        events = run([
            encrypted(1, 7000, b"v" * 33, b"\x0b\x00", clock=900),
            encrypted(2, 7000, b"s" * 40, b"\x09\x00", clock=901),
            encrypted(3, 7000, b"p" * 31, b"\x09\x00", clock=902, marker=True),
        ])
        (frame,) = of(events, VideoFrame)
        assert frame.clock == 900
        assert frame.keyframe
        assert frame.data == b"v" * 33 + b"s" * 40 + b"p" * 31

    def test_clock_taken_from_a_later_decrypted_packet_when_the_first_has_none(self):
        """A first packet without a clock element leaves the clock to the next decrypted packet."""
        no_clock = b"\xbe\xde\x00\x03" + bytes(12)
        events = run([
            encrypted(1, 7000, b"v" * 33, b"\x0b\x00", ext=no_clock),
            encrypted(2, 7000, b"s" * 40, b"\x09\x00", clock=901, marker=True),
        ])
        (frame,) = of(events, VideoFrame)
        assert frame.clock == 901


class TestAudio:
    """Audio chunks."""

    def test_audio_chunk(self):
        """Audio chunk."""
        (chunk,) = of(run([packet(1, 3000, b"\xd5" * 320, b"\x06\x1b", marker=True, clock=42)]), AudioChunk)
        assert chunk.data == b"\xd5" * 320
        assert chunk.rtp_timestamp == 3000
        assert chunk.clock == 42

    def test_audio_interleaved_in_frame_does_not_end_or_split_it(self):
        """Audio interleaved in frame does not end or split it."""
        events = run([
            packet(1, 100, START + b"A", b"\x0a\x00"),
            packet(2, 190, b"\xd5" * 320, b"\x06\x1b", marker=True),
            packet(3, 100, b"B", b"\x08\x00", marker=True),
        ])
        (frame,) = of(events, VideoFrame)
        assert frame.data == START + b"AB"
        assert len(of(events, AudioChunk)) == 1

    def test_audio_between_keyframe_head_and_tail(self):
        """Audio between an encrypted keyframe head and its clear tail."""
        events = run([
            encrypted(1, 100, START + b"h" * 30, b"\x0b\x00"),
            packet(2, 190, b"\xd5" * 320, b"\x06\x1b"),
            packet(3, 100, b"T", b"\x09\x00", marker=True),
        ])
        (frame,) = of(events, VideoFrame)
        assert frame.keyframe
        assert frame.data == START + b"h" * 30 + b"T"
        assert len(of(events, AudioChunk)) == 1


class TestGap:
    """Loss, reordering and wraparound."""

    def test_gap_mid_frame_drops_the_frame(self):
        """Gap mid frame drops that frame only."""
        events = run([
            packet(1, 100, START + b"A", b"\x0a\x00"),
            packet(3, 100, b"C", b"\x08\x00", marker=True),
            packet(4, 200, START + b"Z", b"\x0a\x00", marker=True),
        ])
        assert [f.data for f in of(events, VideoFrame)] == [START + b"Z"]

    def test_gap_between_frames_drops_nothing(self):
        """Gap between frames drops nothing."""
        events = run([
            packet(1, 100, START + b"A", b"\x0a\x00", marker=True),
            packet(5, 200, START + b"B", b"\x0a\x00", marker=True),
        ])
        assert len(of(events, VideoFrame)) == 2

    def test_continuation_without_start_is_dropped(self):
        """Continuation without start is dropped."""
        assert run([packet(1, 100, b"B", b"\x08\x00", marker=True)]) == []

    def test_new_start_before_marker_drops_previous(self):
        """New start before marker drops previous."""
        events = run([
            packet(1, 100, START + b"A", b"\x0a\x00"),
            packet(2, 200, START + b"B", b"\x0a\x00", marker=True),
        ])
        assert [f.data for f in of(events, VideoFrame)] == [START + b"B"]

    def test_wraparound_is_not_a_gap(self):
        """Wraparound is not a gap."""
        events = run([
            packet(65534, 100, START + b"A", b"\x0a\x00"),
            packet(65535, 100, b"B", b"\x08\x00"),
            packet(0, 100, b"C", b"\x08\x00", marker=True),
        ])
        assert of(events, VideoFrame)[0].data == START + b"ABC"

    def test_gap_across_wraparound(self):
        """Gap across wraparound."""
        events = run([
            packet(65535, 100, START + b"A", b"\x0a\x00"),
            packet(1, 100, b"C", b"\x08\x00", marker=True),
        ])
        assert of(events, VideoFrame) == []

    def test_duplicate_packet_ignored(self):
        """Duplicate packet ignored."""
        p = packet(1, 100, START + b"A", b"\x0a\x00")
        events = run([p, p, packet(2, 100, b"B", b"\x08\x00", marker=True)])
        assert of(events, VideoFrame)[0].data == START + b"AB"

    @pytest.mark.parametrize("behind", [1, 50, MAX_MISORDER])
    def test_late_packet_ignored(self, behind):
        """A packet up to MAX_MISORDER late is ignored, and the stream goes on."""
        d = Depacketiser(KEY)
        assert len(of(feed(d, range(1000, 1200)), VideoFrame)) == 200
        assert d.feed(single(1199 - behind)) == []
        assert len(of(feed(d, range(1200, 1210)), VideoFrame)) == 10


class TestSequenceJump:
    """Far sequence jumps either way are followed at once, so the stream never freezes."""

    @pytest.mark.parametrize("stray", [109 - MAX_MISORDER - 1, 109 - 30_000, 100 + 0x7FFF])
    def test_one_stray_packet_does_not_freeze_the_stream(self, stray):
        """In-order packets after one stray packet far behind or ahead still make every frame."""
        d = Depacketiser(KEY)
        assert len(of(feed(d, range(100, 110)), VideoFrame)) == 10
        feed(d, [stray])
        assert len(of(feed(d, range(110, 310)), VideoFrame)) == 200

    @pytest.mark.parametrize("restart", [40_000, 10])
    def test_restart_is_followed_at_once(self, restart):
        """A sender restart drops only the frame it cut and is followed from its first packet."""
        d = Depacketiser(KEY)
        feed(d, range(1000, 1010))
        d.feed(packet(1010, 99, START + b"open", b"\x0a\x00"))
        events = feed(d, range(restart, restart + 20))
        assert [f.rtp_timestamp for f in of(events, VideoFrame)] == [
            seq * 3960 for seq in range(restart, restart + 20)
        ]


class TestHeader:
    """RTP header handling."""

    def test_padding_removed(self):
        """Padding removed."""
        (frame,) = of(run([packet(1, 1, START + b"A", b"\x0a\x00", marker=True, pad=4)]), VideoFrame)
        assert frame.data == START + b"A"

    def test_csrc_skipped(self):
        """Csrc skipped."""
        (frame,) = of(run([packet(1, 1, START + b"A", b"\x0a\x00", marker=True, csrc=2, clock=5)]), VideoFrame)
        assert frame.data == START + b"A"
        assert frame.clock == 5

    def test_non_rtp_ignored(self):
        """Non rtp ignored."""
        d = Depacketiser(KEY)
        assert d.feed(b"\x00\x01" + b"\x00" * 94) == []
        assert d.feed(b"\x80") == []

    def test_other_payload_type_ignored(self):
        """Other payload type ignored."""
        p = bytearray(packet(1, 1, START, b"\x0a\x00", marker=True))
        p[1] = 0x80 | 8
        assert Depacketiser(KEY).feed(bytes(p)) == []


class TestInnerFlags:
    """Every combination of the inner header flags."""

    @pytest.mark.parametrize(
        ("inner", "keyframe"),
        [(0x0A, False), (0x0B, True)],
    )
    def test_first_packet_starts_a_frame(self, inner, keyframe):
        """A first packet starts a frame and the keyframe flag marks it."""
        (frame,) = of(run([packet(1, 1, START, bytes([inner, 0]), marker=True)]), VideoFrame)
        assert frame.keyframe is keyframe
        assert frame.data == START

    @pytest.mark.parametrize("inner", [0x08, 0x09])
    def test_continuation_joins_the_frame_and_ignores_keyframe_flag(self, inner):
        """A continuation joins the open frame whatever its keyframe flag says."""
        events = run([
            packet(1, 1, START + b"A", b"\x0a\x00"),
            packet(2, 1, b"B", bytes([inner, 0]), marker=True),
        ])
        (frame,) = of(events, VideoFrame)
        assert frame.data == START + b"AB"
        assert not frame.keyframe

    @pytest.mark.parametrize("inner", [0x0C, 0x0D, 0x0E, 0x0F])
    def test_audio_flag_wins_over_the_other_flags(self, inner):
        """Audio is emitted as audio whatever the other flags say."""
        events = run([packet(1, 1, b"\xd5" * 8, bytes([inner, 0x1B]), marker=True)])
        assert [e.data for e in of(events, AudioChunk)] == [b"\xd5" * 8]
        assert of(events, VideoFrame) == []

    @pytest.mark.parametrize("inner", [0x00, 0x01, 0x02, 0x03, 0x04, 0x05, 0x10, 0x12, 0x13])
    def test_non_h265_codec_is_dropped_unless_audio(self, inner):
        """Video with another codec is dropped; audio is not affected by the codec bits."""
        events = run([packet(1, 77, START, bytes([inner, 0]), marker=True)])
        if inner & 0x04:
            assert len(of(events, AudioChunk)) == 1
        else:
            assert events == []

    def test_empty_payload_is_not_h265(self):
        """A packet with no inner header is dropped rather than crashing."""
        assert run([packet(1, 5, b"", marker=True)]) == []
