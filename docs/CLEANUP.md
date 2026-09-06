# Cleanup and recovery — 2026-09-05

## Current structure

The second cleanup consolidates the implementation itself, not just filenames:

- Ten root Python files replace 22 scripts, two runner files and 18 test files.
- `main.py` is the normal command. `pipeline.py` coordinates hospitals.
- Contract creation, source-clause handling, matching, audit rules, fallback
  agents, API access and reporting each have their own named module.
- `rules.py` has one audit body with explicit behavior profiles. It does not load
  archived engines or copy their implementations into versioned modules.
- `test.py` contains the consolidated regression suite with network calls blocked.
- Sixteen prompt records are preserved, with original bodies and checksums, in
  one `AI_USAGE.md`. Historical commands there are not current instructions.
- Normal commands do not generate a visible root `__pycache__` folder.

Use `python3 main.py` for a complete cached audit, or `python3 test.py` for tests.
The old `run_audit.py` and `test all.py` commands are no longer active.

## What was removed from the active repository

`scripts/`, `tests/`, `prompts/`, `run_audit.py`, and `test all.py` were
moved into a recoverable archive. Generated root bytecode was moved there too.
No original contract, invoice format, label, submission template, configuration,
current contract JSON, AI response cache, spending ledger or saved user result
was deleted. The original exercise brief remains unchanged in `EXERCISE.md`.

Versioned **data** paths still exist because frozen evidence, original responses
and provenance hashes reference them. They are not competing code versions.
This refactor does not rename those paths or alter the auditing policy.

## Recovering the previous code

The pre-refactor backup is outside the Git repository:

`/Users/abdulelah./Downloads/Aramco/insurance_auditing_refactor_QYqZPzk4`

- `before/`: the original code, tests, prompt files, runners, README and guides.
- `removed/`: the original active folders and runners, moved after checksum checks.
- `manifest.json`: original file hashes and protected-data tree hashes.

The removed code is recoverable. Restore it into a separate copy of the project
if needed; do not overwrite the new entrypoints or mix two active implementations.
The backup is local and will not be included in a normal repository push.

## Verification

The consolidated suite contains 202 passing tests. In addition, consolidation
was checked against all 43 original extraction request bodies and 480 synthetic
old/new audit comparisons across current, H1 and baseline behavior.

A complete cache-only run through the new files is retained in
`audit_output/test_all_20260905_refactor_verified/`. Its outputs are compared
with `audit_output/test_all_20260905_cleanup_verified/`, including verdicts,
reasons, corrected totals when available, blocker summaries, and CSV cells.

Coverage remains 3,651 answered and 1,173 eligible unanswered, plus 62 duplicate-ID
records, covering all 4,886 records. This is preservation of existing behavior,
not new accuracy evidence for hospitals without labels. No new paid requests
are part of cleanup verification, and the shared spending ledger is preserved.

The completed run's `verification.json` checks source/reference isolation.
`refactor_verification.json` records exact output equality, current code hashes,
preserved-data checks and prompt-history preservation.

## Earlier cleanup

The earlier archive remains separate and untouched:

`/Users/abdulelah./Downloads/Aramco/insurance_auditing_cleanup_MG8o9dun`

It contains 30 previously removed experimental/draft/tooling paths, an original
runner snapshot, its checksum manifest, and a conflict-checking `restore.py`.
That earlier restore command brings back only those historical paths; it does
not undo this consolidation. Its verification run remains in
`audit_output/test_all_20260905_cleanup_verified/`.
