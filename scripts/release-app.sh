#!/usr/bin/env bash
# Backing script for the `git release-app` alias (setup: scripts/README.md).
# Ships the current tip of `main` to production in one step: force-moves the
# version tag to HEAD, waits for release.yml to build it into
# tonmaiart/SuperSkinProDeploy, then runs promote.yml and waits for it.
# Every run rebuilds from HEAD, so a stale staged build can never be promoted.
#
# Usage:
#   git release-app            # version from blender_manifest.toml
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

BRANCH="$(git rev-parse --abbrev-ref HEAD)"
if [ "$BRANCH" != "main" ]; then
    echo "❌ release-app must be run from 'main' (currently on '$BRANCH')."
    exit 1
fi

source "$REPO_ROOT/scripts/lib/git-sync.sh"
ensure_committed_and_pushed "main" "chore: auto-commit before release-app"

TAG_OVERRIDE="${1:-}"

if [ -n "$TAG_OVERRIDE" ]; then
    VERSION="$TAG_OVERRIDE"
    if ! [[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
        echo "❌ '${VERSION}' is not X.Y.Z; release.yml's tag trigger would not fire."
        exit 1
    fi
    echo "ℹ️  Using explicit tag '${VERSION}' (overrides blender_manifest.toml)."
else
    VERSION="$(grep -m1 '^version' blender_manifest.toml | sed -E 's/^version[[:space:]]*=[[:space:]]*"([^"]+)".*/\1/')"
    if [ -z "$VERSION" ]; then
        echo "❌ Could not read 'version' from blender_manifest.toml."
        exit 1
    fi
fi

HEAD_SHA="$(git rev-parse main)"
DEPLOY_REPO="https://github.com/tonmaiart/SuperSkinProDeploy.git"

is_staged() {
    git ls-remote --exit-code --tags "$DEPLOY_REPO" "refs/tags/${VERSION}" >/dev/null 2>&1
}

# Newest release.yml run for this tag at HEAD started at or after $1, as "id status conclusion".
find_run() {
    gh run list --workflow=release.yml --event push --limit 20 \
        --json databaseId,headBranch,headSha,createdAt,status,conclusion \
        --jq ".[] | select(.headBranch == \"${VERSION}\" and .headSha == \"${HEAD_SHA}\" and .createdAt >= \"$1\") | \"\(.databaseId) \(.status) \(.conclusion)\"" \
        | head -n1
}

echo "🏷️  Releasing '${VERSION}' from ${HEAD_SHA:0:7}."
git tag -f "$VERSION" "$HEAD_SHA" >/dev/null

REMOTE_TAG_SHA="$(git ls-remote origin "refs/tags/${VERSION}" | cut -f1)"
RUN_ID=""
if [ "$REMOTE_TAG_SHA" = "$HEAD_SHA" ]; then
    # Re-pushing an unchanged tag fires no workflow, so reuse a successful build or force a fresh push.
    read -r PREV_ID PREV_STATUS PREV_CONCLUSION <<<"$(find_run "1970-01-01T00:00:00Z") _ _ _"
    if [ "$PREV_STATUS" = "completed" ] && [ "$PREV_CONCLUSION" = "success" ] && is_staged; then
        echo "✅ ${HEAD_SHA:0:7} is already built and staged (run ${PREV_ID}) -- skipping the rebuild."
    elif [ "$PREV_STATUS" = "in_progress" ] || [ "$PREV_STATUS" = "queued" ]; then
        RUN_ID="$PREV_ID"
    else
        START="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
        git push origin ":refs/tags/${VERSION}"
        git push origin "refs/tags/${VERSION}"
    fi
else
    START="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    git push origin "refs/tags/${VERSION}" --force
fi

if [ -n "${START:-}" ]; then
    echo "⏳ Waiting for the release.yml run for '${VERSION}' to appear..."
    for _ in $(seq 1 60); do
        RUN_ID="$(find_run "$START" | cut -d' ' -f1)"
        [ -n "$RUN_ID" ] && break
        sleep 5
    done
    if [ -z "$RUN_ID" ]; then
        echo "❌ No release.yml run for '${VERSION}' showed up within 5 minutes. Check the Actions tab."
        exit 1
    fi
fi

if [ -n "$RUN_ID" ]; then
    echo "🏗️  Building all platforms (run ${RUN_ID})..."
    if ! gh run watch "$RUN_ID" --exit-status --interval 30; then
        echo "❌ Build failed -- not promoting. See: gh run view ${RUN_ID} --log-failed"
        exit 1
    fi
    if ! is_staged; then
        echo "❌ release.yml finished but tag '${VERSION}' is missing on SuperSkinProDeploy -- not promoting."
        exit 1
    fi
fi

echo "🚀 Promoting '${VERSION}' to PRODUCTION (tonmaiart/SuperSkinPro)..."
PROMOTE_START="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
gh workflow run promote.yml -f "tag=${VERSION}"

PROMOTE_ID=""
for _ in $(seq 1 24); do
    PROMOTE_ID="$(gh run list --workflow=promote.yml --event workflow_dispatch --limit 5 \
        --json databaseId,createdAt \
        --jq ".[] | select(.createdAt >= \"${PROMOTE_START}\") | .databaseId" | head -n1)"
    [ -n "$PROMOTE_ID" ] && break
    sleep 5
done
if [ -z "$PROMOTE_ID" ]; then
    echo "⚠️  promote.yml was dispatched but its run did not show up within 2 minutes. Check the Actions tab."
    exit 1
fi

if ! gh run watch "$PROMOTE_ID" --exit-status --interval 10; then
    echo "❌ Promotion failed. See: gh run view ${PROMOTE_ID} --log-failed"
    exit 1
fi
echo "✅ '${VERSION}' (${HEAD_SHA:0:7}) is live. Users' in-app updater will pick it up on next check."
