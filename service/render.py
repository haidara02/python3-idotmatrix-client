"""Draws 32x32 train board frames with a hand-made 3x5 pixel font."""

from datetime import datetime
import io
from typing import List, Optional, Tuple

from PIL import Image

from .trains import Departure, minutes_until

SIZE = 32
Color = Tuple[int, int, int]

WHITE: Color = (255, 255, 255)
GREY: Color = (120, 120, 120)
GREEN: Color = (0, 200, 60)
AMBER: Color = (255, 150, 0)
RED: Color = (255, 0, 0)
BLACK: Color = (0, 0, 0)

# 3x5 glyphs, one string per row, "#" = lit pixel.
FONT = {
    "0": ["###", "#.#", "#.#", "#.#", "###"],
    "1": [".#.", "##.", ".#.", ".#.", "###"],
    "2": ["###", "..#", "###", "#..", "###"],
    "3": ["###", "..#", "###", "..#", "###"],
    "4": ["#.#", "#.#", "###", "..#", "..#"],
    "5": ["###", "#..", "###", "..#", "###"],
    "6": ["###", "#..", "###", "#.#", "###"],
    "7": ["###", "..#", "..#", ".#.", ".#."],
    "8": ["###", "#.#", "###", "#.#", "###"],
    "9": ["###", "#.#", "###", "..#", "###"],
    ":": ["...", ".#.", "...", ".#.", "..."],
    "+": ["...", ".#.", "###", ".#.", "..."],
    "-": ["...", "...", "###", "...", "..."],
    " ": ["...", "...", "...", "...", "..."],
    "A": [".#.", "#.#", "###", "#.#", "#.#"],
    "B": ["##.", "#.#", "##.", "#.#", "##."],
    "C": [".##", "#..", "#..", "#..", ".##"],
    "D": ["##.", "#.#", "#.#", "#.#", "##."],
    "E": ["###", "#..", "##.", "#..", "###"],
    "F": ["###", "#..", "##.", "#..", "#.."],
    "G": [".##", "#..", "#.#", "#.#", ".##"],
    "H": ["#.#", "#.#", "###", "#.#", "#.#"],
    "I": ["###", ".#.", ".#.", ".#.", "###"],
    "J": ["..#", "..#", "..#", "#.#", ".#."],
    "K": ["#.#", "#.#", "##.", "#.#", "#.#"],
    "L": ["#..", "#..", "#..", "#..", "###"],
    "M": ["#.#", "###", "###", "#.#", "#.#"],
    "N": ["##.", "#.#", "#.#", "#.#", "#.#"],
    "O": [".#.", "#.#", "#.#", "#.#", ".#."],
    "P": ["##.", "#.#", "##.", "#..", "#.."],
    "Q": [".#.", "#.#", "#.#", "##.", ".##"],
    "R": ["##.", "#.#", "##.", "#.#", "#.#"],
    "S": [".##", "#..", ".#.", "..#", "##."],
    "T": ["###", ".#.", ".#.", ".#.", ".#."],
    "U": ["#.#", "#.#", "#.#", "#.#", "###"],
    "V": ["#.#", "#.#", "#.#", "#.#", ".#."],
    "W": ["#.#", "#.#", "###", "###", "#.#"],
    "X": ["#.#", "#.#", ".#.", "#.#", "#.#"],
    "Y": ["#.#", "#.#", ".#.", ".#.", ".#."],
    "Z": ["###", "..#", ".#.", "#..", "###"],
}


def text_width(text: str, scale: int = 1) -> int:
    return max(len(text) * 4 * scale - scale, 0)


def draw_text(img: Image.Image, x: int, y: int, text: str, color: Color, scale: int = 1) -> None:
    for char in text.upper():
        glyph = FONT.get(char, FONT[" "])
        for row, line in enumerate(glyph):
            for col, cell in enumerate(line):
                if cell != "#":
                    continue
                for dy in range(scale):
                    for dx in range(scale):
                        px, py = x + col * scale + dx, y + row * scale + dy
                        if 0 <= px < SIZE and 0 <= py < SIZE:
                            img.putpixel((px, py), color)
        x += 4 * scale


def draw_centered(img: Image.Image, y: int, text: str, color: Color, scale: int = 1) -> None:
    draw_text(img, (SIZE - text_width(text, scale)) // 2, y, text, color, scale)


def _clock_text(now: datetime, hour24: bool) -> str:
    if hour24:
        return now.strftime("%H:%M")
    return f"{now.hour % 12 or 12}:{now.minute:02d}"


def _status(departure: Departure) -> Tuple[str, Color]:
    if departure.estimated is None:
        return "SCHED", GREY
    delay = departure.delay_minutes
    if delay >= 1:
        return f"+{delay} LATE", AMBER if delay < 5 else RED
    return "ON TIME", GREEN


def render_board(
    now: datetime,
    departures: Optional[List[Departure]],
    line: str,
    line_color: Color,
    walk_minutes: int = 0,
    hour24: bool = False,
) -> Image.Image:
    """Renders the board. departures=None means the data is unavailable."""
    img = Image.new("RGB", (SIZE, SIZE), BLACK)

    # header: line badge on the left, current time on the right
    for y in range(7):
        for x in range(SIZE):
            img.putpixel((x, y), line_color)
    draw_text(img, 1, 1, line, WHITE)
    clock = _clock_text(now, hour24)
    draw_text(img, SIZE - text_width(clock) - 1, 1, clock, WHITE)

    if departures is None:
        draw_centered(img, 12, "NO", RED)
        draw_centered(img, 19, "DATA", RED)
        return img

    upcoming = [
        (minutes_until(d.due, now), d) for d in departures if minutes_until(d.due, now) >= walk_minutes
    ]
    if not upcoming:
        draw_centered(img, 12, "NO", GREY)
        draw_centered(img, 19, "TRAINS", GREY)
        return img

    minutes, departure = upcoming[0]
    color = WHITE
    if walk_minutes:
        slack = minutes - walk_minutes
        color = RED if slack <= 1 else AMBER if slack <= 3 else WHITE

    # next train, large
    if minutes <= 0:
        draw_centered(img, 9, "NOW", color, scale=2)
    else:
        big = str(min(minutes, 99))
        draw_text(img, 1, 9, big, color, scale=2)
        draw_text(img, 1 + text_width(big, 2) + 2, 14, "MIN", GREY)

    # the two after that
    following = " ".join(str(m) for m, _ in upcoming[1:3])
    if following:
        draw_text(img, 1, 21, following, GREY)

    status, status_color = _status(departure)
    draw_centered(img, 27, status, status_color)
    return img


def to_png(img: Image.Image) -> bytes:
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    return buffer.getvalue()
