import pytest

from chroute.devices import Device, DeviceError, match_device

DEVICES = [
    Device(0, "Mike’s iPhone Microphone", "Core Audio", 1, 0, 48000.0),
    Device(1, "Scarlett Solo USB", "Core Audio", 2, 2, 48000.0),
    Device(2, "BlackHole 2ch", "Core Audio", 2, 2, 48000.0),
    Device(3, "MacBook Air Microphone", "Core Audio", 1, 0, 44100.0),
    Device(4, "MacBook Air Speakers", "Core Audio", 0, 2, 44100.0),
    Device(5, "BlackHole 16ch", "Core Audio", 16, 16, 48000.0),
]


def test_case_insensitive_substring():
    assert match_device("scarlett", DEVICES, "input").index == 1
    assert match_device("SOLO", DEVICES, "output").index == 1


def test_kind_filters_candidates():
    # "MacBook Air" matches mic and speakers, but only one has inputs.
    assert match_device("MacBook Air", DEVICES, "input").index == 3
    assert match_device("MacBook Air", DEVICES, "output").index == 4


def test_ambiguous_lists_candidates():
    with pytest.raises(DeviceError) as e:
        match_device("BlackHole", DEVICES, "output")
    msg = str(e.value)
    assert "ambiguous" in msg
    assert "BlackHole 2ch" in msg and "BlackHole 16ch" in msg
    assert "Scarlett" not in msg


def test_exact_name_breaks_tie():
    devices = DEVICES + [Device(6, "BlackHole 2ch Aggregate", "Core Audio", 2, 2, 48000.0)]
    assert match_device("blackhole 2ch", devices, "output").index == 2


def test_no_match_lists_devices_of_that_kind():
    with pytest.raises(DeviceError) as e:
        match_device("Focusrite", DEVICES, "input")
    msg = str(e.value)
    assert "No input device matches 'Focusrite'" in msg
    assert "Scarlett Solo USB" in msg
    assert "Speakers" not in msg  # not an input device


def test_no_match_explains_wrong_side():
    with pytest.raises(DeviceError, match="'MacBook Air Speakers' matched, but it has no input channels"):
        match_device("Speakers", DEVICES, "input")
