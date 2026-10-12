Task: you are the TEST AUTHOR for phase <PHASE> of <PROJECT>. Turn the spec below into contracts and acceptance
tests. Other models will implement it later without seeing you, so the tests are the spec: precise,
deterministic and passable.

Read AGENTS.md first (rule 2 is lifted for the files listed below). Do not list or read anything outside the
working directory. Read CONTRACTS.md, <relevant source files and existing tests to copy the style from>.

Edit or create only:
- <config / protocol files>: <what to add>
- CONTRACTS.md: add a "Phase <PHASE>" section.
- <tests/test_x.py (new)>: <what to cover>
Do NOT edit any implementation file. The implementation does not exist yet, so the new tests are EXPECTED to fail.

Rules for good tests:
- One behaviour per test, with a clear failure message. Use seeds for randomness, and approx for floats.
- Don't depend on unrelated tuning (build tiny fixtures instead of using production data).
- For "must never happen" rules, sweep a dense grid or assert a property. Don't sample a few points.
- Pin only what the spec pins. Leave open what the spec leaves open.

Check before finishing: `<LINT_CMD>` passes, and `<TEST_CMD> --collect-only -q` collects without errors.

SPEC:
<paste specs/<PHASE>.md>
