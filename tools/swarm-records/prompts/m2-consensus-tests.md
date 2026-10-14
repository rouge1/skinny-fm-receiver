You are the CONSENSUS reviewer on the FM receiver. Read-only: do not edit, create or delete files, do not run git
commands that change state, and do not list or read anything outside the working directory. You may run short probes.

Two reviewers reviewed the acceptance tests tools/tests/test_iq_tools.py for the phase m2 tools tools/iq-measure and
tools/iq-picture (spec: tools/swarm-records/specs/m2.md, CONTRACTS-measure.md "Tools"; see the tests with
git diff main -- tools/tests/test_iq_tools.py; the tools do not exist yet). The task prompt told the author that
tools/iq-picture defines at module level peak_panel(path, t0=None, t1=None, band=None, rate=None, center_hz=None,
nfft=None) returning (freq_hz, peak_db), all arguments but path optional: a reviewer's claim that a one-argument
call is wrong is false. Reviewers sometimes misread; VERIFY every claim against the test file (quote file:line),
reject false claims, claims that ask beyond the spec/contract, and over-specification demands. Keep severity honest.
The class rule 'unknown' for a two-tone signal is not pinned by the contract (rules 7/8 are not exact for it): reject
that demand unless the m1 test suite pins it (check tools/tests/test_iq_measure.py).

REVIEWS:
--- grokw
I'll review the phase m2 acceptance tests against the spec and contract, starting with the diff and the Tools section.verdict: changes
issues:
- [high] tools/tests/test_iq_tools.py:panel `peak_panel` is called with only the path; the stated signature is `peak_panel(path, t0, t1, band, rate, center_hz, nfft)`, so a correct implementation raises TypeError. Pass all seven arguments.
- [medium] tools/tests/test_iq_tools.py:check_measure_classes The m1 `ask` signal is missing, so that class is never checked. Add the m1 ASK generator (low level at least 10% of the high level) and expect `class == "ask"`.
- [medium] tools/tests/test_iq_tools.py:check_picture_pngs No check that the picture is a mean and a peak spectrum over a waterfall, that the title names file, centre, rate and span, that the unit is dBm when the file is calibrated and dBFS otherwise, or that matplotlib is on the Agg backend. Assert those, including an `.sdvr` (dBm) and a `.cfile` (dBFS).
- [medium] tools/tests/test_iq_tools.py:check_peak_panel The test requires numpy arrays, a strict local maximum, 15 dB above the median, and bin width `rate/len(freq)`. The contract only requires `(freq_hz, peak_db)` in which both tones are visible. Accept any sequence and treat a tone as present when a nearby sample stands well above the rest of the trace.
- [medium] tools/tests/test_iq_tools.py:picture_refusal Only `pic.png` is checked, so a refusal can still change the input or write another file. Snapshot the folder the way `refuse_measure` does.
- [low] tools/tests/test_iq_tools.py:check_measure_options `--t0`/`--t1` only check the reported source fields, and `--rate`/`--center` are never shown to override a `.sigmf-meta`. Use a span whose class differs from the whole file, and a sidecar whose rate and centre are overridden.
- [low] tools/tests/test_iq_tools.py:check_measure_refusals A file shorter than 1024 samples, and a path that is not `.cfile`/`.sdvr`/`.cdif`, are `IQSourceError` refusals with no test. Add both and expect exit 1, a stderr message, and no write.
--- musefree
verdict: changes
issues:
- [high] tools/tests/test_iq_tools.py:check_measure_classes no ask signal class tested though spec requires each m1 synthetic signal and contract defines ask
- [high] tools/tests/test_iq_tools.py:check_measure_classes no unknown class (two-tone) tested though spec requires each signal's card class
- [high] tools/tests/test_iq_tools.py:panel peak_panel signature (path,t0,t1,band,rate,center_hz,nfft) never checked, only called with one arg
- [high] tools/tests/test_iq_tools.py:check_peak_panel no link between peak_panel data and PNG pixels so a blank PNG with correct size passes
- [high] tools/tests/test_iq_tools.py:check_picture_pngs title naming file/centre/rate/span never checked contrary to Tools contract
- [high] tools/tests/test_iq_tools.py:check_picture_pngs dBm-when-calibrated else dBFS unit rule never checked
- [medium] tools/tests/test_iq_tools.py:check_picture_pngs mean-and-peak panel over waterfall structure and Agg backend never checked
- [medium] tools/tests/test_iq_tools.py:refuse_measure short/junk/unknown-extension files never refused though IQSourceError contract requires it
- [medium] tools/tests/test_iq_tools.py:refuse_measure inverted/outside --band and --t0/--t1 span under 1024 samples refusals never tested
- [medium] tools/tests/test_iq_tools.py:check_card_writes non-JSON garbage overwrite refusal and replace-own-card allowance never tested
- [medium] tools/tests/test_iq_tools.py:check_import_clean iq-measure ban omits dsp though iqmeasure contract bans it
- [medium] tools/tests/test_iq_tools.py:picture_refusal only checks PNG absence not folder snapshot so extra writes pass and inputs never snapshotted
- [medium] tools/tests/test_iq_tools.py:tone_line pins 15dB-over-median plus strict local-maximum plus bin=RATE/len(db) which spec leaves open
- [medium] tools/tests/test_iq_tools.py:panel assumes one-arg call succeeds though required signature states no defaults, risking false failure
- [low] tools/tests/test_iq_tools.py:check_measure_inputs --rate alone, --center alone, --card on bare without --rate, default 30s cap, and empty-stderr on success never tested


Reply with exactly this format and nothing else:
consensus: <N> confirmed, <M> rejected
fix list (for the author):
- [high|medium|low] <file:line> <one line: what to change> (source)
rejected:
- <one line: the claim and why it's wrong> (source)
