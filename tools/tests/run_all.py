"""Run the tests: the four that need no radio, with ``--hw`` the BB60D
check as well, and with ``--hackrf`` the HackRF check (other sessions may
use the HackRF too: make sure it is yours before running it).

    python tools/tests/run_all.py          # about two minutes
    python tools/tests/run_all.py --hw     # plus a BB60D off air
    python tools/tests/run_all.py --hackrf # plus a HackRF off air
"""

import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
TESTS = ['test_sweep.py', 'test_tuning.py', 'test_receive_chain.py', 'test_gui.py']


def main():
    tests = (TESTS + (['hw_bb60_check.py'] if '--hw' in sys.argv else [])
             + (['hw_hackrf_check.py'] if '--hackrf' in sys.argv else []))
    env = dict(os.environ, QT_QPA_PLATFORM='offscreen')
    failed = []
    for name in tests:
        t0 = time.time()
        proc = subprocess.run([sys.executable, os.path.join(HERE, name)], env=env,
                              capture_output=True, text=True, timeout=600)
        ok = proc.returncode == 0
        print(f"{'PASS' if ok else 'FAIL'}  {name}  ({time.time() - t0:.0f} s)")
        if not ok:
            failed.append(name)
            lines = [l for l in (proc.stdout + proc.stderr).splitlines()
                     if 'propagateSizeHints' not in l]
            print('\n'.join('    ' + l for l in lines[-25:]))
    print(f"{len(tests) - len(failed)}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
