Review feedback on your tests-core. Independent reviewers from other models reviewed it, and a consensus run verified each
claim against your test. Apply the fix list below. Edit only tools/tests/test_iq_measure.py. Do not list or read anything outside the working
directory. No implementation file exists; the test is still expected to fail against the missing implementation.
Keep to what the spec and CONTRACTS-measure.md pin; where the fix list says to accept alternatives, accept them.

FIX LIST:
- [high] tools/tests/test_iq_measure.py:828 Match any dotted component of `sys.modules`. `m.split('.')[0] in BANNED` ignores `fm_receiver.library`, `fm_receiver.radios` and `fm_receiver.dsp`, which is what G5 forbids. (source: 2)
- [medium] tools/tests/test_iq_measure.py:469 Add one two-level envelope whose low level is at least 10% of the high level and require class `ask`. Rules 2–3 name `ask`, and no fixture does. (source: 1+2)
- [medium] tools/tests/test_iq_measure.py:469 Add an OOK whose SNR is under 6 dB and require class `noise`, so rule 1 is applied before rule 2. `cw_0db` is only an unkeyed tone. (source: 1)
- [medium] tools/tests/test_iq_measure.py:327 Require `evidence` to be a non-empty list of short sentences. The contract says one sentence per rule that fired, and the shape check allows `[]`. (source: 1+2)
- [medium] tools/tests/test_iq_measure.py:266 Stop using a Hann periodogram's 0.5%–99.5% span as the OOK and FSK 99% bandwidth. Widen the tolerance or compare with a window-independent width. (source: 1)
- [medium] tools/tests/test_iq_measure.py:798 On the copied `sig.cfile`, call `open_iq` and `measure_file` without `rate=`. The meta copied beside it says 2 MS/s, and a conflicting `rate=1e6` is undefined. (source: 1)
- [medium] tools/tests/test_iq_measure.py:344 Add a tone 14 dB above the floor and require it to be absent. Drop the `3 * RATE / 2**20` spacing check. It does not enforce "3 bins", and the contract leaves the FFT length open. (source: 1+2)
- [medium] tools/tests/test_iq_measure.py:631 Require the `.cdif` centre to be `100e6` (`RF_FREQ` passed to `write_blue`) and `power_dbm` to be `None`. The tone check uses whatever centre was reported. (source: 1)
- [medium] tools/tests/test_iq_measure.py:521 For `band=(centre+5 kHz, centre+200 kHz)`, require the occupied bandwidth of the surviving +20 kHz tone so the out-of-band tone is removed from the spectrum. (source: 1)
- [medium] tools/tests/test_iq_measure.py:530 Require OOK `bursts.count == 12` and a 60 ms gap. The fixture is 12 bursts of 40 ms every 100 ms, and only the median duration and the repeat interval are checked. (source: 2)
- [low] tools/tests/test_iq_measure.py:530 Require `symbol_rate_baud is None` for CW, FM, AM and noise. (source: 1+2)
- [low] tools/tests/test_iq_measure.py:584 Assert `IQSource.path`, and `seconds` for the `.sdvr` and `.cdif` sources. Seconds is checked only for the SigMF `.cfile`. (source: 1)
- [low] tools/tests/test_iq_measure.py:644 Refuse a sweep `.sdvr`, a `.fft`, a missing file and a file under 1024 samples from `measure_file` as well as from `open_iq`. (source: 1+2)
- [low] tools/tests/test_iq_measure.py:809 In the G5 subprocess, open a temporary `.cfile` before listing `sys.modules`, so a banned import inside `open_iq` fails the check. (source: 1)
- [low] tools/tests/test_iq_measure.py:611 Assert the bare `.cfile` reports `rate` equal to the `rate=` that was passed. (source: 2)
- [low] tools/tests/test_iq_measure.py:701 Assert `card_path` of a `.bin` is `None`. `None` is checked for `.sdvr`, `.cdif` and `.fft` only. (source: 2)

Done when the file still runs end to end (QT_QPA_PLATFORM=offscreen PYTHONPATH=tools /home/user/miniconda3/envs/gnu/bin/python tools/tests/test_iq_measure.py), failing only for the missing implementation, with no error in the test code itself.
