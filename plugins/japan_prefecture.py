"""Example plugin: show Japanese WAJA prefectures in the DXCC column.

The plugin downloads JJ1WTL's offline JA callbook and refreshes the cached CSV
every 30 days. It uses the callbook Prefecture field, not callsign-area guessing.
"""

from __future__ import annotations

import csv
from datetime import datetime
import logging
from pathlib import Path
import time
from urllib.error import URLError
from urllib.request import Request, urlopen


logger = logging.getLogger(__name__)

ORDER = 400

CALLBOOK_URL = "http://motobayashi.net/callbook/ever/20250913/offline-callbook-ja-20250913-en.csv"
CACHE_TTL_SECONDS = 30 * 24 * 60 * 60
CACHE_DIR = Path("/tmp/wsjt-remote/plugins/japan_prefecture")
CACHE_PATH = CACHE_DIR / "offline-callbook-ja-20250913-en.csv"

NEW_PREFECTURE_COLOR = "nord8"
BAND_PREFECTURE_COLOR = "nord8-soft"

PREFECTURES = {
    "Hokkaido": ("01", "北海道"),
    "Aomori": ("02", "青森"),
    "Iwate": ("03", "岩手"),
    "Akita": ("04", "秋田"),
    "Yamagata": ("05", "山形"),
    "Miyagi": ("06", "宫城"),
    "Fukushima": ("07", "福岛"),
    "Niigata": ("08", "新潟"),
    "Nagano": ("09", "长野"),
    "Tokyo": ("10", "东京"),
    "Kanagawa": ("11", "神奈川"),
    "Chiba": ("12", "千叶"),
    "Saitama": ("13", "埼玉"),
    "Ibaraki": ("14", "茨城"),
    "Tochigi": ("15", "栃木"),
    "Gunma": ("16", "群马"),
    "Yamanashi": ("17", "山梨"),
    "Shizuoka": ("18", "静冈"),
    "Gifu": ("19", "岐阜"),
    "Aichi": ("20", "爱知"),
    "Mie": ("21", "三重"),
    "Kyoto": ("22", "京都"),
    "Shiga": ("23", "滋贺"),
    "Nara": ("24", "奈良"),
    "Osaka": ("25", "大阪"),
    "Wakayama": ("26", "和歌山"),
    "Hyogo": ("27", "兵库"),
    "Toyama": ("28", "富山"),
    "Fukui": ("29", "福井"),
    "Ishikawa": ("30", "石川"),
    "Okayama": ("31", "冈山"),
    "Shimane": ("32", "岛根"),
    "Yamaguchi": ("33", "山口"),
    "Tottori": ("34", "鸟取"),
    "Hiroshima": ("35", "广岛"),
    "Kagawa": ("36", "香川"),
    "Tokushima": ("37", "德岛"),
    "Ehime": ("38", "爱媛"),
    "Kochi": ("39", "高知"),
    "Fukuoka": ("40", "福冈"),
    "Saga": ("41", "佐贺"),
    "Nagasaki": ("42", "长崎"),
    "Kumamoto": ("43", "熊本"),
    "Oita": ("44", "大分"),
    "Miyazaki": ("45", "宫崎"),
    "Kagoshima": ("46", "鹿儿岛"),
    "Okinawa": ("47", "冲绳"),
}
PREFECTURE_ALIASES = {
    "Gumma": "Gunma",
}

callbook = {}
worked_prefectures = set()
worked_prefectures_by_band = {}


def on_start(ctx):
    path = _cached_callbook_path()
    if path:
        _load_callbook(path)
        _rebuild_worked_prefectures(ctx)
        logger.info(
            "japan_prefecture started callbook_entries=%d worked_prefectures=%d",
            len(callbook),
            len(worked_prefectures),
        )
    else:
        logger.warning("japan_prefecture started without callbook cache")


def on_logged_adif(ctx, raw_adif, indexed_count):
    if indexed_count:
        _rebuild_worked_prefectures(ctx)
        logger.info("japan_prefecture rebuilt worked prefectures indexed_count=%d worked_prefectures=%d", indexed_count, len(worked_prefectures))


def on_decode(ctx, decode):
    call = ctx.extract_callsign(decode["message"])
    if not call or not _is_japan_call(ctx, call):
        return
    entry = _lookup_call(call)
    if not entry:
        return

    prefecture = entry["prefecture_zh"]
    contribution = {
        "plugin_note": prefecture,
    }

    code = entry["waja"]
    band = ctx.current_band()
    if code not in worked_prefectures:
        contribution["plugin_color"] = NEW_PREFECTURE_COLOR
    elif band and code not in worked_prefectures_by_band.get(band, set()):
        contribution["plugin_color"] = BAND_PREFECTURE_COLOR
    logger.debug(
        "japan_prefecture matched call=%s prefecture=%s band=%s color=%s",
        call,
        prefecture,
        band,
        contribution.get("plugin_color", ""),
    )
    return contribution


def _cached_callbook_path():
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    if CACHE_PATH.exists() and time.time() - CACHE_PATH.stat().st_mtime <= CACHE_TTL_SECONDS:
        logger.info("japan_prefecture using cached callbook path=%s", CACHE_PATH)
        return CACHE_PATH
    try:
        logger.info("japan_prefecture downloading callbook url=%s", CALLBOOK_URL)
        request = Request(CALLBOOK_URL, headers={"User-Agent": "wsjt-remote/0.1"})
        tmp = CACHE_PATH.with_suffix(".tmp")
        with urlopen(request, timeout=45) as response, tmp.open("wb") as output:
            while True:
                chunk = response.read(1024 * 128)
                if not chunk:
                    break
                output.write(chunk)
        tmp.replace(CACHE_PATH)
    except (OSError, URLError) as exc:
        logger.warning("japan_prefecture callbook download failed: %s", exc)
        return CACHE_PATH if CACHE_PATH.exists() else None
    logger.info("japan_prefecture cached callbook path=%s", CACHE_PATH)
    return CACHE_PATH


def _load_callbook(path):
    callbook.clear()
    with path.open("r", encoding="utf-8", errors="replace", newline="") as handle:
        for row in csv.DictReader(handle):
            call = str(row.get("Call") or "").strip().upper()
            prefecture_en = _canonical_prefecture(str(row.get("Prefecture") or ""))
            if not call or prefecture_en not in PREFECTURES:
                continue
            date = _parse_date(str(row.get("Licensed/Renewed Date (5-year valid)") or ""))
            current = callbook.get(call)
            if current and current["date"] >= date:
                continue
            code, prefecture_zh = PREFECTURES[prefecture_en]
            callbook[call] = {
                "waja": code,
                "prefecture_en": prefecture_en,
                "prefecture_zh": prefecture_zh,
                "date": date,
            }
    logger.info("japan_prefecture loaded callbook path=%s entries=%d", path, len(callbook))


def _rebuild_worked_prefectures(ctx):
    worked_prefectures.clear()
    worked_prefectures_by_band.clear()

    for call in ctx.adif.worked_calls:
        entry = _lookup_call(call)
        if entry:
            worked_prefectures.add(entry["waja"])

    for band, calls in ctx.adif.worked_calls_by_band.items():
        values = set()
        for call in calls:
            entry = _lookup_call(call)
            if entry:
                values.add(entry["waja"])
        worked_prefectures_by_band[band] = values


def _lookup_call(call):
    for candidate in _call_variants(call):
        entry = callbook.get(candidate)
        if entry:
            return entry
    return None


def _call_variants(call):
    value = call.upper().strip()
    parts = [part for part in value.split("/") if part]
    return [value, *parts]


def _is_japan_call(ctx, call):
    match = ctx.lookup_dxcc(call)
    return bool(match and match.entity == "Japan")


def _canonical_prefecture(value):
    name = value.strip()
    return PREFECTURE_ALIASES.get(name, name)


def _parse_date(value):
    try:
        return datetime.strptime(value.strip(), "%Y-%m-%d").date()
    except ValueError:
        return datetime.min.date()
