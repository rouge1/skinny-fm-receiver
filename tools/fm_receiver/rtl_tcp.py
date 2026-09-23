"""An RTL-SDR through ``rtl_tcp``: on this computer, or another one over ssh.

``rtl_tcp`` (from the rtl-sdr package) owns the dongle and serves its
samples on a TCP port. The same client does for both places, so a dongle
plugged in here and one on a machine across the room behave the same:

- **Here** (no host given): ``rtl_tcp`` is started on 127.0.0.1.
- **Another computer** (an ssh host, e.g. one named in ``~/.ssh/config``):
  ``rtl_tcp`` is started there over ssh, listening on that host's address
  as ssh resolves it (``ssh -G``), and the samples come over the network.

If something is already listening on the port, it is used as it is and
left running afterwards: it was not ours to stop.

The protocol (rtl_tcp.c): on connecting, a 12-byte header - ``b"RTL0"``,
then big-endian u32 tuner type and u32 gain count. Commands are 5 bytes,
``>BI``: 1 frequency (Hz), 2 sample rate, 3 gain mode (1 manual), 4 gain
(tenths of a dB). Samples are interleaved unsigned 8-bit I/Q, centred on
127.5.

**Stopping rtl_tcp**: on a SIGTERM it says "Signal caught, exiting!" and
can then hang (seen on macOS, 2026-09-23), so :meth:`RtlTcpServer.stop`
waits a moment and then kills it outright.
"""

import collections
import os
import shutil
import socket
import struct
import subprocess
import sys
import threading
import time

import numpy as np
from gnuradio import gr  # type: ignore

DEFAULT_PORT = 1234
#: Where rtl_tcp may be besides PATH: a non-interactive ssh has only the
#: system PATH, and Homebrew is outside it on a Mac.
EXTRA_PATH = '/opt/homebrew/bin:/usr/local/bin'
LOCAL_HOSTS = ('', 'localhost', '127.0.0.1', '::1')

TUNERS = {1: 'E4000', 2: 'FC0012', 3: 'FC0013', 4: 'FC2580', 5: 'R820T',
          6: 'R828D'}
#: Tuner gains in tenths of a dB (librtlsdr's tables), for the gain slider.
GAINS = {
    1: (-10, 15, 40, 65, 90, 115, 140, 165, 190, 215, 240, 290, 340, 420),
    5: (0, 9, 14, 27, 37, 77, 87, 125, 144, 157, 166, 197, 207, 229, 254,
        280, 297, 328, 338, 364, 372, 386, 402, 421, 434, 439, 445, 480, 496),
}
GAINS[6] = GAINS[5]

CMD_FREQ, CMD_RATE, CMD_GAIN_MODE, CMD_GAIN = 1, 2, 3, 4

#: 8-bit sample -> float, once.
_LUT = ((np.arange(256, dtype=np.float32) - 127.5) / 127.5).astype(np.float32)


class RtlTcpError(RuntimeError):
    pass


def parse_address(text):
    """``host[:port]`` -> (host, port). Blank means this computer."""
    text = (text or '').strip()
    host, port = text, DEFAULT_PORT
    if text.count(':') == 1:
        host, _, p = text.partition(':')
        port = int(p)
    return host.strip(), port


def is_local(host):
    return host in LOCAL_HOSTS


def resolve_ssh_host(host):
    """The address ssh would connect to for ``host`` (its HostName in
    ``~/.ssh/config``), or ``host`` itself."""
    if is_local(host):
        return '127.0.0.1'
    try:
        out = subprocess.run(['ssh', '-G', host], capture_output=True, text=True,
                             timeout=5).stdout
        for line in out.splitlines():
            key, _, value = line.partition(' ')
            if key == 'hostname' and value:
                return value.strip()
    except Exception:
        pass
    return host


def gain_steps(tuner):
    return GAINS.get(tuner) or tuple(range(0, 500, 10))


# ------------------------------------------------------------------ server

class RtlTcpServer:
    """``rtl_tcp`` started by us, here or over ssh, and stopped again."""

    #: How long the dongle may take to open and rtl_tcp to listen. Its own
    #: "listening..." goes to a buffered stdout, so the port is tried instead.
    START_TIMEOUT_S = 10.0

    def __init__(self, host, bind, port):
        self.host, self.bind, self.port = host, bind, port
        self.pid = None
        self.proc = None
        self.log = collections.deque(maxlen=40)

    def _shell(self, script):
        """argv running ``script`` in a POSIX shell, here or on the host."""
        if is_local(self.host):
            return ['sh', '-c', f'PATH="$PATH:{_local_path()}"; {script}']
        script = f'PATH="$PATH:{EXTRA_PATH}"; {script}'
        return ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=5',
                self.host, script]

    def where(self):
        return 'this computer' if is_local(self.host) else self.host

    def running(self):
        return self.proc is not None and self.proc.poll() is None

    def said(self):
        """What rtl_tcp (or ssh) has printed, for an error message."""
        return ' / '.join(l for l in self.log if l and not l.startswith('PID '))

    def start(self):
        if is_local(self.host) and not _find_local_rtl_tcp():
            raise RtlTcpError(
                "rtl_tcp is not installed here. It comes with the rtl-sdr "
                "package (conda-forge 'rtl-sdr', or Homebrew 'librtlsdr').")
        self.proc = subprocess.Popen(
            self._shell(f'echo "PID $$"; exec rtl_tcp -a {self.bind} -p {self.port}'),
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, bufsize=1)
        threading.Thread(target=self._drain, daemon=True, name='rtl_tcp log').start()

    def _drain(self):
        """Keep rtl_tcp's output read (a full pipe would stall it), and note
        its pid."""
        for line in self.proc.stdout:
            line = line.strip()
            self.log.append(line)
            if line.startswith('PID ') and self.pid is None:
                try:
                    self.pid = int(line[4:])
                except ValueError:
                    pass

    def stop(self):
        """TERM, a moment's grace, then KILL: rtl_tcp can hang on TERM."""
        if self.proc is not None and self.pid is None:
            time.sleep(0.5)                    # the pid line may be on its way
        pid, proc = self.pid, self.proc
        self.pid = self.proc = None
        if pid:
            script = (f'kill -TERM {pid} 2>/dev/null; i=0; '
                      f'while kill -0 {pid} 2>/dev/null && [ $i -lt 10 ]; '
                      f'do sleep 0.2; i=$((i+1)); done; '
                      f'kill -KILL {pid} 2>/dev/null; true')
            try:
                subprocess.run(self._shell(script), stdin=subprocess.DEVNULL,
                               capture_output=True, timeout=10)
            except Exception as exc:
                print(f"FM receiver: could not stop rtl_tcp on {self.where()} "
                      f"(pid {pid}): {exc}", file=sys.stderr)
        if proc is not None:
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=3)


def _local_path():
    """Where to look here beyond PATH: beside this Python (the conda env's
    bin, when the app is run without activating it), then Homebrew's."""
    return os.path.dirname(sys.executable) + ':' + EXTRA_PATH


def _find_local_rtl_tcp():
    return shutil.which('rtl_tcp', path=os.environ.get('PATH', '') + ':' + _local_path())


# ------------------------------------------------------------------ client

def connect(address, port, timeout=3.0):
    """Connect and read the header: (socket, tuner type, gain count)."""
    sock = socket.create_connection((address, port), timeout=timeout)
    try:
        sock.settimeout(timeout)
        head = b''
        while len(head) < 12:
            chunk = sock.recv(12 - len(head))
            if not chunk:
                raise RtlTcpError("rtl_tcp closed the connection at once.")
            head += chunk
    except socket.timeout:
        sock.close()
        raise RtlTcpError(f"rtl_tcp at {address}:{port} accepted but sent "
                          "nothing: it may be serving another client.") from None
    except Exception:
        sock.close()
        raise
    if head[:4] != b'RTL0':
        sock.close()
        raise RtlTcpError(f"{address}:{port} is not rtl_tcp.")
    tuner, ngains = struct.unpack('>II', head[4:])
    return sock, tuner, ngains


def open_client(text):
    """Reach the rtl_tcp named by ``text`` (``[host][:port]``), starting it
    if nothing is listening. Returns (socket, tuner, server we started or
    None)."""
    host, port = parse_address(text)
    address = resolve_ssh_host(host)
    try:
        sock, tuner, _ = connect(address, port)
        return sock, tuner, None                 # already running: not ours
    except OSError:
        pass                                     # nothing listening: start it
    server = RtlTcpServer(host, address, port)
    server.start()
    deadline = time.monotonic() + server.START_TIMEOUT_S
    while True:
        time.sleep(0.3)
        try:
            sock, tuner, _ = connect(address, port)
            return sock, tuner, server
        except RtlTcpError:
            server.stop()
            raise
        except OSError:
            pass
        if not server.running() or time.monotonic() > deadline:
            said = server.said()
            server.stop()
            raise RtlTcpError(f"rtl_tcp did not start on {server.where()}"
                              + (f": {said}" if said else '.'))


class rtl_tcp_source(gr.sync_block):
    """IQ from an rtl_tcp socket, as complex float.

    A reader thread keeps the socket drained whether or not the flowgraph
    runs, holding at most :attr:`HOLD_S` of samples and dropping the oldest
    beyond that: after a stop and start, or a retune, what comes out is
    fresh rather than a backlog of the old frequency.
    """

    #: 0.2 s dropped a buffer or two a second in the LO-hopping sweep, whose
    #: Python block stalls now and then while the window draws; 0.5 s
    #: dropped none in 40 s (2026-09-23). A backlog is flushed on every
    #: retune and start, so holding more is never stale, only memory.
    HOLD_S = 0.5
    CHUNK = 1 << 16

    def __init__(self, sock, rate=2.4e6):
        gr.sync_block.__init__(self, name='rtl_tcp_source', in_sig=None,
                               out_sig=[np.complex64])
        self._sock = sock
        self._sock.settimeout(0.5)
        self._send_lock = threading.Lock()
        self._cond = threading.Condition()
        self._queue = collections.deque()
        self._queued = 0
        self._odd = b''
        self.rate = float(rate)
        #: Buffers (socket reads) dropped while the flowgraph ran: what it
        #: missed. Stopped, dropping the oldest is the point, not a loss.
        self.overflows = 0
        self._running = False
        self.error = None
        self._closing = False
        self._thread = threading.Thread(target=self._read, daemon=True,
                                        name='rtl_tcp reader')
        self._thread.start()

    # -- control
    def command(self, cmd, value):
        with self._send_lock:
            if self._sock is not None:
                self._sock.sendall(struct.pack('>BI', cmd, int(value) & 0xFFFFFFFF))

    def flush(self):
        """Forget what is queued: it is from before a retune."""
        with self._cond:
            self._queue.clear()
            self._queued = 0

    def start(self):
        self.flush()
        self._running = True
        return True

    def stop(self):
        self._running = False
        return True

    def release(self):
        """Close the socket and stop reading (the radio is closing)."""
        self._closing = True
        with self._send_lock:
            sock, self._sock = self._sock, None
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            sock.close()
        self._thread.join(timeout=2)
        with self._cond:
            self._cond.notify_all()

    # -- the reader thread
    def _read(self):
        buf = bytearray(self.CHUNK)
        view = memoryview(buf)
        while not self._closing:
            try:
                n = self._sock.recv_into(view)
            except socket.timeout:
                continue
            except OSError as exc:
                if not self._closing:
                    self.error = f"rtl_tcp connection lost: {exc}"
                break
            if n == 0:
                if not self._closing:
                    self.error = "rtl_tcp closed the connection."
                break
            data = self._odd + bytes(view[:n]) if self._odd else view[:n]
            even = len(data) & ~1
            self._odd = bytes(data[even:])
            iq = _LUT[np.frombuffer(data, dtype=np.uint8, count=even)].view(np.complex64)
            with self._cond:
                self._queue.append(iq)
                self._queued += len(iq)
                limit = int(self.rate * self.HOLD_S)
                while self._queued > limit and len(self._queue) > 1:
                    old = self._queue.popleft()
                    self._queued -= len(old)
                    if self._running:
                        self.overflows += 1
                self._cond.notify()
        with self._cond:
            self._cond.notify_all()

    # -- the flowgraph
    def work(self, input_items, output_items):
        out = output_items[0]
        want = len(out)
        done = 0
        with self._cond:
            if not self._queue:
                self._cond.wait(0.1)
            while done < want and self._queue:
                front = self._queue[0]
                take = min(len(front), want - done)
                out[done:done + take] = front[:take]
                done += take
                if take == len(front):
                    self._queue.popleft()
                else:
                    self._queue[0] = front[take:]
                self._queued -= take
        return done
