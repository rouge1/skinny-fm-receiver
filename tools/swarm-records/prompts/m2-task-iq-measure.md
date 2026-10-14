Task: write the command line tool tools/iq-measure (new, executable, plain Python, numpy only: it imports only
fm_receiver.iqmeasure and the standard library; no matplotlib).

Read AGENTS.md first and follow it. Do not list or read anything outside the working directory.
Read CONTRACTS-measure.md (section "Tools" and the iqmeasure interfaces), CLAUDE.md, tools/dvr-sweep (the pattern for
a tool: argparse, sys.path insert of its own folder, `from fm_receiver import ...`), tools/fm_receiver/iqmeasure.py
(the m1 core, read-only) and the acceptance test tools/tests/test_iq_tools.py. The tests are the spec: do not edit any
test or any other file.

Edit only:
- tools/iq-measure (new file; make it executable: chmod +x)

Behaviour (CONTRACTS-measure.md "Tools"): `tools/iq-measure FILE [--t0 S --t1 S] [--band LO HI] [--rate R --center HZ]
[--card]` prints the card as JSON on stdout (only the JSON; indent 2 is fine), `--card` also writes it with
iqmeasure.write_card; on IQSourceError exit 1 with the message on stderr and nothing on stdout. Band is absolute Hz.
Use iqmeasure.measure_file; be a thin wrapper. Module-level importable (main under `if __name__ == '__main__'`,
nothing heavy at import). Match tools/dvr-sweep's style (docstring with usage lines at the top).

Done when these tests that concern iq-measure pass (the picture ones fail until iq-picture exists; that is another
task): run `QT_QPA_PLATFORM=offscreen PYTHONPATH=tools /home/user/miniconda3/envs/gnu/bin/python tools/tests/test_iq_tools.py` and report which checks fail.
End with a short summary: file changed, what you did, the test result, anything you could not do.
