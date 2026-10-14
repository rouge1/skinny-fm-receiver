Review feedback on your tools/iq-picture. Two reviewers from other models found these (reproduced). Apply them. Edit
only tools/iq-picture. Do not list or read anything outside the working directory. Keep the picture itself unchanged
(layout, labels, colours) and the memory budget (30 s at 1 MS/s: under 10 s, under 1 GB extra).

FIX LIST:
- [high] `--t0`/`--t1` nan or inf raise ValueError/OverflowError tracebacks: reject non-finite values (and t0 >= t1)
  with an exit 1 and a message on stderr, no PNG. Likewise a non-finite or non-positive --rate.
- [high] a huge `--nfft` (e.g. 30000000 on a 30 s file) takes 3 to 6 GB: refuse an `--nfft` above 1 << 20 (exit 1,
  message), and make sure `pending`/chunk buffers stay bounded for any accepted nfft.
- [medium] `--nfft` below 8 yields a zero Hann norm and NaN levels: refuse it (exit 1, message).
- [medium] an inverted --band (lo > hi) is silently swapped: refuse it like iq-measure does (exit 1, message).
- [medium] an OSError from savefig (a missing or unwritable output folder) escapes as a traceback: catch OSError,
  exit 1 with a message, no partial PNG left behind.
- [low] a non-power-of-two --nfft: accept it (it works) but say in the --help text that powers of two are fastest.

Done when `QT_QPA_PLATFORM=offscreen PYTHONPATH=tools /home/user/miniconda3/envs/gnu/bin/python
tools/tests/test_iq_tools.py` still passes its iq-picture checks (the iq-measure checks need the other branch).
End with a short summary.
