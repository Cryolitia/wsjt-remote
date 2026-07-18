from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
import importlib.util
import logging
from pathlib import Path
import sys
from types import MappingProxyType, ModuleType
from typing import Any

from . import protocol
from .adif_index import band_from_adif_freq, band_from_hz, dxcc_key, normalize_grid
from .dxcc import normalize_call
from .state import AppState, extract_decode_callsign, extract_decode_grid, is_call, is_calling_own, is_cq_transmit_status, is_grid, is_repliable


logger = logging.getLogger(__name__)

WORKED_FIELDS = (
    "worked_call",
    "worked_call_band",
    "worked_grid",
    "worked_grid_band",
    "worked_dxcc",
    "worked_dxcc_band",
    "worked_grid4",
)
CONTRIBUTION_FIELDS = (
    "plugin_color",
    "plugin_note",
)
DEFAULT_ORDER = 999


@dataclass(frozen=True, slots=True)
class AdifSnapshot:
    qso_count: int
    worked_calls: frozenset[str]
    worked_calls_by_band: MappingProxyType[str, frozenset[str]]
    worked_grids: frozenset[str]
    worked_grids_by_band: MappingProxyType[str, frozenset[str]]
    worked_dxcc: frozenset[str]
    worked_dxcc_by_band: MappingProxyType[str, frozenset[str]]


class PluginContext:
    def __init__(self, manager: PluginManager, adif: AdifSnapshot):
        self._manager = manager
        self._state = manager.state
        self.adif = adif

    @property
    def status(self) -> MappingProxyType[str, Any]:
        return MappingProxyType(dict(self._state.status))

    @property
    def remote(self) -> MappingProxyType[str, Any]:
        return MappingProxyType(self._state.remote.to_json())

    def now(self) -> str:
        return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")

    def extract_callsign(self, message: str) -> str:
        return extract_decode_callsign(message, str(self._state.status.get("de_call") or ""))

    def extract_grid(self, message: str) -> str:
        return extract_decode_grid(message)

    def is_call(self, word: str) -> bool:
        return is_call(word.upper())

    def is_grid(self, word: str) -> bool:
        return is_grid(word.upper())

    def is_cq(self, message: str) -> bool:
        return any(word == "CQ" for word in message.upper().split())

    def is_repliable(self, message: str) -> bool:
        return is_repliable(message)

    def is_calling_own(self, message: str) -> bool:
        return is_calling_own(message, str(self._state.status.get("de_call") or ""))

    def normalize_call(self, call: str) -> str:
        return normalize_call(call)

    def normalize_grid(self, grid: str) -> str:
        return normalize_grid(grid)

    def current_band(self) -> str:
        return band_from_hz(self._state.status.get("dial_frequency"))

    def band_from_hz(self, freq: int | float | None) -> str:
        return band_from_hz(freq)

    def band_from_adif_freq(self, freq: str) -> str:
        return band_from_adif_freq(freq)

    def lookup_dxcc(self, call: str) -> Any:
        return self._state.dxcc.lookup(call)

    def dxcc_key_for_call(self, call: str) -> str:
        return dxcc_key(self._state.dxcc.lookup(call))

    def worked_call(self, call: str, band: str | None = None) -> bool:
        call_key = normalize_call(call)
        if not call_key:
            return False
        if band:
            return call_key in self.adif.worked_calls_by_band.get(band, frozenset())
        return call_key in self.adif.worked_calls

    def worked_grid(self, grid: str, band: str | None = None) -> bool:
        grid_key = normalize_grid(grid)
        if not grid_key:
            return False
        if band:
            return grid_key in self.adif.worked_grids_by_band.get(band, frozenset())
        return grid_key in self.adif.worked_grids

    def worked_dxcc(self, call_or_key: str, band: str | None = None) -> bool:
        value = call_or_key.strip()
        key = value if "|" in value else self.dxcc_key_for_call(value.upper())
        if not key:
            return False
        if band:
            return key in self.adif.worked_dxcc_by_band.get(band, frozenset())
        return key in self.adif.worked_dxcc

    def reply(self, decode: dict[str, Any], modifiers: int = 0) -> None:
        self._manager.reply(decode, modifiers=modifiers)


@dataclass(slots=True)
class Plugin:
    name: str
    module: ModuleType
    order: int


class PluginManager:
    def __init__(self, state: AppState, decode_grace: float = 1.0):
        self.state = state
        self.decode_grace = max(0.0, decode_grace)
        self.plugins: list[Plugin] = []
        self.broadcaster: Any = None
        self._batch_decodes: dict[str, list[dict[str, Any]]] = {}
        self._batch_tasks: dict[str, asyncio.Task[None]] = {}
        self._finalized_slots: set[str] = set()

    @property
    def enabled(self) -> bool:
        return bool(self.plugins)

    def load_dir(self, directory: Path) -> None:
        if not directory.exists() or not directory.is_dir():
            raise FileNotFoundError(f"plugin directory not found: {directory}")
        directory_text = str(directory)
        if directory_text not in sys.path:
            sys.path.insert(0, directory_text)
        for path in sorted(directory.glob("*.py")):
            if path.name.startswith("_"):
                continue
            self._load_file(path)
        self._warn_duplicate_orders()
        self.plugins.sort(key=lambda plugin: (plugin.order, plugin.name))
        logger.info("loaded %d plugin(s) from %s", len(self.plugins), directory)

    def on_start(self) -> None:
        self._call("on_start")

    def on_stop(self) -> None:
        self.cancel_batches()
        self._call("on_stop")

    def on_status(self, status: dict[str, Any]) -> None:
        self._call("on_status", status)

    def on_decode(self, decode: dict[str, Any]) -> None:
        worked = {field: decode[field] for field in WORKED_FIELDS if field in decode}
        removed = [field for field in WORKED_FIELDS if field not in decode]
        protected = {field: decode[field] for field in CONTRIBUTION_FIELDS if field in decode}
        protected_removed = [field for field in CONTRIBUTION_FIELDS if field not in decode]
        contribution = self._decode_contribution(decode)
        for field in removed:
            decode.pop(field, None)
        decode.update(worked)
        for field in protected_removed:
            decode.pop(field, None)
        decode.update(protected)
        decode.update(contribution)
        self._add_decode_to_batch(decode)

    def on_logged_adif(self, raw_adif: str, indexed_count: int) -> None:
        self._call("on_logged_adif", raw_adif, indexed_count)

    def cancel_batches(self) -> None:
        for task in self._batch_tasks.values():
            task.cancel()
        self._batch_tasks.clear()
        self._batch_decodes.clear()
        self._finalized_slots.clear()

    def reply(self, decode: dict[str, Any], modifiers: int = 0) -> None:
        address = self.state.remote.address()
        if self.state.udp_transport is None or address is None:
            raise RuntimeError("WSJT-X is not connected")
        data = protocol.build_reply(self.state.remote.id, decode, modifiers, self.state.remote.schema)
        message = protocol.parse_message(data)
        self.state.udp_transport.sendto(data, address)
        event = self.state.add_debug_event("tx", data, address, message)
        if self.state.reply_watchdog:
            self.state.reply_watchdog.reset_if_armed("plugin_reply")
        logger.info("plugin reply decode_index=%s message=%r", decode.get("index"), decode.get("message"))
        if self.broadcaster:
            asyncio.create_task(self.broadcaster({"event": "debug", "data": event}))
            callsign = extract_decode_callsign(str(decode.get("message") or ""), str(self.state.status.get("de_call") or ""))
            if callsign and callsign != "UNKNOWN":
                asyncio.create_task(
                    self.broadcaster(
                        {
                            "event": "watch",
                            "data": {"action": "add", "callsign": callsign, "decode": decode, "source": "plugin_reply", "auto": True},
                        }
                    )
                )

    def _load_file(self, path: Path) -> None:
        name = path.stem
        spec = importlib.util.spec_from_file_location(f"wsjtx_remote_plugin_{name}", path)
        if not spec or not spec.loader:
            logger.warning("failed to load plugin %s: invalid module spec", path)
            return
        module = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(module)
        except Exception as exc:
            logger.warning("failed to load plugin %s: %s", path, exc)
            return
        self.plugins.append(Plugin(name=name, module=module, order=self._plugin_order(name, module)))

    def _plugin_order(self, name: str, module: ModuleType) -> int:
        if not hasattr(module, "ORDER"):
            logger.warning("plugin %s has no ORDER; defaulting to %d", name, DEFAULT_ORDER)
            return DEFAULT_ORDER
        value = getattr(module, "ORDER")
        if isinstance(value, bool):
            logger.warning("plugin %s ORDER=%r is invalid; defaulting to %d", name, value, DEFAULT_ORDER)
            return DEFAULT_ORDER
        try:
            return int(value)
        except (TypeError, ValueError):
            logger.warning("plugin %s ORDER=%r is invalid; defaulting to %d", name, value, DEFAULT_ORDER)
            return DEFAULT_ORDER

    def _warn_duplicate_orders(self) -> None:
        seen: dict[int, str] = {}
        for plugin in self.plugins:
            existing = seen.get(plugin.order)
            if existing is not None:
                logger.warning(
                    "duplicate plugin ORDER %d: %s and %s; behavior is undefined",
                    plugin.order,
                    existing,
                    plugin.name,
                )
                continue
            seen[plugin.order] = plugin.name

    def _decode_contribution(self, decode: dict[str, Any]) -> dict[str, Any]:
        if not self.plugins:
            return {}
        ctx = PluginContext(self, self._adif_snapshot())
        note = ""
        strong_color = ""
        weak_color = ""
        for plugin in self.plugins:
            fn = getattr(plugin.module, "on_decode", None)
            if not callable(fn):
                continue
            try:
                result = fn(ctx, dict(decode))
            except Exception as exc:
                logger.warning("plugin %s on_decode failed: %s", plugin.name, exc)
                continue
            if not isinstance(result, dict):
                continue
            candidate_note = str(result.get("plugin_note") or "").strip()
            if not note and candidate_note:
                note = candidate_note
            color = str(result.get("plugin_color") or "").strip()
            if not color:
                continue
            if color.endswith("-soft"):
                if not weak_color:
                    weak_color = color
            elif not strong_color:
                strong_color = color
        merged = {}
        if note:
            merged["plugin_note"] = note
        if strong_color:
            merged["plugin_color"] = strong_color
        elif weak_color and not self._has_strong_core_highlight(decode):
            merged["plugin_color"] = weak_color
        return merged

    def _has_strong_core_highlight(self, decode: dict[str, Any]) -> bool:
        return bool(
            (decode.get("dxcc_entity") and decode.get("worked_dxcc") is False)
            or (decode.get("worked_grid4") and decode.get("worked_grid") is False)
            or decode.get("worked_call") is False
        )

    def _call(self, hook: str, *args: Any) -> list[Any]:
        if not self.plugins:
            return []
        ctx = PluginContext(self, self._adif_snapshot())
        results = []
        for plugin in self.plugins:
            fn = getattr(plugin.module, hook, None)
            if not callable(fn):
                continue
            try:
                results.append(fn(ctx, *args))
            except Exception as exc:
                logger.warning("plugin %s %s failed: %s", plugin.name, hook, exc)
        return results

    def _adif_snapshot(self) -> AdifSnapshot:
        adif = self.state.adif
        with adif._lock:
            return AdifSnapshot(
                qso_count=adif.qso_count,
                worked_calls=frozenset(adif.worked_calls),
                worked_calls_by_band=MappingProxyType({band: frozenset(calls) for band, calls in adif.worked_calls_by_band.items()}),
                worked_grids=frozenset(adif.worked_grids),
                worked_grids_by_band=MappingProxyType({band: frozenset(grids) for band, grids in adif.worked_grids_by_band.items()}),
                worked_dxcc=frozenset(adif.worked_dxcc),
                worked_dxcc_by_band=MappingProxyType({band: frozenset(values) for band, values in adif.worked_dxcc_by_band.items()}),
            )

    def _add_decode_to_batch(self, decode: dict[str, Any]) -> None:
        slot = str(decode.get("time") or "")[:8]
        if not slot:
            return
        self._batch_decodes.setdefault(slot, []).append(decode)
        task = self._batch_tasks.pop(slot, None)
        if task:
            task.cancel()
        self._batch_tasks[slot] = asyncio.create_task(self._finalize_batch_later(slot))

    async def _finalize_batch_later(self, slot: str) -> None:
        try:
            await asyncio.sleep(self.decode_grace)
            self._finalize_batch(slot)
        except asyncio.CancelledError:
            raise
        finally:
            current = self._batch_tasks.get(slot)
            if current is asyncio.current_task():
                self._batch_tasks.pop(slot, None)

    def _finalize_batch(self, slot: str) -> None:
        if slot in self._finalized_slots:
            return
        decodes = list(self._batch_decodes.pop(slot, []))
        if not decodes:
            return
        self._finalized_slots.add(slot)
        if not self.state.auto_reply_enabled:
            logger.debug("auto reply skipped slot=%s reason=disabled", slot)
            return
        if not self._can_auto_reply():
            logger.info("auto reply skipped slot=%s reason=tx-busy-non-cq", slot)
            return
        ctx = PluginContext(self, self._adif_snapshot())
        candidates: list[tuple[int, str, dict[str, Any]]] = []
        for plugin in self.plugins:
            fn = getattr(plugin.module, "on_decode_batch", None)
            if not callable(fn):
                continue
            try:
                target = fn(ctx, decodes)
            except Exception as exc:
                logger.warning("plugin %s on_decode_batch failed: %s", plugin.name, exc)
                continue
            decode = self._resolve_decode(target, decodes)
            if decode:
                candidates.append((plugin.order, plugin.name, decode))
        if not candidates:
            return
        if not self.state.auto_reply_enabled:
            logger.info("auto reply skipped slot=%s reason=disabled-before-send candidates=%s", slot, [(order, name, decode.get("index")) for order, name, decode in candidates])
            return
        if not self._can_auto_reply():
            logger.info("auto reply skipped slot=%s reason=tx-busy-non-cq-before-send candidates=%s", slot, [(order, name, decode.get("index")) for order, name, decode in candidates])
            return
        order, name, decode = min(candidates, key=lambda item: (item[0], item[1]))
        logger.info("auto reply selected plugin=%s order=%s decode_index=%s candidates=%s", name, order, decode.get("index"), [(candidate_order, candidate_name, candidate_decode.get("index")) for candidate_order, candidate_name, candidate_decode in candidates])
        try:
            self.reply(decode)
        except Exception as exc:
            logger.warning("plugin %s reply failed: %s", name, exc)
            return
        self._notify_auto_reply_sent(name, decode)

    def _notify_auto_reply_sent(self, plugin_name: str, decode: dict[str, Any]) -> None:
        plugin = next((candidate for candidate in self.plugins if candidate.name == plugin_name), None)
        if plugin is None:
            return
        fn = getattr(plugin.module, "on_auto_reply_sent", None)
        if not callable(fn):
            return
        try:
            fn(PluginContext(self, self._adif_snapshot()), decode)
        except Exception as exc:
            logger.warning("plugin %s on_auto_reply_sent failed: %s", plugin.name, exc)

    def _can_auto_reply(self) -> bool:
        status = self.state.status
        tx_idle = not bool(status.get("tx_enabled")) and not bool(status.get("transmitting"))
        return tx_idle or is_cq_transmit_status(status)

    def _resolve_decode(self, target: Any, decodes: list[dict[str, Any]]) -> dict[str, Any] | None:
        if target is None:
            return None
        if isinstance(target, dict):
            return target if target in decodes else None
        try:
            index = int(target)
        except (TypeError, ValueError):
            return None
        return next((decode for decode in decodes if int(decode.get("index", -1)) == index), None)
