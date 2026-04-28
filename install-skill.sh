#!/usr/bin/env bash
# Installs the gitlab-review-loop skill into the user-global Claude Code skills
# directory on macOS, so it's available from any project.
#
# Usage:
#   ./install-skill.sh                # install (or update) into ~/.claude/skills
#   ./install-skill.sh --uninstall    # remove
#   ./install-skill.sh --target DIR   # install into a custom skills directory
#   ./install-skill.sh --skip-deps    # skip glab/jq dependency checks
#
# Note: skills are a Claude Code (CLI) feature. Claude Desktop (the GUI app)
# does not load skills from ~/.claude/skills.

set -euo pipefail

SKILL_NAME="gitlab-review-loop"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &> /dev/null && pwd)"
SOURCE_FILE="$SCRIPT_DIR/.claude/skills/$SKILL_NAME/SKILL.md"
TARGET_ROOT="${HOME}/.claude/skills"
ACTION="install"
SKIP_DEPS=0

# Required runtime dependencies — `brew_pkg:command` pairs
REQUIRED_DEPS=(
  "glab:glab"   # GitLab CLI — used for `glab api`, `glab mr`, etc.
  "jq:jq"       # JSON processor — every skill snippet pipes through jq
)

while [[ $# -gt 0 ]]; do
  case "$1" in
    --uninstall) ACTION="uninstall"; shift ;;
    --target)    TARGET_ROOT="$2"; shift 2 ;;
    --skip-deps) SKIP_DEPS=1; shift ;;
    -h|--help)
      sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "warning: this script is intended for macOS — proceeding anyway" >&2
fi

if [[ ! -f "$SOURCE_FILE" ]]; then
  echo "error: SKILL.md not found at $SOURCE_FILE" >&2
  exit 1
fi

check_dependencies() {
  local missing=()
  for entry in "${REQUIRED_DEPS[@]}"; do
    local pkg="${entry%%:*}"
    local cmd="${entry##*:}"
    if command -v "$cmd" > /dev/null 2>&1; then
      echo "  ✓ $cmd ($($cmd --version 2>&1 | head -n1))"
    else
      echo "  ✗ $cmd — missing"
      missing+=("$pkg")
    fi
  done

  if [[ ${#missing[@]} -eq 0 ]]; then
    return 0
  fi

  echo
  echo "Missing dependencies: ${missing[*]}"

  if ! command -v brew > /dev/null 2>&1; then
    cat <<EOF
Homebrew not found. Install it first:
  /bin/bash -c "\$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

Then re-run this installer, or install dependencies manually:
  ${missing[*]/#/brew install }
EOF
    return 1
  fi

  # `|| true` absorbs read's exit-1-on-EOF under `set -euo pipefail`
  # (e.g. when stdin is /dev/null in CI or piped runs)
  read -r -p "Install missing packages with brew? [Y/n] " ans || true
  ans=${ans:-Y}
  if [[ "$ans" =~ ^[Yy]$ ]]; then
    brew install "${missing[@]}"
  else
    echo "Skipping dependency install. Re-run with --skip-deps to bypass these checks." >&2
    return 1
  fi
}

verify_glab_auth() {
  if ! command -v glab > /dev/null 2>&1; then
    return 0
  fi
  if glab auth status > /dev/null 2>&1; then
    echo "  ✓ glab authenticated"
  else
    cat <<EOF
  ⚠ glab is installed but not authenticated.
    Run:  glab auth login --hostname your.gitlab.host
    before using the skill.
EOF
  fi
}

TARGET_DIR="$TARGET_ROOT/$SKILL_NAME"

case "$ACTION" in
  install)
    if [[ "$SKIP_DEPS" -eq 0 ]]; then
      echo "Checking dependencies…"
      check_dependencies || exit 1
      verify_glab_auth
      echo
    fi

    mkdir -p "$TARGET_DIR"
    cp -f "$SOURCE_FILE" "$TARGET_DIR/SKILL.md"
    echo "✓ installed $SKILL_NAME → $TARGET_DIR/SKILL.md"
    echo
    echo "Verify:    ls -la $TARGET_DIR"
    echo "Use:       restart Claude Code; the skill will appear in the available-skills list"
    ;;
  uninstall)
    if [[ -d "$TARGET_DIR" ]]; then
      rm -rf "$TARGET_DIR"
      echo "✓ removed $TARGET_DIR"
    else
      echo "nothing to remove at $TARGET_DIR"
    fi
    ;;
esac
