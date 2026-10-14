Review feedback on your docs. Edit only the files you already edited (knowledge/usage.md, knowledge/capabilities.md,
.agents/skills/fm-receiver/SKILL.md, and the others of the original list if needed). Do not list or read anything
outside the working directory. Main has been merged into this branch: iqmeasure.py now applies DATA_GAIN.

FIX LIST:
- [high] knowledge/usage.md ~1051-1055, knowledge/capabilities.md ~249 and .agents/skills/fm-receiver/SKILL.md
  (the iq-measure passage): they say the card does not yet apply DATA_GAIN ("That is open", "power_dbm is null for
  a .cdif"). That was true before and is now false. Read open_iq/_open_cdif in tools/fm_receiver/iqmeasure.py:
  a .cdif with DATA_GAIN is scaled by 10**(-DATA_GAIN/20), full_scale_dbm is 0.0, so power_dbm == power_dbfs
  (dBm); without the keyword power_dbm is null and the level is relative. iq-picture and fmctl measure follow
  (they go through open_iq). Rewrite all three places, and make sure no place still says it is open.
- [medium] knowledge/usage.md ~948-951: the status paragraph and examples were inserted inside the fmctl table,
  leaving the record/capture/scan/wait rows as orphans. Keep one table; move paragraphs and examples after it.
- [low] Say once in usage.md (iq-picture) that the picture's levels are per FFT bin (power over the sum of the
  squared window, as spectra() in tools/iq-picture says), so a steady tone reads higher there than the card's
  power_dbm; both are dBm for a calibrated file.
Do not commit. Done when `git diff --stat main` lists only the allowed files. End with a one-paragraph summary.
