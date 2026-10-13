"""Departures from the Transport for NSW Trip Planner API (the data TripView uses)."""

from dataclasses import dataclass
from datetime import datetime, timezone
import logging
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
