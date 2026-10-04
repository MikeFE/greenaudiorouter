import math

import pytest

from chroute.cli import build_parser, normalize_argv
from chroute.meter import bar, format_db, render, MeterState, PeakDecay, to_db


@pytest.mark.parametrize(
    "argv,expected",
    [
        (["--preset", "rocksmith"], ["run", "--preset", "rocksmith"]),
        (["--in", "x"], ["run", "--in", "x"]),
        (["list"], ["list"]),
        (["presets"], ["presets"]),
        (["run", "--no-meter"], ["run", "--no-meter"]),
        (["--help"], ["--help"]),
        ([], []),
    ],
)
def test_default_subcommand_is_run(argv, expected):
    assert normalize_argv(argv) == expected


def test_run_flags_parse():
    args = build_parser().parse_args(
        normalize_argv(
            ["--in", "Scarlett", "--out", "BlackHole", "--route", "2:1", "--route", "2:2@0.5", "--rate", "48000"]
        )
    )
    assert (args.in_name, args.out_name, args.routes, args.rate) == ("Scarlett", "BlackHole", ["2:1", "2:2@0.5"], 48000)
    assert args.blocksize is None and args.preset is None and args.latency is None  # unset -> preset/defaults
    assert args.no_meter is False


def test_bad_rate_rejected():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["run", "--rate", "0"])


# --- meter formatting ----------------------------------------------------------


def test_db_and_bar():
    assert to_db(1.0) == 0.0
    assert to_db(0.0) == -math.inf
    assert format_db(-math.inf).strip() == "-inf dBFS"
    assert format_db(-12.34) == " -12.3 dBFS"
    assert format_db(2.0) == "  +2.0 dBFS"
    assert bar(0.0, 10) == "█" * 10
    assert bar(-30.0, 10) == "█" * 5 + "░" * 5
    assert bar(-math.inf, 10) == "░" * 10
    assert bar(+6.0, 10) == "█" * 10


def test_peak_decay():
    d = PeakDecay(1)
    assert d.update([1.0]) == [0.0]
    assert d.update([0.0])[0] == pytest.approx(-1.5)  # falls slowly
    assert d.update([1.0]) == [0.0]


def test_render_contains_routes_outputs_and_status():
    st = MeterState(
        in_name="Scarlett Solo USB",
        out_name="BlackHole 2ch",
        rate=48000.0,
        rate_ok=True,
        blocksize=128,
        latency_ms=(10.0, 5.0),
        elapsed=61,
        xruns={"input_overflow": 1, "output_underflow": 2},
        route_labels=["in 2 → out 1", "in 2 → out 2"],
        route_db=[-12.3, -math.inf],
        out_channels=[1, 2],
        out_db=[-12.3, -math.inf],
        out_clips=[0, 3],
        warnings=["BlackHole 2ch is at 44,100 Hz, not 48,000 Hz"],
    )
    text = "\n".join(line.text for line in render(st))
    assert "Scarlett Solo USB → BlackHole 2ch" in text
    assert "48,000 Hz (confirmed)" in text
    assert "block 128" in text
    assert "xruns 3" in text
    assert "in 2 → out 1" in text and "-12.3 dBFS" in text
    assert "out 2" in text and "clips 3" in text
    assert "0:01:01" in text
    assert "44,100 Hz" in text
