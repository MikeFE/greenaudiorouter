# chroute

A small command-line audio channel router for macOS. It takes any input
channel(s) of one CoreAudio device and plays them, in real time, on any output
channel(s) of another. Think of it as a minimal, terminal-only Loopback.

```
chroute --in Scarlett --out BlackHole --route 2:1 --route 2:2 --rate 48000
```

This sends Scarlett input 2 (the instrument jack) to both BlackHole channels,
with **both devices forced to 48 kHz**, so Rocksmith 2014's Microphone mode
(which only listens to channel 1) hears the guitar.

## Install

Requires Python 3.11+ and macOS.

With a plain venv:

```sh
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/chroute list
```

Or with uv:

```sh
uv run chroute list            # run from the checkout
uv tool install -e .           # put `chroute` on your PATH
```

`python -m chroute` also works.

The first time chroute opens an input, macOS asks whether your terminal app
may use the microphone. Say yes. If you said no earlier, see Troubleshooting.

## Usage

### `chroute list`

```
  #  Name                      Host API     In  Out  Default rate  Nominal rate
  1  Scarlett Solo USB         Core Audio    2    2     48,000 Hz     48,000 Hz
  2  BlackHole 2ch             Core Audio    2    2     48,000 Hz     48,000 Hz
```

*Nominal rate* is the rate the device is actually running at right now, as
macOS reports it (the value Audio MIDI Setup shows).

### `chroute run` (the default command)

| Flag | Meaning |
|---|---|
| `--in NAME` | Input device: a case-insensitive substring of its name. |
| `--out NAME` | Output device, matched the same way. |
| `--route X:Y[@GAIN]` | Input channel X → output channel Y, **1-based**. Optional linear gain (`2:1@0.8`). Repeatable. |
| `--rate HZ` | Sample rate forced on both devices. Default 48000. |
| `--blocksize N` | Frames per callback. Default 128. |
| `--latency L` | PortAudio latency hint: `low` (default), `high`, or seconds such as `0.005`. See Latency. |
| `--preset NAME` | Load settings from the presets file. Flags override the preset. |
| `--no-meter` | No live meter; plain log lines instead. |

`chroute --preset rocksmith` is the same as `chroute run --preset rocksmith`.

Routing rules:

- One input can feed several outputs (fan-out), and several routes into one
  output are **summed** (fan-in), so chroute also works as a simple mixer.
- Output channels with no route get silence.
- After summing, every output sample is hard-clipped to [-1, 1]. The meter
  counts clip events per output: one event is one audio block (128 frames by
  default) in which that channel went over full scale.
- Only the channels the routes need are opened (`--route 2:1` opens inputs
  1–2 and output 1).

### Presets

`~/.config/chroute/presets.toml`:

```toml
[rocksmith]
in = "Scarlett"
out = "BlackHole"
rate = 48000
blocksize = 128
routes = ["2:1", "2:2"]
```

Allowed keys: `in`, `out`, `routes`, `rate`, `blocksize`, `latency`.
Precedence is command line > preset > built-in default. `--route` on the
command line *replaces* the preset's routes; it doesn't add to them.

```sh
mkdir -p ~/.config/chroute && cp presets.example.toml ~/.config/chroute/presets.toml
chroute presets           # list them
chroute --preset rocksmith
```

### The meter

```
chroute  Scarlett Solo USB → BlackHole 2ch   0:12:04   Ctrl-C to stop
rate 48,000 Hz (confirmed)  block 128  latency in 53.8 ms / out 10.7 ms
xruns 0  (in overflow 0, out underflow 0)

  in 2 → out 1  [██████████████████░░░░░░░░░░░░]  -12.3 dBFS
  in 2 → out 2  [██████████████████░░░░░░░░░░░░]  -12.3 dBFS

  out 1         [██████████████████░░░░░░░░░░░░]  -12.3 dBFS  clips 0
  out 2         [██████████████████░░░░░░░░░░░░]  -12.3 dBFS  clips 0
```

Bars span -60 to 0 dBFS, with a slow fall-off so short peaks stay visible.
Output rows show the level *before* clipping, so an over reads above 0 dBFS.
Ctrl-C stops the stream and prints a summary (runtime, xruns, clips, and any
sample-rate events).

## How the sample rate is enforced and verified

This is what GarageBand got wrong, so chroute never assumes the rate:

1. **Before opening:** it checks that both devices list `--rate` among their
   supported nominal rates.
2. **When opening:** PortAudio gets `CoreAudioSettings(change_device_parameters=True,
   fail_if_conversion_required=True)` for both devices. That sets the devices'
   nominal rate, and if that's impossible the open fails instead of quietly
   resampling.
3. **After opening:** it confirms the stream's rate equals `--rate`, and reads
   each device's `kAudioDevicePropertyNominalSampleRate` straight from
   CoreAudio (via ctypes; `system_profiler` is the fallback). If either device
   is at the wrong rate, chroute exits with an error.
4. **While running:** it rereads both nominal rates every 2 s. If another app
   changes one, the meter shows a red warning with the device name and its
   real rate. The event also goes in the exit summary.

## Latency

Two separate devices each run on their own clock, so PortAudio puts a ring
buffer between them. Its size follows the `--latency` hint. Figures reported
on the Scarlett Solo → BlackHole at 48 kHz / 128 frames:

| `--latency` | reported in / out | xruns seen in testing |
|---|---|---|
| `low` (default) | ~54 ms / ~11 ms | none in 30 s |
| `0.003` | ~30 ms / ~8 ms | not soak-tested |
| `0` | ~16 ms / ~5 ms | 1 input overflow in 8 s |

The reported input figure is PortAudio's worst-case estimate including that
buffer. If Rocksmith feels late, try `--latency 0.003` or `--latency 0` and
watch the xrun counter. Rocksmith's own latency calibration also helps.

## Manual test plan (needs the hardware)

1. **`chroute list`.** Expect `Scarlett Solo USB` and `BlackHole 2ch`, each
   with `2` in and `2` out.
2. **`chroute --preset rocksmith`** (after copying `presets.example.toml`).
   Expect rows `in 2 → out 1` and `in 2 → out 2`. Strumming moves both bars to
   roughly -30 to -6 dBFS, depending on the Scarlett's gain knob. At rest they
   sit near the noise floor (around -60 dBFS or lower). The status line says
   `48,000 Hz (confirmed)`.
3. **Audio MIDI Setup** (while chroute runs). Expect both Scarlett Solo USB
   and BlackHole 2ch to show **48,000 Hz**.
4. **QuickTime → File → New Audio Recording**, with BlackHole 2ch picked as
   the input (the ⌄ next to the record button). Expect QuickTime's level meter
   to move with the guitar.
5. **Start Rocksmith *after* chroute is running.** Pick Microphone mode and
   select BlackHole. Expect the calibration screen to respond to strumming.
6. **Soak test:** leave it running 30+ minutes at `--blocksize 128`. Expect
   the xrun counter to stay at or near 0. If it climbs steadily, try
   `--latency high` or `--blocksize 256`. Steady growth over hours can also be
   clock drift between the two devices (see the drift-free stretch goal).
7. **Open GarageBand while chroute runs.** If GarageBand moves BlackHole to
   44.1 kHz, expect within about 2 s a red line
   `!! BlackHole 2ch is at 44,100 Hz, not 48,000 Hz! Another app changed it…`,
   and the rate line switching to `MISMATCH`. The input-overflow count rises
   while the rates disagree.

## Troubleshooting

- **"No input signal for 10 s"** means either macOS microphone permission
  (System Settings → Privacy & Security → Microphone → enable your terminal
  app, then quit and reopen the terminal), the wrong input channel (the Solo's
  instrument jack is input 2), or the Scarlett's gain knob is too low. Without
  permission, macOS delivers exact digital silence, so the meter shows `-inf`.
- **"Couldn't open … without resampling"**: another app is holding a device
  at a different rate. Quit it, or set 48 kHz in Audio MIDI Setup, and retry.
- **Device unplugged mid-run:** chroute notices within about 2 s, prints which
  device disappeared, and exits with code 1.
- **Ambiguous device name:** chroute lists the matches; use more of the name.

## Development

```sh
.venv/bin/python -m pytest
```

Layout:

- `routing.py`: route parsing and validation, plus the realtime kernel
  (`Router.process`). Pure numpy, fully unit-tested.
- `devices.py` / `coreaudio.py`: device listing and name matching; nominal
  rate reads through ctypes.
- `engine.py`: the `sd.Stream`, the callback, and rate verification.
- `session.py`: the run loop (meter, rate polling, silence hint, disconnect
  detection, summary).
- `meter.py`: terminal rendering.
- `presets.py`: TOML presets and the CLI > preset > default merge.
- `cli.py`: argparse and subcommands.

The audio callback does no printing, locking, or numpy allocation. Routing is
one `np.matmul` into PortAudio's output buffer through a precomputed gain
matrix, and peaks and clip counts go into preallocated arrays that the meter
thread reads. One block takes ~13 µs, against a budget of 2.7 ms at
128 frames / 48 kHz.

## Not done yet (stretch goals)

- Drift-free mode with a private aggregate device.
- `launchd` agent to start a preset at login.
- Restarting automatically when a device reconnects.
