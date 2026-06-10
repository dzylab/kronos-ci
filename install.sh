#!/usr/bin/env sh
# KRONOS CI — install a local git hook that runs the gate before you push.
#
# Works in ANY editor or AI tool (Cursor, Windsurf, VS Code, a plain terminal, ...) because the
# hook lives at the GIT layer, not inside the editor. It does not matter who wrote the code.
#
# Usage — run this from inside your project's git repository:
#   /path/to/kronos-ci/install.sh             # installs a pre-push hook (recommended)
#   /path/to/kronos-ci/install.sh pre-commit  # installs a pre-commit hook instead
#
# Configure it by copying .kronos-ci.env.example to .kronos-ci.env in your repo root.

set -e

HOOK_TYPE="${1:-pre-push}"
case "$HOOK_TYPE" in
  pre-push | pre-commit) ;;
  *) echo "kronos-ci: unknown hook type '$HOOK_TYPE' (use pre-push or pre-commit)"; exit 1 ;;
esac

# Absolute path to kronos_ci.py (it sits next to this installer).
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
KRONOS_CI="$SCRIPT_DIR/kronos_ci.py"
[ -f "$KRONOS_CI" ] || { echo "kronos-ci: kronos_ci.py not found next to install.sh"; exit 1; }

# The target repo's hooks directory (run this from inside your project).
GIT_DIR=$(git rev-parse --git-dir 2>/dev/null) || { echo "kronos-ci: run this inside a git repository"; exit 1; }
HOOKS_DIR="$GIT_DIR/hooks"
mkdir -p "$HOOKS_DIR"
HOOK="$HOOKS_DIR/$HOOK_TYPE"

# Back up a pre-existing, non-KRONOS hook so we never clobber the user's own.
if [ -e "$HOOK" ] && ! grep -q "KRONOS CI" "$HOOK" 2>/dev/null; then
  mv "$HOOK" "$HOOK.bak"
  echo "kronos-ci: backed up your existing $HOOK_TYPE hook to $HOOK.bak"
fi

cat > "$HOOK" <<EOF
#!/usr/bin/env sh
# KRONOS CI local gate (installed by kronos-ci/install.sh). Settings: .kronos-ci.env in the repo root.
[ -f ".kronos-ci.env" ] && . ./.kronos-ci.env
if command -v python >/dev/null 2>&1; then PY=python; else PY=python3; fi
"\$PY" "$KRONOS_CI" verify
EOF
chmod +x "$HOOK"

echo "kronos-ci: installed the $HOOK_TYPE hook -> $HOOK"
echo "kronos-ci: next, copy .kronos-ci.env.example to .kronos-ci.env and set your test-command."
echo "kronos-ci: it will now run on every 'git $( [ "$HOOK_TYPE" = pre-commit ] && echo commit || echo push )' from any editor."
