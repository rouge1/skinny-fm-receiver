Fix round on the m6 docs (edit only the files you edited before). Do not list or read anything outside the working
directory. Two review findings:
1. knowledge/usage.md (near the iq-decode section): the comment says the default span is "the whole file". Time
   defaults to the whole file, but the band is the whole file only when its rate is at most 1 MS/s, else +/-250 kHz
   about the centre. Say so. Also say --out-rate is a ceiling (default 250000; a wider band is narrowed).
2. .agents/skills/fm-receiver/SKILL.md (around lines 121-122): "Checked on synthetic signals so far; the real-file
   check is the planner's" contradicts the status mark (built, tested on a synthetic frame and over the air once,
   VSG60 to BB60D). Reword to match the status mark in knowledge/capabilities.md.
End with a short summary.
