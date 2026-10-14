# eval/

Measurement scripts kept outside the package and the test suite. Ground truth
here must never reuse Ziplex's own resolver code. Plan and decision rules:
`docs/REVIEW-2026-10-14.md` §7; results go to `docs/EXPERIMENTS.md`.

## edges_python.py

Python import-edge accuracy of a pack against an `ast`-based ground truth.

```bash
ziplex pack <repo> --auto --auto-correct --no-llm --no-cache -o out/repo.json
python eval/edges_python.py <repo> out/repo.json --roots src,.
```

Known ground-truth blind spot: it resolves absolute imports only against the
given `--roots`. Ziplex also tries every ancestor folder of the importing file
(script directories, nested sub-projects under `examples/`), so edges it finds
that way show up as EXTRA. Read EXTRA lines before treating precision as a
resolver bug.

| Date | Repo | Commit | Recall | Precision | Notes |
|---|---|---|---|---|---|
| 2026-10-14 | Ziplex (self) | main `6f76137` | 252/252 (1.00) | 1.00 | was 203/244 before Phase 0 (scanner dropped 10 test files) |
| 2026-10-14 | modelcontextprotocol/python-sdk (held out) | `754254c` | 2698/2698 (1.00) | 0.96 | all 114 EXTRA under `examples/` via ancestor-folder roots; resolver-miss count 0 |
