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
                        if 0 <= px < img.width and 0 <= py < img.height:
                            img.putpixel((px, py), color)
        x += 4 * scale


def draw_centered(img: Image.Image, y: int, text: str, color: Color, scale: int = 1) -> None:
    draw_text(img, (img.width - text_width(text, scale)) // 2, y, text, color, scale)


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


def _catchable(departures: List[Departure], now: datetime, walk_minutes: int) -> List[Tuple[int, Departure]]:
    """(minutes away, departure) for trains you can still reach on foot."""
    return [(m, d) for d in departures if (m := minutes_until(d.due, now)) >= walk_minutes]


# trains further away than this show their departure time rather than minutes
FAR_MINUTES = 60


def _departs_at(departure: Departure, now: datetime, hour24: bool, max_width: int) -> str:
    text = _clock_text(departure.due.astimezone(now.tzinfo), hour24)
    return text if text_width(text) <= max_width else text.replace(":", "")


def _urgency(minutes: int, walk_minutes: int) -> Color:
    if not walk_minutes:
        return WHITE
    slack = minutes - walk_minutes
    return RED if slack <= 1 else AMBER if slack <= 3 else WHITE


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

    upcoming = _catchable(departures, now, walk_minutes)
    if not upcoming:
        draw_centered(img, 12, "NO", GREY)
        draw_centered(img, 19, "TRAINS", GREY)
        return img

    minutes, departure = upcoming[0]
    color = _urgency(minutes, walk_minutes)

    # next train, large
    if minutes <= 0:
        draw_centered(img, 9, "NOW", color, scale=2)
    elif minutes >= FAR_MINUTES:
        departs = _clock_text(departure.due.astimezone(now.tzinfo), hour24)
        if text_width(departs, 2) <= SIZE - 2:
            draw_centered(img, 9, departs, color, scale=2)
        else:
            draw_centered(img, 12, departs, color)
    else:
        big = str(minutes)
        draw_text(img, 1, 9, big, color, scale=2)
        draw_text(img, 1 + text_width(big, 2) + 2, 14, "MIN", GREY)

    # the two after that
    following = " ".join(str(m) for m, _ in upcoming[1:3] if m < FAR_MINUTES)
    if following:
        draw_text(img, 1, 21, following, GREY)

    status, status_color = _status(departure)
    draw_centered(img, 27, status, status_color)
    return img


def render_compact(
    now: datetime,
    departures: Optional[List[Departure]],
    line_color: Color,
    walk_minutes: int = 0,
    hour24: bool = False,
) -> Image.Image:
    """16x16 layout: the next train's minutes large, a rule in the line colour, then the
    train after it on the left and the next train's delay on the right. Trains an hour or
    more away show their departure time instead."""
    img = Image.new("RGB", (16, 16), BLACK)
    if departures is None:
        draw_centered(img, 2, "NO", RED)
        draw_centered(img, 9, "DATA", RED)
        return img

    for x in range(img.width):
        img.putpixel((x, 10), line_color)

    upcoming = _catchable(departures, now, walk_minutes)
    if not upcoming:
        draw_centered(img, 0, "--", GREY, scale=2)
        return img

    minutes, departure = upcoming[0]
    if minutes >= FAR_MINUTES:
        draw_centered(img, 3, _departs_at(departure, now, hour24, img.width), WHITE)
    else:
        draw_centered(img, 0, str(max(0, minutes)), _urgency(minutes, walk_minutes), scale=2)

    if len(upcoming) > 1 and upcoming[1][0] < FAR_MINUTES:
        draw_text(img, 0, 11, str(upcoming[1][0]), GREY)
    delay = departure.delay_minutes
    if delay >= 1:
        late = f"+{min(delay, 9)}"
        draw_text(img, img.width - text_width(late), 11, late, AMBER if delay < 5 else RED)
    return img


def test_pattern(size: int) -> Image.Image:
    """A red border, white diagonals and the size in the middle, to check what the panel accepts."""
    img = Image.new("RGB", (size, size), BLACK)
    for i in range(size):
        for x, y in ((i, 0), (i, size - 1), (0, i), (size - 1, i)):
            img.putpixel((x, y), RED)
        img.putpixel((i, i), WHITE)
        img.putpixel((size - 1 - i, i), WHITE)
    scale = max(1, size // 16)
    label = str(size)
    draw_text(img, (size - text_width(label, scale)) // 2, (size - 5 * scale) // 2, label, GREEN, scale)
    return img


def to_png(img: Image.Image, size: int = SIZE) -> bytes:
    """Encodes a frame, scaled to the panel's size if it was drawn smaller."""
    if img.width != size:
        img = img.resize((size, size), Image.NEAREST)
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    return buffer.getvalue()
