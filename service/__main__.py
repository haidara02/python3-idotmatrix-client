"""Always-on display service: clock, sleep schedule, morning train board, and
automatic recovery when the display loses power.

    python -m service              run the service
    python -m service scan         list nearby displays
    python -m service stops NAME   look up TfNSW stop IDs
    python -m service departures   print departures for the configured stop
    python -m service alerts       print service alerts for the configured stop and line
    python -m service preview OUT  render the train board to a PNG
    python -m service test         flash test colours to find which write type the display accepts
"""

import asyncio
from datetime import datetime, timedelta, timezone
import logging
import signal
import sys
from typing import List, Optional
from zoneinfo import ZoneInfo

from idotmatrix import ConnectionManager

from .config import Config, brightness_at, in_window
from .display import Display
from .render import render_board, render_compact, test_pattern, to_png
from .trains import Departure, alert_level, fetch_alerts, fetch_departures, find_stops

TICK_SECONDS = 10
TIME_RESYNC = timedelta(hours=6)
# grey the board out once departures are this much older than they should be...
STALE_GRACE = timedelta(seconds=30)
# ...and show "NO DATA" rather than counting down from departures this old
NO_DATA_AFTER = timedelta(minutes=5)
ALERT_REFRESH = timedelta(minutes=5)

logger = logging.getLogger("service")


def render_frame(
    cfg: Config, now: datetime, departures: Optional[List[Departure]], stale: bool = False, alert: Optional[str] = None
):
    if cfg.panel_size == 16:
        return render_compact(now, departures, cfg.line_color, cfg.walk_minutes, cfg.clock_24h, stale, alert)
    return render_board(now, departures, cfg.line, cfg.line_color, cfg.walk_minutes, cfg.clock_24h)


def wanted_mode(cfg: Config, now: datetime) -> str:
    if in_window(cfg.sleep, now.time()):
        return "off"
    if cfg.tfnsw_api_key and cfg.stop_id and now.weekday() in cfg.train_days and in_window(cfg.train_window, now.time()):
        return "trains"
    return "clock"


class Service:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.tz = ZoneInfo(cfg.timezone)
        self.display = Display(cfg.address, cfg.write_with_response)
        # what the display is showing right now; None forces everything to be re-sent
        self.shown: Optional[str] = None
        self.last_png: Optional[bytes] = None
        self.brightness: Optional[int] = None
        self.last_time_sync = datetime.min.replace(tzinfo=timezone.utc)
        self.departures: List[Departure] = []
        self.fetched_at: Optional[datetime] = None
        self.fetch_attempted_at = datetime.min.replace(tzinfo=timezone.utc)
        self.alert: Optional[str] = None
        self.alerts_attempted_at = datetime.min.replace(tzinfo=timezone.utc)

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
                    self.brightness = None
                    await self.display.disable_builtin_schedule()
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

        brightness = brightness_at(self.cfg, now.time())
        if brightness != self.brightness:
            await self.display.set_brightness(brightness)
            self.brightness = brightness

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
                # send the time again now the clock is up, in case the first one was dropped
                await self.display.sync_time(datetime.now(self.tz))
                self.shown = "clock"
            return

        await self.refresh_departures()
        await self.refresh_alerts()
        age = datetime.now(timezone.utc) - self.fetched_at if self.fetched_at else NO_DATA_AFTER
        departures = self.departures if age < NO_DATA_AFTER else None
        stale = age > timedelta(seconds=self.cfg.train_refresh) + STALE_GRACE
        png = to_png(render_frame(self.cfg, now, departures, stale, self.alert), self.cfg.panel_size)
        if self.shown != "trains":
            logger.info("showing train board")
            await self.display.enter_image_mode()
            self.last_png = None
            self.shown = "trains"
        if png != self.last_png:
            await self.display.show_png(png)
            self.last_png = png

    async def refresh_alerts(self) -> None:
        now = datetime.now(timezone.utc)
        if now - self.alerts_attempted_at < ALERT_REFRESH:
            return
        self.alerts_attempted_at = now
        try:
            alerts = await asyncio.to_thread(
                fetch_alerts, self.cfg.tfnsw_api_key, self.cfg.stop_id, self.cfg.line, self.cfg.timezone
            )
        except Exception as error:
            # keep showing the last known state; alerts change slowly
            logger.warning(f"could not fetch alerts: {error}")
            return
        level = alert_level(alerts)
        if level != self.alert:
            logger.info(f"line alert: {level or 'none'}")
        self.alert = level

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


async def serve(cfg: Config) -> None:
    service = Service(cfg)
    task = asyncio.current_task()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, task.cancel)
    try:
        await service.run()
    except asyncio.CancelledError:
        logger.info("shutting down")
    finally:
        # otherwise BlueZ keeps the display connected after we exit
        await service.display.disconnect()


async def test_display(cfg: Config) -> None:
    display = Display(cfg.address, cfg.write_with_response)
    await display.connect()
    await display.disable_builtin_schedule()
    for gatt_service in display.conn.client.services:
        print(f"service {gatt_service.uuid}")
        for char in gatt_service.characteristics:
            print(f"    {char.uuid}  {', '.join(char.properties)}")
    for label, with_response, color in (
        ("RED    = write without response", False, (255, 0, 0)),
        ("GREEN  = write with response", True, (0, 255, 0)),
    ):
        print(f"\nnow showing {label}")
        display.with_response = with_response
        try:
            await display.screen(True)
            await display.fill(*color)
        except Exception as error:
            print(f"    failed: {error}")
        await asyncio.sleep(5)
    for size in (16, 32, 64):
        print(f"\nnow showing a {size}x{size} test image: red border, white X, green '{size}'")
        try:
            await display.enter_image_mode()
            await display.show_png(to_png(test_pattern(size), size))
        except Exception as error:
            print(f"    failed: {error}")
        await asyncio.sleep(8)
    await display.disconnect()
    print("\nwhich colours and test images appeared?")


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
        if not (cfg.tfnsw_api_key and cfg.stop_id):
            logger.warning("TFNSW_API_KEY or STOP_ID is not set, train board disabled")
        asyncio.run(serve(cfg))
    elif command == "test":
        asyncio.run(test_display(cfg))
    elif command == "scan":
        print("\n".join(asyncio.run(ConnectionManager.scan())) or "no displays found")
    elif command == "stops":
        _require_key(cfg)
        if len(argv) < 2:
            sys.exit('usage: python -m service stops "<station name>"')
        for location in find_stops(cfg.tfnsw_api_key, " ".join(argv[1:])):
            print(f"{location.get('id'):>12}  {location.get('name')}")
    elif command == "departures":
        _require_key(cfg)
        tz = ZoneInfo(cfg.timezone)
        for d in fetch_departures(cfg.tfnsw_api_key, cfg.stop_id, cfg.platform, cfg.timezone):
            late = f" (+{d.delay_minutes} late)" if d.delay_minutes else ""
            realtime = "" if d.estimated else " [timetable]"
            print(f"{d.due.astimezone(tz):%H:%M}  {d.route:<4} {d.platform:<12} {d.destination}{late}{realtime}")
    elif command == "alerts":
        _require_key(cfg)
        alerts = fetch_alerts(cfg.tfnsw_api_key, cfg.stop_id, cfg.line, cfg.timezone)
        for a in alerts:
            print(f"{'NOW  ' if a.active else 'later'}  {a.kind:<9} {a.priority:<8} {','.join(a.lines):<10} {a.title}")
        print(f"\n{len(alerts)} alert(s) for {cfg.line or 'all lines'}; board shows: {alert_level(alerts) or 'normal'}")
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
        img = render_frame(cfg, now, departures)
        img.resize((320, 320), 0).save(out)
        print(f"wrote {out}")
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
