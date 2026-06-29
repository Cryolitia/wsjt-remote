from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
import math
import struct
from typing import Any


MAGIC = 0xADBCCBDA
SCHEMA = 3
UINT32_MAX = 0xFFFFFFFF


class MessageType(IntEnum):
    Heartbeat = 0
    Status = 1
    Decode = 2
    Clear = 3
    Reply = 4
    QSOLogged = 5
    Close = 6
    Replay = 7
    HaltTx = 8
    FreeText = 9
    WSPRDecode = 10
    Location = 11
    LoggedADIF = 12
    HighlightCallsign = 13
    SwitchConfiguration = 14
    Configure = 15
    AnnotationInfo = 16


@dataclass(slots=True)
class Message:
    schema: int
    type: MessageType | int
    id: str
    fields: dict[str, Any]
    raw_type: int

    def to_json(self) -> dict[str, Any]:
        type_name = self.type.name if isinstance(self.type, MessageType) else f"Unknown({self.raw_type})"
        return {
            "schema": self.schema,
            "type": type_name,
            "type_value": self.raw_type,
            "id": self.id,
            "fields": self.fields,
        }


class ProtocolError(ValueError):
    pass


class Reader:
    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0

    def remaining(self) -> int:
        return len(self.data) - self.pos

    def _take(self, size: int) -> bytes:
        if self.pos + size > len(self.data):
            raise ProtocolError("message ended unexpectedly")
        chunk = self.data[self.pos : self.pos + size]
        self.pos += size
        return chunk

    def u8(self) -> int:
        return self._take(1)[0]

    def u32(self) -> int:
        return struct.unpack(">I", self._take(4))[0]

    def i32(self) -> int:
        return struct.unpack(">i", self._take(4))[0]

    def u64(self) -> int:
        return struct.unpack(">Q", self._take(8))[0]

    def boolean(self) -> bool:
        return self.u8() != 0

    def double(self) -> float:
        return struct.unpack(">d", self._take(8))[0]

    def utf8(self) -> str | None:
        size = self.u32()
        if size == UINT32_MAX:
            return None
        return self._take(size).decode("utf-8", errors="replace")

    def qtime(self) -> str:
        msecs = self.u32()
        if msecs == UINT32_MAX:
            return ""
        hours, rem = divmod(msecs, 3_600_000)
        minutes, rem = divmod(rem, 60_000)
        seconds, ms = divmod(rem, 1_000)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{ms:03d}"


class Writer:
    def __init__(self):
        self.parts: list[bytes] = []

    def bytes(self) -> bytes:
        return b"".join(self.parts)

    def u8(self, value: int) -> None:
        self.parts.append(struct.pack(">B", value & 0xFF))

    def u32(self, value: int) -> None:
        self.parts.append(struct.pack(">I", value & UINT32_MAX))

    def i32(self, value: int) -> None:
        self.parts.append(struct.pack(">i", value))

    def u64(self, value: int) -> None:
        self.parts.append(struct.pack(">Q", value))

    def boolean(self, value: bool) -> None:
        self.u8(1 if value else 0)

    def double(self, value: float) -> None:
        self.parts.append(struct.pack(">d", float(value)))

    def utf8(self, value: str | None) -> None:
        if value is None:
            self.u32(UINT32_MAX)
            return
        data = value.encode("utf-8")
        self.u32(len(data))
        self.parts.append(data)

    def qtime(self, value: str) -> None:
        if not value:
            self.u32(UINT32_MAX)
            return
        main, dot, millis = value.partition(".")
        parts = main.split(":")
        if len(parts) != 3:
            raise ProtocolError(f"invalid QTime: {value}")
        h, m, s = (int(part) for part in parts)
        ms = int((millis + "000")[:3]) if dot else 0
        self.u32(((h * 60 + m) * 60 + s) * 1000 + ms)


def parse_message(data: bytes) -> Message:
    r = Reader(data)
    magic = r.u32()
    if magic != MAGIC:
        raise ProtocolError(f"invalid magic 0x{magic:08x}")
    schema = r.u32()
    if schema < 1 or schema > SCHEMA:
        raise ProtocolError(f"unsupported schema {schema}")
    raw_type = r.u32()
    try:
        msg_type: MessageType | int = MessageType(raw_type)
    except ValueError:
        msg_type = raw_type
    msg_id = r.utf8() or ""
    fields = _parse_fields(r, msg_type)
    return Message(schema=schema, type=msg_type, id=msg_id, fields=fields, raw_type=raw_type)


def _parse_fields(r: Reader, msg_type: MessageType | int) -> dict[str, Any]:
    if not isinstance(msg_type, MessageType):
        return {"unparsed_bytes": r.remaining()}
    if msg_type == MessageType.Heartbeat:
        fields: dict[str, Any] = {}
        if r.remaining() >= 4:
            fields["maximum_schema_number"] = r.u32()
        if r.remaining() >= 4:
            fields["version"] = r.utf8()
        if r.remaining() >= 4:
            fields["revision"] = r.utf8()
        return fields
    if msg_type == MessageType.Status:
        return _parse_status(r)
    if msg_type == MessageType.Decode:
        return _parse_decode(r)
    if msg_type == MessageType.Clear:
        return {"window": r.u8()} if r.remaining() else {}
    if msg_type == MessageType.Close:
        return {}
    if msg_type == MessageType.WSPRDecode:
        return {
            "new": r.boolean(),
            "time": r.qtime(),
            "snr": r.i32(),
            "delta_time": r.double(),
            "frequency": r.u64(),
            "drift": r.i32(),
            "callsign": r.utf8(),
            "grid": r.utf8(),
            "power": r.i32(),
            "off_air": r.boolean(),
        }
    if msg_type == MessageType.LoggedADIF:
        return {"adif": r.utf8() or ""}
    return {"unparsed_bytes": r.remaining()}


def _parse_decode(r: Reader) -> dict[str, Any]:
    return {
        "new": r.boolean(),
        "time": r.qtime(),
        "snr": r.i32(),
        "delta_time": r.double(),
        "delta_frequency": r.u32(),
        "mode": r.utf8(),
        "message": r.utf8(),
        "low_confidence": r.boolean(),
        "off_air": r.boolean(),
    }


def _parse_status(r: Reader) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "dial_frequency": r.u64(),
        "mode": r.utf8(),
        "dx_call": r.utf8(),
        "report": r.utf8(),
        "tx_mode": r.utf8(),
        "tx_enabled": r.boolean(),
        "transmitting": r.boolean(),
        "decoding": r.boolean(),
        "rx_df": r.u32(),
        "tx_df": r.u32(),
        "de_call": r.utf8(),
        "de_grid": r.utf8(),
        "dx_grid": r.utf8(),
    }
    optional_fields = (
        ("tx_watchdog", r.boolean, 1),
        ("sub_mode", r.utf8, 4),
        ("fast_mode", r.boolean, 1),
        ("special_operation_mode", r.u8, 1),
        ("frequency_tolerance", r.u32, 4),
        ("tr_period", r.u32, 4),
        ("configuration_name", r.utf8, 4),
        ("tx_message", r.utf8, 4),
    )
    for name, read, minimum_size in optional_fields:
        if r.remaining() < minimum_size:
            break
        fields[name] = read()
    return fields


def build_message(msg_type: MessageType, msg_id: str, schema: int = SCHEMA) -> Writer:
    if schema < 1 or schema > SCHEMA:
        raise ProtocolError(f"unsupported schema {schema}")
    w = Writer()
    w.u32(MAGIC)
    w.u32(schema)
    w.u32(int(msg_type))
    w.utf8(msg_id)
    return w


def build_heartbeat(msg_id: str, schema: int = SCHEMA, version: str = "wsjtx-remote", revision: str = "") -> bytes:
    w = build_message(MessageType.Heartbeat, msg_id, schema)
    w.u32(SCHEMA)
    w.utf8(version)
    w.utf8(revision)
    return w.bytes()


def build_free_text(msg_id: str, text: str, send: bool, schema: int = SCHEMA) -> bytes:
    w = build_message(MessageType.FreeText, msg_id, schema)
    w.utf8(text)
    w.boolean(send)
    return w.bytes()


def build_halt_tx(msg_id: str, auto_tx_only: bool = False, schema: int = SCHEMA) -> bytes:
    w = build_message(MessageType.HaltTx, msg_id, schema)
    w.boolean(auto_tx_only)
    return w.bytes()


def build_replay(msg_id: str, schema: int = SCHEMA) -> bytes:
    return build_message(MessageType.Replay, msg_id, schema).bytes()


def build_clear(msg_id: str, window: int = 0, schema: int = SCHEMA) -> bytes:
    w = build_message(MessageType.Clear, msg_id, schema)
    w.u8(window)
    return w.bytes()


def build_reply(msg_id: str, decode: dict[str, Any], modifiers: int = 0, schema: int = SCHEMA) -> bytes:
    w = build_message(MessageType.Reply, msg_id, schema)
    w.qtime(str(decode.get("time", "")))
    w.i32(int(decode.get("snr", 0)))
    w.double(float(decode.get("delta_time", 0.0)))
    w.u32(int(decode.get("delta_frequency", 0)))
    w.utf8(str(decode.get("mode", "")))
    w.utf8(str(decode.get("message", "")))
    w.boolean(bool(decode.get("low_confidence", False)))
    w.u8(modifiers)
    return w.bytes()


def build_configure(msg_id: str, fields: dict[str, Any], schema: int = SCHEMA) -> bytes:
    w = build_message(MessageType.Configure, msg_id, schema)
    w.utf8(str(fields.get("mode", "")))
    w.u32(int(fields.get("frequency_tolerance", UINT32_MAX)))
    w.utf8(str(fields.get("submode", "")))
    w.boolean(bool(fields.get("fast_mode", False)))
    w.u32(int(fields.get("tr_period", UINT32_MAX)))
    w.u32(int(fields.get("rx_df", UINT32_MAX)))
    w.utf8(str(fields.get("dx_call", "")))
    w.utf8(str(fields.get("dx_grid", "")))
    w.boolean(bool(fields.get("generate_messages", False)))
    return w.bytes()


def hexdump(data: bytes, max_bytes: int = 4096) -> str:
    shown = data[:max_bytes]
    text = " ".join(f"{byte:02x}" for byte in shown)
    if len(data) > max_bytes:
        return f"{text} ... ({len(data) - max_bytes} more bytes)"
    return text


def clean_json(value: Any) -> Any:
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    if isinstance(value, dict):
        return {str(k): clean_json(v) for k, v in value.items()}
    if isinstance(value, list):
        return [clean_json(v) for v in value]
    return value
