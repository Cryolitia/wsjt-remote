from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
import re
from typing import Any

from .adif_index import AdifIndex, worked_status_json
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
    debug_events: deque[dict[str, Any]] = field(default_factory=lambda: deque(maxlen=500))
    websockets: set[Any] = field(default_factory=set)
    udp_transport: Any = None
    next_decode_index: int = 1
    dxcc: DxccLookup = field(default_factory=DxccLookup)
    adif: AdifIndex = field(init=False)

    def __post_init__(self) -> None:
        self.adif = AdifIndex(self.dxcc)

    def snapshot(self) -> dict[str, Any]:
        return {
            "remote": self.remote.to_json(),
            "status": self.status,
            "decodes": list(self.decodes),
        }

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
        match = self.dxcc.lookup(call) if call else None
        if match:
            item["dxcc_prefix"] = match.prefix
            item["dxcc_entity"] = match.entity
            item["dxcc_label"] = match.label
        grid = extract_decode_grid(str(item.get("message") or ""))
        if grid:
            item["worked_grid4"] = grid
        if self.adif.has_data:
            item.update(worked_status_json(self.adif.lookup(call=call, grid=grid, dxcc=match, frequency_hz=self.status.get("dial_frequency"))))

    def get_decode(self, index: int) -> dict[str, Any] | None:
        for decode in self.decodes:
            if int(decode.get("index", -1)) == index:
                return decode
        return None

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


def extract_decode_callsign(message: str, own_call: str) -> str:
    words = [word for word in message.upper().split() if word]
    if words and words[0] == "CQ":
        for index, word in enumerate(words):
            if index > 0 and is_call(word):
                return word
        return ""
    calls = [word for word in words if is_call(word)]
    if len(calls) >= 2:
        return calls[1]
    own = own_call.upper()
    return next((word for word in calls if word != own), "")


def extract_decode_grid(message: str) -> str:
    for word in message.upper().split():
        if is_grid(word):
            return word[:4]
    return ""


def is_call(word: str) -> bool:
    return not is_grid(word) and bool(re.match(r"^[A-Z0-9/]{3,12}$", word)) and any(ch.isdigit() for ch in word) and any(ch.isalpha() for ch in word)


def is_grid(word: str) -> bool:
    return bool(GRID_RE.match(word))
