"""Keeps a Bluetooth connection to the display and wraps the commands the service uses."""

import asyncio
from datetime import datetime
from functools import partial
import logging
from typing import Awaitable, Optional, Tuple

from bleak import BleakClient
from idotmatrix import Clock, Common, ConnectionManager, Eco, FullscreenColor, Image
from idotmatrix.const import UUID_READ_DATA, UUID_WRITE_DATA

logger = logging.getLogger("service.display")

CONNECT_SETTLE_SECONDS = 2
# the display drops commands that arrive while it's still handling the previous one
COMMAND_GAP_SECONDS = 0.5


class Display:
    """The idotmatrix library logs and swallows send errors, so after each command we
    check is_connected ourselves to notice a dropped connection."""

    def __init__(self, address: str, with_response: bool = True) -> None:
        self.address = address
        self.conn = ConnectionManager()
        self.disconnected = asyncio.Event()
        self.with_response = with_response

    @property
    def with_response(self) -> bool:
        return self.conn.send.keywords["response"]

    @with_response.setter
    def with_response(self, value: bool) -> None:
        # the library's commands all call conn.send(data=...) on this shared instance, so
        # overriding it here picks the write type for every command
        self.conn.send = partial(ConnectionManager.send, self.conn, response=value)

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
        await self._drop_stale_connection(address)
        # a fresh client per attempt, so a stale BlueZ connection never gets reused
        self.conn.address = address
        self.conn.client = BleakClient(address, disconnected_callback=self._on_disconnect, timeout=20)
        await self.conn.client.connect()
        self.disconnected.clear()
        # the display ignores writes sent straight after connecting
        await asyncio.sleep(CONNECT_SETTLE_SECONDS)
        write = self.conn.client.services.get_characteristic(UUID_WRITE_DATA)
        logger.info(
            f"connected to {address} (write properties: {', '.join(write.properties)}, "
            f"chunk size: {write.max_write_without_response_size}, "
            f"sending {'with' if self.with_response else 'without'} response)"
        )
        read = self.conn.client.services.get_characteristic(UUID_READ_DATA)
        if read and "notify" in read.properties:
            await self.conn.client.start_notify(read, self._on_notify)

    def _on_notify(self, _char, data: bytearray) -> None:
        logger.info(f"display replied: {data.hex(' ')}")

    async def _drop_stale_connection(self, address: str) -> None:
        """BlueZ keeps a connection open when a previous run died without disconnecting,
        and a connected display stops advertising, so it can't be found until it's dropped."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "bluetoothctl", "disconnect", address, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
            )
            await asyncio.wait_for(proc.wait(), 10)
        except FileNotFoundError:
            logger.warning("bluetoothctl not installed, can't clear stale connections")
        except asyncio.TimeoutError:
            proc.kill()

    async def disconnect(self) -> None:
        if self.connected:
            await self.conn.client.disconnect()

    def _check(self, what: str, result=None) -> None:
        if not self.connected:
            raise ConnectionError(f"lost connection while sending {what}")
        # library commands return False when they swallowed an error
        if result is False:
            raise RuntimeError(f"could not send {what}, see the idotmatrix error above")

    async def _send(self, what: str, command: Awaitable) -> None:
        result = await command
        self._check(what, result)
        logger.info(f"sent {what}")
        await asyncio.sleep(COMMAND_GAP_SECONDS)

    async def sync_time(self, now: datetime) -> None:
        await self._send(
            f"time {now:%Y-%m-%d %H:%M:%S}",
            Common().setTime(now.year, now.month, now.day, now.hour, now.minute, now.second),
        )

    async def set_brightness(self, percent: int) -> None:
        await self._send(f"brightness {percent}%", Common().setBrightness(max(5, min(100, percent))))

    async def disable_builtin_schedule(self) -> None:
        """The display has its own sleep schedule (set in the phone app) that blanks the
        screen whatever we send. The service does its own scheduling, so turn it off."""
        await self._send("built-in sleep schedule off", Eco().setMode(0, 0, 0, 0, 0, 100))

    async def screen(self, on: bool) -> None:
        await self._send("screen on" if on else "screen off", Common().screenOn() if on else Common().screenOff())

    async def show_clock(self, style: int, color: Tuple[int, int, int], hour24: bool, date: bool) -> None:
        await self._send(f"clock style {style}", Clock().setMode(style, date, hour24, *color))

    async def fill(self, r: int, g: int, b: int) -> None:
        await self._send(f"fill {r}-{g}-{b}", FullscreenColor().setMode(r, g, b))

    async def enter_image_mode(self) -> None:
        await self._send("image mode", Image().setMode(1))

    async def show_png(self, png: bytes) -> None:
        # build the payload with the library but send it ourselves so errors surface
        await self.conn.send(data=Image()._createPayloads(bytearray(png)))
        self._check("image")
        logger.info(f"sent image ({len(png)} bytes)")
