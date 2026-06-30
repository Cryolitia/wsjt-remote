from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from . import protocol
from .state import extract_decode_callsign, is_calling_own


logger = logging.getLogger(__name__)

FT8_REPLY_WATCHDOG_SECONDS = 10 * 15


class ReplyWatchdog:
    def __init__(self, state: Any, broadcaster: Any):
        self.state = state
        self.broadcaster = broadcaster
        self.deadline = 0.0
        self.reason = ""

    @property
    def armed(self) -> bool:
        return self.deadline > 0.0

    def arm(self, reason: str) -> None:
        self.deadline = time.monotonic() + FT8_REPLY_WATCHDOG_SECONDS
        self.reason = reason
        logger.info("reply watchdog armed reason=%s timeout=%ss", reason, FT8_REPLY_WATCHDOG_SECONDS)

    def disarm(self, reason: str) -> None:
        if self.armed:
            logger.info("reply watchdog disarmed reason=%s", reason)
        self.deadline = 0.0
        self.reason = ""

    def reset_if_armed(self, reason: str) -> None:
        if self.armed:
            self.arm(reason)

    def on_status(self, status: dict[str, Any]) -> None:
        if _tx_idle(status):
            self.disarm("tx_idle")
        elif not self.armed:
            self.arm("non_idle")

    def on_decode(self, decode: dict[str, Any]) -> None:
        message = str(decode.get("message") or "")
        own_call = str(self.state.status.get("de_call") or "")
        caller = extract_decode_callsign(message, own_call)
        dx_call = str(self.state.status.get("dx_call") or "").upper().strip()
        if is_calling_own(message, own_call) and caller and caller == dx_call:
            self.reset_if_armed("calling_own")

    async def run(self) -> None:
        while True:
            await asyncio.sleep(1)
            if not self.armed or time.monotonic() < self.deadline:
                continue
            if _tx_idle(self.state.status):
                self.disarm("tx_idle")
                continue
            logger.warning("reply watchdog expired; halting TX reason=%s timeout=%ss", self.reason, FT8_REPLY_WATCHDOG_SECONDS)
            self._halt_tx()
            self.disarm("expired")

    def _halt_tx(self) -> None:
        address = self.state.remote.address()
        if self.state.udp_transport is None or address is None:
            logger.warning("reply watchdog cannot halt TX: WSJT-X is not connected")
            return
        data = protocol.build_halt_tx(self.state.remote.id, False, self.state.remote.schema)
        message = protocol.parse_message(data)
        self.state.udp_transport.sendto(data, address)
        event = self.state.add_debug_event("tx", data, address, message)
        if self.broadcaster:
            asyncio.create_task(self.broadcaster({"event": "debug", "data": event}))


def _tx_idle(status: dict[str, Any]) -> bool:
    return not bool(status.get("tx_enabled")) and not bool(status.get("transmitting"))
