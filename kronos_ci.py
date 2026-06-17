#!/usr/bin/env python3
"""KRONOS CI — a pull-request discipline gate for AI-assisted (and human) development.

Philosophy (shared with KRONOS, github.com/dzylab/kronos): verify ARTIFACTS, not declarations.
Instead of trusting that "tests passed", this RUNS the tests itself. It is a *discipline gate*,
not a security boundary. It runs as a GitHub Action (or any CI, or locally) and fails the check
when required artifacts are missing.

Configuration comes from environment variables (set by the Action's `with:` inputs), so there
is zero runtime dependency — standard library only, no YAML parser:

  INPUT_TEST_COMMAND    shell command that runs the tests (must exit 0). Empty -> skipped.
  INPUT_LINT_COMMAND    shell command that lints. Empty -> skipped.
  INPUT_LINT_REQUIRED   "true" to fail on lint errors, else advisory. Default "false".
  INPUT_REQUIRE_PLAN    "true" to require a plan file. Default "false".
  INPUT_PLAN_GLOB       where plans live. Default "plans/*.md".
  INPUT_PLAN_MIN_LINES  minimum plan length in lines. Default "20".
  INPUT_REQUIRE_DOCS    "true" to require docs changes when code changes. Default "false".
  INPUT_CODE_PATHS      comma-separated globs counted as "code".
  INPUT_DOCS_PATHS      comma-separated globs counted as "docs".
  INPUT_BASE_REF        base git ref for the diff. Default $GITHUB_BASE_REF or HEAD~1.
  INPUT_SCAN_TEST_OUTPUT "true" to also fail TEST on failure markers even when it exits 0.
  INPUT_TEST_FAIL_MARKERS comma-separated substrings that indicate a failed test run.
  INPUT_FORBIDDEN_PATTERNS comma-separated regexes forbidden in added diff lines (DIFFSCAN).
  INPUT_COMMIT_PATTERN  regex every commit subject in the PR range must match. Empty -> skipped.
  INPUT_ALLOW_BYPASS    "true" to honor a bypass token in the latest commit. Default "true".
  INPUT_BYPASS_TOKEN    the bypass token. Default "[kronos skip]".
  INPUT_WORKFLOW_FILE   path to a committed WORKFLOW.md to verify (the whole process). Empty -> skipped.
  INPUT_PROFILE         rigor preset: minimal | standard | strict. Empty -> none.
  INPUT_TYPE            task type: trivial | micro | medium | large | ops (else from the workflow file).
  INPUT_COVERAGE_COMMAND shell command that prints a coverage percentage. Empty -> skipped.
  INPUT_COVERAGE_MIN    minimum coverage percent (the gate fails below it). Default "0".
  INPUT_SECRETS_SCAN    "false" to disable the secrets scan of added diff lines. Default ON.
  INPUT_SIZE_MAX_LINES  fail/warn when the PR adds more lines than this. Empty -> skipped.
  INPUT_SIZE_REQUIRED   "true" to make SIZE a hard gate, else advisory. Default "false".
  INPUT_COMMENT_PR      "true" to post the report as a PR comment (needs a token). Default "false".
  INPUT_COMMAND_TIMEOUT seconds before a test/lint/coverage command is killed. Default "1800".

Commands: verify [--json] (default) | init | --self-test | --version | --help.
Exit code: 0 if every REQUIRED check passes, 1 otherwise.
"""

import fnmatch
import glob as globmod
import os
import re
import subprocess
import sys

__version__ = "0.7.1"

try:  # keep output readable on Windows consoles; harmless elsewhere
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

# Check outcomes.
PASS = "PASS"
FAIL = "FAIL"
SKIP = "SKIP"
ADVISORY = "ADVISORY"

DEFAULT_CODE_PATHS = "src/**,lib/**,app/**,*.py,*.js,*.ts,*.go,*.rs,*.java,*.rb"
DEFAULT_DOCS_PATHS = "docs/**,*.md,README*"
DEFAULT_COMMAND_TIMEOUT = 1800  # seconds; a hung test/lint command must not hang the job forever


# ──────────────────────────── environment helpers ────────────────────────────

def _input(name, default=""):
    """Read an Action input. 'test-command' -> $INPUT_TEST_COMMAND."""
    key = "INPUT_" + name.upper().replace("-", "_")
    return os.environ.get(key, default)


def _input_bool(name, default=False):
    raw = _input(name, "true" if default else "false").strip().lower()
    return raw in ("true", "1", "yes", "on")


def _input_list(name, default):
    raw = _input(name, default)
    return [item.strip() for item in raw.split(",") if item.strip()]


def _repo_root():
    return os.environ.get("GITHUB_WORKSPACE") or os.getcwd()


def _command_timeout():
    try:
        return max(1, int(_input("command-timeout", str(DEFAULT_COMMAND_TIMEOUT))))
    except ValueError:
        return DEFAULT_COMMAND_TIMEOUT


# ──────────────────────────── rigor presets (profile + type) ────────────────────────────

PROFILE_DEFAULTS = {
    "minimal": {},
    "standard": {"lint-required": "false"},
    "strict": {"scan-test-output": "true", "lint-required": "true",
               "require-plan": "true", "require-docs": "true"},
}
TYPE_DEFAULTS = {
    "trivial": {"require-plan": "false", "require-docs": "false"},
    "micro": {"require-plan": "false", "require-docs": "false"},
    "large": {"require-plan": "true", "require-docs": "true"},
    "ops": {"require-plan": "true", "require-docs": "true"},
}


def _workflow_text():
    """Return the contents of the committed workflow-file, or None."""
    name = _input("workflow-file").strip()
    if not name:
        return None
    try:
        with open(os.path.join(_repo_root(), name), encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return None


def _resolve_type():
    """The task type: explicit `type` input, else the workflow file's `**Type:**`, else ''."""
    explicit = _input("type").strip().lower()
    if explicit:
        return explicit
    text = _workflow_text()
    if text:
        match = re.search(r"(?im)^\*\*Type:\*\*\s*([A-Za-z]+)", text)
        if match:
            return match.group(1).strip().lower()
    return ""


def _resolved(name):
    """Effective toggle value: an explicit input wins, else the type, else the profile, else base."""
    raw = _input(name, "").strip()
    if raw:
        return raw
    by_type = TYPE_DEFAULTS.get(_resolve_type(), {})
    if name in by_type:
        return by_type[name]
    by_profile = PROFILE_DEFAULTS.get(_input("profile").strip().lower(), {})
    if name in by_profile:
        return by_profile[name]
    return "false"


def _resolved_bool(name):
    return _resolved(name).strip().lower() in ("true", "1", "yes", "on")


# ──────────────────────────── git + path helpers ────────────────────────────

def _git(args, timeout=20):
    """Run a git command. Return (returncode, stdout). Never raises."""
    try:
        proc = subprocess.run(
            ["git", *args], cwd=_repo_root(),
            capture_output=True, text=True, timeout=timeout,
        )
        return proc.returncode, proc.stdout.strip()
    except Exception:
        return 1, ""


def _ref_exists(ref):
    rc, _ = _git(["rev-parse", "--verify", "--quiet", ref + "^{commit}"])
    return rc == 0


def _resolve_base_ref():
    """Find a usable base ref to diff against, robust to shallow clones. Return ref or None."""
    explicit = _input("base-ref").strip()
    if explicit and _ref_exists(explicit):
        return explicit

    base_branch = os.environ.get("GITHUB_BASE_REF", "").strip()  # set on pull_request events
    if base_branch:
        remote = "origin/" + base_branch
        if not _ref_exists(remote):
            _git(["fetch", "--no-tags", "--depth=1", "origin", base_branch], timeout=60)
        if _ref_exists(remote):
            return remote

    before = os.environ.get("GITHUB_EVENT_BEFORE", "").strip()  # set on push events
    if before and not set(before) <= {"0"} and _ref_exists(before):
        return before

    if _ref_exists("HEAD~1"):
        return "HEAD~1"
    return None


def _merge_base():
    """Resolve the merge-base commit to diff against, or None if it cannot be computed."""
    base = _resolve_base_ref()
    if base is None:
        return None
    rc, mb = _git(["merge-base", base, "HEAD"])
    if rc != 0 or not mb:
        return None
    return mb


def changed_files():
    """List files changed since the merge-base. Return a list, or None if uncomputable."""
    mb = _merge_base()
    if mb is None:
        return None
    rc, out = _git(["diff", "--name-only", "--no-renames", mb, "HEAD"])
    if rc != 0:
        return None
    return [line.strip() for line in out.splitlines() if line.strip()]


def added_lines_with_files():
    """Return [(file, added_line)] for every line added since the merge-base, or None."""
    mb = _merge_base()
    if mb is None:
        return None
    rc, out = _git(["diff", "--unified=0", "--no-color", mb, "HEAD"])
    if rc != 0:
        return None
    result = []
    current = "?"
    for line in out.splitlines():
        if line.startswith("+++ b/"):
            current = line[6:]
        elif line.startswith("+") and not line.startswith("+++"):
            result.append((current, line[1:]))
    return result


def commit_subjects():
    """Return the subject of each commit in merge-base..HEAD, or None if uncomputable."""
    mb = _merge_base()
    if mb is None:
        return None
    rc, out = _git(["log", "--format=%s", mb + "..HEAD"])
    if rc != 0:
        return None
    return [line for line in out.splitlines() if line.strip()]


def path_matches(path, patterns):
    """True if `path` matches ANY glob in `patterns`. Total and exception-free.

    `**` collapses to `*` (fnmatch's `*` already crosses `/`, so `src/**` means "under src/").
    A slash-free pattern (e.g. `*.py`) also matches the basename.
    """
    norm = path.replace("\\", "/")
    if norm.startswith("./"):
        norm = norm[2:]
    base = norm.rsplit("/", 1)[-1]
    for pattern in patterns:
        flat = pattern.replace("**", "*")
        if fnmatch.fnmatch(norm, flat):
            return True
        if "/" not in pattern and fnmatch.fnmatch(base, flat):
            return True
    return False


def run_user_command(label, cmd, capture=False):
    """Run an author-provided shell command. Return (exit_code, output_or_None).

    capture=False streams the output live to the CI log. capture=True captures (and echoes)
    it so it can be scanned for markers. `shell=True` is intentional: the command is
    author-written in the repo's own workflow — the same trust level as the rest of CI.
    """
    print(f"::group::kronos-ci: {label} -> {cmd}")
    timeout = _command_timeout()
    output = None
    try:
        if capture:
            proc = subprocess.run(cmd, shell=True, cwd=_repo_root(), timeout=timeout,
                                  capture_output=True, text=True)
            output = (proc.stdout or "") + (proc.stderr or "")
            if output:
                print(output)
            code = proc.returncode
        else:
            proc = subprocess.run(cmd, shell=True, cwd=_repo_root(), timeout=timeout)
            code = proc.returncode
    except subprocess.TimeoutExpired:
        print(f"kronos-ci: {label} timed out after {timeout}s", file=sys.stderr)
        code, output = 124, ""
    finally:
        print("::endgroup::")
    return code, output


# ──────────────────────────── the checks ────────────────────────────

_FAIL_COUNT_RE = re.compile(r"\b[1-9]\d*\s+(?:failed|errors?)\b")
DEFAULT_FAIL_MARKERS = "Traceback (most recent call last),FAILED,=== FAILURES ==="


def _failure_marker(output):
    """Return the first failure marker found in `output`, or None. Backstops a lying runner."""
    if not output:
        return None
    for marker in _input_list("test-fail-markers", DEFAULT_FAIL_MARKERS):
        if marker in output:
            return marker
    match = _FAIL_COUNT_RE.search(output)
    return match.group(0) if match else None


def check_test():
    cmd = _input("test-command").strip()
    if not cmd:
        return ("TEST", SKIP, "no test-command configured (set it to actually verify tests)")
    scan = _resolved_bool("scan-test-output")
    code, output = run_user_command("test", cmd, capture=scan)
    if code != 0:
        return ("TEST", FAIL, f"test command exited {code}")
    if scan:
        marker = _failure_marker(output)
        if marker:
            return ("TEST", FAIL, f"exited 0 but output shows a failure: '{marker}'")
        return ("TEST", PASS, "tests ran and passed (output scanned, no failure markers)")
    return ("TEST", PASS, "tests ran and passed")


def check_lint():
    cmd = _input("lint-command").strip()
    if not cmd:
        return ("LINT", SKIP, "no lint-command configured")
    code, _ = run_user_command("lint", cmd)
    if code == 0:
        return ("LINT", PASS, "lint clean")
    if _resolved_bool("lint-required"):
        return ("LINT", FAIL, f"lint command exited {code}")
    return ("LINT", ADVISORY, f"lint command exited {code} (advisory; set lint-required to gate)")


_COVERAGE_TOTAL_RE = re.compile(r"(?im)^TOTAL\b.*?(\d+(?:\.\d+)?)\s*%")
_COVERAGE_ANY_RE = re.compile(r"(\d+(?:\.\d+)?)\s*%")


def _parse_coverage(output):
    """Extract the coverage percentage from a report. Prefer a TOTAL line; else the last 'NN%'."""
    if not output:
        return None
    match = _COVERAGE_TOTAL_RE.search(output)
    if match:
        return float(match.group(1))
    all_pcts = _COVERAGE_ANY_RE.findall(output)
    return float(all_pcts[-1]) if all_pcts else None


def check_coverage():
    cmd = _input("coverage-command").strip()
    if not cmd:
        return ("COVERAGE", SKIP, "no coverage-command configured")
    try:
        minimum = float(_input("coverage-min", "0") or 0)
    except ValueError:
        minimum = 0.0
    code, output = run_user_command("coverage", cmd, capture=True)
    if code != 0:
        return ("COVERAGE", FAIL, f"coverage command exited {code}")
    pct = _parse_coverage(output)
    if pct is None:
        return ("COVERAGE", FAIL, "could not find a coverage percentage in the output")
    if minimum and pct < minimum:
        return ("COVERAGE", FAIL, f"coverage {pct:g}% is below the required {minimum:g}%")
    return ("COVERAGE", PASS, f"coverage {pct:g}%" + (f" >= {minimum:g}%" if minimum else ""))


def _plan_present():
    """Return (path, line_count) of the first plan file with >= plan-min-lines lines, or None."""
    pattern = _input("plan-glob", "plans/*.md")
    try:
        min_lines = int(_input("plan-min-lines", "20"))
    except ValueError:
        min_lines = 20
    for path in globmod.glob(pattern, recursive=True):
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                count = sum(1 for _ in fh)
        except OSError:
            continue
        if count >= min_lines:
            return (path, count)
    return None


def check_plan():
    if not _resolved_bool("require-plan"):
        return ("PLAN", SKIP, "require-plan=false")
    found = _plan_present()
    if found:
        return ("PLAN", PASS, f"{found[0]} ({found[1]} lines)")
    return ("PLAN", FAIL, f"no file matching '{_input('plan-glob', 'plans/*.md')}' with enough lines")


def check_docs():
    if not _resolved_bool("require-docs"):
        return ("DOCS", SKIP, "require-docs=false")
    files = changed_files()
    if files is None:
        return ("DOCS", SKIP, "could not determine changed files (shallow clone? set fetch-depth: 0)")
    code_paths = _input_list("code-paths", DEFAULT_CODE_PATHS)
    docs_paths = _input_list("docs-paths", DEFAULT_DOCS_PATHS)
    code_changed = any(path_matches(f, code_paths) for f in files)
    docs_changed = any(path_matches(f, docs_paths) for f in files)
    if code_changed and not docs_changed:
        return ("DOCS", FAIL, "code changed but no docs changed in this diff")
    if not code_changed:
        return ("DOCS", PASS, "no code paths changed")
    return ("DOCS", PASS, "code and docs changed together")


CONFLICT_PREFIXES = ("<<<<<<<", ">>>>>>>")


def check_diffscan():
    """Scan added diff lines for merge-conflict markers (always) and forbidden patterns (opt-in)."""
    forbidden = _input_list("forbidden-patterns", "")
    added = added_lines_with_files()
    if added is None:
        return ("DIFFSCAN", SKIP, "could not compute the diff (shallow clone? set fetch-depth: 0)")
    compiled = []
    for pat in forbidden:
        try:
            compiled.append((pat, re.compile(pat)))
        except re.error:
            pass  # a bad pattern is ignored, never a crash
    problems = []
    for path, line in added:
        if line.startswith(CONFLICT_PREFIXES):
            problems.append(f"{path}: merge-conflict marker")
        for pat, rx in compiled:
            if rx.search(line):
                problems.append(f"{path}: forbidden /{pat}/")
    if problems:
        shown = "; ".join(problems[:5])
        extra = f" (+{len(problems) - 5} more)" if len(problems) > 5 else ""
        return ("DIFFSCAN", FAIL, shown + extra)
    return ("DIFFSCAN", PASS, "no conflict markers" + (", no forbidden patterns" if forbidden else ""))


# Common credential shapes. The report NEVER prints the matched text — only the file and the kind.
_SECRET_PATTERNS = [
    ("AWS access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("GitHub token", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36,}\b")),
    ("GitHub fine-grained token", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{22,}\b")),
    ("private key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("Slack token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("hardcoded password", re.compile(r"(?i)\bpassword\s*[:=]\s*['\"][^'\"]{4,}")),
    ("API key assignment", re.compile(r"(?i)\bapi[_-]?key\s*[:=]\s*['\"][A-Za-z0-9_\-]{16,}['\"]")),
]


def check_secrets():
    """Scan added diff lines for credential shapes. ON by default — a pushed secret is never OK."""
    if not _input_bool("secrets-scan", True):
        return ("SECRETS", SKIP, "secrets-scan=false")
    added = added_lines_with_files()
    if added is None:
        return ("SECRETS", SKIP, "could not compute the diff (shallow clone? set fetch-depth: 0)")
    hits = []
    for path, line in added:
        for kind, rx in _SECRET_PATTERNS:
            if rx.search(line):
                hits.append(f"{path}: possible {kind}")
                break
    if hits:
        shown = "; ".join(hits[:5]) + (f" (+{len(hits) - 5} more)" if len(hits) > 5 else "")
        return ("SECRETS", FAIL, shown + " — rotate it even if you remove the line")
    return ("SECRETS", PASS, "no credential shapes in the added lines")


def check_size():
    """Warn (or fail) when the PR adds more lines than `size-max-lines` — huge PRs evade review."""
    raw = _input("size-max-lines").strip()
    if not raw:
        return ("SIZE", SKIP, "size-max-lines not set")
    try:
        limit = int(raw)
    except ValueError:
        return ("SIZE", SKIP, f"invalid size-max-lines: '{raw}'")
    added = added_lines_with_files()
    if added is None:
        return ("SIZE", SKIP, "could not compute the diff (shallow clone? set fetch-depth: 0)")
    count = len(added)
    if count > limit:
        status = FAIL if _input_bool("size-required") else ADVISORY
        return ("SIZE", status, f"{count} added lines exceed the limit of {limit}")
    return ("SIZE", PASS, f"{count} added lines (limit {limit})")


def check_commitmsg():
    """Require every commit subject in the PR range to match `commit-pattern` (opt-in)."""
    pattern = _input("commit-pattern").strip()
    if not pattern:
        return ("COMMIT-MSG", SKIP, "commit-pattern not set")
    try:
        rx = re.compile(pattern)
    except re.error as exc:
        return ("COMMIT-MSG", SKIP, f"invalid commit-pattern: {exc}")
    subjects = commit_subjects()
    if subjects is None:
        return ("COMMIT-MSG", SKIP, "could not list commits (shallow clone? set fetch-depth: 0)")
    if not subjects:
        return ("COMMIT-MSG", PASS, "no new commits")
    bad = [s for s in subjects if not rx.search(s)]
    if bad:
        return ("COMMIT-MSG", FAIL,
                f"{len(bad)}/{len(subjects)} subject(s) fail /{pattern}/: " + "; ".join(bad[:3]))
    return ("COMMIT-MSG", PASS, f"all {len(subjects)} subject(s) match /{pattern}/")


def bypass_token():
    """Return the bypass token if the latest commit message contains it (and bypass is allowed)."""
    if not _input_bool("allow-bypass", True):
        return None
    token = _input("bypass-token", "[kronos skip]")
    rc, message = _git(["log", "-1", "--format=%B"])
    if rc == 0 and token and token in message:
        return token
    return None


# ──────────────────────────── WORKFLOW discipline check ────────────────────────────
# Verifies a committed WORKFLOW.md (the KRONOS process record) in CI: every stage marked [x] must
# be backed by its real artifact — the same guarantee the local KRONOS hook enforces, re-checked
# on the pull request. This makes KRONOS CI a superset of the KRONOS gates.

MIN_TEST_LOG_LINES = 5
_WF_STAGE_RE = re.compile(r"(?m)^\s*[-*]\s*\[(.)\]\s*\d+\.\s*\*\*([A-Za-z-]+)\*\*")
# Stages that a TRIVIAL / MICRO task may legitimately leave skipped or open.
_RELAXABLE_BY_TYPE = {"trivial": {"PLAN", "TEST"}, "micro": {"PLAN"}}


def _wf_section(text, title):
    match = re.search(r"(?ims)^##\s+" + re.escape(title) + r"\s*\n(.*?)(?=^##\s|\Z)", text)
    return match.group(1).strip() if match else ""


def _wf_real_lines(body):
    return [ln for ln in body.splitlines() if ln.strip() and not ln.strip().startswith("<!--")]


def _verify_wf_stage(name, text):
    """Return an error string if a [x] stage's artifact is missing, else None."""
    if name == "PLAN":
        return None if _plan_present() else "PLAN[x] but no plan file with enough lines"
    if name == "CODE":
        files = changed_files()
        return "CODE[x] but the diff is empty" if (files is not None and not files) else None
    if name == "TEST":
        if len(_wf_real_lines(_wf_section(text, "Test log"))) < MIN_TEST_LOG_LINES:
            return f"TEST[x] but '## Test log' has < {MIN_TEST_LOG_LINES} lines"
        return None
    if name == "DOCS":
        if not _wf_real_lines(_wf_section(text, "Docs updated")):
            return "DOCS[x] but '## Docs updated' is empty"
        return None
    return None  # COMMIT (commit exists in CI) or an unknown stage -> do not block


def check_workflow():
    """Verify the committed workflow-file: every [x] stage must carry its real artifact."""
    text = _workflow_text()
    if text is None:
        return ("WORKFLOW", SKIP, "workflow-file not set or not found")
    stages = [(m.group(2).upper(), m.group(1)) for m in _WF_STAGE_RE.finditer(text)]
    if not stages:
        return ("WORKFLOW", FAIL, "workflow-file has no parsable stages")
    relaxable = _RELAXABLE_BY_TYPE.get(_resolve_type(), set())
    decisions = _wf_section(text, "Decisions log").upper()
    problems = []
    for name, mark in stages:
        if mark == "x":
            err = _verify_wf_stage(name, text)
            if err:
                problems.append(err)
        elif mark == "⊘":
            if name not in relaxable and "SKIP" not in decisions and name not in decisions:
                problems.append(f"{name} skipped without a reason in '## Decisions log'")
        elif name != "COMMIT" and name not in relaxable:
            problems.append(f"{name} is still open ([ ])")
    if problems:
        return ("WORKFLOW", FAIL, "; ".join(problems[:4]) + (" …" if len(problems) > 4 else ""))
    return ("WORKFLOW", PASS, f"{len(stages)} stages closed and verified")


# ──────────────────────────── reporting + outputs ────────────────────────────

def _write_step_summary(results):
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    lines = ["## KRONOS CI", "", "| Check | Status | Note |", "|---|---|---|"]
    for name, status, note in results:
        lines.append(f"| {name} | {status} | {note} |")
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
    except OSError:
        pass


def _set_output(key, value):
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(f"{key}={value}\n")
    except OSError:
        pass


def _annotate(results):
    for name, status, note in results:
        if status == FAIL:
            print(f"::error::KRONOS CI {name}: {note}")
        elif status == ADVISORY:
            print(f"::warning::KRONOS CI {name}: {note}")


def _pr_number():
    """The pull-request number from GITHUB_REF ('refs/pull/N/merge'), or None."""
    match = re.match(r"refs/pull/(\d+)/", os.environ.get("GITHUB_REF", ""))
    return match.group(1) if match else None


def post_pr_comment(results, failed):
    """Post the report as a PR comment (opt-in). STRICTLY fail-safe: never affects the exit code."""
    if not _input_bool("comment-pr"):
        return
    token = os.environ.get("GITHUB_TOKEN") or _input("github-token").strip()
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    number = _pr_number()
    if not (token and repo and number):
        print("::warning::KRONOS CI: comment-pr is on but the token / repository / PR number is missing")
        return
    verdict = "BLOCKED: " + ", ".join(failed) if failed else "all required checks passed"
    lines = [f"## KRONOS CI — {verdict}", "", "| Check | Status | Note |", "|---|---|---|"]
    lines += [f"| {n} | {s} | {note} |" for n, s, note in results]
    import json
    import urllib.request
    request = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/issues/{number}/comments",
        data=json.dumps({"body": "\n".join(lines)}).encode(),
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                 "User-Agent": "kronos-ci"},
        method="POST",
    )
    try:
        urllib.request.urlopen(request, timeout=15)
        print("kronos-ci: posted the report as a PR comment")
    except Exception as exc:  # network/auth problems must never fail the gate
        print(f"::warning::KRONOS CI: could not post the PR comment ({exc.__class__.__name__})")


def _print_json(result, failed, results):
    """Machine contract: the LAST stdout line is a single JSON object (user commands stream above)."""
    import json
    print(json.dumps({
        "version": __version__,
        "result": result,
        "failed": failed,
        "checks": [{"name": n, "status": s, "note": note} for n, s, note in results],
    }))


def verify(json_mode=False):
    """Run every enabled check, report, and return the process exit code."""
    token = bypass_token()
    if token:
        print(f"\nKRONOS CI: BYPASSED via '{token}' in the latest commit message")
        print(f"::warning::KRONOS CI was bypassed via '{token}' in the latest commit message")
        _write_step_summary([("BYPASS", "WARN", f"gate bypassed via '{token}' in the commit message")])
        _set_output("result", "bypassed")
        _set_output("failed-checks", "")
        if json_mode:
            _print_json("bypassed", [], [])
        return 0

    results = [check_test(), check_coverage(), check_lint(), check_plan(), check_docs(),
               check_diffscan(), check_secrets(), check_size(), check_commitmsg(), check_workflow()]

    print("\nKRONOS CI — verification report")
    for name, status, note in results:
        print(f"  [{status:<8}] {name:<10} — {note}")

    failed = [name for name, status, _ in results if status == FAIL]
    _write_step_summary(results)
    _annotate(results)
    _set_output("result", "fail" if failed else "pass")
    _set_output("failed-checks", ",".join(failed))
    post_pr_comment(results, failed)

    if failed:
        print(f"\nKRONOS CI: BLOCKED — failed: {', '.join(failed)}")
    else:
        print("\nKRONOS CI: passed")
    if json_mode:
        _print_json("fail" if failed else "pass", failed, results)
    return 1 if failed else 0


# ──────────────────────────── self-test (hermetic, stdlib) ────────────────────────────

_WATCHED_GITHUB = ("GITHUB_BASE_REF", "GITHUB_EVENT_BEFORE", "GITHUB_WORKSPACE")


def _clear_case_env():
    for key in [k for k in os.environ if k.startswith("INPUT_")] + list(_WATCHED_GITHUB):
        os.environ.pop(key, None)


def _run_self_case(label, expected_status, env, setup=None):
    """Run a single check under a clean env and compare its status. Return True on match."""
    import tempfile
    watched = [k for k in os.environ if k.startswith("INPUT_")] + list(_WATCHED_GITHUB)
    saved = {k: os.environ.get(k) for k in watched}
    _clear_case_env()
    cwd0 = os.getcwd()
    tmp = tempfile.mkdtemp(prefix="kronos-ci-")
    try:
        os.chdir(tmp)
        env = dict(env)
        check_from_env = env.pop("_check", None)  # remove the non-string before updating environ
        check_from_setup = setup(tmp) if setup else None
        os.environ.update(env)
        check = check_from_setup or check_from_env
        _name, status, _note = check()
        ok = status == expected_status
        print(f"  [{'OK' if ok else 'FAIL'}] {label}: {status} (want {expected_status})")
        return ok
    finally:
        os.chdir(cwd0)
        _clear_case_env()
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v


def _py(code):
    """A portable shell command that runs `code` in this interpreter."""
    return f'"{sys.executable}" -c "{code}"'


def _init_git_repo(path, files, subject="change"):
    """Create a throwaway git repo with a baseline commit, then a second commit applying `files`.
    Sets INPUT_BASE_REF to the baseline so the diff helpers compare baseline..HEAD."""
    subprocess.run(["git", "init", "-q"], cwd=path)
    subprocess.run(["git", "config", "user.email", "t@t.t"], cwd=path)
    subprocess.run(["git", "config", "user.name", "t"], cwd=path)
    with open(os.path.join(path, "seed.txt"), "w", encoding="utf-8") as fh:
        fh.write("seed\n")
    subprocess.run(["git", "add", "-A"], cwd=path)
    subprocess.run(["git", "commit", "-qm", "baseline"], cwd=path)
    base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=path,
                          capture_output=True, text=True).stdout.strip()
    for rel, content in files.items():
        full = os.path.join(path, rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as fh:
            fh.write(content)
    subprocess.run(["git", "add", "-A"], cwd=path)
    subprocess.run(["git", "commit", "-qm", subject], cwd=path)
    os.environ["INPUT_BASE_REF"] = base


def self_test():
    print("KRONOS CI self-test")
    ok = True

    ok &= _run_self_case("TEST pass", PASS,
                         {"INPUT_TEST_COMMAND": _py("import sys;sys.exit(0)"), "_check": check_test})
    ok &= _run_self_case("TEST fail", FAIL,
                         {"INPUT_TEST_COMMAND": _py("import sys;sys.exit(1)"), "_check": check_test})
    ok &= _run_self_case("TEST unset -> skip", SKIP, {"_check": check_test})

    ok &= _run_self_case("LINT advisory", ADVISORY,
                         {"INPUT_LINT_COMMAND": _py("import sys;sys.exit(1)"), "_check": check_lint})
    ok &= _run_self_case("LINT required -> fail", FAIL,
                         {"INPUT_LINT_COMMAND": _py("import sys;sys.exit(1)"),
                          "INPUT_LINT_REQUIRED": "true", "_check": check_lint})

    def make_plan(tmp):
        os.makedirs(os.path.join(tmp, "plans"))
        with open(os.path.join(tmp, "plans", "p.md"), "w", encoding="utf-8") as fh:
            fh.write("\n".join(f"line {i}" for i in range(30)))
        return check_plan
    ok &= _run_self_case("PLAN present", PASS,
                         {"INPUT_REQUIRE_PLAN": "true", "INPUT_PLAN_MIN_LINES": "20"}, setup=make_plan)
    ok &= _run_self_case("PLAN absent -> fail", FAIL,
                         {"INPUT_REQUIRE_PLAN": "true"}, setup=lambda tmp: check_plan)

    def docs_code_only(tmp):
        _init_git_repo(tmp, {"src/app.py": "print(1)\n"})
        return check_docs
    ok &= _run_self_case("DOCS code-only -> fail", FAIL,
                         {"INPUT_REQUIRE_DOCS": "true"}, setup=docs_code_only)

    def docs_code_and_docs(tmp):
        _init_git_repo(tmp, {"src/app.py": "print(1)\n", "README.md": "# docs\n"})
        return check_docs
    ok &= _run_self_case("DOCS code+docs -> pass", PASS,
                         {"INPUT_REQUIRE_DOCS": "true"}, setup=docs_code_and_docs)

    # TEST output-scan (correctness backstop for a lying runner)
    ok &= _run_self_case("TEST scan: exit0 but FAILED -> fail", FAIL,
                         {"INPUT_TEST_COMMAND": _py("print('FAILED');import sys;sys.exit(0)"),
                          "INPUT_SCAN_TEST_OUTPUT": "true", "_check": check_test})
    ok &= _run_self_case("TEST scan: exit0 clean -> pass", PASS,
                         {"INPUT_TEST_COMMAND": _py("print('ok');import sys;sys.exit(0)"),
                          "INPUT_SCAN_TEST_OUTPUT": "true", "_check": check_test})

    # DIFFSCAN (conflict markers always; forbidden patterns opt-in)
    def diffscan_conflict(tmp):
        _init_git_repo(tmp, {"f.py": "ok\n<<<<<<< HEAD\nx\n"})
        return check_diffscan
    ok &= _run_self_case("DIFFSCAN conflict marker -> fail", FAIL, {}, setup=diffscan_conflict)

    def diffscan_forbidden(tmp):
        _init_git_repo(tmp, {"f.py": "x = 1  # TODO fix\n"})
        return check_diffscan
    ok &= _run_self_case("DIFFSCAN forbidden pattern -> fail", FAIL,
                         {"INPUT_FORBIDDEN_PATTERNS": "TODO"}, setup=diffscan_forbidden)

    def diffscan_clean(tmp):
        _init_git_repo(tmp, {"f.py": "x = 1\n"})
        return check_diffscan
    ok &= _run_self_case("DIFFSCAN clean -> pass", PASS, {}, setup=diffscan_clean)

    # COMMIT-MSG (commit subject convention)
    def commitmsg_ok(tmp):
        _init_git_repo(tmp, {"f.py": "1\n"}, subject="feat: add thing")
        return check_commitmsg
    ok &= _run_self_case("COMMIT-MSG match -> pass", PASS,
                         {"INPUT_COMMIT_PATTERN": r"^(feat|fix): "}, setup=commitmsg_ok)

    def commitmsg_bad(tmp):
        _init_git_repo(tmp, {"f.py": "1\n"}, subject="random change")
        return check_commitmsg
    ok &= _run_self_case("COMMIT-MSG no match -> fail", FAIL,
                         {"INPUT_COMMIT_PATTERN": r"^(feat|fix): "}, setup=commitmsg_bad)

    # BYPASS (token in the latest commit message)
    def bypass_present(tmp):
        _init_git_repo(tmp, {"x.py": "1\n"}, subject="[kronos skip] hotfix")
        return lambda: ("BYPASS", PASS if bypass_token() else FAIL, "")
    ok &= _run_self_case("BYPASS token present -> detected", PASS, {}, setup=bypass_present)

    def bypass_absent(tmp):
        _init_git_repo(tmp, {"x.py": "1\n"}, subject="normal commit")
        return lambda: ("BYPASS", PASS if bypass_token() else FAIL, "")
    ok &= _run_self_case("BYPASS absent -> none", FAIL, {}, setup=bypass_absent)

    # WORKFLOW discipline check (verify a committed WORKFLOW.md)
    def _make_wf(tmp, stages, test_log="", docs="", decisions="", with_plan=False, typ="MEDIUM"):
        if with_plan:
            os.makedirs(os.path.join(tmp, "plans"), exist_ok=True)
            with open(os.path.join(tmp, "plans", "p.md"), "w", encoding="utf-8") as fh:
                fh.write("\n".join(f"l{i}" for i in range(30)))
        body = (f"**Type:** {typ}\n\n## Stages\n\n{stages}\n\n"
                f"## Test log\n\n{test_log}\n\n## Docs updated\n\n{docs}\n\n"
                f"## Decisions log\n\n{decisions}\n")
        with open(os.path.join(tmp, "WORKFLOW.md"), "w", encoding="utf-8") as fh:
            fh.write(body)
        return check_workflow

    def wf_honest(tmp):
        return _make_wf(tmp,
            "- [x] 1. **PLAN** -> p\n- [x] 2. **CODE** -> c\n- [x] 3. **TEST** -> t\n"
            "- [x] 4. **DOCS** -> d\n- [x] 5. **COMMIT** -> abc1234",
            test_log="l1\nl2\nl3\nl4\nl5", docs="- README.md", with_plan=True)
    ok &= _run_self_case("WORKFLOW honest -> pass", PASS,
                         {"INPUT_WORKFLOW_FILE": "WORKFLOW.md"}, setup=wf_honest)

    def wf_lying(tmp):
        return _make_wf(tmp, "- [x] 3. **TEST** -> t", test_log="")
    ok &= _run_self_case("WORKFLOW TEST[x] + empty log -> fail", FAIL,
                         {"INPUT_WORKFLOW_FILE": "WORKFLOW.md"}, setup=wf_lying)

    def wf_open(tmp):
        return _make_wf(tmp, "- [x] 1. **PLAN** -> p\n- [ ] 2. **CODE** -> c", with_plan=True)
    ok &= _run_self_case("WORKFLOW open stage -> fail", FAIL,
                         {"INPUT_WORKFLOW_FILE": "WORKFLOW.md"}, setup=wf_open)

    def wf_skip(tmp):
        return _make_wf(tmp, "- [x] 1. **PLAN** -> p\n- [⊘] 3. **TEST** -> t",
                        decisions="SKIPPED stage 3: trivial", with_plan=True)
    ok &= _run_self_case("WORKFLOW skip-with-reason -> pass", PASS,
                         {"INPUT_WORKFLOW_FILE": "WORKFLOW.md"}, setup=wf_skip)

    def wf_trivial(tmp):
        return _make_wf(tmp, "- [ ] 1. **PLAN** -> p\n- [x] 2. **CODE** -> c\n- [ ] 3. **TEST** -> t",
                        typ="TRIVIAL")
    ok &= _run_self_case("WORKFLOW trivial relaxes PLAN/TEST -> pass", PASS,
                         {"INPUT_WORKFLOW_FILE": "WORKFLOW.md"}, setup=wf_trivial)

    # profile + type resolution
    ok &= _run_self_case("profile=strict requires plan -> fail", FAIL,
                         {"INPUT_PROFILE": "strict", "_check": check_plan})
    ok &= _run_self_case("type=trivial relaxes plan -> skip", SKIP,
                         {"INPUT_PROFILE": "strict", "INPUT_TYPE": "trivial", "_check": check_plan})

    # COVERAGE (parse a percentage out of the runner's output)
    ok &= _run_self_case("COVERAGE 95% >= 80 -> pass", PASS,
                         {"INPUT_COVERAGE_COMMAND": _py("print('TOTAL 120 6 95%')"),
                          "INPUT_COVERAGE_MIN": "80", "_check": check_coverage})
    ok &= _run_self_case("COVERAGE 42% < 80 -> fail", FAIL,
                         {"INPUT_COVERAGE_COMMAND": _py("print('TOTAL 120 70 42%')"),
                          "INPUT_COVERAGE_MIN": "80", "_check": check_coverage})
    ok &= _run_self_case("COVERAGE no percentage -> fail", FAIL,
                         {"INPUT_COVERAGE_COMMAND": _py("print('no numbers here')"),
                          "INPUT_COVERAGE_MIN": "80", "_check": check_coverage})

    # SECRETS (the fixture key is built at runtime so this source never matches itself)
    def secrets_hit(tmp):
        _init_git_repo(tmp, {"config.py": "aws = '" + "AKIA" + "X" * 16 + "'\n"})
        return check_secrets
    ok &= _run_self_case("SECRETS AWS-shaped key -> fail", FAIL, {}, setup=secrets_hit)

    def secrets_clean(tmp):
        _init_git_repo(tmp, {"config.py": "value = 42\n"})
        return check_secrets
    ok &= _run_self_case("SECRETS clean diff -> pass", PASS, {}, setup=secrets_clean)
    ok &= _run_self_case("SECRETS disabled -> skip", SKIP,
                         {"INPUT_SECRETS_SCAN": "false", "_check": check_secrets})

    # SIZE (PR bloat guard)
    def three_lines(tmp):
        _init_git_repo(tmp, {"f.py": "a = 1\nb = 2\nc = 3\n"})
        return check_size
    ok &= _run_self_case("SIZE over limit -> advisory", ADVISORY,
                         {"INPUT_SIZE_MAX_LINES": "1"}, setup=three_lines)
    ok &= _run_self_case("SIZE over limit + required -> fail", FAIL,
                         {"INPUT_SIZE_MAX_LINES": "1", "INPUT_SIZE_REQUIRED": "true"},
                         setup=three_lines)
    ok &= _run_self_case("SIZE under limit -> pass", PASS,
                         {"INPUT_SIZE_MAX_LINES": "100"}, setup=three_lines)

    # init (bootstrap generator)
    def init_creates(tmp):
        with open(os.path.join(tmp, "pyproject.toml"), "w", encoding="utf-8") as fh:
            fh.write("[tool]\n")
        def probe():
            cmd_init()
            made = (os.path.exists(os.path.join(tmp, ".github", "workflows", "kronos.yml"))
                    and os.path.exists(os.path.join(tmp, ".kronos-ci.env")))
            return ("INIT", PASS if made else FAIL, "")
        return probe
    ok &= _run_self_case("init creates workflow + env", PASS, {}, setup=init_creates)

    def init_keeps(tmp):
        env_path = os.path.join(tmp, ".kronos-ci.env")
        with open(env_path, "w", encoding="utf-8") as fh:
            fh.write("# mine\n")
        def probe():
            cmd_init()
            with open(env_path, encoding="utf-8") as fh:
                kept = fh.read() == "# mine\n"
            return ("INIT", PASS if kept else FAIL, "")
        return probe
    ok &= _run_self_case("init never overwrites", PASS, {}, setup=init_keeps)

    # --json machine contract (the last stdout line parses as JSON)
    script = os.path.abspath(__file__)
    def json_contract(tmp):
        def probe():
            import json
            env = {k: v for k, v in os.environ.items() if not k.startswith("INPUT_")}
            proc = subprocess.run([sys.executable, script, "verify", "--json"],
                                  cwd=tmp, capture_output=True, text=True, env=env, timeout=120)
            try:
                data = json.loads(proc.stdout.strip().splitlines()[-1])
                good = data.get("result") == "pass" and len(data.get("checks", [])) == 10
            except Exception:
                good = False
            return ("JSON", PASS if good else FAIL, "")
        return probe
    ok &= _run_self_case("--json: last line is valid JSON (10 checks)", PASS, {}, setup=json_contract)

    # command-timeout (a hung command fails, not hangs)
    ok &= _run_self_case("command-timeout kills a hung test -> fail", FAIL,
                         {"INPUT_TEST_COMMAND": _py("import time;time.sleep(5)"),
                          "INPUT_COMMAND_TIMEOUT": "1", "_check": check_test})

    # PR number parsing (no network)
    ok &= _run_self_case("PR number parsed from GITHUB_REF", PASS,
                         {"GITHUB_REF": "refs/pull/77/merge",
                          "_check": lambda: ("PR", PASS if _pr_number() == "77" else FAIL, "")})

    print(f"\n=== Self-test: {'ALL CHECKS PASSED' if ok else 'FAILED'} ===")
    return 0 if ok else 1


# ──────────────────────────── init (project bootstrap) ────────────────────────────

_STACK_HINTS = [
    ("pyproject.toml", "pytest -q"), ("pytest.ini", "pytest -q"),
    ("setup.py", "pytest -q"), ("setup.cfg", "pytest -q"),
    ("package.json", "npm test"), ("go.mod", "go test ./..."), ("Cargo.toml", "cargo test"),
]

_INIT_WORKFLOW = """name: KRONOS CI
on: [pull_request]
jobs:
  kronos:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with: {{ fetch-depth: 0 }}
      - uses: dzylab/kronos-ci@v{version}
        with:
          test-command: "{test_command}"
"""

_INIT_ENV = """# KRONOS CI local settings (sourced by the git hook installed via install.sh).
export INPUT_TEST_COMMAND="{test_command}"
"""


def _detect_test_command():
    for marker, command in _STACK_HINTS:
        if os.path.exists(os.path.join(_repo_root(), marker)):
            return command
    return "echo 'set your test command' && exit 1"


def cmd_init():
    """Generate a starter workflow + local config. Never overwrites existing files."""
    root = _repo_root()
    test_command = _detect_test_command()
    created = []
    wf_path = os.path.join(root, ".github", "workflows", "kronos.yml")
    if not os.path.exists(wf_path):
        os.makedirs(os.path.dirname(wf_path), exist_ok=True)
        with open(wf_path, "w", encoding="utf-8") as fh:
            fh.write(_INIT_WORKFLOW.format(version=__version__, test_command=test_command))
        created.append(wf_path)
    env_path = os.path.join(root, ".kronos-ci.env")
    if not os.path.exists(env_path):
        with open(env_path, "w", encoding="utf-8") as fh:
            fh.write(_INIT_ENV.format(test_command=test_command))
        created.append(env_path)
    if created:
        print("kronos-ci: created:")
        for path in created:
            print(f"  {path}")
        print(f"kronos-ci: detected test command: {test_command}")
        print("kronos-ci: review the files, adjust the test command, commit. Done.")
    else:
        print("kronos-ci: nothing to do — both files already exist (never overwritten).")
    return 0


# ──────────────────────────── entry point ────────────────────────────

HELP = """kronos-ci — verify PR artifacts (tests, coverage, lint, plan, docs, secrets, size, workflow).

Usage:
  kronos_ci.py [verify] [--json]  run the gate (default). Config via INPUT_* env vars.
                                  --json: the LAST stdout line is a machine-readable JSON object.
  kronos_ci.py init               generate .github/workflows/kronos.yml + .kronos-ci.env (stack auto-detected).
  kronos_ci.py --self-test        run internal hermetic tests.
  kronos_ci.py --version          print the version.
  kronos_ci.py --help             this message.

Exit code 0 = all required checks passed, 1 = at least one required check failed.
"""


def main(argv):
    args = argv[1:] or ["verify"]
    arg = args[0]
    if arg in ("--self-test", "selftest"):
        return self_test()
    if arg in ("--version", "-v"):
        print(f"kronos-ci {__version__}")
        return 0
    if arg in ("--help", "-h", "help"):
        print(HELP)
        return 0
    if arg == "init":
        return cmd_init()
    if arg in ("verify", "--json"):
        return verify(json_mode="--json" in args)
    print(f"unknown argument: {arg}\n", file=sys.stderr)
    print(HELP, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
