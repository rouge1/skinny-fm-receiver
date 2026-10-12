Review feedback on your Phase 3 docs. A reviewer checked the text against the code. Apply the fix list. Edit only
tools/tests/run_all.py, knowledge/usage.md, knowledge/capabilities.md, knowledge/roadmap.md and
.agents/skills/fm-receiver/SKILL.md. Do not list or read anything outside the working directory. Keep each edit
small; do not claim anything about real-file timings.

FIX LIST (line numbers as in the reviewer's checkout; search the text):
- knowledge/capabilities.md ~line 36: says a sweep DVR is refused; say it is a spectrum recording instead (played
  without sound), refused only as an IQ source.
- knowledge/capabilities.md ~line 40: says BLUE (.cdif, .fft) is not a source; limit that to IQ: a `.fft` is a
  spectrum source now.
- knowledge/roadmap.md ~line 1180: still says a sweep DVR is passed over until phase 3: update.
- knowledge/capabilities.md ~line 241 (test_sceptre_library row): the test now passes over only a text file; sweep
  DVRs are test_sceptre_spectra.py's. Fix the parenthetical.
- knowledge/capabilities.md ~line 192 (the grouped list row): GROUPS are four now, add 'Sceptre spectrum'.
- knowledge/usage.md ~line 777: the tab plays `.sdvr` and `.cdif`: add `.fft` / spectrum.
- knowledge/roadmap.md ~line 1094: "Draw the tiles in the Recordings tab" is unticked though phase 3 shipped it:
  tick it or point to phase 3.
Done when `git diff --stat` shows only those files. End with a short summary.
