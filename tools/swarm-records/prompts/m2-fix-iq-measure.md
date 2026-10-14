Review feedback on your tools/iq-measure. Two reviewers from other models found these (reproduced by them). Apply
them. Edit only tools/iq-measure (tools/fm_receiver/iqmeasure.py is not yours: validate in the tool). Do not list or
read anything outside the working directory.

FIX LIST:
- [medium] tools/iq-measure:46 write_card can raise OSError (e.g. an unwritable folder): catch OSError next to
  IQSourceError, message on stderr, exit 1, nothing on stdout.
- [medium] `--t0 nan` raises ValueError and `--t1 inf` an OverflowError (traceback): reject non-finite --t0/--t1 in the
  tool (exit 1, message on stderr). Also reject `--rate` <= 0 or non-finite (it reaches a ZeroDivisionError for .cdif),
  and a --t0 >= --t1.
- [low] tools/iq-measure:50 handle BrokenPipeError on the stdout write quietly (exit 0 or 1 without a traceback).

Done when it still passes the iq-measure checks of
    QT_QPA_PLATFORM=offscreen PYTHONPATH=tools /home/user/miniconda3/envs/gnu/bin/python tools/tests/test_iq_tools.py
(the picture checks fail until iq-picture exists). End with a short summary.
