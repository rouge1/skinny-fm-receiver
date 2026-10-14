Task: wiring (phase m2). Add the new test file tools/tests/test_iq_tools.py to the suite list in tools/tests/run_all.py,
right after test_iq_measure.py (or where test_iq_measure.py was put by m1), in the same style as its neighbours.
Read AGENTS.md first. Do not list or read anything outside the working directory. Edit only tools/tests/run_all.py.
Check by running: QT_QPA_PLATFORM=offscreen PYTHONPATH=tools /home/user/miniconda3/envs/gnu/bin/python -c "import ast;ast.parse(open('tools/tests/run_all.py').read())" and read run_all.py's own listing/--list option if it has one.
End with a short summary.
