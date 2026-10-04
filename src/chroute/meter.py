"""Terminal level meter. Runs on the main thread, never in the audio callback."""

from __future__ import annotations

import math
import shutil
import sys
from dataclasses import dataclass, field

FLOOR_DB = -60.0
BAR_WIDTH = 30
DECAY_DB_PER_FRAME = 1.5  # at ~10 fps, 15 dB/s fall-off

_RESET = "\x1b[0m"
_BOLD = "\x1b[1m"
_DIM = "\x1b[2m"
_RED = "\x1b[31m"
_YELLOW = "\x1b[33m"
_GREEN = "\x1b[32m"
_BG_RED = "\x1b[41;97;1m"


def to_db(peak: float) -> float:
    return 20.0 * math.log10(peak) if peak > 0 else -math.inf


def format_db(db: float) -> str:
    if db == -math.inf or db < -120:
        return "  -inf dBFS"
    return f"{db:+6.1f} dBFS" if db > 0 else f"{db:6.1f} dBFS"


def bar(db: float, width: int = BAR_WIDTH) -> str:
    if db == -math.inf:
        filled = 0
    else:
        frac = (min(db, 0.0) - FLOOR_DB) / -FLOOR_DB
        filled = max(0, min(width, round(frac * width)))
    return "█" * filled + "░" * (width - filled)


@dataclass
class Line:
    text: str
    style: str = ""


@dataclass
class MeterState:
    in_name: str
    out_name: str
    rate: float | None
    rate_ok: bool
    blocksize: int
    latency_ms: tuple[float, float] | None
    elapsed: float
    xruns: dict[str, int]
    route_labels: list[str]
    route_db: list[float]
    out_channels: list[int]  # 1-based active outputs
    out_db: list[float]
    out_clips: list[int]
    warnings: list[str] = field(default_factory=list)  # shown while the condition holds
    notes: list[str] = field(default_factory=list)  # one-off hints, stay on screen


def _fmt_elapsed(s: float) -> str:
    s = int(s)
    return f"{s // 3600:d}:{s // 60 % 60:02d}:{s % 60:02d}"


def render(st: MeterState) -> list[Line]:
    rate = f"{st.rate:,.0f} Hz" if st.rate else "? Hz"
    rate_style = _GREEN if st.rate_ok else _BG_RED
    lat = f"  latency in {st.latency_ms[0]:.1f} ms / out {st.latency_ms[1]:.1f} ms" if st.latency_ms else ""
    xr_total = sum(st.xruns.values())
    xr = (
        f"xruns {xr_total}  (in overflow {st.xruns.get('input_overflow', 0)}, "
        f"out underflow {st.xruns.get('output_underflow', 0)})"
    )
    lines = [
        Line(f"chroute  {st.in_name} → {st.out_name}   {_fmt_elapsed(st.elapsed)}   Ctrl-C to stop", _BOLD),
        Line(f"rate {rate} ({'confirmed' if st.rate_ok else 'MISMATCH'})  block {st.blocksize}{lat}", rate_style),
        Line(xr, _YELLOW if xr_total else _DIM),
        Line(""),
    ]
    label_w = max([len(lbl) for lbl in st.route_labels] + [len(f"out {c}") for c in st.out_channels])
    for label, db in zip(st.route_labels, st.route_db):
        lines.append(Line(f"  {label:<{label_w}}  [{bar(db)}] {format_db(db)}"))
    lines.append(Line(""))
    for ch, db, clips in zip(st.out_channels, st.out_db, st.out_clips):
        lbl = f"out {ch}"
        lines.append(
            Line(f"  {lbl:<{label_w}}  [{bar(db)}] {format_db(db)}  clips {clips}", _RED if clips else "")
        )
    if st.warnings or st.notes:
        lines.append(Line(""))
    for w in st.warnings:
        lines.append(Line(f"!! {w}", _BG_RED))
    for n in st.notes:
        lines.append(Line(f"hint: {n}", _YELLOW))
    return lines


class PeakDecay:
    """Holds a displayed level that falls slowly, so short peaks stay visible."""

    def __init__(self, n: int):
        self.db = [-math.inf] * n

    def update(self, peaks) -> list[float]:
        for i, p in enumerate(peaks):
            new = to_db(float(p))
            self.db[i] = max(new, self.db[i] - DECAY_DB_PER_FRAME)
            if self.db[i] < FLOOR_DB - 20:
                self.db[i] = -math.inf
        return list(self.db)


class TerminalMeter:
    """Redraws a block of lines in place using ANSI cursor movement."""

    def __init__(self, stream=None):
        self.out = stream or sys.stdout
        self._drawn = 0
        self.out.write("\x1b[?25l")  # hide cursor
        self.out.flush()

    def draw(self, lines: list[Line]) -> None:
        cols = shutil.get_terminal_size((100, 24)).columns
        buf = []
        if self._drawn:
            buf.append(f"\x1b[{self._drawn}F")  # cursor to start of first drawn line
        for line in lines:
            # Truncate so lines never wrap, which would break the cursor maths.
            text = line.text[: max(1, cols - 1)]
            buf.append("\x1b[2K" + (f"{line.style}{text}{_RESET}" if line.style else text) + "\n")
        for _ in range(self._drawn - len(lines)):  # clear leftovers if we shrank
            buf.append("\x1b[2K\n")
        extra = max(0, self._drawn - len(lines))
        if extra:
            buf.append(f"\x1b[{extra}F")
        self._drawn = len(lines)
        self.out.write("".join(buf))
        self.out.flush()

    def close(self) -> None:
        self.out.write("\x1b[?25h")  # show cursor
        self.out.flush()
