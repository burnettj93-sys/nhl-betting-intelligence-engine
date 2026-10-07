#!/bin/sh
# Pins the scheduled jobs' execution checkout (~/nhl_engine_release, a detached git worktree) to ONE commit.
# Scheduled launchd jobs run from there; edit and test in the other checkout, then promote deliberately:
#   deploy/release_checkout.sh <commit-ish>
# Runtime state (databases, odds archive, logs, caches, .env, raw data) is shared by symlink, so promoting code
# never moves or copies state. Every recorded ticket stores the release commit it was staked by.
set -eu
REPO="$(cd "$(dirname "$0")/.." && pwd)"
RELEASE="${NHL_RELEASE_DIR:-$HOME/nhl_engine_release}"
COMMIT="$(git -C "$REPO" rev-parse --verify "$1^{commit}")"
git -C "$RELEASE" checkout --detach "$COMMIT"
echo "release checkout $RELEASE is now at $(git -C "$RELEASE" rev-parse --short=10 HEAD)"
