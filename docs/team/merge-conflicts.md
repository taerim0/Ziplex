# Resolving a merge conflict on aif.json / detail.json / cache.json

If two people each run `ziplex pack` and commit before either sees the
other's change, `aif.json`/`detail.json`/`cache.json` can conflict on merge
the same way any other committed file can. Ziplex doesn't coordinate
this -- see README's "Team use" section -- so here's the recommended way to
handle it when it happens.

**Don't hand-resolve the JSON conflict markers.** These three files are a
generated, fully reproducible snapshot of whatever's actually in the
project's source -- there's no "correct" way to interleave two people's
conflicting summaries/relationships by hand, and doing so risks committing
something that no longer matches either person's `pack` run.

Instead:

1. Resolve the real conflict first -- the actual source files, if any also
   conflicted.
2. Take either side of the generated files to clear the conflict (it
   doesn't matter which, it's about to be regenerated):
   ```
   git checkout --ours  aif.json detail.json cache.json   # or --theirs
   git add aif.json detail.json cache.json
   ```
   (adjust filenames to whatever this project actually committed)
3. Re-run `ziplex pack <project_path>` and review the result normally, the
   same as after any other meaningful change.
4. Commit the freshly regenerated output.

If a re-pack gets missed after a merge like this, `ziplex freshness` (see
README's CI gate section, [`docs/ci/freshness-gate.yml`](../ci/freshness-gate.yml))
catches the drift on the next PR rather than letting it go unnoticed.

See [`gitattributes`](gitattributes) in this same directory for a copyable
snippet that keeps these files out of GitHub's PR diff view in the first
place, so there's less to look at even before a conflict happens.
