from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
import re
from typing import Any

from .adif_index import AdifIndex, band_from_hz, worked_status_json
from .dxcc import DxccLookup
from . import protocol


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


CALL_RE = re.compile(r"<CALL:(\d+)>\s*([^<\s]+)", re.IGNORECASE)
GRID_RE = re.compile(r"^[A-R]{2}\d{2}([A-X]{2})?$")


@dataclass(slots=True)
class RemoteClient:
    id: str = ""
    host: str = ""
    port: int = 0
    schema: int = protocol.SCHEMA
    version: str = ""
    revision: str = ""
    last_seen: str = ""

    @property
    def connected(self) -> bool:
        return bool(self.host and self.port and self.id)

    def address(self) -> tuple[str, int] | None:
        if not self.host or not self.port:
            return None
        return (self.host, self.port)

    def to_json(self) -> dict[str, Any]:
        return {
            "connected": self.connected,
            "id": self.id,
            "host": self.host,
            "port": self.port,
            "schema": self.schema,
            "version": self.version,
            "revision": self.revision,
            "last_seen": self.last_seen,
        }


@dataclass(slots=True)
class AppState:
    remote: RemoteClient = field(default_factory=RemoteClient)
    status: dict[str, Any] = field(default_factory=dict)
    decodes: deque[dict[str, Any]] = field(default_factory=lambda: deque(maxlen=500))
    transmits: deque[dict[str, Any]] = field(default_factory=lambda: deque(maxlen=500))
    debug_events: deque[dict[str, Any]] = field(default_factory=lambda: deque(maxlen=500))
    websockets: set[Any] = field(default_factory=set)
    udp_transport: Any = None
    plugins: Any = None
    reply_watchdog: Any = None
    next_decode_index: int = 1
    next_transmit_index: int = -1
    was_transmitting: bool = False
    dxcc: DxccLookup = field(default_factory=DxccLookup)
    call_grids: dict[str, str] = field(default_factory=dict)
    adif: AdifIndex = field(init=False)

    def __post_init__(self) -> None:
        self.adif = AdifIndex(self.dxcc)

    def snapshot(self) -> dict[str, Any]:
        return {
            "remote": self.remote.to_json(),
            "server_time": utc_now(),
            "status": self.status,
            "decodes": list(self.decodes),
            "transmits": list(self.transmits),
        }

    def clear_activity(self) -> None:
        self.decodes.clear()
        self.transmits.clear()
        self.call_grids.clear()

    def update_remote(self, msg: protocol.Message, addr: tuple[str, int]) -> None:
        self.remote.id = msg.id
        self.remote.host = addr[0]
        self.remote.port = addr[1]
        self.remote.schema = max(1, min(protocol.SCHEMA, int(msg.schema)))
        self.remote.last_seen = utc_now()
        if msg.type == protocol.MessageType.Heartbeat:
            fields = msg.fields
            maximum_schema = int(fields.get("maximum_schema_number") or 2)
            self.remote.schema = max(1, min(protocol.SCHEMA, maximum_schema))
            self.remote.version = str(fields.get("version") or "")
            self.remote.revision = str(fields.get("revision") or "")

    def add_decode(self, msg: protocol.Message) -> dict[str, Any]:
        item = dict(msg.fields)
        item["index"] = self.next_decode_index
        item["id"] = msg.id
        item["received_at"] = utc_now()
        self._add_dxcc(item)
        self.next_decode_index += 1
        self.decodes.append(item)
        return item

    def _add_dxcc(self, item: dict[str, Any]) -> None:
        call = extract_decode_callsign(str(item.get("message") or ""), str(self.status.get("de_call") or ""))
        if call:
            item["dxcc_call"] = call
        lookup_call = "" if call == "UNKNOWN" else call
        match = self.dxcc.lookup(lookup_call) if lookup_call else None
        if match:
            item["dxcc_prefix"] = match.prefix
            item["dxcc_entity"] = match.entity
            item["dxcc_label"] = match.label
        grid = extract_decode_grid(str(item.get("message") or ""))
        if lookup_call and grid:
            self.call_grids[lookup_call] = grid
        lookup_grid = grid or self.call_grids.get(lookup_call, "")
        if lookup_grid:
            item["worked_grid4"] = lookup_grid
        if self.adif.has_data:
            current_band = band_from_hz(self.status.get("dial_frequency"))
            worked = worked_status_json(self.adif.lookup(call=lookup_call, grid=lookup_grid, dxcc=match, frequency_hz=self.status.get("dial_frequency")))
            if not current_band:
                worked.pop("worked_call_band", None)
                worked.pop("worked_grid_band", None)
                worked.pop("worked_dxcc_band", None)
            if not lookup_call:
                worked.pop("worked_call", None)
                worked.pop("worked_call_band", None)
            if not lookup_grid or not self.adif.has_grid_data:
                worked.pop("worked_grid", None)
                worked.pop("worked_grid_band", None)
            if not match:
                worked.pop("worked_dxcc", None)
                worked.pop("worked_dxcc_band", None)
            if not self.adif.has_dxcc_data:
                worked.pop("worked_dxcc", None)
                worked.pop("worked_dxcc_band", None)
            item.update(worked)

    def get_decode(self, index: int) -> dict[str, Any] | None:
        for decode in self.decodes:
            if int(decode.get("index", -1)) == index:
                return decode
        return None

    def transmit_activity(self, status: dict[str, Any]) -> dict[str, Any] | None:
        transmitting = bool(status.get("transmitting"))
        if not transmitting:
            self.was_transmitting = False
            return None
        if self.was_transmitting:
            return None
        self.was_transmitting = True
        received_at = utc_now()
        item = {
            "index": self.next_transmit_index,
            "id": "server-tx",
            "received_at": received_at,
            "new": True,
            "time": received_at[11:23],
            "mode": str(status.get("mode") or ""),
            "message": transmit_message(status),
            "low_confidence": False,
            "off_air": False,
        }
        self.next_transmit_index -= 1
        self.transmits.append(item)
        return item

    def add_debug_event(
        self,
        direction: str,
        data: bytes,
        remote: tuple[str, int] | None,
        message: protocol.Message | None = None,
        error: str | None = None,
    ) -> dict[str, Any]:
        event = {
            "time": utc_now(),
            "direction": direction,
            "remote": f"{remote[0]}:{remote[1]}" if remote else "",
            "size": len(data),
            "hex": protocol.hexdump(data),
            "message": protocol.clean_json(message.to_json()) if message else None,
            "error": error,
        }
        self.debug_events.append(event)
        return event


def extract_adif_call(adif: str) -> str:
    match = CALL_RE.search(adif or "")
    if not match:
        return ""
    length = int(match.group(1))
    return match.group(2)[:length].upper()


def transmit_message(status: dict[str, Any]) -> str:
    explicit = str(status.get("tx_message") or "").strip()
    if explicit:
        return explicit
    own_call = str(status.get("de_call") or "").strip().upper()
    own_grid = str(status.get("de_grid") or "").strip().upper()[:4]
    dx_call = str(status.get("dx_call") or "").strip().upper()
    if not dx_call:
        return f"CQ {own_call} {own_grid}" if own_call and own_grid else "CQ"
    return f"{dx_call} {own_call or 'UNKNOWN'} UNKNOWN"


def extract_decode_callsign(message: str, own_call: str) -> str:
    words = [word for word in message.upper().split() if word]
    own = own_call.upper()
    if words and words[0] == "CQ":
        for index, word in enumerate(words):
            if index > 0 and is_call(word) and word != own:
                return word
        return ""
    if len(words) >= 2:
        if is_hashed_call(words[1]):
            return "UNKNOWN"
        return words[1] if is_call(words[1]) and words[1] != own else ""
    return ""


def is_calling_own(message: str, own_call: str) -> bool:
    own = own_call.upper().strip()
    words = message.upper().split()
    return bool(own and words and words[0] == own)


def is_repliable(message: str) -> bool:
    return any(word in {"CQ", "73", "RRR", "RR73"} for word in message.upper().split())


def extract_decode_grid(message: str) -> str:
    for word in message.upper().split():
        if is_grid(word):
            return word[:4]
    return ""


def is_call(word: str) -> bool:
    return word not in {"RR73", "RRR"} and not is_grid(word) and bool(re.match(r"^[A-Z0-9/]{3,12}$", word)) and any(ch.isdigit() for ch in word) and any(ch.isalpha() for ch in word)


def is_hashed_call(word: str) -> bool:
    return bool(re.match(r"^<[^>]+>$", word))


def is_grid(word: str) -> bool:
    return word != "RR73" and bool(GRID_RE.match(word))
