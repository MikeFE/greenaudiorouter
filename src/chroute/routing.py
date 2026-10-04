"""Route parsing, validation, and the realtime routing kernel.

Pure numpy, no audio I/O, so everything here is unit-testable.

Channel numbers in the user-facing API are 1-based (to match the labels
printed on hardware). Internally everything is 0-based.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

import numpy as np


class RouteError(ValueError):
    """A route spec is malformed or doesn't fit the devices."""


@dataclass(frozen=True)
class Route:
    src: int  # 1-based input channel
    dst: int  # 1-based output channel
    gain: float = 1.0

    def __str__(self) -> str:
        if self.gain == 1.0:
            return f"{self.src}:{self.dst}"
        return f"{self.src}:{self.dst}@{self.gain:g}"


_ROUTE_RE = re.compile(r"^\s*(\d+)\s*:\s*(\d+)\s*(?:@\s*([^\s]+)\s*)?$")


def parse_route(spec: str) -> Route:
    """Parse ``"X:Y"`` or ``"X:Y@GAIN"`` into a :class:`Route`."""
    if not isinstance(spec, str):
        raise RouteError(f"route must be a string like '2:1' or '2:1@0.8', got {spec!r}")
    m = _ROUTE_RE.match(spec)
    if not m:
        raise RouteError(
            f"invalid route {spec!r}: expected IN:OUT or IN:OUT@GAIN, e.g. '2:1' or '2:1@0.8'"
        )
    src, dst = int(m.group(1)), int(m.group(2))
    if src < 1 or dst < 1:
        raise RouteError(f"invalid route {spec!r}: channel numbers start at 1")
    gain = 1.0
    if m.group(3) is not None:
        try:
            gain = float(m.group(3))
        except ValueError:
            raise RouteError(f"invalid route {spec!r}: gain {m.group(3)!r} is not a number") from None
        if not math.isfinite(gain):
            raise RouteError(f"invalid route {spec!r}: gain must be a finite number")
    return Route(src, dst, gain)


def parse_routes(specs: list[str]) -> list[Route]:
    return [parse_route(s) for s in specs]


def validate_routes(
    routes: list[Route],
    in_channels: int,
    out_channels: int,
    in_name: str = "input device",
    out_name: str = "output device",
) -> None:
    """Raise :class:`RouteError` if any route doesn't fit the devices."""
    if not routes:
        raise RouteError("no routes given; add at least one --route IN:OUT (e.g. --route 2:1)")
    for r in routes:
        if r.src > in_channels:
            raise RouteError(
                f"{in_name} has {_plural(in_channels, 'input channel')}; route {r} is invalid."
            )
        if r.dst > out_channels:
            raise RouteError(
                f"{out_name} has {_plural(out_channels, 'output channel')}; route {r} is invalid."
            )


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" + ("" if n == 1 else "s")


@dataclass(frozen=True)
class RoutingPlan:
    """Precomputed routing: how many channels to open and the gain matrix."""

    routes: tuple[Route, ...]
    n_in: int  # input channels to open (1..n_in)
    n_out: int  # output channels to open (1..n_out)
    src_idx: np.ndarray  # (n_routes,) 0-based input channel per route
    dst_idx: np.ndarray  # (n_routes,) 0-based output channel per route
    gains: np.ndarray  # (n_routes,) float32
    matrix: np.ndarray  # (n_in, n_out) float32; out = in @ matrix

    @property
    def active_outputs(self) -> list[int]:
        """Sorted 1-based output channels that have at least one route."""
        return sorted({r.dst for r in self.routes})


def build_plan(routes: list[Route]) -> RoutingPlan:
    if not routes:
        raise RouteError("no routes given")
    # PortAudio opens channels 1..N, so open up to the highest channel used.
    n_in = max(r.src for r in routes)
    n_out = max(r.dst for r in routes)
    src_idx = np.array([r.src - 1 for r in routes], dtype=np.intp)
    dst_idx = np.array([r.dst - 1 for r in routes], dtype=np.intp)
    gains = np.array([r.gain for r in routes], dtype=np.float32)
    matrix = np.zeros((n_in, n_out), dtype=np.float32)
    # Duplicate (src, dst) pairs sum, same as any other fan-in.
    np.add.at(matrix, (src_idx, dst_idx), gains)
    return RoutingPlan(tuple(routes), n_in, n_out, src_idx, dst_idx, gains, matrix)


class Router:
    """Realtime routing kernel.

    :meth:`process` is called from the audio callback. It doesn't allocate
    numpy buffers, take locks, or print. Every array it writes is
    preallocated here.

    Meter data is shared through plain numpy arrays. The callback folds each
    block's peaks into the ``*_peak`` arrays with a running max, and the meter
    thread reads and resets them with :meth:`take_peaks`. A race between the
    two can lose one block's peak, which a meter doesn't care about.
    """

    def __init__(self, plan: RoutingPlan):
        self.plan = plan
        n_in, n_out, n_routes = plan.n_in, plan.n_out, len(plan.routes)
        self._matrix = plan.matrix
        self._src_idx = plan.src_idx
        self._abs_gains = np.abs(plan.gains).astype(np.float32)

        # Shared with the meter thread.
        self.in_peak = np.zeros(n_in, dtype=np.float32)
        self.route_peak = np.zeros(n_routes, dtype=np.float32)
        self.out_peak = np.zeros(n_out, dtype=np.float32)  # pre-clip
        self.clip_counts = np.zeros(n_out, dtype=np.int64)

        # Callback scratch.
        self._in_max = np.zeros(n_in, dtype=np.float32)
        self._in_min = np.zeros(n_in, dtype=np.float32)
        self._out_max = np.zeros(n_out, dtype=np.float32)
        self._out_min = np.zeros(n_out, dtype=np.float32)
        self._route_tmp = np.zeros(n_routes, dtype=np.float32)
        self._clipped = np.zeros(n_out, dtype=np.bool_)

    def process(self, indata: np.ndarray, outdata: np.ndarray) -> None:
        """Route one block. ``indata`` is (frames, n_in), ``outdata`` (frames, n_out)."""
        # Mix: fan-out, fan-in summing, gain, and silence on unrouted
        # outputs (all-zero matrix columns) in one matmul.
        np.matmul(indata, self._matrix, out=outdata)

        # Input peaks -> per-route peaks (input peak x |gain|).
        np.amax(indata, axis=0, out=self._in_max)
        np.amin(indata, axis=0, out=self._in_min)
        np.negative(self._in_min, out=self._in_min)
        np.maximum(self._in_max, self._in_min, out=self._in_max)
        np.maximum(self.in_peak, self._in_max, out=self.in_peak)
        np.take(self._in_max, self._src_idx, out=self._route_tmp, mode="clip")
        np.multiply(self._route_tmp, self._abs_gains, out=self._route_tmp)
        np.maximum(self.route_peak, self._route_tmp, out=self.route_peak)

        # Output peaks before clipping, so the meter can show overs.
        np.amax(outdata, axis=0, out=self._out_max)
        np.amin(outdata, axis=0, out=self._out_min)
        np.negative(self._out_min, out=self._out_min)
        np.maximum(self._out_max, self._out_min, out=self._out_max)
        np.maximum(self.out_peak, self._out_max, out=self.out_peak)

        # A clip event is one block in which a channel exceeded full scale.
        np.greater(self._out_max, 1.0, out=self._clipped)
        np.add(self.clip_counts, self._clipped, out=self.clip_counts)
        np.clip(outdata, -1.0, 1.0, out=outdata)

    def take_peaks(self) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Return (in_peak, route_peak, out_peak) since the last call and reset.

        Call this from the meter thread, never from the callback.
        """
        in_p, route_p, out_p = self.in_peak.copy(), self.route_peak.copy(), self.out_peak.copy()
        self.in_peak.fill(0.0)
        self.route_peak.fill(0.0)
        self.out_peak.fill(0.0)
        return in_p, route_p, out_p
