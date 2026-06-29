from __future__ import annotations

import json
import logging
import subprocess
from typing import Any


logger = logging.getLogger(__name__)


def send_alt_n_to_wsjtx() -> None:
    logger.info("querying niri windows for WSJT/JTDX")
    windows = _niri_windows()
    window = next((item for item in windows if _matches_wsjtx(item)), None)
    if window is None:
        logger.warning("no WSJT/JTDX window found among %d windows", len(windows))
        raise RuntimeError("no WSJT/JTDX window found by niri")

    window_id = window.get("id")
    if window_id is None:
        logger.warning("matched WSJT/JTDX window has no id: %s", window)
        raise RuntimeError("matched niri window has no id")

    logger.info("focusing WSJT/JTDX window id=%s title=%r app_id=%r", window_id, window.get("title", ""), window.get("app_id", ""))
    _run(["niri", "msg", "action", "focus-window", "--id", str(window_id)], "niri focus-window failed")
    _run(["wtype", "-M", "alt", "n", "-m", "alt"], "wtype failed")
    logger.info("sent Alt+N to WSJT/JTDX")


def _niri_windows() -> list[dict[str, Any]]:
    result = _run(["niri", "msg", "--json", "windows"], "niri window query failed")
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"failed to parse niri windows JSON: {exc}") from exc
    if not isinstance(data, list):
        raise RuntimeError("niri windows JSON is not a list")
    windows = [item for item in data if isinstance(item, dict)]
    logger.debug("niri returned %d windows", len(windows))
    return windows


def _matches_wsjtx(window: dict[str, Any]) -> bool:
    text = " ".join(
        str(window.get(key) or "")
        for key in ("title", "app_id", "app_id_lowercase", "namespace")
    ).upper()
    return "WSJT" in text or "JTDX" in text


def _run(command: list[str], error_message: str) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(command, check=False, capture_output=True, text=True, timeout=5)
    except FileNotFoundError as exc:
        raise RuntimeError(f"{command[0]} is not available") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"{error_message}: timed out") from exc

    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        logger.warning("command failed: %s: %s", " ".join(command), detail)
        raise RuntimeError(f"{error_message}: {detail}" if detail else error_message)
    return result
