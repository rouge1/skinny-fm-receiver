"""The RTL-SDR's rtl_tcp client, with no radio: a fake rtl_tcp server in
this process sends the header and a tone as unsigned 8-bit IQ, and notes
the commands it is sent.

- ``parse_address``: blank is this computer, a port after a colon.
- The radio opens against it, sets rate, frequency and gain as rtl_tcp's
  commands, and a flowgraph gets the tone back as complex float at the
  right frequency and level.
- A backlog is dropped, not delivered late: the block holds at most
  ``HOLD_S`` of samples while nothing reads them, and a stopped flowgraph's
  backlog is not reported as dropped.
- Closing lets go of the socket, and a server that was already running is
  not stopped (it was not ours).
- Something on the port that is not rtl_tcp is refused with a message.
- A radio that stops sending is noticed: silent (``Engine.data_age``
  grows, and falls again when it comes back), or gone (``lost`` says the
  connection closed).
- A dongle is detected by its USB vendor and product (sysfs on Linux,
  ioreg on a Mac), and Realtek's card readers and network adapters are not
  taken for one.

Run:  python tools/tests/test_rtl_tcp.py        (a few seconds)
"""

import os
import socket
import struct
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import numpy as np  # noqa: E402
from gnuradio import blocks, gr  # noqa: E402

from fm_receiver import radios, rtl_tcp  # noqa: E402

RATE = 2.4e6
TONE_HZ = 100e3


class FakeRtlTcp:
    """Serves one client: the header, then a tone at ``RATE`` pace."""

    def __init__(self, header=b'RTL0' + struct.pack('>II', 5, 29)):
        self.header = header
        self.commands = []
        self.listener = socket.socket()
        self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.listener.bind(('127.0.0.1', 0))
        self.listener.listen(1)
        self.port = self.listener.getsockname()[1]
        self.done = threading.Event()
        self.hold = threading.Event()            # set: connected, sending nothing
        self.client_gone = threading.Event()
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        conn, _ = self.listener.accept()
        conn.sendall(self.header)
        threading.Thread(target=self._commands, args=(conn,), daemon=True).start()
        n = np.arange(24000)
        z = 0.5 * np.exp(2j * np.pi * TONE_HZ / RATE * n)
        iq = np.empty(2 * len(n), dtype=np.uint8)
        iq[0::2] = np.clip(np.round(z.real * 127.5 + 127.5), 0, 255)
        iq[1::2] = np.clip(np.round(z.imag * 127.5 + 127.5), 0, 255)
        chunk = iq.tobytes()                     # 10 ms, a whole number of cycles
        try:
            while not self.done.is_set():
                if not self.hold.is_set():
                    conn.sendall(chunk)
                time.sleep(0.01)
        except OSError:
            pass
        self.client_gone.set()
        try:
            # The command thread is blocked in recv on it: close() alone
            # would not end the connection until that returned.
            conn.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        conn.close()

    def _commands(self, conn):
        buf = b''
        while True:
            try:
                data = conn.recv(64)
            except OSError:
                return
            if not data:
                self.client_gone.set()
                return
            buf += data
            while len(buf) >= 5:
                self.commands.append(struct.unpack('>BI', buf[:5]))
                buf = buf[5:]

    def close(self):
        self.done.set()
        self.listener.close()


def test_parse_address():
    assert rtl_tcp.parse_address('') == ('', 1234)
    assert rtl_tcp.parse_address(' macmini ') == ('macmini', 1234)
    assert rtl_tcp.parse_address('10.0.0.2:1300') == ('10.0.0.2', 1300)
    assert rtl_tcp.is_local('') and rtl_tcp.is_local('localhost')
    assert rtl_tcp.resolve_ssh_host('') == '127.0.0.1'


def test_stream_and_commands():
    fake = FakeRtlTcp()
    radio = radios.make_radio('rtlsdr', rtl_address=f'127.0.0.1:{fake.port}')
    radio.open()
    assert radio.server is None, "a running rtl_tcp was used, not started"
    assert 'R820T' in radio.describe()
    radio.set_rate(RATE)
    radio.set_center(98.7e6)
    radio.apply_gain(50)

    tb = gr.top_block()
    head = blocks.head(gr.sizeof_gr_complex, int(RATE * 0.3))
    sink = blocks.vector_sink_c()
    tb.connect(radio.block, head, sink)
    tb.start()
    tb.wait()
    tb.stop()
    x = np.array(sink.data(), dtype=np.complex64)
    assert len(x) == int(RATE * 0.3), len(x)
    level = np.sqrt(np.mean(np.abs(x) ** 2))
    assert abs(level - 0.5) < 0.02, level
    spec = np.abs(np.fft.fft(x[:65536]))
    peak = np.fft.fftfreq(65536, 1 / RATE)[np.argmax(spec)]
    assert abs(peak - TONE_HZ) < 100, peak

    cmds = dict(fake.commands)
    assert cmds[rtl_tcp.CMD_RATE] == int(RATE), fake.commands
    assert cmds[rtl_tcp.CMD_FREQ] == int(98.7e6), fake.commands
    assert cmds[rtl_tcp.CMD_GAIN_MODE] == 1, fake.commands
    assert cmds[rtl_tcp.CMD_GAIN] in rtl_tcp.GAINS[5], fake.commands

    # Nothing reading: the block keeps the newest HOLD_S, drops the rest -
    # and, the flowgraph stopped, does not count that as a loss.
    time.sleep(0.6)
    with radio.block._cond:
        held = radio.block._queued
    assert held <= RATE * radio.block.HOLD_S + 40000, held
    assert radio.block.overflows == 0, radio.block.overflows

    radio.close()
    assert fake.client_gone.wait(2), "the socket was not closed"
    fake.close()


def test_gain_at_open():
    """Manual gain, at the default percentage, is sent as soon as the radio
    opens - before set_rate/set_center or a flowgraph ever reads a sample.

    rtl_tcp starts the dongle in automatic gain; gain applied only after the
    flowgraph starts (as ``Engine._started()`` also does, for a mid-session
    change) left the first samples of every Receive at the AGC's gain,
    clipping 38-58% for ~200 ms on a real R820T (2026-09-23, 60% gain,
    99.1 MHz). Sending it here instead gives it the rest of start_receive()
    to reach the tuner before any sample is read.
    """
    fake = FakeRtlTcp()
    radio = radios.make_radio('rtlsdr', rtl_address=f'127.0.0.1:{fake.port}')
    radio.open()
    deadline = time.monotonic() + 2.0
    while len(fake.commands) < 2 and time.monotonic() < deadline:
        time.sleep(0.01)
    cmds = dict(fake.commands)
    assert cmds.get(rtl_tcp.CMD_GAIN_MODE) == 1, fake.commands
    assert cmds.get(rtl_tcp.CMD_GAIN) in rtl_tcp.GAINS[5], fake.commands
    assert rtl_tcp.CMD_RATE not in cmds and rtl_tcp.CMD_FREQ not in cmds, \
        "rate/frequency were sent before open() returned"
    radio.close()
    fake.close()


def test_not_rtl_tcp():
    fake = FakeRtlTcp(header=b'HTTP/1.1 200')
    radio = radios.make_radio('rtlsdr', rtl_address=f'127.0.0.1:{fake.port}')
    try:
        radio.open()
    except radios.RadioError as exc:
        assert 'not rtl_tcp' in str(exc), exc
    else:
        raise AssertionError("a server that is not rtl_tcp was accepted")
    fake.close()



def test_lost():
    from fm_receiver.engine import Engine
    fake = FakeRtlTcp()
    radio = radios.make_radio('rtlsdr', rtl_address=f'127.0.0.1:{fake.port}')
    tb = Engine(want_audio=False)
    tb.use_radio(radio)
    try:
        tb.start_receive(98.7e6, RATE)
        time.sleep(1.0)
        assert tb.data_age() < 0.5 and radio.lost() is None, (tb.data_age(), radio.lost())
        # Silent but connected: only the samples stopping tells.
        fake.hold.set()
        time.sleep(1.5)
        assert tb.data_age() > 1.0 and radio.lost() is None, (tb.data_age(), radio.lost())
        fake.hold.clear()
        time.sleep(0.5)
        assert tb.data_age() < 0.5, tb.data_age()
        # Gone: the connection closed, and the radio says so.
        fake.close()
        t0 = time.time()
        while radio.lost() is None and time.time() - t0 < 3:
            time.sleep(0.05)
        assert 'closed' in (radio.lost() or ''), radio.lost()
        assert tb.lost_reason() == radio.lost()
        # Stopped, nothing is expected.
        tb.halt()
        assert tb.data_age() is None
    finally:
        tb.close()


def test_usb_detection():
    import tempfile
    # Linux: a device directory has idVendor/idProduct; an interface has none.
    with tempfile.TemporaryDirectory() as root:
        for name, ids in (('3-1.2', ('1d50', '6089')), ('4-4', ('0bda', '0328')),
                          ('1-2', ('0bda', '2838')), ('1-2:1.0', None)):
            os.mkdir(os.path.join(root, name))
            if ids:
                for key, value in zip(('idVendor', 'idProduct'), ids):
                    with open(os.path.join(root, name, key), 'w') as f:
                        f.write(value + '\n')
        ids = radios._sysfs_usb_ids(root)
        assert sorted(ids) == [(0x0bda, 0x0328), (0x0bda, 0x2838), (0x1d50, 0x6089)], ids
    assert radios._sysfs_usb_ids('/nonexistent') == []
    # A Mac: keys in either order, a hub with none of the IDs in between.
    text = """+-o Root  <class IORegistryEntry>
  | +-o USB 10/100/1000 LAN@02221000  <class IOUSBHostDevice>
  |       "USB Product Name" = "USB 10_100_1000 LAN"
  |       "idVendor" = 3034
  |       "idProduct" = 33107
  |   +-o RTL2838UHIDIR@02124000  <class IOUSBHostDevice>
  |         "idProduct" = 10296
  |         "USB Product Name" = "RTL2838UHIDIR"
  |         "idVendor" = 3034
  +-o AppleT8132USBXHCI@01000000  <class AppleT8132USBXHCI>
"""
    ids = radios._ioreg_usb_ids(text)
    assert ids == [(0x0bda, 0x8153), (0x0bda, 0x2838)], ids
    assert radios.RTL_USB_IDS & set(ids)
    assert not radios.RTL_USB_IDS & {(0x0bda, 0x0328), (0x0bda, 0x8153)}
    # plugged_in: by kind, None where USB can't tell.
    saved = radios.usb_ids
    try:
        radios.usb_ids = lambda: [(0x1d6b, 0x0002), (0x1d50, 0x6089), (0x0bda, 0x2838)]
        assert radios.plugged_in('hackrf') is True and radios.plugged_in('rtlsdr') is True
        assert radios.plugged_in('bb60') is False
        assert radios.plugged_in('rtlsdr', 'macmini') is None     # another computer
        assert radios.plugged_in('usrp') is None and radios.plugged_in('file') is None
        radios.usb_ids = lambda: []                                 # the listing failed
        assert radios.plugged_in('hackrf') is None
    finally:
        radios.usb_ids = saved


if __name__ == '__main__':
    test_parse_address()
    test_stream_and_commands()
    test_gain_at_open()
    test_not_rtl_tcp()
    test_lost()
    test_usb_detection()
    print('rtl_tcp: all checks passed')
