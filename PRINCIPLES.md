# Design Principles

> The engineering principles KRONOS CI assumes a "clean" change follows. This is the **guidance**
> companion to the gate: KRONOS CI verifies *facts* (tests ran, secrets absent, docs changed), and the
> only slice of design quality a machine can honestly enforce is delegated to your linter (see
> [Enforcing the mechanical slice](#enforcing-the-mechanical-slice)). Everything else here is a
> checklist for the human or AI author — not a check the gate runs.

Original distillation, in our own words, of well-known industry principles (SOLID, GoF design
patterns, common code smells). No external source text is reproduced.

## 0. The rule above the rules

Simplicity first. Write the plainest code that solves the problem in front of you. A principle or
pattern earns its place only when it removes pain that *already exists* — never to look clever, never
"for the future." (KISS + YAGNI as the master switch.)

## 1. SOLID — five checks before committing a change to a class/module

- **S** — one reason to change; if a unit does two unrelated jobs, split it.
- **O** — extend by adding code, not by rewriting tested code.
- **L** — a subtype must work anywhere its base does, with no surprises.
- **I** — many small interfaces beat one fat one; don't force a caller to depend on methods it ignores.
- **D** — depend on abstractions, not concretes; high-level code shouldn't import low-level detail.

## 2. Everyday principles

- **DRY** — one source of truth per piece of knowledge; but copy twice before you abstract (rule of three).
- **Composition over inheritance** — prefer "has-a" wiring to deep "is-a" hierarchies.
- **Law of Demeter** — talk to neighbors, not strangers (`a.b().c().d()` is a smell).
- **Fail fast** — validate at the boundary; crash loud, not silent-wrong.
- **Make the change easy, then make the easy change.**

## 3. Code smells — with mechanical thresholds

These are the smells a linter can measure; the values are defaults — tune them per repo:

| Smell | Default trip |
|---|---|
| Long method | > 50 lines |
| Long parameter list | > 4 params |
| Large file / class | > 500 lines |
| Deep nesting | > 4 levels |
| High complexity | cyclomatic > 10 |
| Duplicate block | repeated > N lines |
| Dead / commented-out code | any |
| Magic number / string | unnamed literal in logic |

Judgment-only smells (no gate, guidance only): primitive obsession, feature envy, shotgun surgery,
speculative generality, data clumps, inappropriate intimacy.

## 4. Patterns — reach for one only when the smell is already there

A pattern is a named answer to a recurring pain. Pick by the problem, not the catalog:

| When you see | Consider |
|---|---|
| many `if`/`switch` on a type | **Strategy / State** |
| a constructor with many optional args | **Builder** |
| scattered, conditional object creation | **Factory Method** |
| needing to notify many on a change | **Observer** |
| wrapping a third-party API you don't control | **Adapter / Facade** |
| adding behavior without a subclass explosion | **Decorator** |
| walking a tree uniformly | **Composite** |

If there is no recurring pain, the simplest function or class wins. A pattern bolted onto trivial code
is itself a smell (speculative generality).

## Enforcing the mechanical slice

Section 3 is the only part a tool can check objectively. KRONOS CI ships no complexity analyzer of its
own (zero dependencies, by design) — instead, wire the thresholds into the linter you already use and
let KRONOS CI run it as the **LINT** check:

```yaml
- uses: dzylab/kronos-ci@v0.6.0
  with:
    test-command: "pytest -q"
    lint-command: "ruff check ."      # configure complexity / length rules in your linter
    lint-required: "true"             # make smell violations fail the gate
```

With Ruff, enable `C901` (complexity), `PLR0913` (too many arguments), and the `pylint` length rules;
with ESLint, `complexity`, `max-lines`, `max-params`, `max-depth`. KRONOS CI treats a non-zero linter
exit as a gate failure when `lint-required: true`.

## What this is NOT

This document is **guidance**, not a gate. The KRONOS CI gate verifies facts (tests ran, a diff exists,
no secrets leaked); it does not and cannot certify "good design." Only the Section 3 thresholds are
machine-enforceable, and only through your linter. See [THREAT_MODEL.md](THREAT_MODEL.md) for the gate's
honest scope, and the sibling [KRONOS](https://github.com/dzylab/kronos) engine for the same principles
baked into its standards layer.
