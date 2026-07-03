"""Example plugin: show Chinese provinces in the DXCC column.

Mainland Chinese amateur callsigns encode the province using the call area digit
and the first suffix letter, e.g. BI7BST -> 7B -> Hunan.
"""

import logging


logger = logging.getLogger(__name__)

ORDER = 300

CHINA_PROVINCE_RANGES = (
    ("1", "A", "X", "北京"),
    ("2", "A", "H", "黑龙江"),
    ("2", "I", "P", "吉林"),
    ("2", "Q", "X", "辽宁"),
    ("3", "A", "F", "天津"),
    ("3", "G", "L", "内蒙古"),
    ("3", "M", "R", "河北"),
    ("3", "S", "X", "山西"),
    ("4", "A", "H", "上海"),
    ("4", "I", "P", "山东"),
    ("4", "Q", "X", "江苏"),
    ("5", "A", "H", "浙江"),
    ("5", "I", "P", "江西"),
    ("5", "Q", "X", "福建"),
    ("6", "A", "H", "安徽"),
    ("6", "I", "P", "河南"),
    ("6", "Q", "X", "湖北"),
    ("7", "A", "H", "湖南"),
    ("7", "I", "P", "广东"),
    ("7", "Q", "X", "广西"),
    ("7", "Y", "Z", "海南"),
    ("8", "A", "F", "四川"),
    ("8", "G", "L", "重庆"),
    ("8", "M", "R", "贵州"),
    ("8", "S", "X", "云南"),
    ("9", "A", "F", "陕西"),
    ("9", "G", "L", "甘肃"),
    ("9", "M", "R", "宁夏"),
    ("9", "S", "X", "青海"),
    ("0", "A", "F", "新疆"),
    ("0", "G", "L", "西藏"),
)
SPECIAL_CHINA_CALLS = {
    "BS7H": "黄岩岛",
}

NEW_PROVINCE_COLOR = "nord8"
BAND_PROVINCE_COLOR = "nord8-soft"

worked_provinces = set()
worked_provinces_by_band = {}


def on_start(ctx):
    _rebuild_worked_areas(ctx)
    logger.info("china_province started worked_provinces=%d", len(worked_provinces))


def on_logged_adif(ctx, raw_adif, indexed_count):
    if indexed_count:
        _rebuild_worked_areas(ctx)
        logger.info("china_province rebuilt worked areas indexed_count=%d worked_provinces=%d", indexed_count, len(worked_provinces))


def on_decode(ctx, decode):
    call = ctx.extract_callsign(decode["message"])
    if not call or not _is_china_call(ctx, call):
        return

    province = _china_province(call)
    if not province:
        return

    contribution = {
        "plugin_note": province,
    }

    band = ctx.current_band()
    if province not in worked_provinces:
        contribution["plugin_color"] = NEW_PROVINCE_COLOR
    elif band and province not in worked_provinces_by_band.get(band, set()):
        contribution["plugin_color"] = BAND_PROVINCE_COLOR
    logger.debug("china_province matched call=%s province=%s band=%s color=%s", call, province, band, contribution.get("plugin_color", ""))
    return contribution


def _rebuild_worked_areas(ctx):
    worked_provinces.clear()
    worked_provinces_by_band.clear()

    for call in ctx.adif.worked_calls:
        if _is_china_call(ctx, call):
            province = _china_province(call)
            if province:
                worked_provinces.add(province)

    for band, calls in ctx.adif.worked_calls_by_band.items():
        provinces = set()
        for call in calls:
            if _is_china_call(ctx, call):
                province = _china_province(call)
                if province:
                    provinces.add(province)
        worked_provinces_by_band[band] = provinces


def _is_china_call(ctx, call):
    match = ctx.lookup_dxcc(call)
    return bool(match and match.entity == "China")


def _china_province(call):
    for candidate in _call_variants(call):
        special = SPECIAL_CHINA_CALLS.get(candidate)
        if special:
            return special
        province = _province_from_candidate(candidate)
        if province:
            return province
    return ""


def _call_variants(call):
    value = call.upper().strip()
    parts = [part for part in value.split("/") if part]
    return [value, *parts]


def _province_from_candidate(call):
    for index, char in enumerate(call):
        if not char.isdigit() or index + 1 >= len(call):
            continue
        suffix_first = call[index + 1]
        if not suffix_first.isalpha():
            continue
        for area, start, end, province in CHINA_PROVINCE_RANGES:
            if char == area and start <= suffix_first <= end:
                return province
    return ""
