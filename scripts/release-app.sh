#!/usr/bin/env bash
# Backing script for the `git release-app` alias (see scripts/README.md for
# the one-time `git config` command that wires the alias to this file).
# Pairs with `git release-stage` (scripts/release-stage.sh), which deploys
# to staging first -- this is the separate, manual, second half that
# actually promotes an already-staged, already-tested build to production.
#
# Does NOT build, rebuild, or re-test anything itself -- it only wraps
# `gh workflow run promote.yml -f tag=<version>`, auto-reading the version
# from blender_manifest.toml so you don't have to type the `-f tag=X.Y.Z`
# flag by hand. Before dispatching, it checks that the tag actually exists
# on tonmaiart/SuperSkinProDeploy (staging) -- if `git release-stage` was
# never run for this version, promote.yml would just fail on GitHub's side
# with a less obvious error, so this catches that locally first.
#
# What promote.yml itself does (see .github/workflows/promote.yml and
# docs/release-workflow.md): checks out the exact already-tested commit
# from tonmaiart/SuperSkinProDeploy at the given tag, patches the
# staging-only identity (blender_manifest.toml's id/name, addon_updater's
# repo_config.json target) back to production values, and force-pushes the
# result to https://github.com/tonmaiart/SuperSkinPro.git -- the repo real
# users' in-app updater checks. This is the step that actually reaches
# users; only run it after installing and verifying the staged build.
#
# Usage:
#   git release-app            # promote blender_manifest.toml's version
#   git release-app 1.0.4      # explicit tag override
set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"

if ! command -v gh >/dev/null 2>&1; then
    echo "❌ GitHub CLI ('gh') not found. Install it: https://cli.github.com/"
    exit 1
fi

if ! gh auth status >/dev/null 2>&1; then
    echo "❌ 'gh' is not logged in. Run: gh auth login"
    exit 1
fi

TAG_OVERRIDE="${1:-}"

if [ -n "$TAG_OVERRIDE" ]; then
    VERSION="$TAG_OVERRIDE"
    echo "ℹ️  Using explicit tag '${VERSION}' (overrides blender_manifest.toml)."
else
    VERSION="$(grep -m1 '^version' blender_manifest.toml | sed -E 's/^version[[:space:]]*=[[:space:]]*"([^"]+)".*/\1/')"
    if [ -z "$VERSION" ]; then
        echo "❌ Could not read 'version' from blender_manifest.toml."
        exit 1
    fi
fi

echo "🔎 Checking '${VERSION}' was actually staged on tonmaiart/SuperSkinProDeploy..."
if ! git ls-remote --exit-code --tags "https://github.com/tonmaiart/SuperSkinProDeploy.git" "refs/tags/${VERSION}" >/dev/null 2>&1; then
    echo "❌ Tag '${VERSION}' not found on SuperSkinProDeploy yet."
    echo "   Run 'git release-stage' first, verify the staged build actually"
    echo "   works, and only then promote it."
    exit 1
fi
echo "✅ Found '${VERSION}' on staging."

echo "🚨 This promotes '${VERSION}' to PRODUCTION (tonmaiart/SuperSkinPro)."
echo "   Real users' in-app updater will detect it on their next launch/reload."
echo "🚀 Dispatching promote.yml..."
gh workflow run promote.yml -f "tag=${VERSION}"

echo ""
echo "   Watch progress with: gh run watch \$(gh run list --workflow=promote.yml --limit 1 --json databaseId --jq '.[0].databaseId')"
echo "   Or check the Actions tab on GitHub."
