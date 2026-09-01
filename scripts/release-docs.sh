#!/usr/bin/env bash
# Backing script for the `git release-docs` alias (see scripts/README.md
# for the one-time `git config` command that wires the alias to this file).
#
# Builds the mkdocs site under documents/ and publishes it to
# https://github.com/tonmaiart/superskinpro-docs.git (main) -- the repo
# that (per its existing content: docs/, mkdocs.yml, requirements.txt,
# site/ all committed straight to main, no GitHub Actions workflow) already
# backs the live https://docs.superskinpro.com/ site. documents/ here has
# no shared git history with that repo (it was flattened in from the
# former standalone superskinpro-docs checkout, see git log around "Add
# documents"), so this pushes a fresh orphan commit and force-pushes it --
# the same pattern .github/workflows/release.yml and promote.yml already
# use to publish this project's addon release/staging repos, just run
# locally instead of in CI.
#
# Usage:
#   git release-docs
set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"

DOCS_DIR="$REPO_ROOT/documents"
DOCS_REMOTE="https://github.com/tonmaiart/superskinpro-docs.git"

if [ ! -d "$DOCS_DIR" ]; then
    echo "❌ documents/ not found at $DOCS_DIR."
    exit 1
fi

# Scoped to documents/ only (not the whole repo) -- unrelated addon-code
# work-in-progress elsewhere in the tree shouldn't block a docs release.
if [ -n "$(git status --porcelain -- documents/)" ]; then
    echo "❌ documents/ has uncommitted changes -- commit or stash them first:"
    git status --short -- documents/
    exit 1
fi

VERSION="$(grep -m1 '^version' blender_manifest.toml | sed -E 's/^version[[:space:]]*=[[:space:]]*"([^"]+)".*/\1/')"
TIMESTAMP="$(date -u +"%Y-%m-%d %H:%M UTC")"

echo "📦 Installing docs dependencies (documents/requirements.txt)..."
python -m pip install -q -r "$DOCS_DIR/requirements.txt"

echo "🏗️  Building the site (documents/ -> documents/site/)..."
( cd "$DOCS_DIR" && python -m mkdocs build )

echo "🚚 Assembling the publish snapshot (docs/, mkdocs.yml, requirements.txt, README.md, site/)..."
PUBLISH_DIR="$(mktemp -d)"
trap 'rm -rf "$PUBLISH_DIR"' EXIT

cp -r "$DOCS_DIR/docs" "$PUBLISH_DIR/docs"
cp "$DOCS_DIR/mkdocs.yml" "$PUBLISH_DIR/mkdocs.yml"
cp "$DOCS_DIR/requirements.txt" "$PUBLISH_DIR/requirements.txt"
cp "$DOCS_DIR/README.md" "$PUBLISH_DIR/README.md"
cp -r "$DOCS_DIR/site" "$PUBLISH_DIR/site"
if [ -d "$DOCS_DIR/.claude" ]; then
    cp -r "$DOCS_DIR/.claude" "$PUBLISH_DIR/.claude"
fi

echo "🏷️  Committing snapshot (addon version ${VERSION:-unknown}, built ${TIMESTAMP})..."
(
    cd "$PUBLISH_DIR"
    git init --quiet
    git add -A
    git -c user.name="$(git -C "$REPO_ROOT" config user.name)" \
        -c user.email="$(git -C "$REPO_ROOT" config user.email)" \
        commit --quiet -m "docs: release (addon v${VERSION:-unknown}, built ${TIMESTAMP})"
    git branch -M main
)

echo "🚀 Force-pushing to ${DOCS_REMOTE} (main)..."
( cd "$PUBLISH_DIR" && git push "$DOCS_REMOTE" "main:main" --force )

echo "✅ Published. If https://docs.superskinpro.com/ doesn't rebuild on its own"
echo "   from this push, check whatever's actually serving that domain"
echo "   (no GitHub Pages / Actions config was found on the superskinpro-docs"
echo "   repo itself -- it's most likely a third-party static host watching"
echo "   this repo's main branch)."
