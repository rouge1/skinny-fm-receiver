Task: add the two new test files to the suite runner.

Read AGENTS.md first and follow it. Do not list or read anything outside the working directory.
Read tools/tests/run_all.py.

Edit only:
- tools/tests/run_all.py: add tools/tests/test_iq_measure.py and tools/tests/test_control_more.py to the list of suites,
  both directly after test_control.py, in that order (test_control.py, test_control_more.py, test_iq_measure.py), in
  the same form the existing entries use (whatever structure it has: names, timeouts, markers). Nothing else.

Do not create the test files (they arrive on other branches). Done when
`QT_QPA_PLATFORM=offscreen PYTHONPATH=tools /home/user/miniconda3/envs/gnu/bin/python -m py_compile tools/tests/run_all.py`
passes. End with a one-paragraph summary.
