# KRONOS CI — Threat Model

KRONOS CI is a **discipline gate, not a security boundary.** This document states plainly what it
defends against and what it does not, so no one mistakes it for more than it is.

## What it is for

An *honest but optimistic* contributor — human or AI — who believes work is finished when it is not:
tests never ran, a "fix" is red, code changed without docs. KRONOS CI removes the trust by running
the checks itself in CI and failing the pull request when a required artifact is missing.

## What it defends against

- **"Tests pass" without running them.** KRONOS CI runs `test-command` itself; a red suite blocks merge.
- **Silent doc rot.** With `require-docs`, code changes that touch no docs fail the gate.
- **Missing planning.** With `require-plan`, a PR with no plan file fails.
- **Faking the above.** These are recomputed in CI from the real diff and real test run — not read
  from a self-reported log.

## What it does NOT defend against

- **A hostile actor.** Whoever can edit `.github/workflows/` can weaken or remove the gate, point
  `test-command` at a no-op, or disable the job. CI gates are not a sandbox.
- **Bad-but-passing code.** It checks that the tests *ran and passed*, not that they are good tests
  or that the code is correct. Quality is the job of review and the tests themselves.
- **Anything outside the pull request.** Work that never reaches a PR/push is outside its scope.
- **Supply-chain or runtime security.** Out of scope by design.

## Honest boundary

KRONOS CI verifies that **work happened**, not that it is **good**. Its guarantee is exactly:
"on this pull request, the configured checks were actually executed and passed." That is a
discipline guarantee, enforced by re-running — nothing more, and nothing pretended.

Mirrors the philosophy of the sibling engine: [github.com/dzylab/kronos](https://github.com/dzylab/kronos).
