# FM Receiver: how to use it

This guide covers how to run the app and how to use each part of the window.
What the app can do, and how well each part has been tested, is in
[capabilities.md](capabilities.md).

## Setting up

The app runs on Linux (x86-64) and on a Mac with Apple Silicon. Both use
the same conda environment, made from `environment.yml` in the project:

```sh
conda env create -f environment.yml      # makes the "gnu" environment
```

On a Mac, get conda from Miniforge first (`brew install --cask miniforge`).
`./fm-receiver` finds it by itself. For `conda activate` in Terminal, run
`conda init zsh` once and open a new window.

The environment is everything the HackRF, an RTL-SDR, a USRP and IQ playback need. The
BB60D needs two
more things that conda doesn't have: Signal Hound's library
(`libbb_api`) and their SoapySDR module. They are not in this repository.
Signal Hound's licence lets their library be copied only to people who own
the hardware, and this repository is public.

### The BB60D on Linux

Install the library in `/usr/local/lib`, and build and install
[Signal Hound's SoapySDR module](https://github.com/SignalHound/soapy-bb60),
as their READMEs describe. The app finds the module in
`/usr/local/lib/SoapySDR`.

### The BB60D on a Mac

Signal Hound's Mac library (5.0.11) does sweeps and IQ, but not real time,
so the **Real time** button is greyed out on a Mac. Two faults in it change how
the app uses the BB60D there:

- **It can't close the device.** `bbCloseDevice` crashes the program, even
  straight after opening. So on a Mac the app opens the BB60D once and keeps
  it until you quit. **Stop** leaves it held: another program (Spike, say)
  can have it only once the FM receiver has quit.
- **Its IQ at 2.5 MS/s and below is unreliable**: sometimes fine,
  sometimes a thousand times too loud, all NaN, or wild values. So on a
  Mac the IQ bandwidth shows 2.5 MS/s greyed out, and its tooltip says
  why. Sweeps are unaffected.

Both are Signal Hound's to fix. When a fixed library comes out, the app
can drop these limits (see `knowledge/roadmap.md`).

Everything here goes into the conda environment: the module and Signal
Hound's library, side by side. No `sudo` is needed. `/usr/local/lib` won't
work: the library calls itself plain `libbb_api.5.dylib`, and on macOS 26
dyld looks for a plain name only in the current folder. Remaking the
environment removes both, so do steps 2-4 again after that.

Run these from the folder that holds `signal_hound_sdk`:

1. Install libusb and CMake. The library loads Homebrew's libusb.
   ```sh
   brew install libusb cmake
   ```
2. Download the [Signal Hound SDK](https://signalhound.com/software/signal-hound-software-development-kit-sdk/)
   and unzip it. The Mac library is in version 5.0.11 and later, from the
   2026-09-14 SDK on. Copy it, unaltered, into the environment under the
   name it links by:
   ```sh
   conda activate gnu
   cp signal_hound_sdk/device_apis/bb_series/lib/macos_arm/libbb_api.5.0.11.dylib \
      "$CONDA_PREFIX/lib/libbb_api.5.dylib"
   ```
   If the SDK came through a web browser, macOS may refuse to load the
   library. Clear the download flag:
   `xattr -d com.apple.quarantine "$CONDA_PREFIX/lib/libbb_api.5.dylib"`.
3. Build the SoapySDR module into the environment, so it uses the
   environment's own SoapySDR:
   ```sh
   git clone https://github.com/SignalHound/soapy-bb60
   cmake -S soapy-bb60 -B soapy-bb60/build \
       -DCMAKE_PREFIX_PATH="$CONDA_PREFIX" \
       -DCMAKE_INSTALL_PREFIX="$CONDA_PREFIX" \
       -DCMAKE_INSTALL_RPATH="$CONDA_PREFIX/lib" \
       -DSignalHoundBB60_INCLUDE_DIRS="$PWD/signal_hound_sdk/device_apis/bb_series/include" \
       -DSignalHoundBB60_LIBRARIES="$CONDA_PREFIX/lib/libbb_api.5.dylib"
   cmake --build soapy-bb60/build && cmake --install soapy-bb60/build
   ```
4. Point the module at the library through its rpath, which is the
   environment's `lib`. This changes the module you just built, not Signal
   Hound's library, and re-signs it, as macOS requires:
   ```sh
   M="$CONDA_PREFIX/lib/SoapySDR/modules0.8/libSignalHoundBB60.so"
   install_name_tool -change libbb_api.5.dylib @rpath/libbb_api.5.dylib "$M"
   codesign -f -s - "$M"
   ```
5. Check. `SoapySDRUtil --info` should list `SignalHoundBB60` under
   "Available factories". With the BB60D plugged in,
   `SoapySDRUtil --find="driver=SignalHoundBB60"` should find it. Then run
   `python tools/tests/run_all.py --hw` with an antenna attached.

## Start it

```sh
./fm-receiver
```

Run this from the project directory, or through a symlink to the script. It
activates the `gnu` conda environment (set `FMRX_CONDA_ENV` to use a
different one) and opens the window directly; there is no launcher and no
setup dialog.

- **First run:** the app opens whichever radio is plugged in, checking for the
  BB60D first, then the HackRF, then an RTL-SDR plugged into this computer, and starts in Receive on 98.7 MHz.
- **After that:** it reopens with the same radio, mode, frequency and window
  layout you last used.

Useful options (`./fm-receiver --help` lists them all):

| Option | What it does |
|---|---|
| `--radio bb60` / `hackrf` / `usrp` / `rtlsdr` / `file` | Choose the radio for this run |
| `--usrp-address 192.168.10.2` | Connect to a USRP at this address |
| `--rtl-address macmini` | Use an RTL-SDR on another computer (an ssh host), and show its address box. Without it the RTL-SDR is this computer's |
| `--file PATH` | Play back an IQ recording instead of a radio |
| `--freq 95.1` | Tune to this station (MHz) |
| `--mode sweep` / `receive` / `recordings` | Start in this tab |
| `--sweep 87.5 108` | Set the sweep span (MHz) |
| `--theme slate` / `reading-room` / `walnut` | Choose the colour theme |
| `--realtime` | Show the Sweep tab's **Real time** button (BB60D, not on a Mac); it is hidden otherwise |
| `--no-audio` | Don't use the sound card (recording still works) |
| `--no-save` | Don't save settings when the window closes |

## The window

```
┌ FM Receiver  Radio:[BB60D ▾] [Stop]   status line ................... Themes (●) ┐
│┌ Sweep | Receive | Recordings┐ ┌ RF spectrum ─────────────────────────────────────┐ │
││ controls for the mode      │ │                                                   │ │
│└────────────────────────────┘ │ waterfall                                         │ │
│┌ RF gain ───────────────────┐ ├ readout      Span Ref Range Avg □Peak □Waterfall ┤ │
│┌ Audio: Mute  Volume  L/R ──┐ ├───────────────────────────────────────────────────┤ │
│┌ Record: □WAV □IQ ch □IQ band│ │ Sweep: stations found  /  Receive: MPX + RDS     │ │
└────────────────────────────────────────────────────────────────────────────────────┘
```

- **Header**
  - **Radio** switches to another radio straight away.
  - **Stop** closes the radio so other programs can use it; **Start** opens it
    again. (Not a BB60D on a Mac: that stays held until the app quits - see
    *Setting up*.)
  - The **status line** shows what is running, or what went wrong, for
    example "Input overloaded - turn the RF gain down". On a HackRF or an
    RTL-SDR it always ends with the share of samples clipped ("clipped 0.13%"):
    green under 0.3%, amber to 1%, and over that the overload warning. In
    a sweep it is the worst step of the last sweep, and names that step.
    If the radio stops sending (unplugged, or an RTL-SDR's network or
    rtl_tcp gone), it turns red and says so: at once when the radio can
    tell, or after 3 s without samples. A radio on this computer's USB
    that was unplugged opens again by itself when you plug it back in, in
    the tab you were in (about 2 s after it is back).
  - The **Themes** disc, top right, is the theme in force: a click moves to
    the next (Slate, Reading Room, Walnut). Hover over the disc or the word
    Themes to see its name, even while another window has the focus.
- **The first two tabs are the radio's two modes.** Switching tabs switches
  the radio between them. On a BB60D the switch takes 0.02-0.3 s.
- **The third tab, Recordings,** plays back what you recorded. The radio is
  closed while it is open, and opens again when you go back.
- **RF gain** applies to the radio in both modes, and each radio remembers its
  own setting. In Sweep it sits inside the tab, under the Sweep and Tuner
  boxes; elsewhere it is under the tabs, above **Audio** and **Record**,
  which are hidden in Sweep since there is nothing to hear or record while
  the radio sweeps. On a BB60D the **AGC** box beside it hands the gain to the
  device in its own sweep: the slider greys out, and the **Ref level** knob
  moves to 5 dB over the strongest signal, which is how Signal Hound
  recommends setting it. Turn the knob by hand to hold a higher level; AGC
  takes over again when the signals rise past it or fall 10 dB below. In
  Receive the slider sets the gain even with AGC ticked (the IQ stream has
  no automatic gain), and says so when you hover over it.
- On a BB60D, hover over the **status line** for its temperature, USB
  voltage and current. Below 4.4 V the status line warns: measurements may
  be off, so check the cable and the USB port.

## A typical session

1. **Sweep the band.** Open the **Sweep (FFT)** tab. It starts on *Full
   range of the radio*: 9 kHz to 6 GHz on a BB60D, about four times a second.
   **Stations found** lists every FM station standing clear of the noise.
   Choose *FM broadcast 87.5-108* for a closer look at the band.
2. **Pick a station.** Double-click it in the list, or double-click its peak
   in the spectrum. The app switches to **Receive (IQ)** tuned to it.
3. **Listen.** Audio starts at once. Stereo and RDS lock within a few seconds:
   station name, PI and call sign, program type, RadioText, Now Playing and
   the clock.
4. **Record.** Tick what you want and press **Record** (Ctrl+R). Press it again
   to stop. The files are saved in `recordings/`.
5. **Listen back** in the **Recordings** tab (Ctrl+3).
6. **Go back** to the Sweep tab (Ctrl+1) at any time.

## Sweep (FFT)

A sweep covers more spectrum than the radio can see at once. Nothing is
demodulated. There are two kinds:

- **The BB60D sweeps itself.** Signal Hound's API sweeps in the device, 9 kHz
  to 6 GHz in about 230 ms (26 GHz/s), and the levels are in **dBm**, as the
  device measures them.
- **The HackRF sweeps itself too**, with its firmware's sweep mode: 1 MHz to
  6 GHz in about 0.75 s (8 GHz/s), levels in **dBFS**.
- **A USRP hops its LO** across the span. Each step's FFT is stitched into
  one picture, with levels in dBFS.

The tab has four boxes: **Sweep** (the band, how it is swept, Pause, the
station threshold), **Stations found**, **Tuner** (where the receiver will
tune, with **Listen** to its right) and **RF gain**. While sweeping, the RF
spectrum and waterfall take the whole right-hand side.

| Control | What it does |
|---|---|
| **Band** | Presets: **Full range of the radio** (the default, 9 kHz to 6 GHz on a BB60D), FM 87.5-108, Japan 76-95, OIRT 65.8-74, VHF 30-300. **Custom** is selected automatically when you set your own bounds. |
| **Start** / **Stop** | The sweep's lower and upper bounds, in MHz to the kHz (9 kHz is 0000.009). Hover a digit and roll the wheel, or type a frequency. They stay inside the radio's range and at least 200 kHz apart. The sweep changes as soon as the digits come to rest. |
| **Tuner** | The same tuner the Receive tab has, here so you can place it while you sweep: it is the marker on the spectrum, it is what **Listen** tunes to, and it is what **Real time** watches around. Hover a digit and roll the wheel, type a frequency, or use the ▲/▼ beside it; a click on the spectrum moves it too, and so does a middle-drag of the orange channel band (see below). |
| **Real time** *(BB60D, only with `--realtime`)* | Hidden unless the app is started with `--realtime`: Receive at 40 MS/s shows as much band (27 MHz), as often (30 times a second), and plays the station as well. A button that stays down. Pressing it drops the sweep to the 27 MHz it can watch, centred on the **Tuner**, and watches that instead of sweeping; letting it out gives back the span that was there. Put the tuner outside the window afterwards and the window moves to it. Widen the bounds past 27 MHz and it sweeps instead, saying so in the line under the buttons. Not on a Mac, whose Signal Hound library has no real time. See *Sweep, real time and IQ* below. |
| **RBW** *(BB60D, HackRF)* | Resolution bandwidth. **Auto** keeps a sweep near 80,000 points: on a BB60D 300 kHz over the full range and 1 kHz over the FM band; on a HackRF it is the FFT's bin width, 76 kHz over the full range and 2.4 kHz (its finest) over the FM band. Narrower shows more detail and a lower noise floor. If you pick one too fine for the span, it is raised, and the line under the buttons says so. |
| **Step bandwidth** *(USRP, RTL-SDR)* | The radio's sample rate while sweeping, which sets how much each step sees. Wider means fewer steps. Changing this restarts the radio. |
| **FFT** *(USRP, RTL-SDR)* | Bins per FFT. This sets the RBW: 4096 bins at 20 MS/s gives 4.9 kHz. |
| **Frames per step** *(USRP, RTL-SDR)* | FFT frames averaged at each step. More gives a smoother trace and a slower sweep. |
| **Settle** *(USRP, RTL-SDR)* | How long to wait after each retune before trusting the samples; 5 ms by default on a USRP, 100 ms on an RTL-SDR (a retune arrives 30-70 ms later over the network). **If a signal appears twice, or shows where there is nothing, increase this.** |
| **Pause / Resume** | Freezes the sweep, for example to study the trace. |
| **Listen** | Receive the station the **Tuner** is on - the selected station, or wherever the marker is. |
| **Station threshold** | How far above the noise floor a channel must be to go in the list (default 15 dB). |

The line under the buttons shows the plan and the measured speed, for example
"The BB60D's own sweep, RBW 300 kHz (auto), 76,801 points – 233 ms per sweep
(4.3/s, 25.8 GHz/s)".

Stations are listed only where FM broadcasting is (65.8 to 108 MHz), however
wide the sweep.

The **orange band** on the sweep is the channel the receiver would take, on
the tuner: grab it with the **middle button** and drag it to where you want
to listen (snapped to the Step if **Snap to step** is on), or roll the wheel
over it to change how wide the channel is. Nothing is retuned while you drag:
the radio is sweeping, and does one thing at a time. What the band picks is
where **Listen** and the **Receive** tab start from, and what **Real time**
centres its window on. Zoomed right out a 200 kHz channel would be thinner
than a pixel, so the band is drawn a little wider than it is - enough to stay
a handle.

### Sweep, real time and IQ

The BB60D can work three ways, one at a time. "Real time" is a spectrum
analyser's word for *without gaps*, not for *live*: all three are live.

| | Sweep | Real time | IQ (the Receive tab) |
|---|---|---|---|
| How much spectrum | Up to all of it, 9 kHz to 6 GHz | One chunk, up to 27 MHz | One chunk, up to 27 MHz (at 40 MS/s) |
| How it covers it | Steps across, a moment at each frequency | Stays put and analyses every sample | Stays put and sends every sample here |
| What comes back | A trace each pass | A trace every 33 ms, and a density map | The signal itself |
| Listen or record | No | No | Yes |
| A short burst | Missed if the sweep is elsewhere just then | Never missed if it lasts 307 us or more (at 10 kHz RBW) | The samples have it; the spectrum picture only glances at about 1% of them |

Sweep is like a guard walking the building with a torch: every room, but
each only for a moment. Real time is a camera fixed on one room, missing
nothing that happens there. IQ is that camera's raw feed, which the app
decodes to sound and RDS.

In real time a **density map** is drawn behind the trace, in the waterfall's
colours: brighter where a level is hit more often. A steady station shows
as a bright band at its level. The noise is a cloud near the floor. A burst,
or a signal hidden under a stronger one, shows as a fainter patch no single
trace would keep. The map runs from the view's **Ref level** down its
**Range**; turn those and it follows. A pixel fades out over about a second
after its last hit, so a burst leaves a fading patch where it was. The trace
is the highest level each point reached since the screen was last drawn:
every 33 ms frame counts, and so the peak hold and the waterfall catch a
burst however short.
 A HackRF sweeps with its firmware too: its whole range, 1 MHz to 6 GHz,
in about 0.75 s, and the FM band 30 times a second. Its levels are dBFS,
not calibrated. The RBW list sets its bin width (2.4 kHz at the finest);
there is no real time and no AGC on it. Over the whole range at a gain that
suits FM, a strong TV or phone transmitter clips its step: the status line
says so, and names it.

In the **station list**, one click moves the marker and a double-click starts
listening. Once you have listened to a station, its RDS name appears next to
it.

## Receive (IQ)

In Receive the radio runs at a narrow IQ bandwidth, and the app demodulates
one station. The tab has three boxes, top to bottom: **Radio** sets the
radio itself, **Tuner** picks the station inside the radio's band, and
**RDS** shows the station as decoded.

Each box has a **chevron** at the right of its title: click it, or the
title, to fold the box away and again to open it; it slides shut or open,
and the chevron turns. A folded **Radio** box
still shows its **Center** and **Center on tuner**, and a folded **Tuner**
box its tuner, each gliding to the middle of the box once it has folded
(the Step knob fades out as the Tuner box folds, and back in as it opens);
a folded **RDS** box shows its **Now playing** and **RadioText**. The app remembers which are
folded.

```
┌ Radio ───────────────────────────────────────┐
│       Center: [0098.400 MHz] [Center on tuner]│   the radio's own frequency
│  Tuner range: 94.800 - 102.000 MHz            │   where the tuner can go
│ IQ bandwidth: [10 MS/s ▾]                     │   how much band it takes in
└───────────────────────────────────────────────┘
┌ Tuner ───────────────────────────────────────┐
│        Tuner: [0099.100 MHz] [▲▼]   (Step)    │   the station you hear
│Channel filter: [200 kHz] [▲▼]                 │
└───────────────────────────────────────────────┘
┌ RDS ─────────────────────────────────────────┐
│ Station, Standard, Stereo / Snap to step,     │
│ Clear RDS, Signal, Audio, then the RDS itself │
└───────────────────────────────────────────────┘
```

The multiplex (MPX) spectrum fills the bottom right.

### Radio

| Control | What it does |
|---|---|
| **Center** | The radio's centre frequency (its LO), drawn as a **dashed line** on the spectrum (not the waterfall, which is left clear), thin and grey in every theme: it is a reference, and orange is kept for where you are tuned. Outside the tuner's reach the spectrum is dimmed. Moving it moves the band the tuner can reach. If the tuner is still inside the new band it stays where it is; if not, it is pulled in to the nearer edge. **While you move the Center the line fades away**, so you can see the spectrum under it, and it comes back once you stop. |
| **Center on tuner** | Puts the Center 300 kHz below the tuner, so there is room to tune either way. |
| **Tuner range** | The lowest and highest the tuner can go around this Center. It is about three quarters of the IQ bandwidth, less half a channel at each end. |
| **IQ bandwidth** | The radio's sample rate in Receive: how much of the band the spectrum shows, and the tuner can reach (BB60D 2.5/5/10/20/40 MS/s, 2.5 greyed out on a Mac; HackRF 2-20 MS/s; RTL-SDR 2 and 2.4 MS/s). Changing it rebuilds the receiver; the Center stays if the tuner still fits. |

**The tuner stops at the edge of the band.** Rolling, stepping, typing,
clicking or dragging past it leaves the tuner at the edge, and **Tuner
range** says so. To go further, move the Center. The parts of the spectrum
the tuner cannot reach are shaded, with a dotted line at each limit.

There is one exception: a station picked from outside Receive (from the
Sweep list, or with `--freq`) places the Center for you, because there is
no band yet to stay inside.

On a HackRF or an RTL-SDR the tuner also keeps 100 kHz clear of the Center, where the
radio's DC spike is: it jumps over that gap in the direction you are tuning.
The BB60D has no spike, so there is no gap.

An IQ recording's Center is where it was recorded, so it cannot be moved.

**Which IQ bandwidth?** On the BB60D, 10 MS/s (the default). At 2.5, 5 and
10 MS/s the app uses the same CPU (about half a core) and receives just as
well, and 10 MS/s shows about 7.5 MHz of the band instead of 1.9 MHz.
**40 MS/s shows 27 MHz** - the whole FM band and more, 30 times a second -
and the tuner reaches ±11.85 MHz from the Center; it costs more CPU (84% of
a core against 57% at 10) and receives just as well (no samples lost, RDS
100%). 20 MS/s shows 17.8 MHz. The exception is a long **IQ – whole band**
recording: at 10 MS/s that is 80 MB/s (4.8 GB a minute), and at 40 MS/s
320 MB/s, more than most disks keep up with; choose 2.5 MS/s (20 MB/s) for
those - or 5 MS/s (40 MB/s) on a Mac, where 2.5 is greyed out for the BB60D.

### Tuner

| Control | What it does |
|---|---|
| **Tuner** | The station you hear, to 1 kHz. **Hover over a digit** and it lights up; **roll the mouse wheel** to move that digit up or down. It carries as arithmetic does: rolling up the tens digit of 90.000 gives 100.000, and so does rolling up the ones digit of 99.000. With the pointer over a digit, **Up/Down** do the same and **PageUp/PageDown** move it by ten. **Type a digit** (or press Enter, or double-click) to type a whole frequency in MHz; Enter sets it and Escape leaves it as it was. |
| **▲ / ▼ beside the tuner** | Steps the tuner down or up by one **Step**. Click a half (hold it to repeat), or roll the wheel over it. Ctrl+Left and Ctrl+Right do the same from anywhere in the window. |
| **Step** (knob) | Four settings: 10, 50, 100 and 200 kHz. It sets what the arrows and Ctrl+Left/Right move by, and what Snap rounds to. FM channels are 200 kHz apart in the Americas, on the odd tenths (88.1, 88.3 … 107.9), and a 200 kHz Step keeps to those; Europe's are 100 kHz apart. |
| **Channel filter** | 60-400 kHz, applied live. (The **IQ – channel** recording is 500 kS/s; the filter stops at 400 kHz because the channel is sampled at 500 kS/s and the filter needs room to roll off inside it.) Hover a digit and roll the wheel, use its **▲ / ▼** (5 kHz a click), or roll the wheel over the orange band on the spectrum. A narrower filter rejects a strong neighbour, but below about 180 kHz stereo and RDS start to suffer. Wider than about 250 kHz the audio takes in any neighbour that close; the widths up to 400 kHz are for the **IQ – channel** recording, which then holds an HD Radio station's digital sidebands (±200 kHz). |

**The tuner's marker** (the thin orange line at the tuner, on the spectrum
only - the waterfall is left clear) is hidden while you are not tuning: the orange band
shows where the station is. It fades in as soon as you tune, by any means,
and fades out again a moment after you stop. Pressing the middle button on
the orange band - grabbing the tuner - shows it too, for as long as you
hold it. In Sweep the marker is always shown, since it is your pick.

### Tuning with the mouse on the spectrum

The **orange band** on the RF spectrum is the channel filter, centred on the
tuner. It brightens under the pointer.

- **Middle-button drag on the orange band:** tunes. The band and the tuner
  follow the pointer, and the audio follows as you drag. The drag stays
  inside the tuner range, so the radio itself never moves.
- **Mouse wheel over the orange band:** makes the channel filter wider or
  narrower, 5 kHz a notch (1 kHz with Shift).
- **Click the spectrum or the waterfall:** tunes there, within the range.
- **Right-click the spectrum or the waterfall:** offers **Tuner to 99.100
  MHz** - the frequency under the pointer, already rounded by **Snap to
  step**, so you can see where it would land before anything moves. Choose
  it and the tuner goes there; press Escape or click away and nothing
  happens. Sweeping, that only says where **Listen**, the Receive tab and
  **Real time** will start from; the radio goes on sweeping.
- Everywhere else the wheel zooms and a left or middle drag pans the view,
  as before.

Peak hold starts again whenever the Center moves. The MPX view's peak hold
starts again on every tune, so nothing from the previous station stays on
screen.

### RDS

Top to bottom:

| Row | What it shows or does |
|---|---|
| **Station** | The most consistent PS name, or the RT+ station name. |
| **Standard** | RBDS with 75 µs de-emphasis for the Americas, or RDS with 50 µs for Europe and elsewhere. |
| **Stereo** | Turn it off for mono, which is quieter on a weak station. With no pilot, the audio is mono anyway. |
| **Snap to step** | Clicks, right-click picks and middle-drags on the spectrum tune to the nearest multiple of the Step. Off, they tune to where the pointer is, to the kHz. |
| **Clear RDS** | Clears the decoded data and starts decoding again. |
| **Signal** | The power in the channel and how far the station stands above the floor: green above 30 dB, amber above 15 dB, red below that. |
| **Audio** | *Stereo – pilot locked (standard phase)*, or *Mono – no stereo pilot*. Stereo needs a pilot that stands 10 dB over the noise beside it, so an empty channel, a mono station, or a station tuned slightly off reads *Mono*. *Standard phase* is what broadcasters send; *cosine phase* is what the RF bench toolkit's own transmitter sends. The app detects which by itself. |
| **Station ID (PI)** | With the call sign, if the station's own text confirms it; otherwise it says "maybe". |
| **Program type**, **Now showing (PS)**, **Now playing**, **RadioText** | As they arrive. Now playing is the RT+ artist and title. |
| **Flags**, **Station clock**, **Decode quality** | TP, TA and TMC; the station's clock; how many groups, and how many blocks were good. |

Not every station sends RDS. If the PI stays at "-" for 20 s on a strong
station, it probably has none. In testing, 102.1 was one of these.

The **MPX view** (bottom right) is the demodulated multiplex from 0 to
125 kHz: mono audio, the 19 kHz pilot, stereo around 38 kHz and RDS at
57 kHz.

## Views: bandwidth and amplitude

Each spectrum has its own dials, at the right-hand end of the row under it;
the readout of the frequency and level under the pointer is in the
bottom-left corner of the plot, on a small panel of its own so a busy trace
can't wash it out.

**The level axis** (dBFS, or dBm on the BB60D's own sweep) is a handle too.
Its numbers light orange under the pointer. Roll the wheel over it to zoom
the amplitude scale about the level under the pointer, as the wheel zooms
the span: Ref level and Range change together (Shift for fine). Press the
middle button on it and drag up or down to move the Ref level; the trace
goes with the pointer. The knobs follow either way.

**The waterfall's time scale** runs down its left side: the newest row at
the top ("now"), then 2 s, 4 s ... ago. The waterfall remembers the last
five minutes. Its numbers light orange under the pointer; roll the wheel
over them to show more of the past or less (2 s to 5 minutes, 20 s at
first; Shift for fine). Zoomed out, each row on screen shows the strongest
of the rows it covers, so a short burst stays visible. The view remembers
how much time it showed.
**Hover over a dial and roll the mouse wheel** to turn it, or drag it up or
down; hold Shift for fine steps. A dial under the pointer lights orange, and
glows in Slate and Walnut or lifts on a shadow in Reading Room, so you can
see which one the wheel will turn. Double-click a dial to reset it. The
same goes for the Volume and Step knobs.

| Dial | One wheel notch |
|---|---|
| Span | ×1.25 wider or narrower |
| Ref level | 2 dB |
| Range | 5 dB |
| Average | 1 |
| Volume | 2% |
| Step | the next setting |

| Dial / control | What it sets |
|---|---|
| **Span** | How much frequency is shown. In Receive it is centred on the station; in Sweep, on the middle of the view. |
| **Ref level** | The level at the top of the scale. |
| **A** (the key) | **Auto scale**: sets Ref level and Range so the trace on screen sits in the middle of the spectrum, from its noise floor to its highest peak with 10 dB to spare at either end (30 dB at least). The span is left as it is. Under **AGC** the Ref level is the radio's, so only the Range moves. |
| **Range** | dB from the top of the scale to the bottom, i.e. the amplitude scale. The waterfall colours follow Ref level and Range. |
| **Average** | Frames averaged in Receive, or sweeps averaged in Sweep. |
| **Peak hold** | Draws a dashed trace of the highest level seen. Untick it to clear. |
| **Waterfall** | Shows or hides the waterfall. |
| **Full span** | Zooms out to everything available. |

You can also use the mouse on the plot: the wheel zooms, dragging pans, and
the Span dial follows. In Sweep the view stops at 0 Hz and 6 GHz, however far
you drag or zoom out. Hovering shows the frequency and level under the
pointer. In Receive, the wheel and the middle button over the orange channel
band work on the channel instead; see *Tuning with the mouse on the
spectrum* above.

The waterfall's colours are the theme's own: pale ice on slate in Slate,
ink on paper in Reading Room, and the tan and cream of a radio dial in
Walnut. The noise floor sinks into the plot's background.

Sweep, Receive and the MPX view each remember their own dial settings.

## Audio

- **Mute** (Ctrl+M) turns red when on. It silences the speaker only; the level
  meters and any recording carry on.
- **Volume** (dial: roll the wheel over it, or Ctrl+Up/Down) follows a square
  law, so the middle of the dial sounds like the middle.
- The **L/R meters** show the level before the volume control. The bar is the
  RMS level, the lighter bar the peak, and the tick the recent peak. It turns
  amber above -6 dBFS and red above -1 dBFS.

## Recording

Tick any combination of the three kinds, then press **Record** (Ctrl+R).
Recording works in Receive only.

| Kind | Contents | Size |
|---|---|---|
| **Audio (WAV)** | The station as heard: stereo, 48 kHz, 16-bit, after de-emphasis and before the volume control. Mute and volume don't affect it. | about 0.2 MB/s |
| **IQ – channel** | Just the tuned station, through the channel filter, 500 kS/s complex, with the station at 0 Hz. Open the filter to 400 kHz to keep an HD Radio station's sidebands. | 4 MB/s |
| **IQ – whole band** | Everything the radio receives at its IQ bandwidth. The size is shown next to the checkbox. | 8 bytes per sample: 20 MB/s at 2.5 MS/s, 80 MB/s at 10 MS/s |

- **Where files go:** `recordings/` in the project folder. Use **Folder...** to
  change it.
- **File names** look like `fm-98.70MHz-20260921-181500-audio.wav`, with
  `-iq-channel` or `-iq-band` for the IQ kinds. Every file of one recording
  has the same name up to the kind, and
  `fm-98.70MHz-20260921-181500-recording.json` beside them holds what the
  station sent over RDS while it was recorded (see *Recordings* below).
- **IQ file format:** each IQ recording is a `.cfile` of complex float32 data
  with two description files beside it. `.sigmf-meta` is SigMF, which other
  SDR tools read. `.json` is the RF bench toolkit's capture format, so its
  `scripts/test_rds_core.py` can decode the recording.
- **Retuning while recording IQ** closes the file and continues in a new one
  (`-part2`, `-part3`, …), so each file has one centre frequency. A
  whole-band recording stays in the same file if only the tuner moves within
  the band; moving the Center starts a new part. During a middle-drag the
  new part starts when you let go, not at every step of the drag.
- Changing mode or radio, or pressing Stop, ends the recording. The panel
  lists what was saved.

## Recordings: listening back

The **Recordings** tab (Ctrl+3) lists everything in the recordings folder,
newest first, and plays it. The radio is closed while you are in this tab,
so other programs can use it (except a BB60D on a Mac, which stays open
until the app quits). Going back to Sweep or Receive opens it again, tuned
where it was.

1. **Pick a recording.** Each line is one press of Record: the station, its
   RDS name and call sign if they were heard, then the date, the length,
   what was recorded and the size.
2. **Choose what to play** in **Play**, if the recording has more than one
   file. The IQ is chosen first (the whole band before the channel), and
   the WAV is in the list too.
3. **Press Play**, or double-click the line. Press it again to pause.
4. **Jump** by clicking or dragging on the strip under the Play button.

What you see and hear depends on the file:

| File | On the right | Sound | RDS |
|---|---|---|---|
| **IQ – whole band** | The RF spectrum and waterfall of the whole band, and the multiplex, as in Receive | Made from the IQ, as in Receive | Decoded as it plays |
| **IQ – channel** | The same, for the one station | The same | The same |
| **WAV** | The sound's spectrum, left and right together (0-16 kHz shown; Span goes to 24), and its waterfall | The WAV as it was recorded | The RadioText and Now Playing logged while it was recorded |

- **In a whole-band recording, click another station** in the spectrum or
  the waterfall to listen to it. The Receive tab's controls (channel filter,
  stereo, region, the RDS details) work on the recording too.
- **The strip** is the whole recording at a glance: time from left to right,
  frequency upwards, in the waterfall's colours. A station is a line along
  it; music in a WAV shows its rhythm. The bright line is where you are, and
what has already played is dimmed.
- **Loop** starts again from the beginning at the end. Without it, playback
  stops and goes back to the start.
- **Delete...** removes every file of the chosen recording, after asking.
  **Show in folder** opens the folder in your file manager (Finder on a
  Mac), and **Folder...** chooses another one; Record saves there too.
- **Parts.** A recording that was retuned has parts (`-part2` and on). Each
  is its own entry in **Play**, with the station it was on.
- **Names.** Record saves the station's RDS name, PI and call sign, and each
  RadioText and Now Playing with its time, in the `-recording.json` file.
  Recordings made before this have none: playing their IQ back fills them
  in. A name is kept only once it has held for 8 seconds, because some
  stations scroll words through the name.
- A WAV plays at 48 kHz only, which is what Record makes.

## Playing other IQ files

The Recordings tab plays this app's recordings. For any other IQ file, such
as SigMF or the RF bench toolkit's captures, choose **IQ recording
(playback)** in the Radio list and open the `.cfile`, `.sigmf-meta` or
`.json` file. Or start the app with `./fm-receiver --file PATH`.

The recording plays in real time on a loop, as if it were a radio, and it
opens tuned to the station it was recorded on. Tuning moves the channel
within the recorded band. A whole-band recording therefore lets you listen to
any station that was in the band. A playback can't sweep.

## Radios

| Radio | Notes |
|---|---|
| **Signal Hound BB60D** | IQ bandwidth 10 MS/s by default (see *Which IQ bandwidth?* above). RF gain 60% (attenuator fully open, no RF amplification) is the tested best for FM. More gain overloads the front end with every other station in the band; if the status line says *Input overloaded*, turn it down. It sweeps itself, 9 kHz to 6 GHz, and the RF gain applies to that sweep too. The device stays open across mode switches: 0.02 s to Sweep, 0.2 s back. A full-range sweep uses about one CPU core, nearly all of it Signal Hound's API. On a Mac it has no real time, no 2.5 MS/s (greyed out), and stays open until the app quits (see *Setting up*). |
| **HackRF One** | Gain is spread over the preamp, LNA and VGA, with the toolkit's plan. **40% (the default) was best on the bench antenna**: 99% of RDS blocks good. At 47% and above the strong local stations drove its 8-bit ADC to full scale and RDS was lost. When that happens the status line says *Input overloaded – turn the RF gain down* with the share of samples clipped; turn the gain down until it goes. Wider IQ bandwidths let more stations in, so they need less gain: at 10 MS/s, 40% already clipped a little. A weak antenna may want more; too little shows as a pilot locking while RDS stays buried. It sweeps with its firmware: the IQ stream's device is closed while it does, and opened again for Receive (about 0.1 s each way). Over the whole range a gain that suits FM clips in the TV and phone bands; the status line names the step. |
| **RTL-SDR** | Through `rtl_tcp`, which the app starts and stops itself. A dongle plugged into this computer just works: choose RTL-SDR (it needs `rtl_tcp` installed: the environment's `rtl-sdr`, or Homebrew's `librtlsdr` on a Mac; on Linux the kernel's TV driver is detached by itself, and if rtl_tcp says *Kernel driver is active* anyway, `sudo rmmod dvb_usb_rtl28xxu`). **On another computer** (an advanced use): start the app with `--rtl-address HOST`, where HOST is an ssh host such as a name from `~/.ssh/config`, with `:port` if 1234 is taken. Only then does an address box appear next to the Radio list, to change it while the app runs (blank there is this computer). That computer needs `rtl_tcp` and key-based ssh login (no password prompt). If an rtl_tcp is already running there, the app uses it and leaves it running. 2.4 MS/s is its widest; it sweeps by hopping, about 3 s over the FM band. 60% gain (the default) is right for the FM band on an R820T; the status line shows the clipped share, as for a HackRF. |
| **Ettus USRP** | Type the IP address in the box next to the Radio list, or leave it blank to use the first USRP found. |

## Keyboard shortcuts

On a Mac, Ctrl is the ⌘ Command key.

| Keys | Action |
|---|---|
| Ctrl+1 / Ctrl+2 / Ctrl+3 | Sweep / Receive / Recordings |
| Ctrl+Left / Ctrl+Right | Step the tuner down / up by the Step |
| Up / Down (pointer on a digit) | That digit of the Tuner, Center or Channel filter up / down |
| PageUp / PageDown | The same digit by ten |
| Left / Right (entry focused) | Choose the digit Up/Down change |
| 0-9 or Enter (entry focused) | Type a value; Enter sets it, Escape cancels |
| Ctrl+M | Mute |
| Ctrl+Up / Ctrl+Down | Volume ±5 |
| Ctrl+R | Start/stop recording |
| A | Auto scale the spectrum on show (not while typing in a box) |

## Settings

Settings are saved to `~/.config/fm-receiver/config.json` when the window
closes. They include:

- the radio, and per radio its gain, rates and settle time;
- the tab, the tuner and the Center;
- the dials and audio settings;
- the recording choices and folder, and whether playback loops;
- the theme and the window layout.

To start fresh, delete the file. To use a different settings file, set
`FMRX_CONFIG=/path/to.json`.

## Troubleshooting

| Symptom | What to do |
|---|---|
| "No … was found" in the status line | Check the cable, and close anything else using the radio (Spike, GQRX, hackrf_transfer, another copy of this app). Then choose the radio again or press Start. |
| *No samples from the … for N s* or *… lost* | The radio has stopped sending: unplugged, its USB reset, or, for an RTL-SDR on another computer, the network or rtl_tcp there gone. Check the cable (or the other computer), then press **Stop** and **Start**. It clears by itself if the samples come back. |
| *… unplugged - plug it back in and it will open again by itself* | Plug it back in: *… is back - opening it again* follows, and it carries on in the tab it was in. Only for a radio on this computer's USB; one on the network, or a radio that stopped while still plugged in, needs **Stop** and **Start**. |
| Ghost copies of signals in a sweep | On a USRP or an RTL-SDR, increase **Settle** (an RTL-SDR on a slower network may need more than its 100 ms). (The BB60D and HackRF sweep themselves.) |
| Part of the left column is hidden under the spectrum | Drag the divider right. The column resizes itself on a theme change, so this should not happen any more. |
| No RDS on a strong station | It may not send RDS; check the MPX view for a hump at 57 kHz. On a weak station, try a narrower channel filter. |
| *Input overloaded* | Turn the RF gain down. On a HackRF or an RTL-SDR it also gives the share of samples clipped; turn down until the message goes. In a sweep it names the step that clipped: over the whole range a strong TV transmitter can clip one step at a gain that suits FM, and then the FM band preset is the one to use. |
| Another program can't open the radio | Press **Stop** (or close the app): Stop lets go of the device. On a Mac, a BB60D is let go only when the app quits. |
| The tuner won't go any further | It is at the edge of the band around the Center: move the **Center**, or press **Center on tuner** and carry on. |
| No sound | The **Audio** panel says if the sound card could not be opened. Check the **Mute** button. |
| Stereo sounds noisy | Untick **Stereo**. A weak station sounds cleaner in mono. |

## Tests

```sh
conda activate gnu
python tools/tests/run_all.py          # no radio needed, about a minute and a half
python tools/tests/run_all.py --hw     # plus the BB60D check, off air
```
