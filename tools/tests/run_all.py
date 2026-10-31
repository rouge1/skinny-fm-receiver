"""Run the tests: the ones that need no radio, with ``--hw`` the BB60D
check as well, with ``--hackrf`` the HackRF check (other sessions may
use the HackRF too: make sure it is yours before running it), and with
``--rtl`` the RTL-SDR check (this app closed, so no rtl_tcp is running).

    python tools/tests/run_all.py          # about six minutes
    python tools/tests/run_all.py --hw     # plus a BB60D off air
    python tools/tests/run_all.py --hackrf # plus a HackRF off air
    python tools/tests/run_all.py --rtl    # plus an RTL-SDR off air

On Linux each file runs under its own memory cap (systemd-run, when there is
one): a file over it fails alone, as "over the memory cap", and the run goes
on. systemd-oomd killed whole sessions on one-array loads, and a cap round the
whole run killed every file after the heavy one. FMRX_TEST_MEMORY sets the cap
(default 6G).

Each file gets a temp folder of its own (TMPDIR), removed when it ends: some
files leave their folders behind, and runs piled up 16 GB of them in /tmp.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
TESTS = ['test_sweep.py', 'test_tuning.py', 'test_receive_chain.py', 'test_rds_heard.py', 'test_rds_pty.py', 'test_gui.py',
         'test_recordings.py', 'test_rtl_tcp.py', 'test_bb60_lost.py',
         'test_control.py', 'test_control_more.py',
         'test_iq_measure.py', 'test_iq_measure_cells.py', 'test_iq_measure_classes.py', 'test_iq_measure_levels.py', 'test_iq_measure_rates.py', 'test_iq_measure_carriers.py', 'test_iq_measure_programme.py', 'test_iq_tools.py', 'test_iq_decode.py',
         'test_iq_catalog.py', 'test_iq_frames.py', 'test_iq_decode_bursts.py',
         'test_iqlte.py', 'test_iqlte_crs.py', 'test_iqlte_tdd.py', 'test_iqlte_idle.py', 'test_iqlte_si.py', 'test_iqlte_phy.py', 'test_iqlte_pdcch.py', 'test_iqlte_pdsch.py', 'test_iqlte_cancel.py', 'test_iqlte_timing.py', 'test_iqlte_memory.py', 'test_iqlte_own_si.py', 'test_iqlte_si_cancel.py',
         'test_iq_lte.py', 'test_iqnr.py', 'test_iq_nr.py', 'test_iq_nr_decode.py', 'test_iqnr_phy.py',
         'test_iqnr_pdsch.py', 'test_iqnr_combine.py', 'test_iqnr_cancel.py', 'test_iqnr_shared.py',
         'test_hdradio.py', 'test_sceptre_dvr.py',
         'test_dvr_hd.py', 'test_dvr_iq.py', 'test_sceptre_library.py',
         'test_spectra.py', 'test_sceptre_spectra.py',
         'test_narrow_files.py', 'test_atsc.py', 'test_cells.py']
TIMEOUT = 600   # seconds per file
MEMORY = os.environ.get('FMRX_TEST_MEMORY', '6G')


def _capped(cmd):
    """``cmd`` under its own memory cap, on Linux with systemd-run; as it is elsewhere."""
    if sys.platform.startswith('linux') and shutil.which('systemd-run'):
        return ['systemd-run', '--user', '--scope', '-q', '-p', f'MemoryMax={MEMORY}',
                '-p', 'MemorySwapMax=0'] + cmd
    return cmd


def main():
    tests = (TESTS + (['hw_bb60_check.py'] if '--hw' in sys.argv else [])
             + (['hw_hackrf_check.py'] if '--hackrf' in sys.argv else [])
             + (['hw_rtl_check.py'] if '--rtl' in sys.argv else []))
    env = dict(os.environ, QT_QPA_PLATFORM='offscreen')
    failed = []
    for name in tests:
        t0 = time.time()
        tmp = tempfile.mkdtemp(prefix='fmrx-run-')
        try:
            proc = subprocess.run(_capped([sys.executable, os.path.join(HERE, name)]),
                                  env=dict(env, TMPDIR=tmp),
                                  capture_output=True, text=True, timeout=TIMEOUT)
            ok, why, output = proc.returncode == 0, '', proc.stdout + proc.stderr
            if proc.returncode in (-9, 137):
                why = f', killed: over the memory cap ({MEMORY})'
        except subprocess.TimeoutExpired as e:
            # A timeout is a failure of that file, not of the run: the output
            # so far comes back as bytes even with text=True.
            ok, why = False, f', timed out after {TIMEOUT} s'
            output = ''.join(o.decode(errors='replace') if isinstance(o, bytes) else o
                             for o in (e.stdout, e.stderr) if o)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        print(f"{'PASS' if ok else 'FAIL'}  {name}  ({time.time() - t0:.0f} s{why})",
              flush=True)
        if not ok:
            failed.append(name)
            lines = [l for l in output.splitlines() if 'propagateSizeHints' not in l]
            if lines:
                print('\n'.join('    ' + l for l in lines[-25:]), flush=True)
    print(f"{len(tests) - len(failed)}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
