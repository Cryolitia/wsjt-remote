"""Example plugin: highlight World Wide Award stations from a TXT file.

Place wwa_stations.txt next to this plugin, with one callsign per line.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
import json
import logging
from pathlib import Path

from wsjtx_remote.adif_index import parse_adif_records


logger = logging.getLogger(__name__)

ORDER = 100

CACHE_PATH = Path("/tmp/wsjt-remote/plugins/wwa_worked.json")

stations = set()
worked_keys = set()
blacklisted_until = {}
pending_reply_key = None
pending_reply_seen_active = False
tx_was_idle = True


def on_start(ctx):
    _load_stations(ctx)
    _load_worked(ctx)
    logger.info("wwa started stations=%d worked_keys=%d", len(stations), len(worked_keys))


def on_decode(ctx, decode):
    call = _decode_call(ctx, decode)
    key = _selection_key(ctx, decode, call)
    if not key:
        return

    if key not in worked_keys:
        logger.debug("wwa highlighted call=%s day=%s band=%s", call, key[0].isoformat(), key[1])
        return {
            "plugin_note": "WWA listed station not worked today on this band",
            "plugin_color": "nord8",
        }


def on_decode_batch(ctx, decodes):
    if pending_reply_key is not None:
        logger.info(
            "wwa auto reply skipped: pending call=%s day=%s band=%s",
            pending_reply_key[2],
            pending_reply_key[0].isoformat(),
            pending_reply_key[1],
        )
        return None

    now = _now_utc(ctx)
    candidates, skipped, wwa_received = _wwa_candidates(ctx, decodes, now)
    if not candidates:
        logger.info(
            "wwa auto reply no candidate total=%d wwa_received=%s skipped_not_listed=%d skipped_worked=%d skipped_blacklisted=%d skipped_not_repliable=%d",
            len(decodes),
            wwa_received,
            skipped["not_listed"],
            skipped["worked"],
            skipped["blacklisted"],
            skipped["not_repliable"],
        )
        return None

    snr, key, decode = max(candidates, key=lambda item: item[0])
    logger.info(
        "wwa auto reply selected call=%s day=%s band=%s snr=%s decode_index=%s candidates=%s wwa_received=%s skipped_not_listed=%d skipped_worked=%d skipped_blacklisted=%d skipped_not_repliable=%d",
        key[2],
        key[0].isoformat(),
        key[1],
        snr,
        decode.get("index"),
        [(item_key[2], item_snr, item_decode.get("index")) for item_snr, item_key, item_decode in sorted(candidates, reverse=True)],
        wwa_received,
        skipped["not_listed"],
        skipped["worked"],
        skipped["blacklisted"],
        skipped["not_repliable"],
    )
    return decode


def on_auto_reply_sent(ctx, decode):
    call = _decode_call(ctx, decode)
    key = _selection_key(ctx, decode, call)
    if key:
        _set_pending_reply(key)
        logger.info("wwa pending reply set call=%s day=%s band=%s decode_index=%s", key[2], key[0].isoformat(), key[1], decode.get("index"))


def on_status(ctx, status):
    global pending_reply_key, pending_reply_seen_active, tx_was_idle

    idle = _tx_idle(status)
    if pending_reply_key is not None and not idle:
        pending_reply_seen_active = True
    if pending_reply_key is not None and pending_reply_seen_active and idle and not tx_was_idle:
        if pending_reply_key not in worked_keys:
            until = _now_utc(ctx) + timedelta(minutes=30)
            blacklisted_until[pending_reply_key] = until
            logger.warning(
                "wwa blacklisted unanswered call=%s day=%s band=%s until=%s",
                pending_reply_key[2],
                pending_reply_key[0].isoformat(),
                pending_reply_key[1],
                until.isoformat(),
            )
        pending_reply_key = None
        pending_reply_seen_active = False
    tx_was_idle = idle


def on_logged_adif(ctx, raw_adif, indexed_count):
    global pending_reply_key, pending_reply_seen_active

    logger.info("wwa LoggedADIF received indexed_count=%d", indexed_count)
    if not indexed_count:
        return
    fallback_day = _utc_date(ctx.now())
    changed = False
    records = 0
    for record in parse_adif_records(raw_adif):
        records += 1
        mode = str(record.get("MODE") or "").strip().upper()
        if mode != "FT8":
            logger.info("wwa LoggedADIF skipped call=%s mode=%s reason=mode", record.get("CALL", ""), mode)
            continue
        call = ctx.normalize_call(str(record.get("CALL") or ""))
        band = ctx.band_from_adif_freq(str(record.get("FREQ") or ""))
        if not band:
            logger.info("wwa LoggedADIF skipped call=%s reason=band freq=%r", call, record.get("FREQ", ""))
            continue
        day = _adif_date(record.get("QSO_DATE")) or fallback_day
        if day is None:
            logger.info("wwa LoggedADIF skipped call=%s band=%s reason=date qso_date=%r", call, band, record.get("QSO_DATE", ""))
            continue
        key = (day, band, call)
        if key == pending_reply_key:
            pending_reply_key = None
            pending_reply_seen_active = False
            blacklisted_until.pop(key, None)
            logger.info("wwa pending reply confirmed by LoggedADIF call=%s day=%s band=%s", call, day.isoformat(), band)
        if call not in stations:
            logger.info("wwa LoggedADIF not a listed WWA station call=%s day=%s band=%s", call, day.isoformat(), band)
            continue
        if key not in worked_keys:
            worked_keys.add(key)
            changed = True
            logger.info("wwa logged worked call=%s day=%s band=%s", call, day.isoformat(), band)
        else:
            logger.info("wwa LoggedADIF already cached call=%s day=%s band=%s", call, day.isoformat(), band)
    if changed:
        _save_worked()
    logger.info("wwa LoggedADIF processed records=%d changed=%s worked_keys=%d", records, changed, len(worked_keys))


def _load_stations(ctx):
    stations.clear()
    try:
        text = Path(__file__).with_name("wwa_stations.txt").read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        logger.warning("wwa failed to read station list: %s", exc)
        return

    for line in text.splitlines():
        call = ctx.normalize_call(line.strip())
        if call:
            stations.add(call)
    logger.info("wwa loaded station list stations=%d", len(stations))


def _load_worked(ctx):
    today = _utc_date(ctx.now())
    if today is None:
        return
    worked_keys.clear()
    try:
        entries = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except OSError:
        logger.info("wwa worked cache not found path=%s", CACHE_PATH)
        return
    except json.JSONDecodeError as exc:
        logger.warning("wwa failed to parse worked cache path=%s: %s", CACHE_PATH, exc)
        return

    changed = False
    if not isinstance(entries, list):
        logger.warning("wwa ignored worked cache path=%s: expected JSON list", CACHE_PATH)
        return
    for entry in entries:
        if not isinstance(entry, dict):
            changed = True
            continue
        day = _cache_date(entry.get("day"))
        band = str(entry.get("band") or "")
        call = ctx.normalize_call(str(entry.get("call") or ""))
        if not call or day != today:
            changed = True
            continue
        worked_keys.add((day, band, call))
    if changed:
        _save_worked()
    logger.info("wwa loaded worked cache path=%s worked_keys=%d", CACHE_PATH, len(worked_keys))


def _save_worked():
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    entries = [{"day": day.isoformat(), "band": band, "call": call} for day, band, call in sorted(worked_keys)]
    CACHE_PATH.write_text(json.dumps(entries, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
    logger.info("wwa saved worked cache path=%s worked_keys=%d", CACHE_PATH, len(worked_keys))


def _decode_call(ctx, decode):
    call = ctx.extract_callsign(str(decode.get("message") or ""))
    if not call or call == "UNKNOWN":
        return ""
    value = ctx.normalize_call(call)
    return value if value in stations else ""


def _selection_key(ctx, decode, call):
    if not call:
        return None
    band = ctx.current_band()
    if not band:
        return None
    day = _utc_date(str(decode.get("received_at") or ""))
    if day is None:
        return None
    return day, band, call


def _wwa_candidates(ctx, decodes, now):
    candidates = []
    received = []
    skipped = {
        "not_listed": 0,
        "worked": 0,
        "blacklisted": 0,
        "not_repliable": 0,
    }
    for decode in decodes:
        call = _decode_call(ctx, decode)
        key = _selection_key(ctx, decode, call)
        if not key:
            skipped["not_listed"] += 1
            continue

        worked = key in worked_keys
        blacklisted = _blacklisted(key, now)
        repliable = ctx.is_repliable(str(decode.get("message") or ""))
        snr = int(decode.get("snr") or -999)
        received.append(
            {
                "call": key[2],
                "day": key[0].isoformat(),
                "band": key[1],
                "snr": snr,
                "index": decode.get("index"),
                "worked": worked,
                "blacklisted": blacklisted,
                "blacklisted_until": blacklisted_until[key].isoformat() if blacklisted else "",
                "repliable": repliable,
            }
        )

        if worked:
            skipped["worked"] += 1
            logger.debug("wwa auto reply candidate skipped worked call=%s day=%s band=%s", key[2], key[0].isoformat(), key[1])
            continue
        if blacklisted:
            skipped["blacklisted"] += 1
            continue
        if not repliable:
            skipped["not_repliable"] += 1
            logger.debug("wwa auto reply candidate skipped non-repliable call=%s day=%s band=%s message=%r", key[2], key[0].isoformat(), key[1], decode.get("message"))
            continue
        candidates.append((snr, key, decode))
    return candidates, skipped, received


def _set_pending_reply(key):
    global pending_reply_key, pending_reply_seen_active
    pending_reply_key = key
    pending_reply_seen_active = False


def _tx_idle(status):
    return not bool(status.get("tx_enabled")) and not bool(status.get("transmitting"))


def _blacklisted(key, now):
    until = blacklisted_until.get(key)
    if until is None:
        return False
    if until <= now:
        blacklisted_until.pop(key, None)
        logger.info("wwa blacklist expired call=%s day=%s band=%s", key[2], key[0].isoformat(), key[1])
        return False
    logger.debug("wwa skipped blacklisted call=%s day=%s band=%s until=%s", key[2], key[0].isoformat(), key[1], until.isoformat())
    return True


def _cache_date(value):
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _adif_date(value):
    if not isinstance(value, str):
        return None
    try:
        return datetime.strptime(value.strip(), "%Y%m%d").date()
    except ValueError:
        return None


def _utc_date(value):
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).date()


def _now_utc(ctx):
    parsed = datetime.fromisoformat(ctx.now())
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)
