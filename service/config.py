"""Settings for the display service, read from environment variables."""

from dataclasses import dataclass
from datetime import time
import os
from typing import List, Optional, Set, Tuple

DAY_NAMES = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def _parse_time(value: str) -> time:
    hour, minute = value.strip().split(":")
    return time(int(hour), int(minute))


def parse_window(value: str) -> Optional[Tuple[time, time]]:
    """Parses "HH:MM-HH:MM". An empty string disables the window."""
    if not value.strip():
        return None
    start, end = value.split("-")
    return _parse_time(start), _parse_time(end)


def parse_days(value: str) -> Set[int]:
    """Parses "mon-fri" or "mon,wed,sat" into weekday numbers (mon = 0)."""
    days: Set[int] = set()
    for part in value.lower().split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start, end = (DAY_NAMES.index(d.strip()) for d in part.split("-"))
            day = start
            while True:
                days.add(day)
                if day == end:
                    break
                day = (day + 1) % 7
        else:
            days.add(DAY_NAMES.index(part))
    return days


def parse_brightness_schedule(value: str) -> List[Tuple[time, int]]:
    """Parses "06:30=60,19:00=30,21:00=10" into (time, percent) pairs, sorted by time."""
    schedule = []
    for part in value.split(","):
        if part.strip():
            at, percent = part.split("=")
            schedule.append((_parse_time(at), int(percent)))
    return sorted(schedule)


def parse_color(value: str) -> Tuple[int, int, int]:
    r, g, b = (int(c) for c in value.split("-"))
    return r, g, b


def _bool(value: str) -> bool:
    return value.strip().lower() in ("1", "true", "yes", "on")


@dataclass
class Config:
    address: str
    write_with_response: bool
    timezone: str
    brightness: int
    brightness_schedule: List[Tuple[time, int]]
    panel_size: int
    sleep: Optional[Tuple[time, time]]
    clock_style: int
    clock_color: Tuple[int, int, int]
    clock_24h: bool
    clock_date: bool
    tfnsw_api_key: str
    stop_id: str
    platform: str
    line: str
    line_color: Tuple[int, int, int]
    walk_minutes: int
    train_window: Optional[Tuple[time, time]]
    train_days: Set[int]
    train_refresh: int

    @classmethod
    def from_env(cls) -> "Config":
        env = os.environ.get
        return cls(
            address=env("IDM_ADDRESS", "auto"),
            write_with_response=_bool(env("WRITE_WITH_RESPONSE", "true")),
            timezone=env("TZ", "Australia/Sydney"),
            brightness=int(env("BRIGHTNESS", "60")),
            brightness_schedule=parse_brightness_schedule(env("BRIGHTNESS_SCHEDULE", "")),
            panel_size=int(env("PANEL_SIZE", "32")),
            sleep=parse_window(env("SLEEP", "22:30-06:30")),
            clock_style=int(env("CLOCK_STYLE", "0")),
            clock_color=parse_color(env("CLOCK_COLOR", "255-255-255")),
            clock_24h=_bool(env("CLOCK_24H", "false")),
            clock_date=_bool(env("CLOCK_DATE", "true")),
            tfnsw_api_key=env("TFNSW_API_KEY", ""),
            stop_id=env("STOP_ID", ""),
            platform=env("PLATFORM", "Platform 1"),
            line=env("LINE", "T8"),
            line_color=parse_color(env("LINE_COLOR", "0-149-76")),
            walk_minutes=int(env("WALK_MINUTES", "0")),
            train_window=parse_window(env("TRAIN_WINDOW", "07:30-09:00")),
            train_days=parse_days(env("TRAIN_DAYS", "mon-fri")),
            train_refresh=int(env("TRAIN_REFRESH", "60")),
        )


def brightness_at(cfg: "Config", now: time) -> int:
    """The latest scheduled brightness at or before now, carrying over from the previous
    day before the first entry. Falls back to BRIGHTNESS without a schedule."""
    if not cfg.brightness_schedule:
        return cfg.brightness
    current = cfg.brightness_schedule[-1][1]
    for at, percent in cfg.brightness_schedule:
        if at <= now:
            current = percent
    return current


def in_window(window: Optional[Tuple[time, time]], now: time) -> bool:
    """Checks whether now falls inside a window. Windows may wrap past midnight."""
    if window is None:
        return False
    start, end = window
    if start <= end:
        return start <= now < end
    return now >= start or now < end
