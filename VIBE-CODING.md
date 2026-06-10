# 🌊 Vibe Coding with KRONOS CI

> For people who build apps with AI and push to GitHub — but don't want to learn the boring CI/CD stuff.
> Plain words: what this is, why it helps, and how to switch it on in about 3 minutes.
> If you can copy-paste a file, you can do this.

---

## The problem (you've felt this)

You build with an AI assistant. It says "✅ Done!" — you push to GitHub. But later:

- the tests it "wrote" never actually ran,
- a change quietly broke something else,
- you (or a teammate) merge it… and now your main branch is broken. 😬

You find out too late — after you've built more on top of it.

## What KRONOS CI does (one sentence)

**It's a robot referee on GitHub that runs your tests on every change and blocks the merge if they fail.**

Every time someone opens a Pull Request (a proposed change), KRONOS CI **runs your tests itself** and
puts a ✅ or a ❌ on it. A ❌ means "don't merge — this is broken." Broken code simply can't sneak into
your main branch.

You never have to remember to run the tests. The referee does it — every time, for everyone.

## Why YOU want it (even if you're "not a real developer")

- 🧠 **You're not the tester anymore.** GitHub checks the AI's work for you, automatically.
- 🚦 **A clear traffic light.** Green ✅ = safe to merge. Red ❌ = stop, fix it first.
- 🔑 **It catches leaked secrets — automatically.** Accidentally pushed an API key, a password, or an
  AWS token? KRONOS CI spots it in the change and blocks the merge **before it goes public**. This is
  on by default — you don't have to configure anything.
- 👥 **Works for any tool, any teammate.** It lives on GitHub, below your editor — Cursor, Copilot,
  a local AI, or a human, doesn't matter.
- 🆓 **Free.** Runs on GitHub's machines. Nothing to install on your computer.

## Switch it on (3 minutes)

**Laziest way (1 command).** From your project folder:

```bash
python /path/to/kronos-ci/kronos_ci.py init
```

It figures out how your project runs tests (pytest / npm / go / cargo) and creates the files for you.
Commit them — done.

**Or by hand:**

1. In your repo, make a file at **`.github/workflows/kronos.yml`**.
2. Paste this. Change `pytest -q` to whatever runs your tests (`npm test`, `go test ./...`, etc.):

```yaml
name: KRONOS CI
on: [pull_request]
jobs:
  kronos:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with: { fetch-depth: 0 }
      - uses: dzylab/kronos-ci@v0.5.0
        with:
          test-command: "pytest -q"
```

3. Commit it. Open a Pull Request — you'll see a **"KRONOS CI"** check run.

**To make it actually BLOCK merges:** repo **Settings → Branches → add a rule → "Require status
checks to pass" → pick KRONOS CI**. Now a red check stops the merge button.

## "My PR is red ❌ — help!"

That's the whole point — it caught a failure.

1. Click **"Details"** on the check to see what failed.
2. Ask your AI to fix it.
3. Push again — the check re-runs. Green ✅ = good to merge.

## Want more guardrails? (optional)

You can also require a plan to exist, or require docs to change when code changes:

```yaml
        with:
          test-command: "pytest -q"
          require-plan: "true"     # a plan file (plans/*.md) must exist
          require-docs: "true"     # docs must change when code changes
```

## Quick answers

**Do I need to understand CI/CD?** No. Paste the file, set your test command, done.

**Will it touch my code?** No. It only reads your Pull Request and runs your tests. It says yes or no.

**Does it cost anything / phone home?** No. It runs on GitHub's free runners; nothing is sent anywhere.

**Not a coder?** If you can copy-paste a file and type the command that runs your tests, you can use it.

---

## Want the full details?

- **[README.md](README.md)** — the complete overview, all options.
- **[THREAT_MODEL.md](THREAT_MODEL.md)** — honestly, what it does and does *not* protect against.

---

Happy vibe coding. 🌊 **Push fast — and don't break main.**
