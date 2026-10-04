"""Command-line interface."""

from __future__ import annotations

import argparse
import sys

from chroute import __version__
from chroute.devices import DeviceError, format_rate, list_devices, nominal_rates
from chroute.presets import DEFAULT_PATH, PresetError, load_presets

SUBCOMMANDS = {"list", "presets", "run"}


def cmd_list(args: argparse.Namespace) -> int:
    devices = list_devices()
    rates = nominal_rates()
    name_w = max((len(d.name) for d in devices), default=4)
    print(f"{'#':>3}  {'Name':<{name_w}}  {'Host API':<10}  {'In':>3}  {'Out':>3}  {'Default rate':>12}  {'Nominal rate':>12}")
    for d in devices:
        print(
            f"{d.index:>3}  {d.name:<{name_w}}  {d.hostapi:<10}  {d.max_in:>3}  {d.max_out:>3}  "
            f"{format_rate(d.default_rate):>12}  {format_rate(rates.get(d.name)):>12}"
        )
    return 0


def cmd_presets(args: argparse.Namespace) -> int:
    presets = load_presets(DEFAULT_PATH)
    if not presets:
        print(f"No presets. Create {DEFAULT_PATH}, for example:\n")
        print('[rocksmith]\nin = "Scarlett"\nout = "BlackHole"\nrate = 48000\nblocksize = 128\nroutes = ["2:1", "2:2"]')
        return 0
    print(f"Presets in {DEFAULT_PATH}:")
    for name, p in presets.items():
        parts = [f"{p.get('in', '?')} -> {p.get('out', '?')}", "routes " + " ".join(p.get("routes", []))]
        if "rate" in p:
            parts.append(f"{p['rate']} Hz")
        if "blocksize" in p:
            parts.append(f"block {p['blocksize']}")
        print(f"  {name:<16} " + ", ".join(parts))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="chroute",
        description="Route audio from any input channel(s) of one CoreAudio device "
        "to any output channel(s) of another.",
        epilog="Running 'chroute' with run flags but no subcommand is the same as 'chroute run'.",
    )
    parser.add_argument("--version", action="version", version=f"chroute {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="{list,presets,run}")

    p_list = sub.add_parser("list", help="list audio devices")
    p_list.set_defaults(func=cmd_list)

    p_presets = sub.add_parser("presets", help=f"list presets in {DEFAULT_PATH}")
    p_presets.set_defaults(func=cmd_presets)

    return parser


def normalize_argv(argv: list[str]) -> list[str]:
    """Make ``run`` the default subcommand: ``chroute --preset x`` == ``chroute run --preset x``."""
    if argv and argv[0] not in SUBCOMMANDS and argv[0] not in ("-h", "--help", "--version"):
        return ["run", *argv]
    return argv


def main(argv: list[str] | None = None) -> int:
    argv = normalize_argv(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 0
    try:
        return args.func(args)
    except (DeviceError, PresetError) as e:
        print(f"chroute: error: {e}", file=sys.stderr)
        return 2
