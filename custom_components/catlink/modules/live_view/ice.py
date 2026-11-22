"""ICE-controlling agent on one UDP socket, carrying the camera's commands."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterable
import contextlib
import ipaddress
import json
import math
import secrets
import time

from ...const import _LOGGER
from . import sdp, stun

CHECK_INTERVAL = 0.05
KEEPALIVE_INTERVAL = 2.5
KEEPALIVE_RETRY_INTERVAL = 0.5
LOSS_TIMEOUT = 5.0
RTP_TIMEOUT = 8.0
COMMAND_RETRY_INTERVAL = 0.2
COMMAND_RETRIES = 3
TRANSACTION_TIMEOUT = 5.0

START_STREAMING_SEQ = 2
STOP_STREAMING_SEQ = 20
STOP_P2P_SEQ = 20000

LAN_NETWORKS = tuple(
    ipaddress.ip_network(network)
    for network in (
        "10.0.0.0/8",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "169.254.0.0/16",
        "fe80::/10",
        "fc00::/7",
    )
)


def is_lan_address(address: str) -> bool:
    """Return whether an address is RFC 1918, link-local or unique-local."""
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    return any(ip in network for network in LAN_NETWORKS)


def _compact(body: dict) -> str:
    """Return a command body as the compact JSON the camera expects."""
    return json.dumps(body, separators=(",", ":"))


def _is_rtp(data: bytes) -> bool:
    """Return whether a datagram is RTP; RTCP shares the range but not the types."""
    return len(data) >= 12 and 128 <= data[0] < 192 and not 192 <= data[1] <= 223


class IceError(Exception):
    """ICE did not connect to the camera."""


class IceAgent(asyncio.DatagramProtocol):
    """The controlling side of ICE, plus the commands and keepalive after it."""

    def __init__(
        self,
        *,
        local_ufrag: str,
        local_pwd: str,
        session_id: str,
        local_addresses: Iterable[str],
        on_rtp: Callable[[bytes], None],
        on_lost: Callable[[], None],
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Initialize the agent; nothing is bound until bind()."""
        self._local_ufrag = local_ufrag
        self._local_pwd = local_pwd.encode()
        self._session_id = session_id
        self._local_addresses = list(local_addresses)
        self._on_rtp = on_rtp
        self._on_lost = on_lost
        self._clock = clock
        self._tiebreaker = secrets.token_bytes(8)
        self._priority = stun.check_priority(sdp.HOST_PRIORITY)
        self._network_info = stun.network_info_value(sdp.NETWORK_ID, sdp.NETWORK_COST)
        self._transport: asyncio.DatagramTransport | None = None
        self._port = 0
        self._remote_ufrag = ""
        self._remote_pwd: bytes | None = None
        self._targets: list[tuple[str, int]] = []
        self._pending: dict[
            bytes, tuple[float, tuple[str, int], asyncio.Event | None]
        ] = {}
        self._acks: dict[tuple[str, int], asyncio.Event] = {}
        self._connected = asyncio.Event()
        self._selected: tuple[str, int] | None = None
        self._last_heard = 0.0
        self._last_rtp = math.inf
        self._keepalive: asyncio.Task | None = None
        self._lost = False

    async def bind(self) -> None:
        """Open the one UDP socket that carries ICE, the commands and the media."""
        await asyncio.get_running_loop().create_datagram_endpoint(
            lambda: self, local_addr=("0.0.0.0", 0)
        )

    def local_candidates(self) -> list[str]:
        """Return a host candidate line for each local address, for the trickle."""
        return [
            sdp.candidate_line(address, self._port, self._local_ufrag)
            for address in self._local_addresses
        ]

    async def connect(
        self,
        remote_ufrag: str,
        remote_pwd: str,
        remote_candidates: Iterable[sdp.Candidate],
        timeout: float,
    ) -> None:
        """Check the LAN candidates given and added meanwhile until one answers."""
        self._remote_ufrag = remote_ufrag
        self._remote_pwd = remote_pwd.encode()
        for candidate in remote_candidates:
            self.add_remote_candidate(candidate)
        deadline = self._clock() + timeout
        while not self._connected.is_set():
            if self._clock() >= deadline:
                if not self._targets:
                    raise IceError("The camera offered no LAN candidate")
                raise IceError("The camera did not answer ICE checks")
            for target in self._targets:
                self._send_check(target)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._connected.wait(), CHECK_INTERVAL)
        self._keepalive = asyncio.create_task(self._keep_alive())

    def add_remote_candidate(self, candidate: sdp.Candidate) -> None:
        """Also check a camera candidate, if it is LAN UDP and ICE is not connected."""
        if self._selected is not None or candidate.transport != "udp":
            return
        if not is_lan_address(candidate.ip):
            _LOGGER.debug("Dropping a non-LAN camera candidate")
            return
        if (target := (candidate.ip, candidate.port)) not in self._targets:
            self._targets.append(target)

    async def start_streaming(self) -> bool:
        """Ask the camera for its main stream; return whether it acknowledged in time."""
        self._last_rtp = self._clock()
        return await self._command(
            self._request("startStreaming", START_STREAMING_SEQ, streamType=0)
        )

    async def stop(self) -> None:
        """Send stopStreaming and stopP2p while the camera answers, then close."""
        if self._transport is None or self._transport.is_closing():
            return
        if self._keepalive is not None:
            self._keepalive.cancel()
            await asyncio.wait({self._keepalive})
        try:
            if self._selected is not None and not self._lost:
                await self._command(self._request("stopStreaming", STOP_STREAMING_SEQ))
                await self._command(
                    self._request("stopP2p", STOP_P2P_SEQ), stun_answer=True
                )
        finally:
            self._transport.close()

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        """Keep the transport and the port it is bound to."""
        self._transport = transport
        self._port = transport.get_extra_info("sockname")[1]

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        """Demultiplex STUN from RTP; RTP counts only from the selected camera."""
        if stun.is_stun(data):
            self._on_stun(data, addr)
        elif _is_rtp(data) and addr == self._selected:
            self._last_heard = self._last_rtp = self._clock()
            self._on_rtp(data)

    def _on_stun(self, data: bytes, addr: tuple[str, int]) -> None:
        """Answer the camera's checks and match the answers to ours."""
        try:
            message = stun.decode(data)
        except ValueError:
            return
        if message.type == stun.BINDING_REQUEST:
            self._answer_check(message, addr)
        elif message.type == stun.BINDING_SUCCESS:
            self._check_answered(message, addr)

    def _answer_check(self, message: stun.Message, addr: tuple[str, int]) -> None:
        """Answer a verified camera check and act on the command it carries."""
        username = message.get(stun.USERNAME) or b""
        if not (
            username.partition(b":")[0] == self._local_ufrag.encode()
            and stun.verify_fingerprint(message)
            and stun.verify_integrity(message, self._local_pwd)
        ):
            return
        self._send(
            stun.binding_success(message.transaction_id, *addr, self._local_pwd), addr
        )
        if addr == self._selected:
            self._last_heard = self._clock()
        software = message.get(stun.SOFTWARE)
        if software is None:
            return
        try:
            body = json.loads(software)
            message_type = body["params"]["messageType"]
            seq = body["seq_num"]
            if not (isinstance(message_type, str) and isinstance(seq, int)):
                return
        except (ValueError, LookupError, TypeError):
            return
        if body.get("type") == "response":
            if (ack := self._acks.get((message_type, seq))) is not None:
                ack.set()
        elif body.get("type") == "request" and self._remote_pwd is not None:
            response = {
                "version": "2.0",
                "type": "response",
                "seq_num": seq,
                "params": {"messageType": message_type},
            }
            self._send_check(addr, _compact(response))

    def _check_answered(self, message: stun.Message, addr: tuple[str, int]) -> None:
        """Take a verified answer from the address we checked as proof of life."""
        pending = self._pending.get(message.transaction_id)
        if (
            pending is None
            or pending[1] != addr
            or not (
                stun.verify_fingerprint(message)
                and stun.verify_integrity(message, self._remote_pwd)
            )
        ):
            return
        del self._pending[message.transaction_id]
        if self._selected is None:
            self._selected = addr
            self._connected.set()
        if addr == self._selected:
            self._last_heard = self._clock()
        if (answered := pending[2]) is not None:
            answered.set()

    def _send_check(
        self,
        addr: tuple[str, int],
        software: str | None = None,
        answered: asyncio.Event | None = None,
    ) -> None:
        """Send one check, remembering it until it is answered or too old."""
        now = self._clock()
        self._pending = {
            tid: pending
            for tid, pending in self._pending.items()
            if now - pending[0] < TRANSACTION_TIMEOUT
        }
        tid = sdp.random_string(12).encode()
        self._pending[tid] = (now, addr, answered)
        self._send(
            stun.binding_request(
                tid,
                f"{self._remote_ufrag}:{self._local_ufrag}",
                self._remote_pwd,
                tiebreaker=self._tiebreaker,
                priority=self._priority,
                network_info=self._network_info,
                software=software,
            ),
            addr,
        )

    def _send(self, data: bytes, addr: tuple[str, int]) -> None:
        """Send a datagram, never to an address off the LAN."""
        if is_lan_address(addr[0]):
            self._transport.sendto(data, addr)

    def _request(self, message_type: str, seq: int, **params: int) -> dict:
        """Return a command request body for this session."""
        return {
            "version": "2.0",
            "type": "request",
            "seq_num": seq,
            "params": {
                "messageType": message_type,
                **params,
                "sessionId": self._session_id,
            },
        }

    async def _command(self, body: dict, *, stun_answer: bool = False) -> bool:
        """Send a command, retransmitting until the camera acknowledges it."""
        done = asyncio.Event()
        key = (body["params"]["messageType"], body["seq_num"])
        if not stun_answer:
            self._acks[key] = done
        software = _compact(body)
        try:
            for _ in range(COMMAND_RETRIES + 1):
                self._send_check(self._selected, software, done if stun_answer else None)
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(done.wait(), COMMAND_RETRY_INTERVAL)
                    return True
            return False
        finally:
            if not stun_answer:
                del self._acks[key]

    async def _keep_alive(self) -> None:
        """Check until LOSS_TIMEOUT passes in silence or RTP_TIMEOUT without media."""
        answered = asyncio.Event()
        answered.set()
        next_check = resend_at = self._clock() + KEEPALIVE_INTERVAL
        while True:
            now = self._clock()
            silent = min(self._last_heard + LOSS_TIMEOUT, self._last_rtp + RTP_TIMEOUT)
            if now >= silent:
                break
            if now >= next_check:
                answered = asyncio.Event()
                next_check, resend_at = now + KEEPALIVE_INTERVAL, now
            if not answered.is_set() and now >= resend_at:
                self._send_check(self._selected, answered=answered)
                resend_at = now + KEEPALIVE_RETRY_INTERVAL
            wake = next_check if answered.is_set() else min(next_check, resend_at)
            await asyncio.sleep(min(wake, silent) - now)
        self._lost = now >= self._last_heard + LOSS_TIMEOUT
        _LOGGER.debug(
            "The camera stopped %s",
            "answering ICE checks" if self._lost else "sending media",
        )
        self._on_lost()
