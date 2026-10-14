You are a REVIEWER on the FM receiver (PyQt5 + GNU Radio app). Do not edit, create or delete any file. Do not run git
commands that change state. Do not list or read anything outside the working directory. You may run the tests and
short probes with QT_QPA_PLATFORM=offscreen PYTHONPATH=tools /home/user/miniconda3/envs/gnu/bin/python.

Another model wrote documentation for the new measurement tools. See: git diff main. The truth source is
CONTRACTS-measure.md plus tools/fm_receiver/iqmeasure.py, tools/iq-measure, tools/iq-picture and the command
table in tools/fm_receiver/control.py. Spec: every fmctl command of the contract's table is in knowledge/usage.md
with an example and in the skill's command list (.agents/skills/fm-receiver/SKILL.md); capabilities.md status marks
read "built, tested on synthetic signals" (never "verified on air"); the roadmap ticks Round 20's filter, step,
radio, start/stop and Round 22's classify item as "first version", leaving iq-demod and iq-frames planned, not
built; docs must say .cdif levels are dBm only when the file has DATA_GAIN, cards are written only beside this
app's own .cfile, and iq-demod and iq-frames are NOT built; no captured data or real recording names; only the
six named files edited (workflow.md only a short pointer under steps 3 and 4).

Review for: statements that disagree with the code (option names, argument forms, defaults, refusals, card
fields), missing commands, a claim that a decoder or iq-demod/iq-frames exists, broken markdown tables, edits
outside scope. Don't report style nits.

Reply with exactly this format and nothing else:
verdict: pass | changes
issues:
- [high|medium|low] <file:line> <one line: what is wrong and the fix>
