"""Keeps a Bluetooth connection to the display and wraps the commands the service uses."""

import asyncio
from datetime import datetime
import logging
from typing import Optional, Tuple

from bleak import BleakClient
from idotmatrix import Clock, Common, ConnectionManager, Image

logger = logging.getLogger("service.display")


class Display:
    """The idotmatrix library logs and swallows send errors, so after each command we
    check is_connected ourselves to notice a dropped connection."""

    def __init__(self, address: str) -> None:
        self.address = address
        self.conn = ConnectionManager()
        self.disconnected = asyncio.Event()

    @property
    def connected(self) -> bool:
        return bool(self.conn.client and self.conn.client.is_connected)

    def _on_disconnect(self, _client: BleakClient) -> None:
        logger.warning("display disconnected")
        self.disconnected.set()

    async def connect(self) -> None:
        address = self.address
        if address == "auto":
            found = await ConnectionManager.scan()
            if not found:
                raise ConnectionError("no IDM-* display found while scanning")
            # stick with this display from now on rather than whichever one is nearest
            address = self.address = found[0]
        # a fresh client per attempt, so a stale BlueZ connection never gets reused
        self.conn.address = address
        self.conn.client = BleakClient(address, disconnected_callback=self._on_disconnect, timeout=20)
        await self.conn.client.connect()
        self.disconnected.clear()
        logger.info(f"connected to {address}")

    async def disconnect(self) -> None:
        if self.connected:
            await self.conn.client.disconnect()

    def _check(self, what: str) -> None:
        if not self.connected:
            raise ConnectionError(f"lost connection while sending {what}")

    async def sync_time(self, now: datetime) -> None:
        await Common().setTime(now.year, now.month, now.day, now.hour, now.minute, now.second)
        self._check("time")

    async def set_brightness(self, percent: int) -> None:
        await Common().setBrightness(max(5, min(100, percent)))
        self._check("brightness")

    async def screen(self, on: bool) -> None:
        await (Common().screenOn() if on else Common().screenOff())
        self._check("screen on" if on else "screen off")

    async def show_clock(self, style: int, color: Tuple[int, int, int], hour24: bool, date: bool) -> None:
        await Clock().setMode(style, date, hour24, *color)
        self._check("clock")

    async def enter_image_mode(self) -> None:
        await Image().setMode(1)
        self._check("image mode")

    async def show_png(self, png: bytes) -> None:
        # build the payload with the library but send it ourselves so errors surface
        await self.conn.send(data=Image()._createPayloads(bytearray(png)))
        self._check("image")
