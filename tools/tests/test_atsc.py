"""ATSC television - no radio, no antenna.

- PSIP: a Terrestrial Virtual Channel Table and an Event Information Table,
  built by hand with their CRCs and cut across transport packets, are read
  back (names, 16.1 style numbers, now and next); a corrupt one is ignored.
- ffmpeg's stream lines become '1080i MPEG-2 29.97 fps' and 'AC-3 5.1'.
- Loopback through the real Engine: an MPEG-2 transport stream made by
  ffmpeg is modulated as 8VSB at 10 MS/s with a crystal 13.3 ppm out, played
  as an IQ file through ``ReceiveChain`` with the television branch, and
  must come out as the same programs with a picture ffmpeg decoded, under
  1% bad packets once locked.

Run:  python tools/tests/test_atsc.py     (about 40 s; needs ffmpeg)
"""

import math
import os
import shutil
import subprocess
import sys
import tempfile
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from fm_receiver import atsc  # noqa: E402
from fm_receiver.atsc_core import (PID_PSIP, Psip, TsAnalyzer,  # noqa: E402
                                   channel_center_mhz, section_crc_ok)
from fm_receiver.engine import Engine  # noqa: E402
from fm_receiver.radios import IQFile  # noqa: E402

failures = []


def check(name, ok, detail=''):
    print(f"  {'ok  ' if ok else 'FAIL'} {name}{'  ' + detail if detail else ''}")
    if not ok:
        failures.append(name)


# ------------------------------------------------------------ PSIP

def mpeg_crc(data):
    crc = 0xFFFFFFFF
    for b in data:
        crc ^= b << 24
        for _ in range(8):
            crc = ((crc << 1) ^ 0x04C11DB7) if crc & 0x80000000 else crc << 1
            crc &= 0xFFFFFFFF
    return crc


def section(table_id, ext, body):
    """A long-form section: header, body, CRC."""
    length = 5 + len(body) + 4
    head = bytes([table_id, 0xF0 | (length >> 8), length & 0xFF,
                  ext >> 8, ext & 0xFF, 0xC1, 0, 0])
    raw = head + body
    return raw + mpeg_crc(raw).to_bytes(4, 'big')


def tvct(channels):
    body = bytes([0, len(channels)])
    for name, major, minor, program, source in channels:
        body += name.ljust(7).encode('utf-16-be')
        bits = (0xF << 20) | (major << 10) | minor
        body += bits.to_bytes(3, 'big') + bytes([4]) + (0).to_bytes(4, 'big')
        body += (1).to_bytes(2, 'big') + program.to_bytes(2, 'big')
        body += bytes([0x00, 0x01]) + source.to_bytes(2, 'big')
        body += bytes([0xFC, 0x00])
    body += bytes([0xFC, 0x00])
    return section(0xC8, 1, body)


def eit(source, events):
    body = bytes([0, len(events)])
    for event_id, start_gps, seconds, title in events:
        text = bytes([1]) + b'eng' + bytes([1, 0, 0, len(title)]) + title.encode()
        body += (0xC000 | event_id).to_bytes(2, 'big') + start_gps.to_bytes(4, 'big')
        body += (0xC00000 | seconds).to_bytes(3, 'big') + bytes([len(text)]) + text
        body += bytes([0xF0, 0x00])
    return section(0xCB, source, body)


def packets(pid, sect, first_cc=0):
    """Cut a section across 188-byte transport packets."""
    data = bytes([0]) + sect
    out, cc = [], first_cc
    for i in range(0, len(data), 184):
        chunk = data[i:i + 184].ljust(184, b'\xff')
        head = bytes([0x47, (0x40 if i == 0 else 0) | (pid >> 8), pid & 0xFF,
                      0x10 | (cc & 15)])
        out.append(head + chunk)
        cc += 1
    return b''.join(out)


def test_psip():
    print("PSIP")
    chans = [('STATION HD', 16, 1, 3, 1), ('SCC', 16, 2, 4, 2), ('KIDS', 16, 3, 5, 3),
             ('MORE', 16, 4, 6, 4), ('FIVE', 16, 5, 7, 5),
             ('SIX', 16, 6, 8, 6), ('SEVEN', 16, 7, 9, 7), ('EIGHT', 16, 8, 10, 8)]
    table = tvct(chans)
    check("the table spans packets", len(table) > 184, f"{len(table)} bytes")
    check("its CRC verifies", section_crc_ok(table))
    now = int(time.time()) - atsc_gps_offset() - 600
    events = eit(1, [(1, now, 1800, 'Morning News'), (2, now + 1800, 1800, 'Cooking')])
    a = TsAnalyzer()
    a.feed(packets(PID_PSIP, table))
    ch = a.psip.channels
    check("eight channels read", len(ch) == 8, str(len(ch)))
    check("names and numbers", ch[3]['name'] == 'STATION HD' and (ch[3]['major'], ch[3]['minor']) == (16, 1)
          and ch[7]['name'] == 'FIVE' and ch[7]['source'] == 5, str(ch.get(3)))
    mgt_body = bytes([0, 0, 0, 1]) + (0x0100).to_bytes(2, 'big') + (0xE000 | 0x1D00).to_bytes(2, 'big') \
        + bytes([0]) + (0).to_bytes(4, 'big') + bytes([0xF0, 0]) + bytes([0xF0, 0])
    # MGT: protocol_version (1) tables_defined (2) then the loop
    a.psip._section(PID_PSIP, section(0xC7, 0, bytes([0]) + (1).to_bytes(2, 'big')
                                      + (0x0100).to_bytes(2, 'big') + (0xE000 | 0x1D00).to_bytes(2, 'big')
                                      + bytes([0]) + (0).to_bytes(4, 'big') + bytes([0xF0, 0]) + bytes([0xF0, 0])))
    check("the MGT names the EIT's PID", 0x1D00 in a.psip.pids, str(sorted(a.psip.pids)))
    a.feed(packets(0x1D00, events))
    now_ev, next_ev = a.psip.now_next(1, time.time())
    check("now and next", now_ev and now_ev[2] == 'Morning News' and next_ev and next_ev[2] == 'Cooking',
          str((now_ev, next_ev)))
    bad = bytearray(table)
    bad[40] ^= 0xFF
    b = TsAnalyzer()
    b.feed(packets(PID_PSIP, bytes(bad)))
    check("a corrupt table is ignored", not b.psip.channels)


def atsc_gps_offset():
    from fm_receiver.atsc_core import GPS_EPOCH_UNIX, GPS_UTC_LEAP_S
    return GPS_EPOCH_UNIX - GPS_UTC_LEAP_S


def test_describe():
    print("ffmpeg's lines")
    v = atsc.describe_video("mpeg2video (Main) ([2][0][0][0] / 0x0002), yuv420p(tv, top first), "
                            "1920x1080 [SAR 1:1 DAR 16:9], Closed Captions, 29.97 fps, 29.97 tbr")
    check("video", v == '1080i MPEG-2 29.97 fps', v)
    a = atsc.describe_audio("ac3 (AC-3 / 0x332D4341), 48000 Hz, 5.1(side), fltp, 384 kb/s")
    check("audio", a == 'AC-3 5.1', a)
    check("stereo", atsc.describe_audio("ac3, 48000 Hz, stereo, fltp, 192 kb/s") == 'AC-3 stereo')


# ------------------------------------------------------------ loopback

def make_ts(path, seconds):
    cmd = ['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i',
           'testsrc2=size=704x480:rate=30000/1001', '-f', 'lavfi', '-i',
           'sine=frequency=1000:sample_rate=48000', '-t', str(seconds),
           '-c:v', 'mpeg2video', '-b:v', '6M', '-minrate', '6M', '-maxrate', '6M',
           '-bufsize', '1835k', '-c:a', 'ac3', '-b:a', '192k', '-ac', '2',
           '-muxrate', '19392658', '-f', 'mpegts', path]
    subprocess.run(cmd, check=True)


def render_8vsb(ts_path, out_path, seconds, rate, ppm, center_hz):
    from gnuradio import blocks, dtv, filter, gr
    from gnuradio.filter import firdes
    from fm_receiver.atsc_core import SYMBOL_RATE

    class Render(gr.top_block):
        def __init__(self):
            gr.top_block.__init__(self, 'render', catch_exceptions=True)
            pilot = (6e6 - SYMBOL_RATE / 2) / 2
            src = blocks.file_source(gr.sizeof_char, ts_path, True, 0, 0)
            chain = [src, dtv.atsc_pad(), dtv.atsc_randomizer(), dtv.atsc_rs_encoder(),
                     dtv.atsc_interleaver(), dtv.atsc_trellis_encoder(),
                     dtv.atsc_field_sync_mux(),
                     blocks.vector_to_stream(gr.sizeof_char, 1024),
                     blocks.keep_m_in_n(gr.sizeof_char, 832, 1024, 4),
                     dtv.dvbs2_modulator_bc(dtv.FECFRAME_NORMAL, dtv.C1_4, dtv.MOD_8VSB,
                                            dtv.INTERPOLATION_OFF),
                     blocks.rotator_cc(((-3e6 + pilot) / SYMBOL_RATE) * math.pi * 2, False),
                     filter.fft_filter_ccc(1, firdes.root_raised_cosine(
                         0.11, SYMBOL_RATE, SYMBOL_RATE / 2, 0.1152, 200), 1)]
            # 10.762237 -> rate: a rational that is exact at 10 MS/s is not
            # needed - an arbitrary resampler is, and the crystal error with it.
            d = ppm * 1e-6
            chain.append(filter.mmse_resampler_cc(0.0, SYMBOL_RATE / rate * (1.0 + d)))
            chain.append(blocks.rotator_cc(2 * math.pi * d * center_hz / rate, False))
            chain.append(blocks.multiply_const_cc(0.05))
            self.connect(*chain)
            self.connect(chain[-1], blocks.head(gr.sizeof_gr_complex, int(rate * seconds)),
                         blocks.file_sink(gr.sizeof_gr_complex, out_path, False))

    tb = Render()
    tb.run()


def write_meta(path_base, rate, center_hz):
    import json
    with open(path_base + '.sigmf-meta', 'w') as fh:
        json.dump({'global': {'core:datatype': 'cf32_le', 'core:sample_rate': rate,
                              'core:version': '1.0.0'},
                   'captures': [{'core:sample_start': 0, 'core:frequency': center_hz}],
                   'annotations': []}, fh)


def test_loopback():
    print("8VSB loopback through the Engine")
    if not shutil.which('ffmpeg'):
        print("  skip (no ffmpeg)")
        return
    rate, centre = 10e6, channel_center_mhz(32) * 1e6
    folder = tempfile.mkdtemp(prefix='fmrx-atsc-')
    tb = None
    try:
        ts = os.path.join(folder, 'in.ts')
        make_ts(ts, 4)
        base = os.path.join(folder, 'air')
        render_8vsb(ts, base + '.sigmf-data', 8.0, rate, 13.3, centre)
        write_meta(base, rate, centre)
        radio = IQFile(base + '.sigmf-meta', repeat=True, throttle=True)
        tb = Engine(want_audio=False)
        tb.use_radio(radio)
        tb.start_receive(centre, radio.rate, center_hz=centre, atsc=True, region='RBDS',
                         stereo=True, volume=0.5, muted=True)
        check("the chain took the television branch", getattr(tb.rx, 'atsc', None) is tb.atsc)
        deadline = time.monotonic() + 30
        st = {}
        while time.monotonic() < deadline:
            st = tb.atsc.poll()
            if st['picture'] and st['lock'] == 'locked':
                break
            time.sleep(0.25)
        check("locked", st.get('lock') == 'locked', str(st.get('lock')))
        check("channel 32 recognised", st.get('channel') == 32, str(st.get('channel')))
        check("a program with a picture", bool(st.get('picture')), str(st.get('programs')))
        time.sleep(2.0)
        st = tb.atsc.poll()
        check("under 1% bad packets", st['bad_pct'] is not None and st['bad_pct'] < 1.0,
              f"{st['bad_pct']}")
        check("the clock error was found", abs(st['ppm'] - 13.3) < 3.0, f"{st['ppm']:.1f} ppm")
        fmt = st['programs'][0]['format'] if st['programs'] else ''
        check("ffmpeg says what the picture is", '480' in fmt and 'MPEG-2' in fmt, fmt)
        frame = tb.atsc.video.frame()
        check("a whole picture", frame is not None and len(frame[0]) == atsc.FRAME_BYTES)
        img = np.frombuffer(frame[0], dtype=np.uint8).reshape(atsc.VIDEO_H, atsc.VIDEO_W, 3)
        check("and not blank", img.std() > 20, f"std {img.std():.0f}")
        audio_deadline = time.monotonic() + 5
        while time.monotonic() < audio_deadline and tb.atsc._audio_frames < 4800:
            time.sleep(0.1)
        check("sound arrives", tb.atsc._audio_frames >= 4800 or tb.atsc._audio_on,
              f"{tb.atsc._audio_frames} frames")
        # Another start without the television branch lets go of ffmpeg.
        tb.start_receive(centre, radio.rate, center_hz=centre, atsc=False, region='RBDS',
                         stereo=True, volume=0.5, muted=True)
        time.sleep(0.5)
        check("leaving stops ffmpeg", not tb.atsc.video.alive())
    finally:
        if tb is not None:
            tb.close()
        shutil.rmtree(folder, ignore_errors=True)


def main():
    test_psip()
    test_describe()
    test_loopback()
    if failures:
        print(f"\n{len(failures)} failed: {', '.join(failures)}")
        return 1
    print("\nall passed")
    return 0


if __name__ == '__main__':
    sys.exit(main())
