"""Preset loading (TOML) and merging presets with CLI flags."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_PATH = Path("~/.config/chroute/presets.toml").expanduser()
DEFAULT_RATE = 48000
DEFAULT_BLOCKSIZE = 128

_KEYS = {"in": str, "out": str, "rate": int, "blocksize": int, "routes": list}


class PresetError(Exception):
    """Bad presets file or unknown preset. The message is for the user."""


def load_presets(path: Path = DEFAULT_PATH) -> dict[str, dict[str, Any]]:
    """Load and validate all presets. A missing file means no presets."""
    if not path.exists():
        return {}
    try:
        with path.open("rb") as f:
            data = tomllib.load(f)
    except tomllib.TOMLDecodeError as e:
        raise PresetError(f"{path}: invalid TOML: {e}") from None
    presets: dict[str, dict[str, Any]] = {}
    for name, table in data.items():
        if not isinstance(table, dict):
            raise PresetError(f"{path}: '{name}' must be a table like [{name}], not a plain value")
        for key, value in table.items():
            if key not in _KEYS:
                raise PresetError(
                    f"{path}: preset [{name}] has unknown key '{key}' (allowed: {', '.join(_KEYS)})"
                )
            expected = _KEYS[key]
            # bool is a subclass of int; don't accept rate = true.
            if not isinstance(value, expected) or isinstance(value, bool):
                raise PresetError(
                    f"{path}: preset [{name}] key '{key}' must be {expected.__name__}, got {value!r}"
                )
        if "routes" in table and not all(isinstance(r, str) for r in table["routes"]):
            raise PresetError(f"{path}: preset [{name}] routes must be strings like \"2:1\"")
        presets[name] = table
    return presets


def get_preset(name: str, path: Path = DEFAULT_PATH) -> dict[str, Any]:
    presets = load_presets(path)
    if name in presets:
        return presets[name]
    if not path.exists():
        raise PresetError(f"Preset '{name}' not found: no presets file at {path}")
    avail = ", ".join(sorted(presets)) or "(none)"
    raise PresetError(f"Preset '{name}' not found in {path}. Available: {avail}")


@dataclass(frozen=True)
class Settings:
    in_name: str
    out_name: str
    routes: list[str]
    rate: int
    blocksize: int


def resolve_settings(
    *,
    in_name: str | None = None,
    out_name: str | None = None,
    routes: list[str] | None = None,
    rate: int | None = None,
    blocksize: int | None = None,
    preset: dict[str, Any] | None = None,
) -> Settings:
    """Merge settings: CLI flags win over the preset, which wins over defaults.

    ``None`` (or an empty route list) means the flag wasn't given. CLI
    ``--route`` flags replace the preset's routes; they aren't appended.
    """
    p = preset or {}
    merged_in = in_name if in_name is not None else p.get("in")
    merged_out = out_name if out_name is not None else p.get("out")
    merged_routes = routes if routes else p.get("routes", [])
    merged_rate = rate if rate is not None else p.get("rate", DEFAULT_RATE)
    merged_block = blocksize if blocksize is not None else p.get("blocksize", DEFAULT_BLOCKSIZE)

    missing = [flag for flag, v in (("--in", merged_in), ("--out", merged_out)) if not v]
    if missing:
        raise PresetError(f"missing {' and '.join(missing)} (give it on the command line or in a --preset)")
    if not merged_routes:
        raise PresetError("no routes given; add at least one --route IN:OUT (e.g. --route 2:1)")
    if merged_rate <= 0:
        raise PresetError(f"sample rate must be positive, got {merged_rate}")
    if merged_block <= 0:
        raise PresetError(f"blocksize must be positive, got {merged_block}")
    return Settings(merged_in, merged_out, list(merged_routes), int(merged_rate), int(merged_block))
