Task: wiring and docs for the new tool tools/iq-decode (phase m6). Read AGENTS.md first and follow it. Do not list
or read anything outside the working directory.
Edit only: tools/tests/run_all.py, knowledge/usage.md, knowledge/capabilities.md, knowledge/roadmap.md,
knowledge/workflow.md (the step 7 pointer only), CLAUDE.md (the code list: add `iq-decode` and `extract`), and
.agents/skills/fm-receiver/SKILL.md. (AGENTS.md says not to edit CLAUDE.md and knowledge/ unless the task names
them: this task does.)
Read tools/iq-decode (its docstring), CONTRACTS-measure.md (last section), tools/swarm-records/specs/m6.md
("Tasks" row wiring+docs), and how iq-measure is documented in each of those files: match it.
Do:
1. run_all.py: add test_iq_decode.py next to the other iq tests (it SKIPs without rtl_433 and exits 0).
2. usage.md: a short section for `tools/iq-decode FILE [options]`: what it prints (JSON keys), that it cuts the signal
   out of any file the app reads, scales it (the gain is reported; a capture's level is far below rtl_433's
   detection floor), that it needs rtl_433 installed (`apt install rtl-433`, `brew install rtl_433`), exit codes.
3. capabilities.md: a line with the status mark `built, tested on a synthetic frame and over the air once (VSG60
   to BB60D)`, saying it needs rtl_433; use the file's existing mark vocabulary.
4. roadmap.md: tick the item for it if one exists, else add the shipped item in the matching phase.
5. workflow.md: in step 7 a pointer: "tools/iq-decode FILE does this step (needs rtl_433)".
6. CLAUDE.md code list and SKILL.md: one line each for iq-decode and for `iqmeasure.extract`.
No captured data, file names of real recordings or IDs from real captures in any text; the synthetic Nexus-TH
example (id 181, 19.0 C) is fine. Run QT_QPA_PLATFORM=offscreen PYTHONPATH=tools
/home/user/miniconda3/envs/gnu/bin/python tools/tests/test_iq_decode.py after. End with a short summary.
