"""Device listing, name matching, and nominal-rate queries."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from typing import Literal

from chroute import coreaudio

Kind = Literal["input", "output"]


class DeviceError(Exception):
    """No matching device, ambiguous match, etc. The message is for the user."""


@dataclass(frozen=True)
class Device:
    index: int
    name: str
    hostapi: str
    max_in: int
    max_out: int
    default_rate: float

    def channels(self, kind: Kind) -> int:
        return self.max_in if kind == "input" else self.max_out


def list_devices() -> list[Device]:
    import sounddevice as sd

    hostapis = sd.query_hostapis()
    return [
        Device(
            index=d["index"],
            name=d["name"],
            hostapi=hostapis[d["hostapi"]]["name"],
            max_in=d["max_input_channels"],
            max_out=d["max_output_channels"],
            default_rate=d["default_samplerate"],
        )
        for d in sd.query_devices()
    ]


def _describe(d: Device) -> str:
    return f"[{d.index}] {d.name} ({d.max_in} in, {d.max_out} out)"


def match_device(query: str, devices: list[Device], kind: Kind) -> Device:
    """Find the one device of ``kind`` whose name contains ``query``, ignoring case.

    If several devices contain the query but exactly one name equals it
    (ignoring case), that one wins. Otherwise more than one match is an error.
    """
    q = query.casefold()
    capable = [d for d in devices if d.channels(kind) > 0]
    matches = [d for d in capable if q in d.name.casefold()]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        exact = [d for d in matches if d.name.casefold() == q]
        if len(exact) == 1:
            return exact[0]
        lines = "\n".join(f"  {_describe(d)}" for d in matches)
        raise DeviceError(
            f"{kind.capitalize()} device name {query!r} is ambiguous; it matches:\n{lines}\n"
            f"Use a longer part of the name."
        )
    wrong_side = [d for d in devices if q in d.name.casefold()]
    hint = ""
    if wrong_side:
        names = ", ".join(repr(d.name) for d in wrong_side)
        hint = f"{names} matched, but it has no {kind} channels.\n"
    if capable:
        lines = "\n".join(f"  {_describe(d)}" for d in capable)
        avail = f"Available {kind} devices:\n{lines}"
    else:
        avail = f"No {kind} devices found."
    raise DeviceError(f"No {kind} device matches {query!r}.\n{hint}{avail}")


# --- nominal sample rate -------------------------------------------------------


def _system_profiler_rates() -> dict[str, float]:
    out = subprocess.run(
        ["system_profiler", "SPAudioDataType", "-json"],
        capture_output=True,
        text=True,
        timeout=15,
        check=True,
    ).stdout
    rates: dict[str, float] = {}
    for section in json.loads(out).get("SPAudioDataType", []):
        for item in section.get("_items", []):
            if "_name" in item and "coreaudio_device_srate" in item:
                rates[item["_name"]] = float(item["coreaudio_device_srate"])
    return rates


def nominal_rates() -> dict[str, float]:
    """Device name -> nominal sample rate as macOS reports it.

    Reads CoreAudio directly; falls back to ``system_profiler`` (slow) if
    that fails. Returns ``{}`` if neither works.
    """
    try:
        return coreaudio.nominal_rates_by_name()
    except (coreaudio.CoreAudioError, OSError):
        pass
    try:
        return _system_profiler_rates()
    except (OSError, subprocess.SubprocessError, ValueError):
        return {}


def nominal_rate(name: str) -> float | None:
    return nominal_rates().get(name)


def available_rates(name: str) -> list[float] | None:
    """Discrete nominal rates the device supports, or None if unknown."""
    try:
        dev = coreaudio.find_device_id(name)
        if dev is None:
            return None
        rates: set[float] = set()
        for lo, hi in coreaudio.available_rates(dev):
            rates.update({lo, hi})
        return sorted(rates)
    except (coreaudio.CoreAudioError, OSError):
        return None


def device_present(name: str) -> bool | None:
    """Whether CoreAudio still has a device with this name (None if unknown).

    PortAudio caches its device list at startup, so ask CoreAudio instead.
    """
    try:
        return coreaudio.find_device_id(name) is not None
    except (coreaudio.CoreAudioError, OSError):
        return None


def format_rate(rate: float | None) -> str:
    if rate is None:
        return "?"
    return f"{rate:,.0f} Hz" if rate == int(rate) else f"{rate:,.1f} Hz"
