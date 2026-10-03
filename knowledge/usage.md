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

**HD Radio** (optional) needs the `nrsc5` program, which isn't in apt or
conda. On Ubuntu:

```sh
sudo apt install build-essential cmake autoconf automake libtool pkg-config \
                 libfftw3-dev libao-dev librtlsdr-dev libusb-1.0-0-dev git
git clone https://github.com/theori-io/nrsc5 ~/src/nrsc5
cd ~/src/nrsc5 && mkdir -p build && cd build
cmake .. -DUSE_SSE=ON && make -j$(nproc) && sudo make install && sudo ldconfig
```

The window finds it on PATH, or in `/usr/local/bin`, Homebrew's bin or
`~/.local/bin` (not yet tried on a Mac).

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
| `--file PATH` | Play back an IQ recording, or a Sceptre DVR's IQ (`.sdvr`), instead of a radio |
| `--freq 95.1` | Tune to this station (MHz) |
| `--mode sweep` / `receive` / `recordings` | Start in this tab |
| `--sweep 87.5 108` | Set the sweep span (MHz) |
| `--theme slate` / `reading-room` / `walnut` | Choose the colour theme |
| `--realtime` | Show the Sweep tab's **Real time** button (BB60D, not on a Mac); it is hidden otherwise |
| `--no-audio` (or `--mute`) | Start muted: press **Mute** (Ctrl+M) to hear it. Only for this run: the saved Mute setting is left as it was |
| `--no-save` | Don't save settings when the window closes |
| `--no-control` | No control socket: `tools/fmctl` can't reach this window (see [Controlling the window from a script](#controlling-the-window-from-a-script-fmctl)) |

## The window

```
┌ FM Receiver  Radio:[BB60D ▾] [Stop]   status line ................... Themes (●) ┐
│┌ Sweep | Receive | Recordings┐ ┌ RF spectrum ─────────────────────────────────────┐ │
││ controls for the mode      │ │                                                   │ │
│└────────────────────────────┘ │ waterfall                                         │ │
│┌ RF gain ───────────────────┐ ├ readout      Span Ref Range Avg □Peak □Waterfall ┤ │
│┌ Audio: Mute  Volume  L/R ──┐ ├───────────────────────────────────────────────────┤ │
│┌ Record: □WAV □IQ ch □IQ band│ │ Receive: Multiplex                               │ │
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
- **RF gain** applies to the radio in every mode, and each radio remembers its
  own setting. It is the top row of the tab's first box: in Receive a row of
  the Radio box, in Sweep the first row of the Sweep box; elsewhere its box is
  under the tabs, above **Audio** and **Record**. Audio and Record are hidden
  in Sweep since there is nothing to hear or record while the radio sweeps.
  On a BB60D the **AGC** box beside it hands the gain to the
  device in its own sweep: the slider greys out, and AGC sets the device's
  reference level to 5 dB over the strongest signal, which is how Signal
  Hound recommends setting it. It rises at once and falls only when the
  strongest input of the last minute has dropped 10 dB, so a burst that comes
  and goes (a WiFi radio next to the device) keeps its level between bursts.
  If the device overloads at a level that is already covered, AGC puts 5 dB
  more headroom on, up to 30 dB, and takes it off again only after ten quiet
  minutes. The label beside the slider reads the reference level
  (*ref -10 dBm*): the BB60D has no gain percentage there. The **Ref level** knob
  is only the view's: turn it (and Range) as you like, and AGC leaves it
  alone and does not blank the display when it moves the device's level.
- **AGC in Receive** (BB60D, HackRF, RTL-SDR, USRP): the app
  moves the gain itself, and **the slider moves with it**. When the radio
  overloads (a BB60D says so; on the others, over 1% of the samples clip)
  it turns the gain down 10% (5% if under 10% clip), again every couple of
  seconds while that lasts. After a minute with no overload and under 0.3%
  clipped it tries 5% back up, but never above where you last put the
  slider yourself (hover over the slider to see that limit). Moving the
  slider by hand sets a new limit, and the gain. A try that brings the
  overload back is undone, and the next waits twice as long (up to 16
  minutes). **Each change can leave a gap in the samples** (about 0.1 s on
  a BB60D; none on a HackRF, though each try at more gain can clip for a
  moment), a click in the audio, so **once it
  has settled, untick AGC**: the gain stays where AGC put it. On a HackRF
  or RTL-SDR the box is greyed in Sweep, which has no AGC for them, and
  keeps its tick for Receive. The gain and the limit are both remembered,
  so the next start begins at the settled gain. Worth knowing: **an
  overloaded BB60D sends no samples at all**, so the spectrum freezes and
  the sound stops; the status line says *Input overloaded*, not that the
  radio is lost.
- On a BB60D, hover over the **status line** for its temperature, USB
  voltage and current. Below 4.4 V the status line warns: measurements may
  be off, so check the cable and the USB port.

## A typical session

1. **Sweep the band.** Open the **Sweep (FFT)** tab. It starts on *Full
   range of the radio*: 9 kHz to 6 GHz on a BB60D, about four times a second.
   Choose *FM broadcast 87.5-108* for a closer look at the band.
2. **Pick a station.** Double-click its peak in the spectrum, or click it and
   press **Listen**. The app switches to **Receive (IQ)** tuned to it.
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

The tab has two boxes: **Sweep** (RF gain at the top, as in Receive, then the
band and how it is swept, Pause) and **Tuner** (where the receiver will
tune, with **Listen** to its right). While sweeping, the RF spectrum and
waterfall take the whole right-hand side.

| Control | What it does |
|---|---|
| **Band** | Presets: **Full range of the radio** (the default, 9 kHz to 6 GHz on a BB60D), FM 87.5-108, Japan 76-95, OIRT 65.8-74, VHF 30-300. **Custom** is selected automatically when you set your own bounds. |
| **Start** / **Stop** | The sweep's lower and upper bounds, in MHz to the kHz (9 kHz is 0000.009). Hover a digit and roll the wheel, or type a frequency. They stay inside the radio's range and at least 200 kHz apart. The sweep changes as soon as the digits come to rest. |
| **Tuner** | The same tuner the Receive tab has, here so you can place it while you sweep: it is the marker on the spectrum, it is what **Listen** tunes to, and it is what **Real time** watches around. Hover a digit and roll the wheel, type a frequency, or use the ▲/▼ beside it; a click on the spectrum moves it too, and so does a middle-drag of the orange channel band (see below). |
| **Real time** *(BB60D, only with `--realtime`)* | Hidden unless the app is started with `--realtime`: Receive at 40 MS/s shows as much band (27 MHz), as often (30 times a second), and plays the station as well. A button that stays down. Pressing it drops the sweep to the 27 MHz it can watch, centred on the **Tuner**, and watches that instead of sweeping; letting it out gives back the span that was there. Put the tuner outside the window afterwards and the window moves to it. Widen the bounds past 27 MHz and it sweeps instead, saying so in the line at the foot of the tab. Not on a Mac, whose Signal Hound library has no real time. See *Sweep, real time and IQ* below. |
| **RBW** *(BB60D, HackRF)* | Resolution bandwidth. **Auto** keeps a sweep near 80,000 points: on a BB60D 300 kHz over the full range and 1 kHz over the FM band; on a HackRF it is the FFT's bin width, 76 kHz over the full range and 2.4 kHz (its finest) over the FM band. Narrower shows more detail and a lower noise floor. If you pick one too fine for the span, it is raised, and the line at the foot of the tab says so. |
| **Step bandwidth** *(USRP, RTL-SDR)* | The radio's sample rate while sweeping, which sets how much each step sees. Wider means fewer steps. Changing this restarts the radio. |
| **FFT** *(USRP, RTL-SDR)* | Bins per FFT. This sets the RBW: 4096 bins at 20 MS/s gives 4.9 kHz. |
| **Frames per step** *(USRP, RTL-SDR)* | FFT frames averaged at each step. More gives a smoother trace and a slower sweep. |
| **Settle** *(USRP, RTL-SDR)* | How long to wait after each retune before trusting the samples; 5 ms by default on a USRP, 100 ms on an RTL-SDR (a retune arrives 30-70 ms later over the network). **If a signal appears twice, or shows where there is nothing, increase this.** |
| **Pause / Resume** | Freezes the sweep, for example to study the trace. |
| **Listen** | Receive the station the **Tuner** is on - wherever the marker is. |

The line at the foot of the tab shows the plan and the
measured speed, for example
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

## Receive (IQ)

In Receive the radio runs at a narrow IQ bandwidth, and the app demodulates
one station: **Radio** sets the radio itself (with its **RF gain** and
AGC), **Tuner** picks the station inside the radio's band (and, where it
has them, its HD Radio programs), and under them two tabs show the station
as decoded: **RDS**, and **HD Radio**'s station, programs, now playing,
pictures and signal. The tab on show is remembered, and changing tab
glides the height and fades the page in.

The Radio and Tuner boxes have a **chevron** at the right of the title: click it, or the
title, to fold the box away and again to open it; it slides shut or open,
and the chevron turns. A folded **Radio** box
still shows its **Center** and **Center on tuner**, and a folded **Tuner**
box its **Tuner range** (↔) and tuner, and no taller than those, each
gliding to the middle of the box once it has folded
(the Step and Channel filter knobs fade out as the Tuner box folds, and
back in as it opens, and its HD Radio rows go). The **RDS / HD Radio**
tabs have their chevron at the right of the tab bar: folded, only the tabs
show; click a tab to open them again. The app remembers which are folded.

```
┌ Radio ───────────────────────────────────────┐
│       Center: [0098.400 MHz] [Center on tuner]│   the radio's own frequency
│ IQ bandwidth: [10 MS/s ▾]                     │   how much band it takes in
│      RF gain: □ AGC ──────●──────── 60%       │   how hard it listens
└───────────────────────────────────────────────┘
┌ Tuner ───────────────────────────────────────┐
│               ↔ 94.800 - 102.000 MHz          │   where the tuner can go
│  Tuner: [0099.100 MHz] [▲▼] (Step) (Channel   │   the station you hear,
│                                     filter)   │   and what the analog hears
│HD Radio: ● [HD1][HD2][HD3][HD4]               │   its digital programs
│          Playing HD2 (digital) - MP1, ...     │   and how they come in
└───────────────────────────────────────────────┘
┌[RDS]─[HD Radio]──────────────────────────────┐
│ Station, Standard, Stereo / Snap to step,     │   RDS: the station as
│ Clear RDS, Signal, Audio, then the RDS itself │   decoded
└───────────────────────────────────────────────┘
```

The multiplex (MPX) spectrum fills the bottom right.

### Radio

| Control | What it does |
|---|---|
| **Center** | The radio's centre frequency (its LO), drawn as a **dashed line** on the spectrum (not the waterfall, which is left clear), thin and grey in every theme: it is a reference, and orange is kept for where you are tuned. Outside the tuner's reach the spectrum is dimmed. Moving it moves the band the tuner can reach. If the tuner is still inside the new band it stays where it is; if not, it is pulled in to the nearer edge. **While you move the Center the line fades away**, so you can see the spectrum under it, and it comes back once you stop. |
| **Center on tuner** | Puts the Center 300 kHz below the tuner, so there is room to tune either way. |
| **IQ bandwidth** | The radio's sample rate in Receive: how much of the band the spectrum shows, and the tuner can reach (BB60D 2.5/5/10/20/40 MS/s, 2.5 greyed out on a Mac; HackRF 2-20 MS/s; RTL-SDR 2 and 2.4 MS/s). The lowest is marked **(sharper)**: the spectrum's FFT is the same size over less band, so its bins are finer; the highest **(wider)**. Changing it rebuilds the receiver; the Center stays if the tuner still fits. Picking the rate already running does nothing. |
| **RF gain** | The radio's gain, with **AGC** where the radio says when it overloads (see *RF gain* above). The same slider as in Sweep, and each radio remembers its own. |

**The tuner stops at the edge of the band.** Rolling, stepping, typing,
clicking or dragging past it leaves the tuner at the edge, and **Tuner
range** (the ↔ line on top of the tuner) says so: *↔ Band edge: move
Center*. To go further, move the Center. The parts of the spectrum
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
| **Tuner range** (↔, right on top of the tuner, centred over its digits) | The lowest and highest the tuner can go around the radio's Center, in MHz. It is about three quarters of the IQ bandwidth, less half a channel at each end. It stays in sight when the box is folded. Its tooltip says how far it keeps clear of the Center on a HackRF or RTL-SDR. |
| **Step** (knob) | Four settings: 10, 50, 100 and 200 kHz. It sets what the arrows and Ctrl+Left/Right move by, and what Snap rounds to. FM channels are 200 kHz apart in the Americas, on the odd tenths (88.1, 88.3 … 107.9), and a 200 kHz Step keeps to those; Europe's are 100 kHz apart. |
| **Channel filter** (knob, right of Step) | 60-400 kHz, applied live. (The **IQ – channel** recording is 500 kS/s; the filter stops at 400 kHz because the channel is sampled at 500 kS/s and the filter needs room to roll off inside it.) Roll the wheel over the knob (5 kHz a notch, 1 kHz with Shift), drag it up or down, or roll the wheel over the orange band on the spectrum, which lights the knob. It is the analog's filter only: HD Radio gets the station's whole ±200 kHz at any width. A narrower filter rejects a strong neighbour, but below about 180 kHz stereo and RDS start to suffer. Wider than about 250 kHz the audio takes in any neighbour that close; the widths up to 400 kHz are for the **IQ – channel** recording, which then holds an HD Radio station's digital sidebands (±200 kHz). |

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
| **Stereo** (under Standard) | Turn it off for mono, which is quieter on a weak station. With no pilot, the audio is mono anyway. |
| **Snap to step** (beside Stereo) | Clicks, right-click picks and middle-drags on the spectrum tune to the nearest multiple of the Step. Off, they tune to where the pointer is, to the kHz. |
| **Audio** | *Stereo – pilot locked (standard phase)*, or *Mono – no stereo pilot*. Stereo needs a pilot that stands 10 dB over the noise beside it, so an empty channel, a mono station, or a station tuned slightly off reads *Mono*. *Standard phase* is what broadcasters send; *cosine phase* is what the RF bench toolkit's own transmitter sends. The app detects which by itself. |
| **Signal** | The power in the channel and how far the station stands above the floor: green above 30 dB, amber above 15 dB, red below that. |
| **Decode quality** | How many RDS groups have arrived, and how many blocks were good. |
| **Station ID (PI)** | With the call sign, if the station's own text confirms it; otherwise it says "maybe". |
| **Program type**, **Now showing (PS)**, **Now playing**, **RadioText** | As they arrive. Now playing is the RT+ artist and title. |
| **Flags**, **Station clock** | TP, TA and TMC; the station's clock. |
| **Clear RDS** (at the bottom) | Clears the decoded data and starts decoding again. |

Not every station sends RDS. If the PI stays at "-" for 20 s on a strong
station, it probably has none. In testing, 102.1 was one of these.

The **MPX view** (bottom right) is the demodulated multiplex from 0 to
125 kHz: mono audio, the 19 kHz pilot, stereo around 38 kHz and RDS at
57 kHz.

### HD Radio

Many US FM stations also send digital programs, HD Radio, in two flat
shoulders either side of the station (130-198 kHz out) - see
[digital-radio.md](digital-radio.md) for which ones here. This box plays
them. It is **always on** where the **nrsc5** program is installed (Setting
up, above); without it the status says so and only HD1, the analog, plays.

The **HD Radio** row of the **Tuner** box holds a lamp, the program buttons
and, under them, the status; the **HD Radio** tab under the Tuner shows the
station's own name and what is playing.

```
┌ Tuner ───────────────────────────────────────┐
│ ...                                           │
│ HD Radio: ● [HD1] [HD2] [HD3] [HD4]           │
│           Playing HD2 (digital) - MP1,        │
│           BER 0.114, 31 kbps                  │
└──────────────────────────────────────────────┘
┌[RDS]─[HD Radio]──────────────────────────────┐
│     Station: HOT - HOT 99.5     ┌──────────┐ │
│      Signal: MP1 · BER 0.042 ·  │          │ │
│              31 kbps ...        │  album   │ │
│              0% ... lost        │   art    │ │
│     Message: (the station's     │          │ │
│              own text)          └──────────┘ │
│       Alert: (only while one is on)          │
│    Programs: HD1 HOT 99.5 (Top 40) ·         │
│              HD2 Pride Radio (Top 40)        │
│ Now playing: Fisher - What A Life            │
│       Album: ...                             │
│       Genre: ...                             │
│        Logo: [logo]                          │
└──────────────────────────────────────────────┘
```

The station's IQ, sidebands and all (the channel filter doesn't matter),
goes to nrsc5; its audio comes back through the same **Volume**, **Mute**,
meters and **Audio** recording as the analog.

| Row | What it shows or does |
|---|---|
| **Lamp** (Tuner box) | Whether the station has HD Radio here. A ring: no digital signal (none on this station, or not found yet). **Green**: it has, and it comes in clean. **Amber**: it has, but a tenth or more of it is being lost (weak here: HD2-4 would play with gaps, HD1 falls back to the analog). Its tooltip is the status. |
| **HD1-HD8** (Tuner box) | The programs: a station can carry up to eight (HD1-HD8; most carry up to four, 107.7 here five). HD1-HD4 always have a button; HD5-HD8 appear only when the station lists them. A button is greyed until the station lists that program (its digital data says which it carries; a listed program with no audio stays grey). **Nothing lit is analog FM.** Click an available one and it lights orange: that program plays digitally. Click the lit one again and it goes out: back to analog FM. Click another to change program (the decoder starts again: 2-4 s to sync). A retune goes back to analog FM. HD1 is the main program, usually the analog's own; HD2-HD4 are extra ones. Their tooltips give each program's name and type. |
| **Status** (Tuner box, under the buttons) | One line, short (the numbers are in the HD Radio tab's Signal): what plays (*Playing analog FM*, *Playing HD2 (digital)*) stays put, and the rest scrolls through the room left when it does not fit; hover for the whole line. With nothing lit: *Playing analog FM*, and what the station has - *HD Radio here: HD1, HD2* (*weak here* if much is lost), *looking for HD Radio*, *no HD Radio on this station* after 10 s, *HD Radio signal lost (weak)*. With a program lit: *Digital signal found, waiting for HD2*, *Playing HD2 (digital)* with the service mode (MP1-MP3), the bit error rate (under ~0.05 is clean, ~0.2 is the edge) and the program's bit rate; *No HD Radio on this station* after 10 s without; *HD2 carries no audio* for a program the station lists but sends nothing on (94.7's HD2 on 2026-09-27). *N% of the digital audio lost (weak signal)*: each lost packet is a gap of about 46 ms, which sounds choppy on HD2-4. That is the reception, not the app; a better antenna or a stronger station fixes it. Whenever HD1 has gone back to the analog (below), the line starts *Playing analog FM* and says why: *HD1 too weak here*, *digital signal lost (weak)* (it had the digital and lost its sync), *waiting for HD1*. HD2-HD8 have no analog behind them (the analog FM is HD1's program), so while theirs is not playing you hear silence, and the line says so: *Tuning HD2... (silent)*, *Starting HD2... (silent)*, *HD2 lost - weak signal (silent)*, *HD2 is off the air (silent)*, *No HD Radio here (silent)*. They never switch to another program by themselves: going back to analog FM is your choice (click the lit button). *Playing HD2 (digital) - breaking up* means packets are being lost; the HD Radio tab's Signal gives how many.

Rows, pictures and buttons that come and go (an alert, the logo, the album art, HD5-HD8) fade in and out, and the tabs glide to their new height rather than jumping. |
| **Station** (HD Radio tab) | The station's own name and slogan, from its digital data, as large as RDS shows its name. |
| **Message** (HD Radio tab) | A free-text message the station sends, if any. |
| **Alert** (HD Radio tab) | An emergency alert, in red, with its category and the places it covers (SAME, FIPS or ZIP codes); the row shows only while one is on and goes when the station ends it. |
| **Programs** (HD Radio tab) | Every program the station lists, with its name and type; the one playing in bold, *no audio* on one it lists but isn't sending. |
| **Now playing** (HD Radio tab) | Artist and title of the program the decoder is on (HD1 until you pick another), from its digital data (often ads and slogans between songs). |
| **Album art** (HD Radio tab, top right) | The album art of what is playing, when the station sends it; nothing there otherwise. It sits beside Station to Programs; the rows below it use the tab's full width. |
| **Logo** (HD Radio tab) | The station's logo, once one has arrived (a logo can take minutes to come round); the row shows only then. Pictures come as files, kept in a temporary folder while the window is open; a program change on the same station keeps them. |
| **Album**, **Genre** (HD Radio tab) | From the same data as Now playing, when the station sends them. |
| **Signal** (HD Radio tab) | The service mode (MP1-MP3), the bit error rate (green under 0.05, amber to 0.15, red above), MER for the lower and upper sidebands (higher is better), the program's bit rate, how much of the digital signal was lost over the last 3 s (green at 0%, amber to 10%, red above: each lost packet is a gap in the sound), and the frequency offset the decoder corrected. |

With a program lit, while it waits, HD1 plays the analog and HD2-HD4 are
silent. **HD1 plays the digital only once it has run 3 s without a lost
packet**, and goes back
to the analog when more than a tenth of the last 1.5 s was lost, so a weak
station (98.7 here) stays on the analog rather than chopping. The digital
audio is a few seconds behind the analog (the decoder's own delay), so each
switch jumps a moment; the waits keep switches rare. HD2-HD4 have no
analog, so on a weak station they play with the gaps.

The IQ bandwidth must be at least 420 kS/s after the first stage, which
every radio's Receive rates are. An **IQ – channel** recording (500 kS/s)
plays with its HD Radio if its channel filter was 400 kHz when it was
recorded; an **IQ – band** recording always does.

## Views: bandwidth and amplitude

Each spectrum has its own dials, at the left-hand end of the row under it;
the readout of the frequency and level under the pointer is in the
bottom-left corner of the plot, on a small panel of its own so a busy trace
can't wash it out; it goes when the pointer leaves the plot. The dials can
be set from a script too: `fmctl view` (see
[Controlling the window from a script](#controlling-the-window-from-a-script-fmctl)).

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
see which one the wheel will turn. Zooming the plot or waterfall lights
Span the same way, and the wheel or middle-drag on the level axis lights
Ref level and Range, for a moment after the last change, so you can see
which dials moved. The same goes for the Volume, Step and Channel filter
knobs. A double-click does nothing to any knob (there is no reset).

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
  it; music in a WAV shows its rhythm. Its colours follow the spectrum's
  **Ref level** and **Range**, as the waterfall's do: turn them and the strip
  changes with it (the RF spectrum's for an IQ recording, the sound's for a
  WAV). Its levels are the spectrum's own, so a station that reads -30 dBFS
  there has the same colour in the strip. The bright line is where you are, and
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

**A Sceptre DVR.** Signal Hound's Sceptre keeps a DVR ring, `dvr.sdvr`. Made
from its **IQ** tab (a BB60D at the widest, 28 MS/s, 27 MHz around the
centre), the file plays here as it is: choose it in the same way (the
**`.sdvr`** filter), or `--file /path/to/dvr.sdvr`. Pause the DVR in Sceptre
first, so the file stops changing while it is read (a running one can be
opened, and the newest run is then left out). It holds about 8.6 s in
a 1 GB ring, played on a loop, and the tuner reaches the whole 27 MHz, so
every station in it can be tuned in turn (a lower reference level in
Sceptre, -20 to -30 dBm, gives a better signal than 0). A DVR made from
Sceptre's **Sweep** tab holds spectra, not IQ, and is refused here (see
`tools/dvr-sweep` below). How the file
is laid out: [sceptre-dvr.md](sceptre-dvr.md).

HD Radio works on a DVR (the **HD Radio** row lights on the stations that
have it), but the 8.6 s loop restarts the decoder at each seam, so HD1 plays
and HD2 and HD3, which take longer to come in, drop out. For whole programs,
decode the DVR offline with **`tools/dvr-hd`**:

```
tools/dvr-hd dvr.sdvr --info            what the DVR holds: rate, centre, band, times, full-scale dBm
tools/dvr-hd dvr.sdvr --scan            the stereo stations in it, and stop
tools/dvr-hd dvr.sdvr                   decode HD1-HD4 of every stereo station found
tools/dvr-hd dvr.sdvr 100.3 90.9 -o out just those (MHz), into the folder out/
                     --programs N       HD1 to HDN (default 4)   --keep-iq  keep the IQ
```

It reads the DVR once through and writes `hd_<MHz>_HD<n>.wav` (44.1 kHz
stereo) for each program that decoded, and `summary.json` (services, kbps,
MER, BER). About 12 s a station and a few minutes for a whole band. Plain
Python with numpy, scipy and `nrsc5`: no GNU Radio environment needed, and
the DVR is only read. See [digital-radio.md](digital-radio.md), "Offline,
from a Sceptre DVR", for what it does.

**The spectrum a DVR holds: `tools/dvr-sweep`.** A sweep DVR (Sceptre's Sweep
tab, 9 kHz to 6 GHz) is spectra, not IQ; this reads and draws it, running or
not:

```
tools/dvr-sweep dvr.sdvr                        what it holds: tiles, bins, times, gaps
tools/dvr-sweep dvr.sdvr --png all.png          a waterfall of all of it
tools/dvr-sweep dvr.sdvr --band 88 108 --png fm.png
```

From Python: `sceptre_dvr.Sweeps(path).read(f_lo, f_hi)` gives `(times, freqs,
bytes)`, one row a sweep. While Sceptre is still recording, the newest tile is
left out and an overwritten one dropped (the report says so). The levels are
dBm (`dbm=True`; `--bytes` for the raw signed bytes). Each tile carries its own
scale, so the raw bytes step up and down between tiles as bands across the
waterfall, and the dBm is what removes them (`sceptre-dvr.md`, "A tile's own
scale"). It is Sceptre's own decode: it matches Sceptre's 32-bit `.fft` export
of the same capture to float rounding, and the BB60D's own sweep to a couple
of dB.

**A DVR as a plain IQ file: `tools/dvr-to-iq`.** For anything else that reads
IQ (SDR++, GNU Radio, inspectrum, URH), or to keep a DVR before Sceptre
records over it (it is a 1 GB ring):

```
tools/dvr-to-iq dvr.sdvr                                the whole band, 28 MS/s (about 1.9 GB)
tools/dvr-to-iq dvr.sdvr --center 99.269 --rate 3.5     a 3 MHz channel at 3.5 MS/s
tools/dvr-to-iq dvr.sdvr --center 100.3 --rate 0.5 --station 100.3 -o fm100
                --start 2 --seconds 3 (a piece)   --units mw   --force
```

It writes `BASE.cfile` (complex float32), `BASE.sigmf-meta` and `BASE.json`,
as this app's recordings have, so `./fm-receiver --file BASE.sigmf-meta` plays
it (`--station` is where the app tunes on open). Values are in units of the
ADC's full scale (1.0), and the metadata says how many dBm that is; `--units
mw` writes Sceptre's square root of milliwatts, so `|x|²` is power in mW. The
channel is mixed to 0 Hz, low-passed and resampled (it matches Sceptre's own
extraction of a channel to 0.04 dB); the whole band is an exact copy. Plain
Python with numpy and scipy, and the DVR is only read.

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
| Up / Down (pointer on a digit) | That digit of the Tuner or Center up / down |
| PageUp / PageDown | The same digit by ten |
| Left / Right (entry focused) | Choose the digit Up/Down change |
| 0-9 or Enter (entry focused) | Type a value; Enter sets it, Escape cancels |
| Ctrl+M | Mute |
| Ctrl+Up / Ctrl+Down | Volume ±5 |
| Ctrl+R | Start/stop recording |
| A | Auto scale the spectrum on show (not while typing in a box) |

## Controlling the window from a script: `fmctl`

While the window is open, `tools/fmctl` works it from a terminal, a script,
or Claude. Each command works the control a click would, so you see it
happen: the tuner's digits move, the tab changes. Settings are saved as
usual when the window closes. The radio stays with the window, so nothing
else has to open it.

```sh
tools/fmctl status                        # everything below, as JSON
tools/fmctl tune 99.1
tools/fmctl 'tune 99.1; wait 3; status'   # several, one after another
tools/fmctl screenshot ~/window.png
tools/fmctl help                          # every command
```

| Command | Does |
|---|---|
| `status` | Radio, tab, the RDS or HD Radio tab on show, Tuner and its range, Center, IQ bandwidth, gain and AGC, volume and mute, channel filter, step, clipped %, recording; in Receive the signal (dBFS in the channel, SNR), stereo pilot, RDS (PI, call sign and whether it is confirmed, PS, name, RadioText, PTY, % blocks good, the TP and TA flags, TMC, the station clock, RT+ title and artist, data applications, and how many groups of each type came) and HD Radio (on, program, what is heard, sync, station, programs, BER, now playing, the status line) |
| `tune MHZ` | The Tuner. Outside the band around the Center, the Center moves first, as **Center on tuner** does (not for an IQ file, whose band is fixed) |
| `center MHZ` | The Radio box's Center (Receive tab only) |
| `gain PERCENT` | The RF gain slider |
| `agc on` / `off` | The AGC box |
| `mode sweep` / `receive` / `recordings` | The tabs |
| `volume PERCENT`, `mute on` / `off` | Audio |
| `hd 1`-`8` / `analog` | HD Radio: the program to play (lights that HD button), or `analog` for analog FM (none lit). HD Radio is always on: there is no off, in the window or here. `status`'s `hd` has the lamp too. Needs nrsc5 |
| `station rds` / `hd` | The **RDS \| HD Radio** tabs under the Tuner, as a click on a tab does (a folded box opens). Receive's: refused from the other tabs. Show **RDS** while reading RDS, so the person at the window sees what is being read |
| `screenshot PATH.png` | A picture of the window, as it is on screen |
| `sweep START STOP` | The Sweep tab over START-STOP MHz (switching to it first) |
| `peakhold on` / `off` / `clear` | The RF spectrum's **Peak hold**: the most each frequency reached, which catches short bursts |
| `peaks [THRESHOLD_DB [START STOP]]` | The signals on the RF spectrum (the held trace while Peak hold is on) THRESHOLD_DB (default 10) over the floor, within START-STOP MHz if given: each one's frequency, level, height over the floor and width, the strongest 20 |
| `view [rf` / `mpx` / `audio] [span X` / `full] [center X] [ref DB] [range DB] [avg N]` | A spectrum view's dials: **Span** and where it is centred (MHz for `rf`, kHz for `mpx` and `audio`), **Ref level** and **Range** (the level scale, the waterfall's colours too; display only), **Average** (smooths the trace, and so what `peaks` reads). With no settings it reports them, with no view all three |
| `rate MSPS` | The IQ bandwidth |
| `record audio` / `iq-channel` / `iq-band` `on` / `off`, `record start` / `stop` | The Record box. `stop` replies with the files saved, the `.sigmf-meta` beside each IQ file included |
| `capture SECONDS [iq-band` / `iq-channel` / `audio]` | Records only that (IQ of the whole band by default) for SECONDS and replies with the files; the Record box's ticks are put back after. Refused over 4 GB: at 40 MS/s the whole band is 320 MB/s, so 12 s at most |
| `scan START STOP [step KHZ] [quick S] [listen S] [snr DB]` | Tunes each channel from START to STOP MHz and replies with one row for each: level, SNR, stereo pilot, and `rds` (the same block as `status`) and `hd` (station, programs, now playing, BER) where something was decoded. The step is the Tuner's Step unless given. Each channel is heard for `quick` seconds (2); one with a pilot or an SNR of `snr` dB (6) or more is heard `listen` seconds more (12; 0 for none), long enough for RDS and for HD Radio's name. A channel outside an IQ file's band is a row with the reason. Receive's, refused while recording (retuning would spoil it). The Tuner goes back where it was afterwards, and also if the client leaves, which ends the scan. Up to 30 minutes, worst case |
| `wait SECONDS` | Replies after that long (up to 120 s) with the window running meanwhile, so a later command sees the result: RDS takes a few seconds |

A survey of the FM band, for example: every channel of the US band is read for 2 s, and the ones that look like stations for 12 s more (about 10 minutes on the BB60D, with 33 channels heard for the longer time; the reply, a table of the channels with their RDS and HD Radio, comes at the end, so run it in the background):

```sh
tools/fmctl 'mode receive; scan 87.9 107.9' > fm-band.json
tools/fmctl 'scan 98.5 99.1 quick 3 listen 20'        # four channels, listened to longer
```

A survey of the ISM bands, for example (a BB60D, which sweeps itself):

```sh
tools/fmctl 'sweep 300 1000; peakhold on; wait 90; peaks 8 433 435; peaks 8 902 928'
tools/fmctl 'mode receive; tune 915; center 915; capture 5'    # the whole 27 MHz, 1.6 GB
tools/fmctl 'view rf span 2 center 915.2 ref -40 range 60; screenshot /tmp/915.png'
```

A command the window would refuse is refused, with the reason: `gain` while
AGC is on, `agc` on a radio without it, `center` outside Receive, `tune`
past what the radio can reach. Every reply ends with the status line, so a
radio's complaint shows there. `fmctl` prints each reply as JSON and exits
with 1 if a command failed, or 2 if no window is listening. It is plain
Python, with no conda environment needed.

It talks to the window over a Unix socket that only your account can open
(`$XDG_RUNTIME_DIR/fm-receiver.sock` on Linux, under `$TMPDIR` on a Mac),
never over the network. A second window finds the socket taken and runs
without one. Commands from several clients run one at a time, in turn;
a `wait` holds the others back until it ends. To use another socket path,
set `FMRX_CONTROL=/path/to.sock` for both the window and `fmctl`.

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
| *Input overloaded* | Turn the RF gain down, or tick **AGC** and let it find the gain (then untick it). An overloaded BB60D sends nothing at all, so the spectrum stops too. On a HackRF or an RTL-SDR it also gives the share of samples clipped; turn down until the message goes. In a sweep it names the step that clipped: over the whole range a strong TV transmitter can clip one step at a gain that suits FM, and then the FM band preset is the one to use. |
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
