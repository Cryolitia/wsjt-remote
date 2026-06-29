from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path

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
    app = await create_app(state, Path(args.static_dir).resolve())
    broadcaster = app["broadcast"]
    transport = await start_udp(state, broadcaster, args.udp_host, args.udp_port)
    asyncio.create_task(heartbeat_loop(state, broadcaster))

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, args.web_host, args.web_port)
    await site.start()
    logger.info("web listening on http://%s:%s/", args.web_host, args.web_port)
    logger.info("udp listening on %s:%s", args.udp_host, args.udp_port)
    logger.info("static files from %s", Path(args.static_dir).resolve())
    try:
        while True:
            await asyncio.sleep(3600)
    finally:
        logger.info("shutting down")
        transport.close()
        await runner.cleanup()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="WSJT-X remote web controller")
    parser.add_argument("--udp-host", default="0.0.0.0")
    parser.add_argument("--udp-port", type=int, default=2237)
    parser.add_argument("--web-host", default="127.0.0.1")
    parser.add_argument("--web-port", type=int, default=8080)
    parser.add_argument("--static-dir", default=str(default_static_dir()))
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return parser


def main() -> None:
    args = build_parser().parse_args()
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        pass
