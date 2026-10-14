Two checks of tools/tests/test_control_more.py still fail with your control.py. Edit only tools/fm_receiver/control.py;
do not edit tests or app.py (if a helper in app.py is needed, say so). Read the failing checks in the test to see
exactly what they assert.

FAIL filter refused outside Receive: receive
FAIL open loads the file: {'file': '/tmp/fmrx-ctl-more-g8q2a1px/second.cfile'}

Done when test_control_more.py and test_control.py both pass (QT_QPA_PLATFORM=offscreen PYTHONPATH=tools /home/user/miniconda3/envs/gnu/bin/python tools/tests/<file>). Do not list or read anything outside the working directory.
