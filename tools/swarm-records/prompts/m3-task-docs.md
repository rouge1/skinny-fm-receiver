Task: document the new measurement tools and fmctl commands where the project keeps its knowledge.

Read AGENTS.md first and follow it. Do not list or read anything outside the working directory.
Read CONTRACTS-measure.md (the source of truth for what was built), tools/fm_receiver/iqmeasure.py,
tools/iq-measure, tools/iq-picture, and the fmctl command table in tools/fm_receiver/control.py (to check the
real command names and arguments). Read each file you edit before changing it and match its style.

Edit only:
- knowledge/usage.md: every fmctl command in the contract's table (filter, step, radio, start, stop, open, delete,
  recordings-folder, marker, measure, and the new status keys) with a short example each; tools/iq-measure and
  tools/iq-picture with options and an example; the card (what it holds, written only beside this app's own
  .cfile as <base>.card.json, never beside .sdvr/.cdif/.fft, never over a file that is not a card).
- knowledge/capabilities.md: a row for each new capability (iqmeasure.py and the card, iq-measure, iq-picture,
  the fmctl commands, fmctl measure, the cdif DATA_GAIN reading) with the status mark `built, tested on synthetic
  signals` in the file's own mark style (NOT `verified on air`; the real-file check is the planner's).
- knowledge/roadmap.md: tick the items that shipped: Round 20's `filter`, `step`, `radio`, `start`/`stop`
  commands; Round 22's classify item as "first version". Leave iq-demod and iq-frames as planned, not built.
- knowledge/workflow.md: a short pointer under steps 3 and 4 only, to iq-measure, iq-picture and fmctl measure.
- CLAUDE.md: add iqmeasure.py, tools/iq-measure and tools/iq-picture to the code list, in the existing style.
- .agents/skills/fm-receiver/SKILL.md: the new commands in its command list and the two tools.

Facts the text must state:
- `.cdif` levels are dBm only when the file has the DATA_GAIN keyword (samples scaled by 10**(-DATA_GAIN/20));
  without it the level is relative (power_dbm is null).
- Cards are written only beside this app's own .cfile.
- iq-demod and iq-frames are NOT built; there is no decoder. The tools say what a signal is (class, levels,
  bursts, tones), they do not decode it.
No captured data, real recording names or IDs in any text; synthetic examples only.

Done when `git diff --stat` shows only the files above, and
`grep -c "iq-measure" knowledge/usage.md .agents/skills/fm-receiver/SKILL.md` are both non-zero.
End with a one-paragraph summary. Do not commit.
