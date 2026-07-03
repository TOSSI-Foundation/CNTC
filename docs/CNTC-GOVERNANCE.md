# CNTC Governance — how the standard is versioned and changed

The value of a verdict comes from the standard being **stable, reviewed, and versioned** —
not something an individual run can quietly redefine. This note keeps the CNTC catalogs
trustworthy. (It is intentionally lightweight; scale it up if CNTC becomes an external
program.)

## What is "the standard"

The requirement catalogs in [`cntc/standards/*.yaml`](../cntc/standards/). Each has a
`version` and a `profile`. The human-readable rationale lives in
[CNTC-REQUIREMENTS.md](CNTC-REQUIREMENTS.md). Together they *are* the standard; the engine
and the verdict engine are just the mechanism.

## Versioning rules

Each catalog carries a semver-ish `version:`. Bump it whenever the catalog changes:

| Change | Bump | Why |
|--------|------|-----|
| Tighten/loosen a threshold, change a verdict rule, change a test's class | **minor** (0.x) | a UPF's result can change → not comparable across the bump |
| Add a new test / new profile | **minor** | more coverage |
| Wording/comment only | **patch** | no grading impact |
| Redefine essential gate policy (`all` ↔ `min_essential`) | **minor**, and announce | changes what "certified" means |

Every emitted verdict records `catalog_version`, so a scorecard is always attributable to an
exact version of the standard. **Never** compare two scorecards graded under different
catalog versions without noting it.

## Who may change a threshold

- A change to any `class: essential` test, the `gate:` policy, or a `verdict:` rule is a
  **standard change** — it must be reviewed by a second maintainer (PR review), and the
  catalog `version` bumped in the same change.
- `normal` / `bonus` additions and comment edits are lower-friction but still versioned.
- Per-campaign tuning belongs in the **campaign config** (`profile:`, `baseline:`), **not**
  by editing the shared catalog for one run.

## Guardrails (automated)

- `cntc lint` structurally validates every catalog (verdict kinds, operators, classes,
  required fields). Run it in CI; it exits non-zero on any issue.
- `tests/test_verdict.py` pins the grading semantics (essential gate, `na` never → `pass`,
  each verdict kind). Changing these tests is itself a reviewable change.
- **Drift check (recommended next):** extend `cntc lint` to assert every catalog `metric`
  key is actually emitted by that test in the engine, so a renamed metric can't silently make
  a test grade `na`. Tracked as a follow-up.

## Honesty principles (non-negotiable)

1. A test that could not be judged is **`na`**, never `pass`. "We can't detect a crash" must
   not read as "no crash."
2. **Performance is hardware-dependent.** Never publish an absolute-Mpps pass/fail in the
   shared catalog; grade relative to a baseline on the same `rig_class`, and stamp the rig on
   every verdict.
3. A `PASS` states the **profile and rig** it was earned under. An `INCOMPLETE` is not a pass.
