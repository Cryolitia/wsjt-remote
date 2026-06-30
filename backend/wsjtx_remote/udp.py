from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
import socket
from typing import Any

from . import protocol
from .state import AppState, extract_adif_call


logger = logging.getLogger(__name__)


@dataclass(slots=True)
class UdpForwardTarget:
    text: str
    address: tuple[Any, ...]
    sock: socket.socket


class UdpForwarder:
    def __init__(self, targets: list[str]):
        self.targets = [_resolve_forward_target(target) for target in targets]

    def forward(self, data: bytes) -> None:
        for target in self.targets:
            try:
                target.sock.sendto(data, target.address)
            except OSError as exc:
                logger.warning("failed to forward raw UDP to %s: %s", target.text, exc)

    def close(self) -> None:
        for target in self.targets:
            target.sock.close()


class WSJTXUDPProtocol(asyncio.DatagramProtocol):
    def __init__(self, state: AppState, broadcaster: Any, forwarder: UdpForwarder | None = None):
        self.state = state
        self.broadcaster = broadcaster
        self.forwarder = forwarder

    def connection_made(self, transport: asyncio.DatagramTransport) -> None:
        self.state.udp_transport = transport
        logger.info("udp socket ready")

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        if self.forwarder:
            self.forwarder.forward(data)
        try:
            msg = protocol.parse_message(data)
            logger.debug("rx %s from %s:%s (%d bytes)", _message_type_name(msg), addr[0], addr[1], len(data))
            event = self.state.add_debug_event("rx", data, addr, msg)
            self.state.update_remote(msg, addr)
            asyncio.create_task(self.broadcaster({"event": "debug", "data": event}))
            asyncio.create_task(self._handle_message(msg))
        except Exception as exc:
            logger.warning("failed to parse udp datagram from %s:%s: %s", addr[0], addr[1], exc)
            event = self.state.add_debug_event("rx", data, addr, None, str(exc))
            asyncio.create_task(self.broadcaster({"event": "debug", "data": event}))

    async def _handle_message(self, msg: protocol.Message) -> None:
        if msg.type == protocol.MessageType.Heartbeat:
            logger.info("heartbeat from %s version=%s schema=%s", msg.id, msg.fields.get("version", ""), msg.schema)
            await self.broadcaster({"event": "state", "data": self.state.snapshot()})
        elif msg.type == protocol.MessageType.Status:
            previous_frequency = self.state.status.get("dial_frequency")
            next_frequency = msg.fields.get("dial_frequency")
            frequency_changed = bool(previous_frequency and next_frequency and previous_frequency != next_frequency)
            if frequency_changed:
                self.state.clear_activity()
                if self.state.plugins:
                    self.state.plugins.cancel_batches()
                logger.info("dial frequency changed from %s to %s; cleared activity", previous_frequency, next_frequency)
                await self.broadcaster({"event": "clear", "data": {"reason": "frequency", "dial_frequency": next_frequency}})
            self.state.status = dict(msg.fields)
            if self.state.reply_watchdog:
                self.state.reply_watchdog.on_status(self.state.status)
            if self.state.plugins:
                self.state.plugins.on_status(self.state.status)
            logger.info(
                "status id=%s mode=%s tx_enabled=%s transmitting=%s tx_message=%r",
                msg.id,
                self.state.status.get("mode", ""),
                self.state.status.get("tx_enabled", False),
                self.state.status.get("transmitting", False),
                self.state.status.get("tx_message", ""),
            )
            await self.broadcaster({"event": "status", "data": self.state.status})
            await self.broadcaster({"event": "state", "data": self.state.snapshot()})
        elif msg.type == protocol.MessageType.Decode:
            decode = self.state.add_decode(msg)
            if self.state.plugins:
                self.state.plugins.on_decode(decode)
            if self.state.reply_watchdog:
                self.state.reply_watchdog.on_decode(decode)
            logger.info("decode #%s snr=%s df=%s message=%r", decode["index"], decode["snr"], decode["delta_frequency"], decode["message"])
            await self.broadcaster({"event": "decode", "data": decode})
        elif msg.type == protocol.MessageType.Clear:
            self.state.clear_activity()
            if self.state.plugins:
                self.state.plugins.cancel_batches()
            logger.info("clear received id=%s fields=%s", msg.id, msg.fields)
            await self.broadcaster({"event": "clear", "data": msg.fields})
        elif msg.type == protocol.MessageType.Close:
            logger.info("close received id=%s", msg.id)
            await self.broadcaster({"event": "close", "data": {"id": msg.id}})
        elif msg.type == protocol.MessageType.LoggedADIF:
            adif = str(msg.fields.get("adif") or "")
            call = extract_adif_call(adif)
            indexed = self.state.adif.add_adif(adif)
            if self.state.plugins:
                self.state.plugins.on_logged_adif(adif, indexed)
            logger.info("logged adif call=%s indexed=%s", call or "", indexed)
            await self.broadcaster({"event": "logged_adif", "data": {"adif": adif, "call": call}})


async def start_udp(state: AppState, broadcaster: Any, host: str, port: int, forwarder: UdpForwarder | None = None) -> asyncio.DatagramTransport:
    loop = asyncio.get_running_loop()
    transport, _ = await loop.create_datagram_endpoint(
        lambda: WSJTXUDPProtocol(state, broadcaster, forwarder),
        local_addr=(host, port),
    )
    return transport


def create_udp_forwarder(targets: list[str]) -> UdpForwarder | None:
    if not targets:
        return None
    forwarder = UdpForwarder(targets)
    for target in forwarder.targets:
        logger.info("forwarding raw UDP to %s", target.text)
    return forwarder


def _resolve_forward_target(text: str) -> UdpForwardTarget:
    host, port = _parse_forward_target(text)
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_DGRAM)
    if not infos:
        raise ValueError(f"failed to resolve UDP forward target: {text}")
    family, socktype, proto, _, address = infos[0]
    sock = socket.socket(family, socktype, proto)
    return UdpForwardTarget(text=text, address=address, sock=sock)


def _parse_forward_target(text: str) -> tuple[str, int]:
    value = text.strip()
    if value.startswith("["):
        end = value.find("]")
        if end < 0 or end + 1 >= len(value) or value[end + 1] != ":":
            raise ValueError(f"invalid UDP forward target: {text}")
        host = value[1:end]
        port_text = value[end + 2 :]
    else:
        host, sep, port_text = value.rpartition(":")
        if not sep or ":" in host:
            raise ValueError(f"invalid UDP forward target: {text}")
    try:
        port = int(port_text)
    except ValueError as exc:
        raise ValueError(f"invalid UDP forward port: {text}") from exc
    if not host or not (0 < port <= 65535):
        raise ValueError(f"invalid UDP forward target: {text}")
    return host, port


def send_datagram(state: AppState, data: bytes, message: protocol.Message | None = None) -> None:
    address = state.remote.address()
    if state.udp_transport is None or address is None:
        raise RuntimeError("WSJT-X is not connected")
    state.udp_transport.sendto(data, address)
    state.add_debug_event("tx", data, address, message)
    logger.info("tx %s to %s:%s (%d bytes)", _message_type_name(message) if message else "raw", address[0], address[1], len(data))


async def heartbeat_loop(state: AppState, broadcaster: Any) -> None:
    while True:
        await asyncio.sleep(15)
        if not state.remote.connected:
            continue
        data = protocol.build_heartbeat("wsjtx-remote", state.remote.schema)
        try:
            send_datagram(state, data, protocol.parse_message(data))
            await broadcaster({"event": "debug", "data": state.debug_events[-1]})
        except Exception as exc:
            logger.debug("heartbeat send skipped: %s", exc)


def _message_type_name(message: protocol.Message) -> str:
    return message.type.name if isinstance(message.type, protocol.MessageType) else f"Unknown({message.raw_type})"
