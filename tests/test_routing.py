import numpy as np
import pytest

from gaudioroute.routing import (
    Route,
    RouteError,
    Router,
    build_plan,
    parse_route,
    parse_routes,
    validate_routes,
)

FRAMES = 128
RATE = 48000


def sine(freq=440.0, amp=0.5, frames=FRAMES):
    t = np.arange(frames, dtype=np.float32) / RATE
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def run(routes, indata):
    plan = build_plan(parse_routes(routes))
    router = Router(plan)
    out = np.full((indata.shape[0], plan.n_out), 123.0, dtype=np.float32)  # garbage
    router.process(indata, out)
    return router, out


# --- parsing -----------------------------------------------------------------


@pytest.mark.parametrize(
    "spec,expected",
    [
        ("2:1", Route(2, 1, 1.0)),
        ("2:1@0.5", Route(2, 1, 0.5)),
        ("1:2@1", Route(1, 2, 1.0)),
        (" 10 : 3 @ 0.25 ", Route(10, 3, 0.25)),
        ("1:1@-1", Route(1, 1, -1.0)),
        ("1:1@1e-1", Route(1, 1, 0.1)),
    ],
)
def test_parse_valid(spec, expected):
    assert parse_route(spec) == expected


@pytest.mark.parametrize(
    "spec",
    ["", "2", "2:", ":1", "2-1", "a:b", "2:1@", "2:1@x", "2:1@nan", "2:1@inf", "0:1", "1:0", "-1:2", "2:1:3", "1.5:2"],
)
def test_parse_invalid(spec):
    with pytest.raises(RouteError):
        parse_route(spec)


def test_route_str_roundtrip():
    assert str(parse_route("2:1")) == "2:1"
    assert str(parse_route("2:1@0.8")) == "2:1@0.8"


# --- validation --------------------------------------------------------------


def test_validate_ok():
    validate_routes(parse_routes(["2:1", "2:2"]), 2, 2)


def test_validate_output_out_of_range_message():
    with pytest.raises(RouteError) as e:
        validate_routes(parse_routes(["2:3"]), 2, 2, "Scarlett Solo USB", "BlackHole 2ch")
    assert str(e.value) == "BlackHole 2ch has 2 output channels; route 2:3 is invalid."


def test_validate_input_out_of_range_message():
    with pytest.raises(RouteError) as e:
        validate_routes(parse_routes(["2:1"]), 1, 2, "MacBook Air Microphone", "BlackHole 2ch")
    assert str(e.value) == "MacBook Air Microphone has 1 input channel; route 2:1 is invalid."


def test_validate_empty():
    with pytest.raises(RouteError):
        validate_routes([], 2, 2)


# --- plan --------------------------------------------------------------------


def test_plan_opens_only_needed_channels():
    plan = build_plan(parse_routes(["2:1"]))
    assert (plan.n_in, plan.n_out) == (2, 1)
    plan = build_plan(parse_routes(["1:4"]))
    assert (plan.n_in, plan.n_out) == (1, 4)
    assert plan.active_outputs == [4]


# --- processing --------------------------------------------------------------


def test_rocksmith_fan_out():
    """Scarlett input 2 (guitar) to both BlackHole channels; input 1 ignored."""
    guitar = sine(110.0, 0.4)
    mic = sine(1000.0, 0.9)
    indata = np.stack([mic, guitar], axis=1)
    _, out = run(["2:1", "2:2"], indata)
    np.testing.assert_allclose(out[:, 0], guitar, atol=1e-7)
    np.testing.assert_allclose(out[:, 1], guitar, atol=1e-7)


def test_gain():
    x = sine(amp=0.5)
    _, out = run(["1:1@0.5"], x[:, None])
    np.testing.assert_allclose(out[:, 0], x * 0.5, atol=1e-7)


def test_fan_in_sums():
    a, b = sine(100.0, 0.3), sine(300.0, 0.2)
    _, out = run(["1:1", "2:1@0.5"], np.stack([a, b], axis=1))
    np.testing.assert_allclose(out[:, 0], a + 0.5 * b, atol=1e-6)


def test_duplicate_route_sums():
    x = sine(amp=0.25)
    _, out = run(["1:1", "1:1"], x[:, None])
    np.testing.assert_allclose(out[:, 0], 2 * x, atol=1e-7)


def test_unrouted_outputs_silent():
    x = sine(amp=0.5)
    router, out = run(["1:3"], x[:, None])  # outputs 1 and 2 have no route
    assert out.shape == (FRAMES, 3)
    assert np.all(out[:, 0] == 0.0)
    assert np.all(out[:, 1] == 0.0)
    np.testing.assert_allclose(out[:, 2], x, atol=1e-7)
    assert router.clip_counts.tolist() == [0, 0, 0]


def test_clipping_and_clip_counts():
    x = sine(amp=0.8)
    router, out = run(["1:1@2", "1:2"], x[:, None])
    assert out[:, 0].max() == pytest.approx(1.0)
    assert out[:, 0].min() == pytest.approx(-1.0)
    assert np.all(np.abs(out) <= 1.0)
    np.testing.assert_allclose(out[:, 1], x, atol=1e-7)  # unaffected
    assert router.clip_counts.tolist() == [1, 0]
    # One more clipping block increments the counter again.
    router.process(x[:, None], out)
    assert router.clip_counts.tolist() == [2, 0]


def test_fan_in_clips_after_summing():
    """Each input alone is under full scale; their sum isn't."""
    x = sine(amp=0.7)
    router, out = run(["1:1", "2:1"], np.stack([x, x], axis=1))
    assert np.all(np.abs(out) <= 1.0)
    assert router.clip_counts.tolist() == [1]


def test_peaks_and_take_resets():
    x = sine(amp=0.5)
    router, _ = run(["1:1@0.5", "1:2"], x[:, None])
    in_p, route_p, out_p = router.take_peaks()
    peak = np.abs(x).max()
    assert in_p[0] == pytest.approx(peak)
    assert route_p.tolist() == pytest.approx([peak * 0.5, peak])
    assert out_p.tolist() == pytest.approx([peak * 0.5, peak])
    in_p, route_p, out_p = router.take_peaks()
    assert in_p.sum() == route_p.sum() == out_p.sum() == 0.0


def test_peaks_hold_max_across_blocks():
    plan = build_plan(parse_routes(["1:1"]))
    router = Router(plan)
    out = np.zeros((FRAMES, 1), dtype=np.float32)
    router.process(sine(amp=0.9)[:, None], out)
    router.process(sine(amp=0.1)[:, None], out)
    _, _, out_p = router.take_peaks()
    assert out_p[0] == pytest.approx(np.abs(sine(amp=0.9)).max())


def test_out_peak_is_pre_clip():
    x = sine(amp=0.8)
    router, _ = run(["1:1@2"], x[:, None])
    _, _, out_p = router.take_peaks()
    assert out_p[0] == pytest.approx(np.abs(x).max() * 2)


def test_negative_gain_inverts():
    x = sine(amp=0.5)
    router, out = run(["1:1@-1"], x[:, None])
    np.testing.assert_allclose(out[:, 0], -x, atol=1e-7)
    _, route_p, _ = router.take_peaks()
    assert route_p[0] == pytest.approx(np.abs(x).max())


def test_process_does_not_reallocate_shared_arrays():
    """The meter holds references to these arrays, so they must stay the same objects."""
    plan = build_plan(parse_routes(["2:1", "2:2"]))
    router = Router(plan)
    ids = [id(router.in_peak), id(router.route_peak), id(router.out_peak), id(router.clip_counts)]
    out = np.zeros((FRAMES, 2), dtype=np.float32)
    for _ in range(5):
        router.process(np.random.default_rng(0).uniform(-1, 1, (FRAMES, 2)).astype(np.float32), out)
    assert ids == [id(router.in_peak), id(router.route_peak), id(router.out_peak), id(router.clip_counts)]


def test_handles_odd_block_sizes():
    x = sine(frames=37)
    _, out = run(["1:2"], x[:, None])
    assert out.shape == (37, 2)
    np.testing.assert_allclose(out[:, 1], x, atol=1e-7)
