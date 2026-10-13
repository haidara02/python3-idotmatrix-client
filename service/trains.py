"""Departures from the Transport for NSW Trip Planner API (the data TripView uses)."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
import logging
import re
from typing import List, Optional
from zoneinfo import ZoneInfo

import requests

API_BASE = "https://api.transport.nsw.gov.au/v1/tp"
API_VERSION = "10.2.1.42"

logger = logging.getLogger("service.trains")


@dataclass
class Departure:
    planned: datetime
    estimated: Optional[datetime]
    route: str
    destination: str
    platform: str

    @property
    def due(self) -> datetime:
        return self.estimated or self.planned

    @property
    def delay_minutes(self) -> int:
        if self.estimated is None:
            return 0
        return round((self.estimated - self.planned).total_seconds() / 60)


def _parse_utc(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _get(api_key: str, endpoint: str, params: dict) -> dict:
    response = requests.get(
        f"{API_BASE}/{endpoint}",
        params={"outputFormat": "rapidJSON", "coordOutputFormat": "EPSG:4326", "version": API_VERSION, **params},
        headers={"Authorization": f"apikey {api_key}"},
        timeout=15,
    )
    response.raise_for_status()
    return response.json()


def fetch_departures(api_key: str, stop_id: str, platform: str, tz: str) -> List[Departure]:
    """Returns upcoming departures from a stop, filtered to one platform if given."""
    now = datetime.now(ZoneInfo(tz))
    data = _get(
        api_key,
        "departure_mon",
        {
            "mode": "direct",
            "type_dm": "stop",
            "name_dm": stop_id,
            "depArrMacro": "dep",
            "itdDate": now.strftime("%Y%m%d"),
            "itdTime": now.strftime("%H%M"),
            "TfNSWDM": "true",
        },
    )
    departures: List[Departure] = []
    for event in data.get("stopEvents", []):
        location = event.get("location", {})
        stop_platform = location.get("disassembledName") or location.get("name", "")
        if platform and platform.lower() not in stop_platform.lower():
            continue
        planned = _parse_utc(event.get("departureTimePlanned"))
        if planned is None:
            continue
        transportation = event.get("transportation", {})
        departures.append(
            Departure(
                planned=planned,
                estimated=_parse_utc(event.get("departureTimeEstimated")),
                route=transportation.get("disassembledName", ""),
                destination=transportation.get("destination", {}).get("name", ""),
                platform=stop_platform,
            )
        )
    departures.sort(key=lambda d: d.due)
    return departures


def find_stops(api_key: str, query: str) -> List[dict]:
    """Looks up stop IDs by name, e.g. "Central Station"."""
    data = _get(api_key, "stop_finder", {"type_sf": "stop", "name_sf": query, "TfNSWSF": "true"})
    return data.get("locations", [])


def minutes_until(when: datetime, now: Optional[datetime] = None) -> int:
    now = now or datetime.now(timezone.utc)
    return int((when - now).total_seconds() // 60)


TRACKWORK = re.compile(r"track ?work|buses replace|replace trains|planned work", re.IGNORECASE)
MAJOR = re.compile(r"major delay|significant delay|severe|suspended|not running|no trains", re.IGNORECASE)


@dataclass
class Alert:
    title: str
    priority: str
    kind: str  # "major", "trackwork" or "info"
    active: bool
    lines: List[str] = field(default_factory=list)


def _affects_line(info: dict, line: str) -> bool:
    for affected in info.get("affected", {}).get("lines", []):
        for name in (affected.get("disassembledName"), affected.get("number"), affected.get("name")):
            if name and (name == line or name.startswith(line + " ")):
                return True
    return False


def _active(info: dict, now: datetime) -> bool:
    """Alerts are often published ahead of time, with validity windows for when they apply."""
    windows = info.get("timestamps", {}).get("validity", [])
    if not windows:
        return True
    for window in windows:
        start, end = _parse_utc(window.get("from")), _parse_utc(window.get("to"))
        if (start is None or start <= now) and (end is None or now < end):
            return True
    return False


def fetch_alerts(api_key: str, stop_id: str, line: str, tz: str) -> List[Alert]:
    """Today's service alerts at a stop that affect the given line."""
    now = datetime.now(ZoneInfo(tz))
    data = _get(
        api_key,
        "add_info",
        {
            "filterDateValid": now.strftime("%d-%m-%Y"),
            "filterPublicationStatus": "current",
            "itdLPxx_selStop": stop_id,
        },
    )
    alerts: List[Alert] = []
    for info in data.get("infos", {}).get("current", []):
        if line and not _affects_line(info, line):
            continue
        text = " ".join(str(info.get(key, "")) for key in ("subtitle", "urlText", "content"))
        priority = info.get("priority", "")
        if TRACKWORK.search(text):
            kind = "trackwork"
        elif priority in ("high", "veryHigh") or MAJOR.search(text):
            kind = "major"
        else:
            kind = "info"
        alerts.append(
            Alert(
                title=info.get("subtitle") or info.get("urlText") or "",
                priority=priority,
                kind=kind,
                active=_active(info, now),
                lines=[l.get("disassembledName") or l.get("number") or "" for l in info.get("affected", {}).get("lines", [])],
            )
        )
    return alerts


def alert_level(alerts: List[Alert]) -> Optional[str]:
    """The most serious kind of alert in effect right now, if any."""
    kinds = {a.kind for a in alerts if a.active}
    for kind in ("major", "trackwork"):
        if kind in kinds:
            return kind
    return None
