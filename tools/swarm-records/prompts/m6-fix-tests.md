Fix round on tools/tests/test_iq_decode.py (edit only that file). Do not list or read anything outside the working
directory. Two reviewers (grokw, musefree) agree on these confirmed items:
1. [high] check 8 (no pipe): the regex misses `capture_output=True`, `stdout=sp.PIPE`, `from subprocess import PIPE`
   and `out = subprocess.PIPE`, and trips on a docstring or comment that merely quotes `stdout=subprocess.PIPE`.
   Use `ast`: parse the tool's source, and fail if any Call node passes `capture_output` (true) or a `stdout`
   keyword whose value is `PIPE`/`subprocess.PIPE` (Name or Attribute ending in PIPE), or if the name PIPE is
   imported from subprocess. Comments and docstrings are ignored by ast. Also require positively that some Call to
   rtl_433 (a subprocess.run/Popen/call with a `stdout=` keyword that is not PIPE or DEVNULL) exists.
2. [med] negative control (check 2): require the direct rtl_433 run to finish with exit 0 (rtl_433 returns 0 on
   end of file) and its stdout file to exist; fail with a message that says the control did not run otherwise.
   Then zero lines with a "model" key.
3. [med] cleanup check 7: besides the empty TMPDIR, assert that nothing new appears beside the input file and in the
   fixture folder after a run without --keep (compare directory listings before and after).
4. [med] cover the contract's other flags in a cheap check each, on the 250 kS/s frame (.cfile + meta):
   `-R 19` gives `protocols == [19]` (or whatever the contract's JSON shows: protocols is the -R list) and still
   decodes Nexus-TH id 181 (Nexus-TH is protocol 19 in rtl_433; verify by running rtl_433 -R help once and say
   what you found); `--t0 0.0 --t1 0.2` of the frame: exit 0 and the JSON parses (packets may be 0 or more: only
   assert the document's keys); `--out-rate 125000` is reflected: rate_sps <= 125000 and > 0; `--rate 250000
   --center 433920000` on a bare .cfile without a meta decodes it (center_hz == 433920000).
5. [med] the garbage .sdvr: rename the check to say "garbage .sdvr" (a real sweep DVR is covered by
   test_iq_measure.py); keep exit 1 with stderr.
Run the file again (it should still fail every tool check because tools/iq-decode does not exist; the file itself
must run cleanly and report). End with a short summary.
