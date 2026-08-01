#!/usr/bin/env bash
# Backing script for the `git commit-release` alias (see scripts/README.md
# for the one-time `git config` command that wires the alias to this file).
#
# Does NOT build or commit anything itself -- it only force-moves a tag to
# the current tip of `main` and pushes it to `origin` (the dev repo), after
# verifying `main` is clean and fully in sync with `origin/main`. Pushing
# that tag is what .github/workflows/release.yml's
# `on: push: tags: ["[0-9]+.[0-9]+.[0-9]+"]` trigger is watching for; the
# actual cross-platform Rust build + dev-file cleanup + force-push of the
# cleaned result (as `main`, tagged with the same version) to
# https://github.com/tonmaiart/SuperSkinPro.git all happen there, on
# GitHub's runners, not on this machine. There is no more `release` branch
# anywhere in this pipeline.
#
# Usage:
#   git commit-release            # tag name = blender_manifest.toml's version
#   git commit-release 1.0.4      # explicit tag name, overrides the manifest
#                                  # version and force-moves/pushes THAT tag
#                                  # instead (e.g. to re-trigger a release for
#                                  # an already-used version number).
set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"

BRANCH="$(git rev-parse --abbrev-ref HEAD)"
if [ "$BRANCH" != "main" ]; then
    echo "❌ commit-release must be run from 'main' (currently on '$BRANCH')."
    exit 1
fi

if [ -n "$(git status --porcelain)" ]; then
    echo "❌ Working tree is not clean -- commit or stash your changes first:"
    git status --short
    exit 1
fi

echo "🔄 Fetching origin/main..."
git fetch origin main --quiet

LOCAL="$(git rev-parse main)"
REMOTE="$(git rev-parse origin/main)"
BASE="$(git merge-base main origin/main)"

if [ "$LOCAL" = "$REMOTE" ]; then
    echo "✅ 'main' is up to date with 'origin/main' at ${LOCAL:0:7}."
elif [ "$LOCAL" = "$BASE" ]; then
    echo "❌ 'main' is behind 'origin/main'. Pull first: git pull origin main"
    exit 1
elif [ "$REMOTE" = "$BASE" ]; then
    echo "❌ 'main' has unpushed commits. Push first: git push origin main"
    exit 1
else
    echo "❌ 'main' and 'origin/main' have diverged -- reconcile before releasing."
    exit 1
fi

TAG_OVERRIDE="${1:-}"

if [ -n "$TAG_OVERRIDE" ]; then
    VERSION="$TAG_OVERRIDE"
    if ! [[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
        echo "⚠️  '${VERSION}' doesn't look like X.Y.Z -- the release workflow's"
        echo "   tag trigger may not fire. Continuing anyway since it was explicit."
    fi
    echo "ℹ️  Using explicit tag '${VERSION}' (overrides blender_manifest.toml)."
else
    VERSION="$(grep -m1 '^version' blender_manifest.toml | sed -E 's/^version[[:space:]]*=[[:space:]]*"([^"]+)".*/\1/')"
    if [ -z "$VERSION" ]; then
        echo "❌ Could not read 'version' from blender_manifest.toml."
        exit 1
    fi
fi

echo "🏷️  Moving '${VERSION}' tag to ${LOCAL:0:7} and pushing to origin..."
git tag -f "$VERSION" "$LOCAL"
git push origin "$VERSION" --force

ORIGIN_URL="$(git remote get-url origin)"
REPO_HTTPS="${ORIGIN_URL%.git}"
REPO_HTTPS="${REPO_HTTPS/git@github.com:/https:\/\/github.com\/}"

echo "🚀 Pushed tag '${VERSION}' -- GitHub Actions will now build Rust for"
echo "   all 3 OSes, clean up dev-only files, and force-push the result as"
echo "   'main' (tagged '${VERSION}') to https://github.com/tonmaiart/SuperSkinPro.git."
echo "   Watch progress: ${REPO_HTTPS}/actions"
