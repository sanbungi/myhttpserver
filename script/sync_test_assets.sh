#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  script/sync_test_assets.sh [options]

Options:
  --source-dir <path>   Source repo path (default: ../myhttpserver-test-asset)
  --branch <name>       Source branch/ref to sync (default: main)
  --commit              Commit gitlink update in parent repo
  --message <text>      Commit message used with --commit
  -h, --help            Show this help

Behavior:
  1) Fetch source repo ref into submodule test-assets
  2) Checkout source commit in submodule
  3) Stage test-assets in parent repo
  4) Optionally commit parent gitlink update
EOF
}

SOURCE_DIR=""
SOURCE_BRANCH="main"
DO_COMMIT=0
COMMIT_MESSAGE=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --source-dir)
      SOURCE_DIR="${2:-}"
      shift 2
      ;;
    --branch)
      SOURCE_BRANCH="${2:-}"
      shift 2
      ;;
    --commit)
      DO_COMMIT=1
      shift
      ;;
    --message)
      COMMIT_MESSAGE="${2:-}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage
      exit 2
      ;;
  esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SUBMODULE_NAME="test-assets"
SUBMODULE_DIR="$REPO_ROOT/$SUBMODULE_NAME"

if [[ -z "$SOURCE_DIR" ]]; then
  SOURCE_DIR="$REPO_ROOT/../myhttpserver-test-asset"
fi
SOURCE_DIR="$(cd "$SOURCE_DIR" && pwd)"

if ! git -C "$REPO_ROOT" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "Parent repo not found: $REPO_ROOT" >&2
  exit 1
fi

if ! git -C "$SOURCE_DIR" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "Source repo not found: $SOURCE_DIR" >&2
  exit 1
fi

if ! git -C "$SUBMODULE_DIR" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "Submodule is not initialized: $SUBMODULE_DIR" >&2
  exit 1
fi

if [[ -n "$(git -C "$SOURCE_DIR" status --porcelain)" ]]; then
  echo "Source repo has uncommitted changes: $SOURCE_DIR" >&2
  echo "Commit or stash source changes before sync." >&2
  exit 1
fi

SOURCE_COMMIT="$(git -C "$SOURCE_DIR" rev-parse "$SOURCE_BRANCH")"
SOURCE_SHORT="$(git -C "$SOURCE_DIR" rev-parse --short "$SOURCE_COMMIT")"

echo "[sync] source: $SOURCE_DIR @ $SOURCE_BRANCH ($SOURCE_SHORT)"

git -C "$SUBMODULE_DIR" fetch "$SOURCE_DIR" "$SOURCE_BRANCH" >/dev/null
git -C "$SUBMODULE_DIR" checkout "$SOURCE_COMMIT" >/dev/null

if [[ -z "$(git -C "$REPO_ROOT" status --porcelain -- "$SUBMODULE_NAME")" ]]; then
  echo "[sync] no gitlink change"
  exit 0
fi

git -C "$REPO_ROOT" add "$SUBMODULE_NAME"
echo "[sync] staged: $SUBMODULE_NAME -> $SOURCE_SHORT"

if [[ "$DO_COMMIT" -eq 1 ]]; then
  if [[ -z "$COMMIT_MESSAGE" ]]; then
    COMMIT_MESSAGE="test-assets submoduleを${SOURCE_SHORT}へ更新"
  fi
  git -C "$REPO_ROOT" commit -m "$COMMIT_MESSAGE" -- "$SUBMODULE_NAME"
  echo "[sync] committed: $COMMIT_MESSAGE"
else
  echo "[sync] commit skipped (use --commit)"
fi
