# gaudioroute

A small command-line audio channel router for macOS. It takes any input
channel(s) of one CoreAudio device and plays them, in real time, on any output
channel(s) of another. Think of it as a minimal, terminal-only Loopback.

## Why?

I wanted to play Rocksmith 2014 on macOS in Microphone mode with a Scarlett
Solo audio interface. The problem is that Rocksmith only listens to **channel 1**
of the device you pick. On the Solo, channel 1 is the XLR mic input, and the
guitar's instrument jack is **channel 2**. If you select the Scarlett directly,
Rocksmith hears the empty mic socket and nothing else, and neither Rocksmith nor
the Scarlett lets you swap the channels.

So the guitar has to be moved onto channel 1 of *some* device that Rocksmith
can select. [BlackHole 2ch](https://existential.audio/blackhole/) provides that
device. It's a virtual cable: whatever is played into its output channel N
comes out of its input channel N. gaudioroute does the moving:

```
Scarlett in 2 (guitar) ──gaudioroute──▶ BlackHole 1 & 2 ──▶ Rocksmith (mic: BlackHole)
```

```
gaudioroute --in Scarlett --out BlackHole --route 2:1 --route 2:2 --rate 48000
```

It also locks both devices to **48 kHz**, which Rocksmith needs. Routing through
GarageBand also gets the signal through, but GarageBand switches BlackHole to
44.1 kHz and then Rocksmith won't calibrate.

## Install

Requires Python 3.11+ and macOS.

With a plain venv:

```sh
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/gaudioroute list
```

Or with uv:

```sh
uv run gaudioroute list            # run from the checkout
uv tool install -e .           # put `gaudioroute` on your PATH
```

The first time gaudioroute opens an input, macOS asks whether your terminal app
may use the microphone. Say yes. If you said no earlier, see Troubleshooting.

## Usage

`gaudioroute list` shows your devices, their channel counts and the sample
rate each one is currently running at.

`gaudioroute run` (the default command) starts routing:

| Flag | Meaning |
|---|---|
| `--in NAME` | Input device (any part of its name, case-insensitive). |
| `--out NAME` | Output device, matched the same way. |
| `--route X:Y[@GAIN]` | Input channel X → output channel Y, counting from 1. Optional gain, e.g. `2:1@0.8`. Repeatable. |
| `--rate HZ` | Sample rate for both devices. Default 48000. |
| `--blocksize N` | Frames per callback. Default 128. |
| `--latency L` | `low` (default), `high`, or seconds such as `0.003`. |
| `--preset NAME` | Load settings from a preset. Flags override it. |
| `--no-meter` | Plain log lines instead of the live meter. |

One input can feed several outputs, and routes into the same output are mixed
together. Output channels with no route stay silent.

While it runs, a live meter shows the level of each route, plus counters for
dropouts (xruns) and clipping. If another app changes either device's sample
rate, the meter shows a red warning. Press Ctrl-C to stop.

### Presets

Save settings in `~/.config/gaudioroute/presets.toml`:

```toml
[rocksmith]
in = "Scarlett"
out = "BlackHole"
rate = 48000
routes = ["2:1", "2:2"]
```

```sh
mkdir -p ~/.config/gaudioroute && cp presets.example.toml ~/.config/gaudioroute/presets.toml
gaudioroute presets           # list them
gaudioroute --preset rocksmith
```

`--route` on the command line replaces the preset's routes rather than adding
to them.

## Using it with Rocksmith

1. Run `gaudioroute --preset rocksmith` and check that the meter moves when you
   strum.
2. Start Rocksmith **after** gaudioroute is running. Pick Microphone mode and
   select BlackHole.
3. If playing feels laggy, try `--latency 0.003` or `--latency 0`, and keep an
   eye on the xrun counter. Rocksmith's own latency calibration also helps.

To check the signal without Rocksmith, start a new audio recording in QuickTime
with BlackHole 2ch as the input. QuickTime's level meter should move with the
guitar.

## Troubleshooting

- **"No input signal for 10 s"**: the most likely cause is the microphone
  permission (System Settings → Privacy & Security → Microphone → enable your
  terminal app, then restart the terminal). It can also mean the wrong input
  channel (the Solo's instrument jack is input 2) or the Scarlett's gain knob
  being too low.
- **"Couldn't open … without resampling"**: another app is holding a device
  at a different rate. Quit that app, or set 48 kHz in Audio MIDI Setup, and
  try again.
- **Dropouts or crackles**: try `--latency high` or `--blocksize 256`.
- **Ambiguous device name**: gaudioroute lists the matches. Type more of the
  name.

## Development

```sh
.venv/bin/python -m pytest
```

## Not done yet

- Drift-free mode with a private aggregate device.
- `launchd` agent to start a preset at login.
- Restarting automatically when a device reconnects.
