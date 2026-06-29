from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
import re
from typing import Any

from . import protocol


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


CALL_RE = re.compile(r"<CALL:(\d+)>\s*([^<\s]+)", re.IGNORECASE)


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
        self.next_decode_index += 1
        self.decodes.append(item)
        return item

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
