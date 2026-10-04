"""The ``gaudioroute run`` main loop: start the engine, watch it, draw the meter, summarize."""

from __future__ import annotations

import signal
import sys
import time

from gaudioroute.devices import format_rate, list_devices, match_device
from gaudioroute.engine import XRUN_FIELDS, Engine, EngineError
from gaudioroute.meter import MeterState, PeakDecay, TerminalMeter, render
from gaudioroute.presets import Settings
from gaudioroute.routing import build_plan, parse_routes, validate_routes

FRAME_INTERVAL = 0.1  # meter redraw, seconds
RATE_CHECK_INTERVAL = 2.0
SILENCE_HINT_AFTER = 10.0
SILENCE_PEAK = 10 ** (-80 / 20)  # below -80 dBFS counts as silent
STALL_TIMEOUT = 2.0
_STOP_SIGNALS = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)

SILENCE_HINT = (
    "No input signal for 10 s. Check: (1) macOS microphone permission for your terminal app "
    "(System Settings → Privacy & Security → Microphone), then restart the terminal; "
    "(2) the input channel number (the Scarlett Solo's instrument jack is input 2); "
    "(3) the interface's gain knob."
)


class _Stop(Exception):
    def __init__(self, reason: str, code: int = 1):
        super().__init__(reason)
        self.reason = reason
        self.code = code


def _raise_interrupt(signum, frame):
    raise KeyboardInterrupt


def _fmt_duration(s: float) -> str:
    s = int(s)
    return f"{s // 3600:d}:{s // 60 % 60:02d}:{s % 60:02d}"


def run_session(settings: Settings, show_meter: bool = True) -> int:
    devs = list_devices()
    in_dev = match_device(settings.in_name, devs, "input")
    out_dev = match_device(settings.out_name, devs, "output")
    routes = parse_routes(settings.routes)
    validate_routes(routes, in_dev.max_in, out_dev.max_out, in_dev.name, out_dev.name)
    plan = build_plan(routes)

    engine = Engine(in_dev, out_dev, plan, settings.rate, settings.blocksize, settings.latency)
    engine.start()
    try:
        engine.check_stream_rate()
        problems = engine.rate_problems()
        mismatched = [p for p in problems if p.actual is not None]
        if mismatched:
            raise EngineError(
                "Sample rate verification failed: "
                + "; ".join(p.message(settings.rate) for p in mismatched)
                + ". Refusing to run with mismatched rates."
            )
    except BaseException:
        engine.close()
        raise

    # Install explicitly: a shell starts background jobs with SIGINT ignored.
    prev_handlers = {sig: signal.signal(sig, _raise_interrupt) for sig in _STOP_SIGNALS}

    show_meter = show_meter and sys.stdout.isatty()
    meter = TerminalMeter() if show_meter else None
    route_labels = [
        f"in {r.src} → out {r.dst}" + ("" if r.gain == 1.0 else f" @{r.gain:g}") for r in routes
    ]
    out_channels = plan.active_outputs
    out_cols = [c - 1 for c in out_channels]
    route_decay, out_decay = PeakDecay(len(routes)), PeakDecay(len(out_channels))

    warnings: dict[str, str] = {}  # key -> message, shown while active
    notes: list[str] = []
    events: list[str] = []  # for the summary
    unverified = [p.message(settings.rate) for p in problems if p.actual is None]
    for msg in unverified:
        warnings[f"unverified:{msg}"] = msg + " (can't confirm it's not resampling)"

    def log(msg: str) -> None:
        if meter is None:
            print(msg, flush=True)

    if meter is None:
        lat = engine.latency_ms
        print(
            f"gaudioroute: {in_dev.name} → {out_dev.name}, {format_rate(engine.stream_rate)} confirmed, "
            f"block {settings.blocksize}"
            + (f", latency in {lat[0]:.1f} ms / out {lat[1]:.1f} ms" if lat else "")
            + ". Ctrl-C to stop.",
            flush=True,
        )
        for msg in warnings.values():
            print(f"gaudioroute: WARNING: {msg}", flush=True)

    start = time.monotonic()
    next_rate_check = start + RATE_CHECK_INTERVAL
    max_input_seen = 0.0
    last_cb_count, last_cb_time = -1, start
    stop: _Stop | None = None
    try:
        while True:
            time.sleep(FRAME_INTERVAL)
            now = time.monotonic()
            elapsed = now - start

            in_p, route_p, out_p = engine.router.take_peaks()
            max_input_seen = max(max_input_seen, float(in_p[plan.src_idx].max()))

            if engine.finished.is_set():
                gone = engine.missing_devices()
                raise _Stop(_disconnect_msg(gone) if gone else "The audio stream stopped unexpectedly.")

            # Stalled callbacks usually mean a device vanished.
            cb = int(engine.callback_count[0])
            if cb != last_cb_count:
                last_cb_count, last_cb_time = cb, now
                warnings.pop("stall", None)
            elif now - last_cb_time > STALL_TIMEOUT:
                gone = engine.missing_devices()
                if gone:
                    raise _Stop(_disconnect_msg(gone))
                if "stall" not in warnings:
                    log(f"gaudioroute: WARNING: no audio for {STALL_TIMEOUT:.0f}+ s")
                warnings["stall"] = f"audio callbacks stopped {now - last_cb_time:.0f} s ago"

            if now >= next_rate_check:
                next_rate_check = now + RATE_CHECK_INTERVAL
                gone = engine.missing_devices()
                if gone:
                    raise _Stop(_disconnect_msg(gone))
                current = {p.device: p for p in engine.rate_problems() if p.actual is not None}
                for name in dict.fromkeys((in_dev.name, out_dev.name)):
                    key = f"rate:{name}"
                    if name in current and key not in warnings:
                        msg = (
                            f"{current[name].message(settings.rate)}! Another app changed it. "
                            f"Quit that app and restart gaudioroute."
                        )
                        warnings[key] = msg
                        events.append(f"{_fmt_duration(elapsed)}  {current[name].message(settings.rate)}")
                        log(f"gaudioroute: WARNING: {msg}")
                    elif name not in current and key in warnings:
                        del warnings[key]
                        events.append(f"{_fmt_duration(elapsed)}  {name} back at {format_rate(settings.rate)}")
                        log(f"gaudioroute: {name} is back at {format_rate(settings.rate)}")

            if not notes and elapsed > SILENCE_HINT_AFTER and max_input_seen < SILENCE_PEAK:
                notes.append(SILENCE_HINT)
                log(f"gaudioroute: hint: {SILENCE_HINT}")

            if meter is not None:
                st = MeterState(
                    in_name=in_dev.name,
                    out_name=out_dev.name,
                    rate=engine.stream_rate,
                    rate_ok=not any(k.startswith("rate:") for k in warnings),
                    blocksize=settings.blocksize,
                    latency_ms=engine.latency_ms,
                    elapsed=elapsed,
                    xruns=dict(zip(XRUN_FIELDS, engine.xruns.tolist())),
                    route_labels=route_labels,
                    route_db=route_decay.update(route_p),
                    out_channels=out_channels,
                    out_db=out_decay.update(out_p[out_cols]),
                    out_clips=engine.router.clip_counts[out_cols].tolist(),
                    warnings=list(warnings.values()),
                    notes=notes,
                )
                meter.draw(render(st))
    except KeyboardInterrupt:
        pass
    except _Stop as s:
        stop = s
    finally:
        runtime = time.monotonic() - start
        engine.close()
        if meter is not None:
            meter.close()
        for sig, handler in prev_handlers.items():
            signal.signal(sig, handler)

    xruns = dict(zip(XRUN_FIELDS, engine.xruns.tolist()))
    clips = engine.router.clip_counts[out_cols].tolist()
    if stop is not None:
        print(f"\ngaudioroute: {stop.reason}", file=sys.stderr)
    print(f"\ngaudioroute stopped after {_fmt_duration(runtime)}.")
    print(
        f"  xruns: {sum(xruns.values())} ("
        + ", ".join(f"{k.replace('_', ' ')} {v}" for k, v in xruns.items())
        + ")"
    )
    print(f"  clips: {sum(clips)} (" + ", ".join(f"out {c}: {n}" for c, n in zip(out_channels, clips)) + ")")
    if events:
        print("  sample rate events:")
        for e in events:
            print(f"    {e}")
    return stop.code if stop is not None else 0


def _disconnect_msg(names: list[str]) -> str:
    return f"{' and '.join(names)} disconnected. Stopping."
