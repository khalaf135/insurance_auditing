# Preparing your GitHub submission

The repository is prepared for a normal `git add .`. Review what will be staged
before committing. Publication to the repository below was explicitly requested
by the applicant; future commits and pushes should also be intentional.

## What belongs in the repository

- Code, the single test suite, original exercise data and current contract JSONs.
- `submission.csv`, both report PDFs, editable reports, prompt snapshots and AI-use history.
- The selected frozen H1 inputs, all-hospital replay tasks and saved response
  caches needed for the default offline run. These are reproducibility inputs,
  not disposable clutter. The saved spending ledger must accompany the cache.
- `.env.example` with empty values. Normal auditing needs no keys. Copy it to
  `.env` only if you intentionally enable new paid calls.

`.gitignore` excludes local secrets, environments, temporary/editor files and
redundant experimental runs. No local results were deleted. It explicitly keeps
the final assessment evidence and required cached inputs. `.gitattributes`
preserves original bytes because source and replay evidence are hash-verified.
Core auditing uses Python 3.11+ on macOS/Linux, with no pip dependencies; optional
PDF-generation dependencies remain pinned in `docs/requirements.txt`.

## Repository destinations

The submission destination is
[khalaf135/insurance_auditing](https://github.com/khalaf135/insurance_auditing).
The local remotes are configured as follows:

- `origin`: `https://github.com/khalaf135/insurance_auditing.git`
- `upstream`: `https://github.com/majedzahrani3/insurance_auditing.git`

Verify the destinations before pushing:

```sh
git remote -v
```

Push your work only to `origin`. The original exercise repository is retained as
`upstream` for reference, not as a destination for your submission changes.

## Review, commit and push

Run from the `insurance_auditing` directory after configuring your destination:

```sh
git status --short
git add .
git diff --cached --stat
git diff --cached --name-only
git commit -m "Add reproducible invoice auditor and submission"
git push -u origin HEAD
```

Check that `.env`, virtual environments and old full runs are absent from the
staged file list. Do not use `git add -f` for ignored secrets. Ignore rules do not
remove previously committed secrets from history. If a real API key was exposed,
rotate it at its provider; do not assume an ignore rule revokes it.

For a private repository, add the assessor's GitHub account `majedzahrani3` as a
collaborator. Do not push to the original exercise repository. After publishing,
clone your own repository to a fresh folder and run `python3 main.py` to check the
reviewer's workflow. New runs write a separate output folder; the prepared root
`submission.csv` is not overwritten automatically.

## Preparation checks

An isolated copy containing only Git-visible files passed all 228 local tests
and the full offline audit. All 4,886 verdicts, categories, totals, confidence
scores and review reasons matched the prepared run; the 2,743-row submission
was byte-identical. Source/reference/budget verification also passed. No new
API calls were made, and the saved spending ledger was unchanged. Common API
token/private-key patterns were scanned in candidate files with no matches;
this is a check, not a guarantee against every possible kind of secret.
