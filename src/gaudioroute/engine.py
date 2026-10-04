"""Stream setup, the audio callback, and sample-rate verification."""

from __future__ import annotations

import threading
from dataclasses import dataclass

import numpy as np

from gaudioroute import devices
from gaudioroute.devices import Device, format_rate, format_rate_ranges
from gaudioroute.routing import Router, RoutingPlan

# Order of the counters in Engine.xruns.
XRUN_FIELDS = ("input_overflow", "input_underflow", "output_underflow", "output_overflow")
_IN_OVERFLOW, _IN_UNDERFLOW, _OUT_UNDERFLOW, _OUT_OVERFLOW = range(4)

RATE_TOLERANCE = 0.5  # Hz


class EngineError(Exception):
    """Couldn't start or verify the stream. The message is for the user."""


@dataclass(frozen=True)
class RateProblem:
    device: str
    actual: float | None  # None = couldn't read it

    def message(self, wanted: float) -> str:
        if self.actual is None:
            return f"couldn't read the nominal sample rate of {self.device}"
        return f"{self.device} is at {format_rate(self.actual)}, not {format_rate(wanted)}"


class Engine:
    """One duplex ``sd.Stream`` across two devices, routing via :class:`Router`."""

    def __init__(
        self,
        in_dev: Device,
        out_dev: Device,
        plan: RoutingPlan,
        rate: int,
        blocksize: int,
        latency: float | str = "low",
    ):
        self.in_dev = in_dev
        self.out_dev = out_dev
        self.plan = plan
        self.rate = rate
        self.blocksize = blocksize
        self.latency = latency
        self.router = Router(plan)
        # Written by the callback, read by the meter. Plain overwrites, no locks.
        self.xruns = np.zeros(len(XRUN_FIELDS), dtype=np.int64)
        self.callback_count = np.zeros(1, dtype=np.int64)
        self.finished = threading.Event()
        self.stream = None

    # --- realtime ---------------------------------------------------------------

    def _callback(self, indata, outdata, frames, time, status) -> None:
        # No printing, locking, or numpy allocation in here.
        if status:
            if status.input_overflow:
                self.xruns[_IN_OVERFLOW] += 1
            if status.input_underflow:
                self.xruns[_IN_UNDERFLOW] += 1
            if status.output_underflow:
                self.xruns[_OUT_UNDERFLOW] += 1
            if status.output_overflow:
                self.xruns[_OUT_OVERFLOW] += 1
        self.router.process(indata, outdata)
        self.callback_count[0] += 1

    # --- control ----------------------------------------------------------------

    def preflight(self) -> None:
        """Check that both devices support the rate before opening anything."""
        for dev in {self.in_dev.name: self.in_dev, self.out_dev.name: self.out_dev}.values():
            ranges = devices.available_rates(dev.name)
            if ranges and not devices.supports_rate(ranges, self.rate):
                raise EngineError(
                    f"{dev.name} doesn't support {format_rate(self.rate)}. "
                    f"Supported rates: {format_rate_ranges(ranges)}"
                )

    def start(self) -> None:
        import sounddevice as sd

        self.preflight()
        # change_device_parameters: make PortAudio set the device's nominal
        # rate instead of resampling. fail_if_conversion_required: if it
        # can't, fail loudly instead of quietly resampling.
        ca = sd.CoreAudioSettings(change_device_parameters=True, fail_if_conversion_required=True)
        try:
            self.stream = sd.Stream(
                device=(self.in_dev.index, self.out_dev.index),
                samplerate=self.rate,
                blocksize=self.blocksize,
                channels=(self.plan.n_in, self.plan.n_out),
                dtype="float32",
                latency=self.latency,
                extra_settings=(ca, ca),
                callback=self._callback,
                finished_callback=self.finished.set,
            )
            self.stream.start()
        except sd.PortAudioError as e:
            self.close()
            rates = devices.nominal_rates()
            now = ", ".join(
                f"{n} is at {format_rate(rates.get(n))}" for n in dict.fromkeys((self.in_dev.name, self.out_dev.name))
            )
            raise EngineError(
                f"Couldn't open {self.in_dev.name} -> {self.out_dev.name} at {format_rate(self.rate)} "
                f"without resampling: {e}\n"
                f"Right now {now}. Another app (GarageBand, a DAW, a browser tab) may be holding a "
                f"device at a different rate. Quit it, or set the rate in Audio MIDI Setup, and try again."
            ) from None

    def close(self) -> None:
        if self.stream is not None:
            try:
                self.stream.stop()
            except Exception:
                pass
            try:
                self.stream.close()
            except Exception:
                pass
            self.stream = None

    # --- verification -----------------------------------------------------------

    @property
    def stream_rate(self) -> float | None:
        return None if self.stream is None else float(self.stream.samplerate)

    @property
    def latency_ms(self) -> tuple[float, float] | None:
        if self.stream is None:
            return None
        lin, lout = self.stream.latency
        return lin * 1000.0, lout * 1000.0

    def check_stream_rate(self) -> None:
        actual = self.stream_rate
        if actual is None or abs(actual - self.rate) > RATE_TOLERANCE:
            raise EngineError(
                f"Stream opened at {format_rate(actual)} instead of {format_rate(self.rate)}; refusing to resample."
            )

    def rate_problems(self) -> list[RateProblem]:
        """Devices whose nominal rate (as macOS reports it) isn't the requested rate."""
        rates = devices.nominal_rates()
        problems = []
        for name in dict.fromkeys((self.in_dev.name, self.out_dev.name)):
            actual = rates.get(name)
            if actual is None or abs(actual - self.rate) > RATE_TOLERANCE:
                problems.append(RateProblem(name, actual))
        return problems

    def missing_devices(self) -> list[str]:
        """Devices CoreAudio no longer has (unplugged)."""
        return [
            name
            for name in dict.fromkeys((self.in_dev.name, self.out_dev.name))
            if devices.device_present(name) is False
        ]
