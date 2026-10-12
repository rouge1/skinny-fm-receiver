# Rules for worker agents

You are a worker on **FM Receiver**, a PyQt5 + GNU Radio 3.10 desktop app (Python 3.12, numpy, pyqtgraph) that
plays FM and Sceptre recordings. A manager gives you one task at a time, and a reviewer from a different model
reads every line you write. Read `CLAUDE.md` for the project layout and its rules (colours from `theme.TOKENS`,
guard every Qt paint override, settings only through `config.update_config`, nothing platform-specific outside
`sys.platform`).

## Hard rules
1. **Edit only the files your task names.** Create new files only where your task says so.
2. **Never edit** tests, `CONTRACTS.md`, `AGENTS.md`, `CLAUDE.md`, `knowledge/` or `tools/swarm-records/`, unless your
   task explicitly says so. If you think one of them is wrong, say so in your final message instead.
3. **Stay inside the current working directory.** Do not list, read or write anything outside it, except running
   the tools listed below.
4. **Do not run git commands that change state** (no commit, branch, checkout, reset, stash).
5. **No new dependencies.** Available: Python 3.12, numpy, PyQt5, pyqtgraph, GNU Radio from the `gnu` conda env.
6. Do not start long-running servers or open a radio. Do not use `sudo`. Never read, write or delete anything
   in a folder a recording came from: tests build their own synthetic files in a temporary folder.
7. Never write a Sceptre file, or a file beside one.

## Checking your work
Run from the worktree root (the directory you start in):

    QT_QPA_PLATFORM=offscreen PYTHONPATH=tools /home/user/miniconda3/envs/gnu/bin/python tools/tests/<test>.py

For example `tools/tests/test_spectra.py`. A GUI test takes 30 s to a few minutes: wait for it, and give it a
generous timeout. Your task is done when the tests named in the task pass.

## Style
- Match the surrounding code: comment density, naming, idiom, docstrings that say what and why.
- Vectorise bulk work with numpy; no Python loops over bins or samples.
- A big file (1 GB) is read only where it is needed: memory-map, slice, never `read()` the lot.
- Keep public interfaces exactly as documented in `CONTRACTS.md`.

## Finish
End with a short summary: files changed, what you did, the test result, and anything you could not do.
