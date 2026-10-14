Fix round 2 on tools/iq-decode (edit only that file). Do not list or read anything outside the working directory.
1. [high] `--keep PATH` onto a hard link of the input overwrites the recording: compare with os.path.samefile (or
   st_dev/st_ino) against the input and its sidecar, and refuse (exit 1, message) when the target exists and is the
   same file OR has more than one hard link (st_nlink > 1). Write the kept file to a temp name beside PATH and
   os.replace it, so a failure never truncates an existing file.
2. [med] A non-finite sample before `extract` contaminates a block: say so honestly in the docstring (known limit:
   non-finite samples in the file spread through the band filter's block; the tool zeroes them after extraction and
   warns). No code change needed beyond the wording.
3. [med] Document the exit codes in the docstring: 0 completed (any decode count), 1 bad option, unreadable file,
   or rtl_433 timeout, 2 rtl_433 missing. Mention that --out-rate is a ceiling.
Run the test file again (QT_QPA_PLATFORM=offscreen PYTHONPATH=tools
/home/user/miniconda3/envs/gnu/bin/python tools/tests/test_iq_decode.py). End with a short summary.
