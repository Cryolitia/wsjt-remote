from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
import logging
from pathlib import Path
import re
import threading
from typing import Any, Iterable

from .dxcc import DxccLookup, DxccMatch, normalize_call


logger = logging.getLogger(__name__)

ADIF_TAG_RE = re.compile(r"<([A-Z0-9_]+)(?::(\d+)(?::[^>]*)?)?>", re.IGNORECASE)
GRID_RE = re.compile(r"^[A-R]{2}\d{2}")

BANDS_MHZ: tuple[tuple[float, float, str], ...] = (
    (1.8, 2.0, "160m"),
    (3.5, 4.0, "80m"),
    (5.0, 5.5, "60m"),
    (7.0, 7.3, "40m"),
    (10.1, 10.15, "30m"),
    (14.0, 14.35, "20m"),
    (18.068, 18.168, "17m"),
    (21.0, 21.45, "15m"),
    (24.89, 24.99, "12m"),
    (28.0, 29.7, "10m"),
    (50.0, 54.0, "6m"),
    (144.0, 148.0, "2m"),
)


@dataclass(frozen=True, slots=True)
class WorkedStatus:
    call: bool = False
    call_band: bool = False
    grid: bool = False
    grid_band: bool = False
    dxcc: bool = False
    dxcc_band: bool = False


@dataclass(slots=True)
class AdifIndex:
    dxcc: DxccLookup
    worked_calls: set[str] = field(default_factory=set)
    worked_calls_by_band: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    worked_grids: set[str] = field(default_factory=set)
    worked_grids_by_band: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    worked_dxcc: set[str] = field(default_factory=set)
    worked_dxcc_by_band: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    qso_count: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock)

    @property
    def has_data(self) -> bool:
        with self._lock:
            return self.qso_count > 0

    def load_file(self, path: Path) -> None:
        if not path.exists():
            logger.warning("ADIF file does not exist: %s", path)
            return
        text = path.read_text(encoding="utf-8", errors="replace")
        count = self.add_adif(text)
        logger.info(
            "loaded ADIF index path=%s records=%d calls=%d grids=%d dxcc=%d",
            path,
            count,
            len(self.worked_calls),
            len(self.worked_grids),
            len(self.worked_dxcc),
        )

    def add_adif(self, adif: str) -> int:
        indexed = 0
        for record in parse_adif_records(adif):
            if self.add_record(record):
                indexed += 1
        return indexed

    def add_record(self, record: dict[str, str]) -> bool:
        if record.get("MODE", "").strip().upper() != "FT8":
            return False
        call = normalize_call(record.get("CALL", ""))
        if not call:
            return False
        grid = normalize_grid(record.get("GRIDSQUARE", ""))
        band = band_from_adif_freq(record.get("FREQ", ""))
        dxcc = self.dxcc.lookup(call)
        dxcc_key_value = dxcc_key(dxcc) if dxcc else ""

        with self._lock:
            self.qso_count += 1
            self.worked_calls.add(call)
            if band:
                self.worked_calls_by_band.setdefault(band, set()).add(call)
            if grid:
                self.worked_grids.add(grid)
                if band:
                    self.worked_grids_by_band.setdefault(band, set()).add(grid)
            if dxcc_key_value:
                self.worked_dxcc.add(dxcc_key_value)
                if band:
                    self.worked_dxcc_by_band.setdefault(band, set()).add(dxcc_key_value)

        logger.debug("adif indexed call=%s grid=%s band=%s dxcc=%s", call, grid, band, dxcc_key_value)
        return True

    def lookup(self, call: str = "", grid: str = "", dxcc: DxccMatch | None = None, frequency_hz: int | float | None = None) -> WorkedStatus:
        call_key = normalize_call(call)
        grid_key = normalize_grid(grid)
        band = band_from_hz(frequency_hz)
        dxcc_key_value = dxcc_key(dxcc) if dxcc else ""

        with self._lock:
            worked_call = bool(call_key and call_key in self.worked_calls)
            worked_grid = bool(grid_key and grid_key in self.worked_grids)
            worked_dxcc = bool(dxcc_key_value and dxcc_key_value in self.worked_dxcc)
            worked_call_band = bool(band and call_key and call_key in self.worked_calls_by_band.get(band, set()))
            worked_grid_band = bool(band and grid_key and grid_key in self.worked_grids_by_band.get(band, set()))
            worked_dxcc_band = bool(band and dxcc_key_value and dxcc_key_value in self.worked_dxcc_by_band.get(band, set()))

        return WorkedStatus(
            call=worked_call,
            call_band=worked_call_band,
            grid=worked_grid,
            grid_band=worked_grid_band,
            dxcc=worked_dxcc,
            dxcc_band=worked_dxcc_band,
        )


def parse_adif_records(text: str) -> Iterable[dict[str, str]]:
    record: dict[str, str] = {}
    pos = 0
    while True:
        match = ADIF_TAG_RE.search(text, pos)
        if not match:
            break
        tag = match.group(1).upper()
        if tag == "EOH":
            record = {}
            pos = match.end()
            continue
        if tag == "EOR":
            if record:
                yield record
            record = {}
            pos = match.end()
            continue
        length_text = match.group(2)
        if length_text is None:
            pos = match.end()
            continue
        length = int(length_text)
        value_start = match.end()
        value_end = value_start + length
        if value_end > len(text):
            break
        value = text[value_start:value_end]
        pos = value_end
        record[tag] = value.strip()
    if record:
        yield record


def normalize_grid(grid: str) -> str:
    value = grid.strip().upper()[:4]
    return value if GRID_RE.match(value) else ""


def band_from_adif_freq(freq: str) -> str:
    try:
        mhz = float(freq.strip())
    except (TypeError, ValueError):
        return ""
    return band_from_mhz(mhz)


def band_from_hz(freq: int | float | None) -> str:
    if not freq:
        return ""
    try:
        return band_from_mhz(float(freq) / 1_000_000)
    except (TypeError, ValueError):
        return ""


def band_from_mhz(mhz: float) -> str:
    for start, end, band in BANDS_MHZ:
        if start <= mhz <= end:
            return band
    return ""


def dxcc_key(match: DxccMatch | None) -> str:
    return f"{match.prefix}|{match.entity}" if match else ""


def worked_status_json(status: WorkedStatus) -> dict[str, bool]:
    return {
        "worked_call": status.call,
        "worked_call_band": status.call_band,
        "worked_grid": status.grid,
        "worked_grid_band": status.grid_band,
        "worked_dxcc": status.dxcc,
        "worked_dxcc_band": status.dxcc_band,
    }
