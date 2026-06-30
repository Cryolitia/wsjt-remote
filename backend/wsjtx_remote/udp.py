from __future__ import annotations

import asyncio
import logging
from typing import Any

from . import protocol
from .state import AppState, extract_adif_call


logger = logging.getLogger(__name__)


class WSJTXUDPProtocol(asyncio.DatagramProtocol):
    def __init__(self, state: AppState, broadcaster: Any):
        self.state = state
        self.broadcaster = broadcaster

    def connection_made(self, transport: asyncio.DatagramTransport) -> None:
        self.state.udp_transport = transport
        logger.info("udp socket ready")

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
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
                logger.info("dial frequency changed from %s to %s; cleared activity", previous_frequency, next_frequency)
                await self.broadcaster({"event": "clear", "data": {"reason": "frequency", "dial_frequency": next_frequency}})
            self.state.status = dict(msg.fields)
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
            logger.info("decode #%s snr=%s df=%s message=%r", decode["index"], decode["snr"], decode["delta_frequency"], decode["message"])
            await self.broadcaster({"event": "decode", "data": decode})
        elif msg.type == protocol.MessageType.Clear:
            self.state.clear_activity()
            logger.info("clear received id=%s fields=%s", msg.id, msg.fields)
            await self.broadcaster({"event": "clear", "data": msg.fields})
        elif msg.type == protocol.MessageType.Close:
            logger.info("close received id=%s", msg.id)
            await self.broadcaster({"event": "close", "data": {"id": msg.id}})
        elif msg.type == protocol.MessageType.LoggedADIF:
            adif = str(msg.fields.get("adif") or "")
            call = extract_adif_call(adif)
            indexed = self.state.adif.add_adif(adif)
            logger.info("logged adif call=%s indexed=%s", call or "", indexed)
            await self.broadcaster({"event": "logged_adif", "data": {"adif": adif, "call": call}})


async def start_udp(state: AppState, broadcaster: Any, host: str, port: int) -> asyncio.DatagramTransport:
    loop = asyncio.get_running_loop()
    transport, _ = await loop.create_datagram_endpoint(
        lambda: WSJTXUDPProtocol(state, broadcaster),
        local_addr=(host, port),
    )
    return transport


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
