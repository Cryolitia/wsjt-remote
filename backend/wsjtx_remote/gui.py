from __future__ import annotations

import json
import logging
import subprocess
import time
from typing import Any


logger = logging.getLogger(__name__)


def send_alt_n_to_wsjtx() -> None:
    _focus_wsjtx_window()
    _run(["wtype", "-M", "alt", "n", "-m", "alt"], "wtype failed")
    logger.info("sent Alt+N to WSJT/JTDX")


def trigger_cq_to_wsjtx(clear_dx: bool = True) -> None:
    _focus_wsjtx_window()
    if clear_dx:
        _run(["wtype", "-k", "F4"], "wtype F4 failed")
        time.sleep(0.15)
    _run(["wtype", "-M", "alt", "n", "-m", "alt"], "wtype Alt+N failed")
    logger.info("sent %sAlt+N to WSJT/JTDX", "F4 then " if clear_dx else "")


def _focus_wsjtx_window() -> None:
    logger.info("querying niri windows for WSJT/JTDX")
    focused = _niri_focused_window()
    if focused and _matches_wsjtx(focused):
        logger.info("current focused window is already WSJT/JTDX id=%s title=%r app_id=%r", focused.get("id"), focused.get("title", ""), focused.get("app_id", ""))
        return

    windows = _niri_windows()
    window = _select_wsjtx_window(windows)
    if window is None:
        logger.warning("no WSJT/JTDX window found among %d windows", len(windows))
        raise RuntimeError("no WSJT/JTDX window found by niri")

    window_id = window.get("id")
    if window_id is None:
        logger.warning("matched WSJT/JTDX window has no id: %s", window)
        raise RuntimeError("matched niri window has no id")

    logger.info("focusing WSJT/JTDX window id=%s title=%r app_id=%r", window_id, window.get("title", ""), window.get("app_id", ""))
    _run(["niri", "msg", "action", "focus-window", "--id", str(window_id)], "niri focus-window failed")


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


def _niri_focused_window() -> dict[str, Any] | None:
    try:
        result = _run(["niri", "msg", "--json", "focused-window"], "niri focused-window query failed")
    except RuntimeError as exc:
        logger.debug("failed to query niri focused window: %s", exc)
        return None
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        logger.debug("failed to parse niri focused-window JSON: %s", exc)
        return None
    return data if isinstance(data, dict) else None


def _matches_wsjtx(window: dict[str, Any]) -> bool:
    app_id = _window_app_id(window)
    if _is_blacklisted_window(window):
        return False
    return app_id in {"jtdx", "wsjtx", "wsjt-x", "org.wsjtx.wsjtx"}


def _select_wsjtx_window(windows: list[dict[str, Any]]) -> dict[str, Any] | None:
    matches = [window for window in windows if _matches_wsjtx(window)]
    if not matches:
        return None
    return max(matches, key=_wsjtx_window_score)


def _wsjtx_window_score(window: dict[str, Any]) -> int:
    title = _window_title(window).upper()
    app_id = _window_app_id(window)
    score = 0
    if app_id in {"jtdx", "wsjtx", "wsjt-x", "org.wsjtx.wsjtx"}:
        score += 20
    if "JTDX  BY" in title or "WSJT-X" in title:
        score += 20
    return score


def _is_blacklisted_window(window: dict[str, Any]) -> bool:
    app_id = _window_app_id(window)
    title = _window_title(window)
    return "sdr" in app_id or "sdr" in title or "频谱" in title or "wide graph" in title or "waterfall" in title


def _window_app_id(window: dict[str, Any]) -> str:
    return str(window.get("app_id") or window.get("app_id_lowercase") or "").strip().lower()


def _window_title(window: dict[str, Any]) -> str:
    return str(window.get("title") or "").strip().lower()


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
