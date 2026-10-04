# Spec: `chroute` — a command-line audio channel router for macOS

## Goal

Build a small, reliable command-line tool that takes audio from **any input channel(s)** of one CoreAudio device and sends it to **any output channel(s)** of another device in real time. It works like a minimal, free, terminal-only version of Rogue Amoeba's Loopback.

The immediate use case is getting Rocksmith 2014 (Windows version, running on macOS through Porting Kit/Wine) to hear my guitar in Microphone mode. The general tool should not be tied to that setup.

## Background (why this exists)

- Hardware: **Focusrite Scarlett Solo 3rd Gen** on a MacBook (Apple Silicon, recent macOS). Input 1 is the XLR mic socket. **Input 2 is the ¼" instrument jack**, which my guitar (Gibson Les Paul) is plugged into.
- Rocksmith's mic mode only listens to **channel 1** of the selected device, so it hears the empty XLR socket and nothing else.
- **BlackHole 2ch** (existential.audio) is installed. It's a virtual pass-through device: audio sent to its output channel N appears on its input channel N. Rocksmith can select BlackHole as its mic.
- Rocksmith wants **48 kHz**.
- I already tried routing through GarageBand (Scarlett → GarageBand → BlackHole). The routing worked, confirmed by recording from BlackHole in QuickTime, but **GarageBand forces BlackHole to 44.1 kHz**, and Rocksmith wouldn't calibrate. The tool must **actually set the devices' nominal sample rate**, not quietly resample.
- Audio MIDI Setup aggregate devices can't reorder or drop channels, which is why routing software is needed at all.

For the Rocksmith case, the target command is roughly:

```
chroute --in Scarlett --out BlackHole --route 2:1 --route 2:2 --rate 48000
```

That means Scarlett input 2 goes to both BlackHole channels, with both devices at 48 kHz.

## Tech stack

- **Python 3.11+**
- **`sounddevice`** (PortAudio bindings) and **`numpy`**
- Package management: `uv` preferred (fall back to a plain venv with `requirements.txt` if that's simpler). Provide a runnable entry point named `chroute`.
- No GUI. No kernel or driver work. Creating virtual devices is BlackHole's job.

## CLI

### `chroute list`

Prints every audio device with:
- Index, full name, host API
- Max input channels / max output channels
- Default sample rate (and current nominal rate if it can be read, see the sample rate section)

### `chroute run` (or the default command)

| Flag | Meaning |
|---|---|
| `--in NAME` | Input device, matched by **case-insensitive substring** of the device name. Error if zero or multiple devices match, listing the candidates. |
| `--out NAME` | Output device, matched the same way. |
| `--route X:Y[@GAIN]` | Repeatable. Input channel X → output channel Y, **1-based** to match hardware labels. Optional linear gain, default 1.0. Example: `--route 2:1@0.8`. |
| `--rate HZ` | Sample rate to force on both devices. Default `48000`. |
| `--blocksize N` | Frames per callback. Default `128`. |
| `--preset NAME` | Load devices, routes, rate, and blocksize from a config file (below). CLI flags override preset values. |
| `--no-meter` | Disable the live level meter. |

### Config file / presets

- Location: `~/.config/chroute/presets.toml`
- Example:

```toml
[rocksmith]
in = "Scarlett"
out = "BlackHole"
rate = 48000
blocksize = 128
routes = ["2:1", "2:2"]
```

- `chroute presets` lists available presets.

## Routing semantics

- Each route copies input channel X, multiplied by its gain, into output channel Y.
- **Multiple routes to the same output channel are summed**, so the tool also works as a mixer.
- Output channels with no route get **silence**.
- After summing, **hard-clip each output sample to [-1.0, 1.0]** and count clip events per output channel. Report them in the meter, never by printing from the audio callback.
- One input can feed several outputs (fan-out) and several inputs can feed one output (fan-in). Both must work.
- Validate routes **before** opening streams. Error clearly if a route references a channel beyond the device's channel count, for example "BlackHole 2ch has 2 output channels; route 2:3 is invalid."
- Open only as many channels as the routes need on each side.

## Audio engine

- Use one `sd.Stream` with `device=(in_index, out_index)` (separate input and output devices) and a callback.
- The callback must be **allocation-free and print-free**. Precompute the routing as index arrays and gains. Use preallocated numpy buffers. Never call `print`, take locks, or allocate in the callback.
- Peak levels and clip counts go from the callback to the meter through preallocated shared numpy arrays. A simple overwrite is fine for a meter; no locking needed.
- Track and report the PortAudio `status` flags (input overflow, output underflow) as counters in the meter.
- Use `sd.CoreAudioSettings(change_device_parameters=True)` for **both** input and output via `extra_settings`, so PortAudio changes the devices' nominal sample rate instead of resampling. Also consider `fail_if_conversion_required=True` so a silent resample fails loudly instead.
- Shut down cleanly on Ctrl-C: stop the stream, restore the terminal, print a short summary (runtime, total xruns, total clips).

## Sample rate verification (important)

This is the exact failure that led to building this tool, so the code must **verify** the rate, not assume it.

- After opening the stream, confirm the stream's actual sample rate equals `--rate`.
- Also check the **devices' nominal sample rate** as macOS reports it. Options, in order of preference, are your call:
  1. `pyobjc` (`pyobjc-framework-CoreAudio`) to read `kAudioDevicePropertyNominalSampleRate`
  2. Parsing `system_profiler SPAudioDataType -json`
- If another app (GarageBand, for example) has the device at a different rate or changes it while running, **warn loudly** with the device name and the rate it's actually at. Checking every few seconds while running is a nice-to-have.

## Live meter

- Redraws in place in the terminal about 10 times per second, from a normal thread or loop, never from the audio callback.
- One row per **route**: `in 2 → out 1  [██████░░░░░░] -12.3 dBFS`
- Also one row per **active output channel** showing the summed level and a clip counter.
- A status line with the device names, the confirmed sample rate, the blocksize, and xrun counts.
- Peak hold or decay is nice-to-have, not required.

## Errors and user guidance

Handle these with a clear message instead of a traceback:

- **No matching device / ambiguous match:** list the candidates.
- **Invalid route channel numbers:** say the device's real channel count.
- **Silent input for more than 10 s after start:** print a hint once. The likely cause is macOS microphone permission for Terminal or iTerm (System Settings → Privacy & Security → Microphone), the wrong input channel, or the interface's gain being too low.
- **Sample rate couldn't be set or got changed:** see the sample rate section.
- **Device disconnected mid-run:** exit cleanly with a message.

## Project layout (suggested)

```
chroute/
  pyproject.toml
  README.md
  src/chroute/
    __init__.py
    cli.py         # argparse/click, subcommands
    devices.py     # listing, name matching, nominal-rate queries
    routing.py     # parse "X:Y@G", validate, build routing matrix (pure, no audio)
    engine.py      # stream setup + callback
    meter.py       # terminal meter
    presets.py     # TOML loading/merging
  tests/
    test_routing.py
    test_presets.py
```

## Testing

### Automated (no hardware needed)

- `routing.py` is pure numpy and fully unit-tested: parsing (`"2:1"`, `"2:1@0.5"`, bad input), validation against channel counts, fan-out, fan-in summing, gain, clipping, and silence on unrouted outputs.
- Test the callback logic by calling the routing function on synthetic numpy blocks, for example a sine on input channel 2, and asserting the output.
- Preset loading and CLI-override precedence.

### Manual test plan (on my Mac), put in the README

1. `chroute list`: Scarlett Solo and BlackHole 2ch both appear with correct channel counts.
2. `chroute --preset rocksmith`: the meter moves for route `2→1` and `2→2` when strumming, and stays near silent at rest.
3. Audio MIDI Setup shows **both** devices at **48,000 Hz** while running.
4. QuickTime → New Audio Recording → input BlackHole 2ch: the meter moves with the guitar.
5. Launch Rocksmith **after** `chroute` is running → Microphone mode → select BlackHole → calibration responds.
6. Run for 30+ minutes: xrun count stays at or near zero at blocksize 128.
7. Open GarageBand while running: `chroute` warns that BlackHole's rate changed.

## Non-goals

- No GUI, menu bar app, or virtual audio driver.
- No resampling or format conversion. If rates don't match, error out.
- No effects or DSP beyond gain, summing, and clipping.
- Not cross-platform; macOS/CoreAudio only is fine.

## Stretch goals (only after everything above works)

1. **Drift-free mode:** create a **private CoreAudio aggregate device** (input device as clock master, output with drift compensation) through `pyobjc` and run a single duplex stream on it. This avoids occasional clicks from two independent device clocks during long sessions.
2. A `launchd` plist or `chroute install-agent` to start a preset automatically at login.
3. Rerun a preset automatically when a device reconnects.

## Working style

- Start with `routing.py` and its tests, then `devices.py` plus `chroute list`, then the engine, then the meter.
- Make small commits. Run the tests after each step.
- When something needs my hardware to verify, stop and tell me exactly what to run and what output to expect.
