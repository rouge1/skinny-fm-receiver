"""Run the tests: the twenty-six that need no radio, with ``--hw`` the BB60D
check as well, with ``--hackrf`` the HackRF check (other sessions may
use the HackRF too: make sure it is yours before running it), and with
``--rtl`` the RTL-SDR check (this app closed, so no rtl_tcp is running).

    python tools/tests/run_all.py          # about six minutes
    python tools/tests/run_all.py --hw     # plus a BB60D off air
    python tools/tests/run_all.py --hackrf # plus a HackRF off air
    python tools/tests/run_all.py --rtl    # plus an RTL-SDR off air
"""

import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
TESTS = ['test_sweep.py', 'test_tuning.py', 'test_receive_chain.py', 'test_gui.py',
         'test_recordings.py', 'test_rtl_tcp.py', 'test_bb60_lost.py',
         'test_control.py', 'test_control_more.py',
         'test_iq_measure.py', 'test_iq_measure_cells.py', 'test_iq_tools.py', 'test_iq_decode.py',
         'test_iq_catalog.py', 'test_iq_frames.py', 'test_iq_decode_bursts.py',
         'test_iqlte.py', 'test_iqlte_crs.py', 'test_iqlte_tdd.py', 'test_iqlte_idle.py', 'test_iqlte_si.py',
         'test_iq_lte.py', 'test_iqnr.py', 'test_iq_nr.py', 'test_iq_nr_decode.py', 'test_iqnr_phy.py',
         'test_hdradio.py', 'test_sceptre_dvr.py',
         'test_dvr_hd.py', 'test_dvr_iq.py', 'test_sceptre_library.py',
         'test_spectra.py', 'test_sceptre_spectra.py',
         'test_narrow_files.py', 'test_atsc.py']
TIMEOUT = 600   # seconds per file


def main():
    tests = (TESTS + (['hw_bb60_check.py'] if '--hw' in sys.argv else [])
             + (['hw_hackrf_check.py'] if '--hackrf' in sys.argv else [])
             + (['hw_rtl_check.py'] if '--rtl' in sys.argv else []))
    env = dict(os.environ, QT_QPA_PLATFORM='offscreen')
    failed = []
    for name in tests:
        t0 = time.time()
        try:
            proc = subprocess.run([sys.executable, os.path.join(HERE, name)], env=env,
                                  capture_output=True, text=True, timeout=TIMEOUT)
            ok, why, output = proc.returncode == 0, '', proc.stdout + proc.stderr
        except subprocess.TimeoutExpired as e:
            # A timeout is a failure of that file, not of the run: the output
            # so far comes back as bytes even with text=True.
            ok, why = False, f', timed out after {TIMEOUT} s'
            output = ''.join(o.decode(errors='replace') if isinstance(o, bytes) else o
                             for o in (e.stdout, e.stderr) if o)
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
