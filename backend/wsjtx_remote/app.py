from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
from pathlib import Path
import signal

from aiohttp import web

from .state import AppState
from .udp import heartbeat_loop, start_udp
from .web import create_app


logger = logging.getLogger(__name__)


def default_static_dir() -> Path:
    return Path(__file__).resolve().parents[3] / "frontend"


async def run(args: argparse.Namespace) -> None:
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    state = AppState()
    if args.adif:
        state.dxcc.load()
        state.adif.load_file(Path(args.adif).expanduser().resolve())
    state.dxcc.start_background_refresh()
    app = await create_app(state, Path(args.static_dir).resolve())
    broadcaster = app["broadcast"]
    transport = await start_udp(state, broadcaster, args.udp_host, args.udp_port)
    heartbeat_task = asyncio.create_task(heartbeat_loop(state, broadcaster), name="wsjt-remote-heartbeat")

    runner = web.AppRunner(app, shutdown_timeout=2)
    await runner.setup()
    site = web.TCPSite(runner, args.web_host, args.web_port)
    await site.start()
    logger.info("web listening on http://%s:%s/", args.web_host, args.web_port)
    logger.info("udp listening on %s:%s", args.udp_host, args.udp_port)
    logger.info("static files from %s", Path(args.static_dir).resolve())
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop_event.set)
    try:
        await stop_event.wait()
    finally:
        logger.info("shutting down")
        heartbeat_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await heartbeat_task
        transport.close()
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(runner.cleanup(), timeout=2)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="WSJT-X remote web controller")
    parser.add_argument("--udp-host", default="0.0.0.0")
    parser.add_argument("--udp-port", type=int, default=2237)
    parser.add_argument("--web-host", default="127.0.0.1")
    parser.add_argument("--web-port", type=int, default=8080)
    parser.add_argument("--static-dir", default=str(default_static_dir()))
    parser.add_argument("--adif", default="", help="Path to a read-only ADIF log used for worked-before lookups")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return parser


def main() -> None:
    args = build_parser().parse_args()
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        pass
