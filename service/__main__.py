"""Always-on display service: clock, sleep schedule, morning train board, and
automatic recovery when the display loses power.

    python -m service              run the service
    python -m service scan         list nearby displays
    python -m service stops NAME   look up TfNSW stop IDs
    python -m service departures   print departures for the configured stop
    python -m service preview OUT  render the train board to a PNG
"""

import asyncio
from datetime import datetime, timedelta, timezone
import logging
import sys
from typing import List, Optional
from zoneinfo import ZoneInfo

from idotmatrix import ConnectionManager

from .config import Config, in_window
from .display import Display
from .render import render_board, to_png
from .trains import Departure, fetch_departures, find_stops

TICK_SECONDS = 10
TIME_RESYNC = timedelta(hours=6)
# show "NO DATA" rather than counting down from departures this old
STALE_AFTER = timedelta(minutes=5)

logger = logging.getLogger("service")


def wanted_mode(cfg: Config, now: datetime) -> str:
    if in_window(cfg.sleep, now.time()):
        return "off"
    if cfg.tfnsw_api_key and now.weekday() in cfg.train_days and in_window(cfg.train_window, now.time()):
        return "trains"
    return "clock"


class Service:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.tz = ZoneInfo(cfg.timezone)
        self.display = Display(cfg.address)
        # what the display is showing right now; None forces everything to be re-sent
        self.shown: Optional[str] = None
        self.last_png: Optional[bytes] = None
        self.last_time_sync = datetime.min.replace(tzinfo=timezone.utc)
        self.departures: List[Departure] = []
        self.fetched_at: Optional[datetime] = None
        self.fetch_attempted_at = datetime.min.replace(tzinfo=timezone.utc)

    async def run(self) -> None:
        backoff = 5
        while True:
            if not self.display.connected:
                try:
                    await self.display.connect()
                    # the display may have just been power cycled, so set everything up again
                    self.shown = None
                    await self.display.sync_time(datetime.now(self.tz))
                    self.last_time_sync = datetime.now(timezone.utc)
                    await self.display.set_brightness(self.cfg.brightness)
                    backoff = 5
                except Exception as error:
                    logger.info(f"display not reachable ({error}), retrying in {backoff}s")
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, 30)
                    continue

            try:
                await self.update()
            except Exception as error:
                logger.warning(f"update failed: {error}")
                self.shown = None
                await self.display.disconnect()
                await asyncio.sleep(5)
                continue

            try:
                await asyncio.wait_for(self.display.disconnected.wait(), TICK_SECONDS)
            except asyncio.TimeoutError:
                pass

    async def update(self) -> None:
        now = datetime.now(self.tz)
        mode = wanted_mode(self.cfg, now)

        if datetime.now(timezone.utc) - self.last_time_sync > TIME_RESYNC:
            await self.display.sync_time(now)
            self.last_time_sync = datetime.now(timezone.utc)

        if mode == "off":
            if self.shown != "off":
                logger.info("screen off")
                await self.display.screen(False)
                self.shown = "off"
            return

        if self.shown in (None, "off"):
            await self.display.screen(True)

        if mode == "clock":
            if self.shown != "clock":
                logger.info("showing clock")
                await self.display.show_clock(
                    self.cfg.clock_style, self.cfg.clock_color, self.cfg.clock_24h, self.cfg.clock_date
                )
                self.shown = "clock"
            return

        await self.refresh_departures()
        fresh = self.fetched_at and datetime.now(timezone.utc) - self.fetched_at < STALE_AFTER
        png = to_png(
            render_board(
                now,
                self.departures if fresh else None,
                self.cfg.line,
                self.cfg.line_color,
                self.cfg.walk_minutes,
                self.cfg.clock_24h,
            )
        )
        if self.shown != "trains":
            logger.info("showing train board")
            await self.display.enter_image_mode()
            self.last_png = None
            self.shown = "trains"
        if png != self.last_png:
            await self.display.show_png(png)
            self.last_png = png

    async def refresh_departures(self) -> None:
        now = datetime.now(timezone.utc)
        if now - self.fetch_attempted_at < timedelta(seconds=self.cfg.train_refresh):
            return
        self.fetch_attempted_at = now
        try:
            self.departures = await asyncio.to_thread(
                fetch_departures, self.cfg.tfnsw_api_key, self.cfg.stop_id, self.cfg.platform, self.cfg.timezone
            )
            self.fetched_at = now
            logger.debug(f"fetched {len(self.departures)} departures")
        except Exception as error:
            logger.warning(f"could not fetch departures: {error}")


def _require_key(cfg: Config) -> None:
    if not cfg.tfnsw_api_key:
        sys.exit("TFNSW_API_KEY is not set")


def main(argv: List[str]) -> None:
    # force: importing idotmatrix already configured the root logger at DEBUG
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s", force=True)
    # library loggers are chatty at INFO (one line per command)
    logging.getLogger("idotmatrix").setLevel(logging.WARNING)
    cfg = Config.from_env()
    command = argv[0] if argv else "run"

    if command == "run":
        if not cfg.tfnsw_api_key:
            logger.warning("TFNSW_API_KEY is not set, train board disabled")
        asyncio.run(Service(cfg).run())
    elif command == "scan":
        print("\n".join(asyncio.run(ConnectionManager.scan())) or "no displays found")
    elif command == "stops":
        _require_key(cfg)
        for location in find_stops(cfg.tfnsw_api_key, " ".join(argv[1:]) or "Central Station"):
            print(f"{location.get('id'):>12}  {location.get('name')}")
    elif command == "departures":
        _require_key(cfg)
        tz = ZoneInfo(cfg.timezone)
        for d in fetch_departures(cfg.tfnsw_api_key, cfg.stop_id, cfg.platform, cfg.timezone):
            late = f" (+{d.delay_minutes} late)" if d.delay_minutes else ""
            realtime = "" if d.estimated else " [timetable]"
            print(f"{d.due.astimezone(tz):%H:%M}  {d.route:<4} {d.platform:<12} {d.destination}{late}{realtime}")
    elif command == "preview":
        out = argv[1] if len(argv) > 1 else "preview.png"
        now = datetime.now(ZoneInfo(cfg.timezone))
        if cfg.tfnsw_api_key:
            departures = fetch_departures(cfg.tfnsw_api_key, cfg.stop_id, cfg.platform, cfg.timezone)
        else:
            departures = [
                Departure(now + timedelta(minutes=m, seconds=30), now + timedelta(minutes=m + late, seconds=30), "T8", "Central", "Platform 1")
                for m, late in ((6, 2), (14, 0), (21, 0))
            ]
        img = render_board(now, departures, cfg.line, cfg.line_color, cfg.walk_minutes, cfg.clock_24h)
        img.resize((320, 320), 0).save(out)
        print(f"wrote {out}")
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
