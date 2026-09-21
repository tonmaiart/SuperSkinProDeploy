# Shared helper for the `release-*` scripts (release-stage.sh, release-app.sh,
# release-docs.sh). Sourced, not executed directly.
#
# Previously each script hard-failed and told the developer to fix things by
# hand whenever the working tree was dirty or the branch was ahead/behind
# `origin`. That's still the right call for history that has genuinely
# diverged (auto-merging there risks silently resolving conflicts wrong), but
# "forgot to commit" and "forgot to push" are the overwhelmingly common case
# and don't need a human in the loop -- this makes those two self-heal.
#
# Usage: ensure_committed_and_pushed <branch> <commit-message> [pathspec...]
#   <branch>          branch to sync against origin (e.g. "main", or the
#                      current branch via `git rev-parse --abbrev-ref HEAD`)
#   <commit-message>  message used only if there turn out to be uncommitted
#                      changes to commit
#   [pathspec...]     restrict the dirty-check/commit to these paths (e.g.
#                      "documents/"); defaults to the whole working tree
#
# Exits non-zero (without pushing/pulling/committing further) if `origin`
# and `<branch>` have genuinely diverged -- that always needs a human.
ensure_committed_and_pushed() {
    local branch="$1"; shift
    local commit_message="$1"; shift
    local pathspec=("$@")
    if [ ${#pathspec[@]} -eq 0 ]; then
        pathspec=(".")
    fi

    if [ -n "$(git status --porcelain -- "${pathspec[@]}")" ]; then
        echo "📝 Uncommitted changes found -- committing automatically:"
        git status --short -- "${pathspec[@]}"
        git add -- "${pathspec[@]}"
        git commit --quiet -m "$commit_message"
        echo "✅ Committed as $(git rev-parse --short HEAD)."
    fi

    echo "🔄 Fetching origin/${branch}..."
    git fetch origin "$branch" --quiet

    local local_rev remote_rev base_rev
    local_rev="$(git rev-parse "$branch")"
    remote_rev="$(git rev-parse "origin/${branch}")"
    base_rev="$(git merge-base "$branch" "origin/${branch}")"

    if [ "$local_rev" = "$remote_rev" ]; then
        echo "✅ '${branch}' is up to date with 'origin/${branch}'."
    elif [ "$remote_rev" = "$base_rev" ]; then
        echo "⬆️  '${branch}' has unpushed commits -- pushing to origin..."
        git push origin "$branch"
    elif [ "$local_rev" = "$base_rev" ]; then
        echo "⬇️  '${branch}' is behind 'origin/${branch}' -- fast-forwarding (no local changes to lose)..."
        git merge --ff-only "origin/${branch}"
    else
        echo "❌ '${branch}' and 'origin/${branch}' have diverged -- this needs a human, not autopilot:"
        echo "   git pull --rebase origin ${branch}"
        exit 1
    fi
}
