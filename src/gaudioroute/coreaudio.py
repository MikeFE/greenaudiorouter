"""Minimal read-only CoreAudio bindings (ctypes) for device sample rates.

PortAudio reports the rate *it* opened a stream at, but the thing that broke
Rocksmith was the device's *nominal* rate, which another app (GarageBand)
can change under us. This reads ``kAudioDevicePropertyNominalSampleRate``
directly from the HAL. It uses ctypes so there's no pyobjc dependency.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import functools


class CoreAudioError(RuntimeError):
    pass


def _fourcc(s: str) -> int:
    return int.from_bytes(s.encode("ascii"), "big")


_kAudioObjectSystemObject = 1
_kAudioObjectPropertyScopeGlobal = _fourcc("glob")
_kAudioObjectPropertyElementMain = 0
_kAudioHardwarePropertyDevices = _fourcc("dev#")
_kAudioObjectPropertyName = _fourcc("lnam")
_kAudioDevicePropertyNominalSampleRate = _fourcc("nsrt")
_kAudioDevicePropertyAvailableNominalSampleRates = _fourcc("nsr#")
_kCFStringEncodingUTF8 = 0x08000100


class _PropertyAddress(ctypes.Structure):
    _fields_ = [
        ("mSelector", ctypes.c_uint32),
        ("mScope", ctypes.c_uint32),
        ("mElement", ctypes.c_uint32),
    ]


class _AudioValueRange(ctypes.Structure):
    _fields_ = [("mMinimum", ctypes.c_double), ("mMaximum", ctypes.c_double)]


@functools.cache
def _libs():
    ca_path = ctypes.util.find_library("CoreAudio")
    cf_path = ctypes.util.find_library("CoreFoundation")
    if not ca_path or not cf_path:
        raise CoreAudioError("CoreAudio framework not found (gaudioroute only runs on macOS)")
    ca = ctypes.CDLL(ca_path)
    cf = ctypes.CDLL(cf_path)
    ca.AudioObjectGetPropertyDataSize.argtypes = [
        ctypes.c_uint32,
        ctypes.POINTER(_PropertyAddress),
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_uint32),
    ]
    ca.AudioObjectGetPropertyDataSize.restype = ctypes.c_int32
    ca.AudioObjectGetPropertyData.argtypes = [
        ctypes.c_uint32,
        ctypes.POINTER(_PropertyAddress),
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_uint32),
        ctypes.c_void_p,
    ]
    ca.AudioObjectGetPropertyData.restype = ctypes.c_int32
    cf.CFStringGetCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_long, ctypes.c_uint32]
    cf.CFStringGetCString.restype = ctypes.c_bool
    cf.CFRelease.argtypes = [ctypes.c_void_p]
    return ca, cf


def _addr(selector: int) -> _PropertyAddress:
    return _PropertyAddress(selector, _kAudioObjectPropertyScopeGlobal, _kAudioObjectPropertyElementMain)


def _check(status: int, what: str) -> None:
    if status != 0:
        raise CoreAudioError(f"CoreAudio error {status} reading {what}")


def device_ids() -> list[int]:
    ca, _ = _libs()
    addr = _addr(_kAudioHardwarePropertyDevices)
    size = ctypes.c_uint32()
    _check(
        ca.AudioObjectGetPropertyDataSize(_kAudioObjectSystemObject, ctypes.byref(addr), 0, None, ctypes.byref(size)),
        "device list size",
    )
    n = size.value // ctypes.sizeof(ctypes.c_uint32)
    ids = (ctypes.c_uint32 * n)()
    _check(
        ca.AudioObjectGetPropertyData(_kAudioObjectSystemObject, ctypes.byref(addr), 0, None, ctypes.byref(size), ids),
        "device list",
    )
    return list(ids[: size.value // ctypes.sizeof(ctypes.c_uint32)])


def device_name(device_id: int) -> str:
    ca, cf = _libs()
    addr = _addr(_kAudioObjectPropertyName)
    ref = ctypes.c_void_p()
    size = ctypes.c_uint32(ctypes.sizeof(ref))
    _check(
        ca.AudioObjectGetPropertyData(device_id, ctypes.byref(addr), 0, None, ctypes.byref(size), ctypes.byref(ref)),
        f"name of device {device_id}",
    )
    if not ref.value:
        return ""
    try:
        buf = ctypes.create_string_buffer(1024)
        if not cf.CFStringGetCString(ref, buf, len(buf), _kCFStringEncodingUTF8):
            return ""
        return buf.value.decode("utf-8")
    finally:
        cf.CFRelease(ref)


def nominal_rate(device_id: int) -> float:
    ca, _ = _libs()
    addr = _addr(_kAudioDevicePropertyNominalSampleRate)
    rate = ctypes.c_double()
    size = ctypes.c_uint32(ctypes.sizeof(rate))
    _check(
        ca.AudioObjectGetPropertyData(device_id, ctypes.byref(addr), 0, None, ctypes.byref(size), ctypes.byref(rate)),
        f"nominal sample rate of device {device_id}",
    )
    return rate.value


def available_rates(device_id: int) -> list[tuple[float, float]]:
    """Supported nominal rates as (min, max) ranges; usually min == max."""
    ca, _ = _libs()
    addr = _addr(_kAudioDevicePropertyAvailableNominalSampleRates)
    size = ctypes.c_uint32()
    _check(
        ca.AudioObjectGetPropertyDataSize(device_id, ctypes.byref(addr), 0, None, ctypes.byref(size)),
        f"available rates of device {device_id}",
    )
    n = size.value // ctypes.sizeof(_AudioValueRange)
    ranges = (_AudioValueRange * n)()
    _check(
        ca.AudioObjectGetPropertyData(device_id, ctypes.byref(addr), 0, None, ctypes.byref(size), ranges),
        f"available rates of device {device_id}",
    )
    return [(r.mMinimum, r.mMaximum) for r in ranges[: size.value // ctypes.sizeof(_AudioValueRange)]]


def find_device_id(name: str) -> int | None:
    """CoreAudio device ID for an exact device name, or None if it's gone."""
    for dev in device_ids():
        try:
            if device_name(dev) == name:
                return dev
        except CoreAudioError:
            continue
    return None


def nominal_rates_by_name() -> dict[str, float]:
    rates: dict[str, float] = {}
    for dev in device_ids():
        try:
            rates[device_name(dev)] = nominal_rate(dev)
        except CoreAudioError:
            continue
    return rates
