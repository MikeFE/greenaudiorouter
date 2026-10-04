import pytest

from chroute.presets import (
    DEFAULT_BLOCKSIZE,
    DEFAULT_RATE,
    PresetError,
    get_preset,
    load_presets,
    resolve_settings,
)

ROCKSMITH = """
[rocksmith]
in = "Scarlett"
out = "BlackHole"
rate = 48000
blocksize = 128
routes = ["2:1", "2:2"]

[minimal]
in = "Scarlett"
out = "BlackHole"
routes = ["1:1"]
"""


@pytest.fixture
def presets_file(tmp_path):
    p = tmp_path / "presets.toml"
    p.write_text(ROCKSMITH)
    return p


def test_load(presets_file):
    presets = load_presets(presets_file)
    assert set(presets) == {"rocksmith", "minimal"}
    assert presets["rocksmith"]["routes"] == ["2:1", "2:2"]


def test_missing_file_means_no_presets(tmp_path):
    assert load_presets(tmp_path / "nope.toml") == {}


def test_get_preset_unknown_lists_available(presets_file):
    with pytest.raises(PresetError, match="Available: minimal, rocksmith"):
        get_preset("nope", presets_file)


def test_get_preset_no_file(tmp_path):
    with pytest.raises(PresetError, match="no presets file"):
        get_preset("rocksmith", tmp_path / "nope.toml")


@pytest.mark.parametrize(
    "body,match",
    [
        ('[x]\nin = "a"\nbogus = 1\n', "unknown key 'bogus'"),
        ('[x]\nrate = "48000"\n', "'rate' must be int"),
        ("[x]\nrate = true\n", "'rate' must be int"),
        ('[x]\nroutes = "2:1"\n', "'routes' must be list"),
        ("[x]\nroutes = [2]\n", "routes must be strings"),
        ('x = "y"\n', "must be a table"),
        ("[x\n", "invalid TOML"),
    ],
)
def test_load_rejects_bad_files(tmp_path, body, match):
    p = tmp_path / "presets.toml"
    p.write_text(body)
    with pytest.raises(PresetError, match=match):
        load_presets(p)


def test_preset_only(presets_file):
    s = resolve_settings(preset=get_preset("rocksmith", presets_file))
    assert (s.in_name, s.out_name, s.routes, s.rate, s.blocksize) == (
        "Scarlett",
        "BlackHole",
        ["2:1", "2:2"],
        48000,
        128,
    )


def test_defaults_fill_missing_preset_values(presets_file):
    s = resolve_settings(preset=get_preset("minimal", presets_file))
    assert s.rate == DEFAULT_RATE == 48000
    assert s.blocksize == DEFAULT_BLOCKSIZE == 128


def test_cli_overrides_preset(presets_file):
    s = resolve_settings(
        out_name="Speakers",
        rate=44100,
        blocksize=256,
        routes=["1:1@0.5"],
        preset=get_preset("rocksmith", presets_file),
    )
    assert s.in_name == "Scarlett"  # from preset
    assert s.out_name == "Speakers"  # from CLI
    assert s.routes == ["1:1@0.5"]  # CLI replaces, doesn't append
    assert (s.rate, s.blocksize) == (44100, 256)


def test_empty_cli_routes_keep_preset_routes(presets_file):
    s = resolve_settings(routes=[], preset=get_preset("rocksmith", presets_file))
    assert s.routes == ["2:1", "2:2"]


def test_no_preset_cli_only():
    s = resolve_settings(in_name="Scarlett", out_name="BlackHole", routes=["2:1"])
    assert (s.rate, s.blocksize) == (48000, 128)


@pytest.mark.parametrize(
    "kwargs,match",
    [
        ({"out_name": "B", "routes": ["1:1"]}, "missing --in"),
        ({"in_name": "A", "routes": ["1:1"]}, "missing --out"),
        ({"routes": ["1:1"]}, "missing --in and --out"),
        ({"in_name": "A", "out_name": "B"}, "no routes"),
        ({"in_name": "A", "out_name": "B", "routes": ["1:1"], "rate": 0}, "sample rate"),
        ({"in_name": "A", "out_name": "B", "routes": ["1:1"], "blocksize": -1}, "blocksize"),
    ],
)
def test_resolve_errors(kwargs, match):
    with pytest.raises(PresetError, match=match):
        resolve_settings(**kwargs)
