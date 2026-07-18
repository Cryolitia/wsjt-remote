from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Awaitable, Callable

from aiohttp import web, WSCloseCode, WSMsgType

from . import protocol
from .gui import send_alt_n_to_wsjtx
from .state import AppState
from .udp import send_datagram


JsonBroadcaster = Callable[[dict[str, Any]], Awaitable[None]]

logger = logging.getLogger(__name__)


def json_response(data: Any, status: int = 200) -> web.Response:
    return web.json_response(data, status=status, headers={"Access-Control-Allow-Origin": "*"})


@web.middleware
async def cors_middleware(request: web.Request, handler: Any) -> web.StreamResponse:
    if request.method == "OPTIONS":
        return web.Response(headers=_cors_headers())
    response = await handler(request)
    for key, value in _cors_headers().items():
        response.headers[key] = value
    return response


def _cors_headers() -> dict[str, str]:
    return {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Headers": "Content-Type",
        "Access-Control-Allow-Methods": "GET, POST, DELETE, OPTIONS",
    }


async def create_app(state: AppState, static_dir: Path) -> web.Application:
    logger.info("creating web app with static dir %s", static_dir)
    app = web.Application(middlewares=[cors_middleware])
    app["state"] = state
    app["static_dir"] = static_dir

    async def broadcast(payload: dict[str, Any]) -> None:
        dead = []
        for ws in set(state.websockets):
            try:
                await ws.send_json(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            state.websockets.discard(ws)

    app["broadcast"] = broadcast
    app.on_shutdown.append(close_websockets)

    app.router.add_get("/", index)
    app.router.add_get("/debug", debug_page)
    app.router.add_get("/theme.css", theme_css)
    app.router.add_get("/ws", websocket)
    app.router.add_get("/dist/{name}", dist_file)
    app.router.add_get("/api/state", api_state)
    app.router.add_post("/api/cq", api_cq)
    app.router.add_post("/api/free-text", api_free_text)
    app.router.add_post("/api/reply", api_reply)
    app.router.add_post("/api/alt-n", api_alt_n)
    app.router.add_post("/api/halt-tx", api_halt_tx)
    app.router.add_post("/api/clear", api_clear)
    app.router.add_post("/api/auto-reply", api_auto_reply)
    app.router.add_post("/api/transmits/clear", api_clear_transmits)
    app.router.add_post("/api/replay", api_replay)
    app.router.add_get("/api/debug/events", api_debug_events)
    app.router.add_post("/api/debug/send", api_debug_send)
    app.router.add_route("OPTIONS", "/{tail:.*}", lambda request: web.Response(headers=_cors_headers()))
    return app


async def close_websockets(app: web.Application) -> None:
    state: AppState = app["state"]
    websockets = set(state.websockets)
    for ws in websockets:
        try:
            await asyncio.wait_for(ws.close(code=WSCloseCode.GOING_AWAY, message=b"Server shutdown"), timeout=0.5)
        except asyncio.TimeoutError:
            logger.debug("timed out closing websocket during shutdown")
    state.websockets.difference_update(websockets)


def _file_response(request: web.Request, name: str) -> web.FileResponse:
    path = Path(request.app["static_dir"]) / name
    if not path.exists():
        raise web.HTTPNotFound(text="Frontend not built. Run: cd frontend && npm install && npm run build")
    return web.FileResponse(path, headers={"Cache-Control": "no-store, max-age=0"})


async def index(request: web.Request) -> web.FileResponse:
    return _file_response(request, "index.html")


async def debug_page(request: web.Request) -> web.FileResponse:
    return _file_response(request, "debug.html")


async def theme_css(request: web.Request) -> web.FileResponse:
    return _file_response(request, "theme.css")


async def dist_file(request: web.Request) -> web.FileResponse:
    return _file_response(request, f"dist/{request.match_info['name']}")


async def websocket(request: web.Request) -> web.WebSocketResponse:
    state: AppState = request.app["state"]
    ws = web.WebSocketResponse(heartbeat=30)
    await ws.prepare(request)
    state.websockets.add(ws)
    logger.info("websocket connected from %s", request.remote or "unknown")
    await ws.send_json({"event": "state", "data": state.snapshot()})
    try:
        async for msg in ws:
            if msg.type == WSMsgType.ERROR:
                break
    finally:
        state.websockets.discard(ws)
        logger.info("websocket disconnected from %s", request.remote or "unknown")
    return ws


async def api_state(request: web.Request) -> web.Response:
    return json_response(request.app["state"].snapshot())


async def _send(request: web.Request, data: bytes) -> web.Response:
    state: AppState = request.app["state"]
    msg = protocol.parse_message(data)
    try:
        send_datagram(state, data, msg)
        if msg.type == protocol.MessageType.Reply and state.reply_watchdog:
            state.reply_watchdog.reset_if_armed("api_reply")
        await request.app["broadcast"]({"event": "debug", "data": state.debug_events[-1]})
    except Exception as exc:
        logger.warning("failed to send %s: %s", _message_type_name(msg), exc)
        return json_response({"error": str(exc)}, status=400)
    return json_response({"ok": True})


async def api_cq(request: web.Request) -> web.Response:
    state: AppState = request.app["state"]
    own_call = str(state.status.get("de_call") or "").strip().upper()
    own_grid = str(state.status.get("de_grid") or "").strip().upper()[:4]
    text = " ".join(part for part in ("CQ", own_call, own_grid) if part)
    logger.info("api cq free-text text=%r", text)
    return await _send(request, protocol.build_free_text(state.remote.id, text, True, state.remote.schema))


async def api_free_text(request: web.Request) -> web.Response:
    body = await request.json()
    logger.info("api free-text send=%s text=%r", bool(body.get("send", True)), str(body.get("text", "")))
    return await _send(request, protocol.build_free_text(request.app["state"].remote.id, str(body.get("text", "")), bool(body.get("send", True)), request.app["state"].remote.schema))


async def api_reply(request: web.Request) -> web.Response:
    state: AppState = request.app["state"]
    body = await request.json()
    decode = body.get("fields")
    if not decode:
        decode = state.get_decode(int(body.get("decode_index", 0)))
    if not decode:
        logger.warning("reply rejected: decode not found body=%s", body)
        return json_response({"error": "decode not found"}, status=404)
    logger.info("api reply decode_index=%s message=%r", decode.get("index"), decode.get("message"))
    return await _send(request, protocol.build_reply(state.remote.id, decode, int(body.get("modifiers", 0)), state.remote.schema))


async def api_alt_n(request: web.Request) -> web.Response:
    logger.info("api alt-n requested")
    try:
        send_alt_n_to_wsjtx()
    except RuntimeError as exc:
        logger.warning("api alt-n failed: %s", exc)
        return json_response({"error": str(exc)}, status=500)
    return json_response({"ok": True})


async def api_halt_tx(request: web.Request) -> web.Response:
    body = await request.json() if request.can_read_body else {}
    logger.info("api halt-tx auto_tx_only=%s", bool(body.get("auto_tx_only", False)))
    return await _send(request, protocol.build_halt_tx(request.app["state"].remote.id, bool(body.get("auto_tx_only", False)), request.app["state"].remote.schema))


async def api_clear(request: web.Request) -> web.Response:
    state: AppState = request.app["state"]
    body = await request.json() if request.can_read_body else {}
    window = int(body.get("window", 2))
    logger.info("api clear window=%s", window)
    response = await _send(request, protocol.build_clear(state.remote.id, window, state.remote.schema))
    if response.status < 400:
        state.clear_activity()
        if state.plugins:
            state.plugins.cancel_batches()
        await request.app["broadcast"]({"event": "clear", "data": {"window": window}})
    return response


async def api_clear_transmits(request: web.Request) -> web.Response:
    state: AppState = request.app["state"]
    logger.info("api clear transmits")
    state.clear_transmits()
    await request.app["broadcast"]({"event": "transmits-cleared", "data": {}})
    return json_response({"ok": True})


async def api_auto_reply(request: web.Request) -> web.Response:
    state: AppState = request.app["state"]
    body = await request.json()
    state.auto_reply_enabled = bool(body.get("enabled"))
    logger.info("api auto-reply enabled=%s", state.auto_reply_enabled)
    await request.app["broadcast"]({"event": "auto-reply", "data": {"enabled": state.auto_reply_enabled}})
    return json_response({"ok": True, "enabled": state.auto_reply_enabled})


async def api_replay(request: web.Request) -> web.Response:
    logger.info("api replay")
    return await _send(request, protocol.build_replay(request.app["state"].remote.id, request.app["state"].remote.schema))


async def api_debug_events(request: web.Request) -> web.Response:
    return json_response(list(request.app["state"].debug_events))


async def api_debug_send(request: web.Request) -> web.Response:
    state: AppState = request.app["state"]
    body = await request.json()
    msg_type = str(body.get("type", ""))
    fields = body.get("fields") or {}
    builders = {
        "Heartbeat": lambda: protocol.build_heartbeat("wsjtx-remote", state.remote.schema),
        "FreeText": lambda: protocol.build_free_text(state.remote.id, str(fields.get("text", "")), bool(fields.get("send", True)), state.remote.schema),
        "HaltTx": lambda: protocol.build_halt_tx(state.remote.id, bool(fields.get("auto_tx_only", False)), state.remote.schema),
        "Replay": lambda: protocol.build_replay(state.remote.id, state.remote.schema),
        "Clear": lambda: protocol.build_clear(state.remote.id, int(fields.get("window", 2)), state.remote.schema),
        "Configure": lambda: protocol.build_configure(state.remote.id, fields, state.remote.schema),
        "Reply": lambda: protocol.build_reply(state.remote.id, fields, int(fields.get("modifiers", 0)), state.remote.schema),
    }
    if msg_type not in builders:
        logger.warning("debug send rejected: unsupported type %s", msg_type)
        return json_response({"error": f"unsupported debug message type: {msg_type}"}, status=400)
    logger.info("api debug send type=%s fields=%s", msg_type, fields)
    return await _send(request, builders[msg_type]())


def _message_type_name(message: protocol.Message) -> str:
    return message.type.name if isinstance(message.type, protocol.MessageType) else f"Unknown({message.raw_type})"
