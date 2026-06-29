from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import logging
import re
import threading
import time
from typing import Iterable
from urllib.error import URLError
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET


logger = logging.getLogger(__name__)

CACHE_TTL_SECONDS = 24 * 60 * 60
CACHE_DIR = Path("/tmp") / "wsjt-remote" / "dxcc"
CTY_URL = "https://www.country-files.com/bigcty/cty.dat"
JTDX_COUNTRYDAT_URL = "https://raw.githubusercontent.com/jtdx-project/jtdx/master/logbook/countrydat.cpp"
JTDX_ZH_CN_URL = "https://raw.githubusercontent.com/jtdx-project/jtdx/master/translations/jtdx_zh_CN.ts"

CALL_RE = re.compile(r"^[A-Z0-9/]{3,12}$")
JTDX_NAME_RE = re.compile(r'_name\.insert\("((?:[^"\\]|\\.)*)",\s*tr\("((?:[^"\\]|\\.)*)"\)\);')


@dataclass(frozen=True, slots=True)
class DxccMatch:
    prefix: str
    entity: str
    label: str


class DxccLookup:
    def __init__(self, cache_dir: Path = CACHE_DIR, ttl_seconds: int = CACHE_TTL_SECONDS):
        self.cache_dir = cache_dir
        self.ttl_seconds = ttl_seconds
        self._lock = threading.Lock()
        self._refresh_started = False
        self._loaded = False
        self._exact: dict[str, DxccMatch] = {}
        self._prefixes: list[tuple[str, DxccMatch]] = []

    def start_background_refresh(self) -> None:
        with self._lock:
            if self._refresh_started:
                return
            self._refresh_started = True
        thread = threading.Thread(target=self.load, name="dxcc-cache-refresh", daemon=True)
        thread.start()

    def load(self) -> None:
        try:
            cty_path = self._cached_file("cty.dat", CTY_URL)
            countrydat_path = self._cached_file("countrydat.cpp", JTDX_COUNTRYDAT_URL)
            zh_path = self._cached_file("jtdx_zh_CN.ts", JTDX_ZH_CN_URL)
            self._load_files(cty_path, countrydat_path, zh_path)
        except Exception as exc:
            logger.warning("DXCC refresh failed: %s", exc)
            self._load_existing_cache()

    def lookup(self, callsign: str) -> DxccMatch | None:
        call = normalize_call(callsign)
        if not call:
            return None
        if not self._loaded:
            self._load_existing_cache()
        return self._lookup_loaded(call)

    def _lookup_loaded(self, call: str) -> DxccMatch | None:
        with self._lock:
            exact = dict(self._exact)
            prefixes = list(self._prefixes)
        for candidate in call_variants(call):
            match = exact.get(candidate)
            if match:
                return match
        for candidate in call_variants(call):
            for prefix, match in prefixes:
                if candidate.startswith(prefix):
                    return match
        return None

    def _load_existing_cache(self) -> None:
        paths = [self.cache_dir / "cty.dat", self.cache_dir / "countrydat.cpp", self.cache_dir / "jtdx_zh_CN.ts"]
        if not all(path.exists() for path in paths):
            return
        try:
            self._load_files(paths[0], paths[1], paths[2])
        except Exception as exc:
            logger.warning("failed to load existing DXCC cache: %s", exc)

    def _load_files(self, cty_path: Path, countrydat_path: Path, zh_path: Path) -> None:
        name_map = parse_jtdx_name_map(countrydat_path.read_text(encoding="utf-8", errors="replace"))
        zh_map = parse_jtdx_zh_map(zh_path.read_text(encoding="utf-8", errors="replace"))
        exact, prefixes = parse_cty(cty_path.read_text(encoding="utf-8", errors="replace"), name_map, zh_map)
        with self._lock:
            self._exact = exact
            self._prefixes = prefixes
            self._loaded = True
        logger.info("loaded DXCC database exact=%d prefixes=%d", len(exact), len(prefixes))

    def _cached_file(self, name: str, url: str) -> Path:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        path = self.cache_dir / name
        if path.exists() and not self._expired(path):
            return path
        try:
            logger.info("refreshing DXCC cache %s", url)
            request = Request(url, headers={"User-Agent": "wsjt-remote/0.1"})
            tmp = path.with_suffix(path.suffix + ".tmp")
            with urlopen(request, timeout=45) as response, tmp.open("wb") as output:
                while True:
                    chunk = response.read(1024 * 64)
                    if not chunk:
                        break
                    output.write(chunk)
            tmp.replace(path)
            return path
        except (OSError, URLError) as exc:
            if path.exists():
                logger.warning("using expired DXCC cache %s after refresh failure: %s", name, exc)
                return path
            raise

    def _expired(self, path: Path) -> bool:
        return time.time() - path.stat().st_mtime >= self.ttl_seconds


def parse_jtdx_name_map(text: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for entity, source in JTDX_NAME_RE.findall(text):
        result[unescape_cpp(entity)] = unescape_cpp(source)
    return result


def parse_jtdx_zh_map(text: str) -> dict[str, str]:
    result: dict[str, str] = {}
    root = ET.fromstring(text)
    for context in root.findall("context"):
        name = context.findtext("name")
        if name != "CountryDat":
            continue
        for message in context.findall("message"):
            source = message.findtext("source") or ""
            translation = message.findtext("translation") or ""
            if source and translation:
                result[source] = translation
    return result


def parse_cty(text: str, name_map: dict[str, str], zh_map: dict[str, str]) -> tuple[dict[str, DxccMatch], list[tuple[str, DxccMatch]]]:
    exact: dict[str, DxccMatch] = {}
    prefixes: dict[str, DxccMatch] = {}
    for header, aliases in iter_cty_records(text):
        parts = header.split(":")
        if len(parts) < 8:
            continue
        entity = parts[0].strip()
        primary = clean_primary_prefix(parts[7])
        display_key = name_map.get(entity, entity)
        label = zh_map.get(display_key, display_key)
        match = DxccMatch(prefix=primary, entity=entity, label=label)
        for raw_alias in aliases:
            is_exact = raw_alias.startswith("=")
            alias = clean_alias(raw_alias[1:] if is_exact else raw_alias)
            if not alias:
                continue
            if is_exact:
                exact.setdefault(alias, match)
            else:
                prefixes.setdefault(alias, match)
    ordered_prefixes = sorted(prefixes.items(), key=lambda item: len(item[0]), reverse=True)
    return exact, ordered_prefixes


def iter_cty_records(text: str) -> Iterable[tuple[str, list[str]]]:
    header = ""
    alias_text = ""
    for line in text.splitlines():
        if not line.strip():
            continue
        if line[:1].isspace():
            alias_text += line.strip() + " "
            if ";" in line and header:
                yield header, split_aliases(alias_text)
                header = ""
                alias_text = ""
        else:
            if header:
                yield header, split_aliases(alias_text)
            header = line.rstrip()
            alias_text = ""
    if header:
        yield header, split_aliases(alias_text)


def split_aliases(text: str) -> list[str]:
    return [part.strip() for part in text.replace(";", ",").split(",") if part.strip()]


def clean_primary_prefix(value: str) -> str:
    return value.strip().lstrip("*").upper()


def clean_alias(value: str) -> str:
    value = re.sub(r"\([^)]*\)|\[[^]]*\]|<[^>]*>|\{[^}]*\}|~[^~]*~", "", value)
    return value.strip().lstrip("*").upper()


def normalize_call(callsign: str) -> str:
    call = callsign.strip().upper()
    return call if CALL_RE.match(call) and any(ch.isdigit() for ch in call) else ""


def call_variants(call: str) -> list[str]:
    parts = [part for part in call.split("/") if part]
    variants = [call]
    variants.extend(part for part in parts if part != call)
    return variants


def unescape_cpp(value: str) -> str:
    return bytes(value, "utf-8").decode("unicode_escape")
