"""Show WCSA Chinese school award stations in the DXCC column."""

from __future__ import annotations

from html import unescape
import logging
from pathlib import Path
import re
import time
from urllib.error import URLError
from urllib.request import Request, urlopen


logger = logging.getLogger(__name__)

ORDER = 200

SCHOOLS_URL = "https://www.wcsa.ac.cn/schools.html"
CACHE_TTL_SECONDS = 30 * 24 * 60 * 60
CACHE_DIR = Path("/tmp/wsjt-remote/plugins/china_school")
CACHE_PATH = CACHE_DIR / "schools.html"
NEW_SCHOOL_COLOR = "nord8"
BAND_SCHOOL_COLOR = "nord8-soft"

schools = {}
worked_schools = set()
worked_schools_by_band = {}


def on_start(ctx):
    path = _cached_schools_path()
    if path:
        _load_schools(path, ctx)
        _rebuild_worked_schools(ctx)
        logger.info("china_school started schools=%d worked_schools=%d", len(schools), len(worked_schools))
    else:
        logger.warning("china_school started without schools cache")


def on_logged_adif(ctx, raw_adif, indexed_count):
    if indexed_count:
        _rebuild_worked_schools(ctx)
        logger.info("china_school rebuilt worked schools indexed_count=%d worked_schools=%d", indexed_count, len(worked_schools))


def on_decode(ctx, decode):
    call = ctx.extract_callsign(str(decode.get("message") or ""))
    if not call or not _is_china_call(ctx, call):
        return None
    entry = _lookup_school(call)
    if not entry:
        return None

    school_call, school_name = entry
    contribution = {
        "dxcc_label": f"中国\n{school_name}",
        "dxcc_entity": "China",
    }
    band = ctx.current_band()
    if school_call not in worked_schools:
        contribution["plugin_color"] = NEW_SCHOOL_COLOR
    elif band and school_call not in worked_schools_by_band.get(band, set()):
        contribution["plugin_color"] = BAND_SCHOOL_COLOR
    logger.debug("china_school matched call=%s school_call=%s school=%s band=%s color=%s", call, school_call, school_name, band, contribution.get("plugin_color", ""))
    return contribution


def _cached_schools_path():
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    if CACHE_PATH.exists() and time.time() - CACHE_PATH.stat().st_mtime <= CACHE_TTL_SECONDS:
        logger.info("china_school using cached schools path=%s", CACHE_PATH)
        return CACHE_PATH
    try:
        logger.info("china_school downloading schools url=%s", SCHOOLS_URL)
        request = Request(SCHOOLS_URL, headers={"User-Agent": "wsjt-remote/0.1"})
        tmp = CACHE_PATH.with_suffix(".tmp")
        with urlopen(request, timeout=45) as response, tmp.open("wb") as output:
            while True:
                chunk = response.read(1024 * 128)
                if not chunk:
                    break
                output.write(chunk)
        tmp.replace(CACHE_PATH)
    except (OSError, URLError) as exc:
        logger.warning("china_school schools download failed: %s", exc)
        return CACHE_PATH if CACHE_PATH.exists() else None
    logger.info("china_school cached schools path=%s", CACHE_PATH)
    return CACHE_PATH


def _load_schools(path, ctx):
    schools.clear()
    text = path.read_text(encoding="utf-8", errors="replace")
    for item in re.findall(r"<li[^>]*>(.*?)</li>", text, flags=re.IGNORECASE | re.DOTALL):
        value = unescape(re.sub(r"<[^>]+>", "", item)).strip()
        match = re.match(r"([A-Z0-9/]{3,12})\s*[:：]\s*(.+)$", value, flags=re.IGNORECASE)
        if not match:
            continue
        call = ctx.normalize_call(match.group(1))
        name = " ".join(match.group(2).split())
        if call and name:
            schools[call] = name
    logger.info("china_school loaded schools path=%s entries=%d", path, len(schools))


def _rebuild_worked_schools(ctx):
    worked_schools.clear()
    worked_schools_by_band.clear()

    for call in ctx.adif.worked_calls:
        entry = _lookup_school(call)
        if entry:
            worked_schools.add(entry[0])

    for band, calls in ctx.adif.worked_calls_by_band.items():
        values = set()
        for call in calls:
            entry = _lookup_school(call)
            if entry:
                values.add(entry[0])
        worked_schools_by_band[band] = values


def _lookup_school(call):
    for candidate in _call_variants(call):
        school = schools.get(candidate)
        if school:
            return candidate, school
    return None


def _call_variants(call):
    value = call.upper().strip()
    parts = [part for part in value.split("/") if part]
    return [value, *parts]


def _is_china_call(ctx, call):
    match = ctx.lookup_dxcc(call)
    return bool(match and match.entity == "China")
