#!/usr/bin/env bash
# VPS Production Deployment block (2026-09-24), Part 2/17.
#
# Idempotent. Creates the persistent-state directories described in
# docs/VPS_PRODUCTION_LAYOUT.md and symlinks every mutable path this
# codebase's REPO_ROOT-relative code already expects (nhl.db,
# operational/*.db, operational/odds_archive/, operational/backups/,
# operational/logs/, fantasy/storage/*, research/*/*.db, .env) into
# them. Zero application code changes -- this only rearranges where
# the files physically live.
#
# Safe to re-run: an existing symlink pointing at the right target is
# left alone; a REAL file already sitting at a symlink's target path is
# moved into data/ first (never overwritten, never deleted), then
# symlinked -- so running this against a first, not-yet-separated
# deploy preserves whatever real data already accumulated there.
#
# Usage:
#   BASE=/opt/nhl-engine APP=/opt/nhl-engine/app ./deploy/provision_layout.sh
#
# Does NOT install systemd units, does NOT install Caddy, does NOT
# start anything -- see docs/VPS_CUTOVER_RUNBOOK.md for the full
# sequence this script is one step of.

set -euo pipefail

BASE="${BASE:-/opt/nhl-engine}"
APP="${APP:-$BASE/app}"
DATA="$BASE/data"
LOGS="$BASE/logs"
BACKUPS="$BASE/backups"
SECRETS="$BASE/secrets"

echo "Provisioning layout under $BASE (app=$APP) ..."

# Deliberately does NOT pre-create any of the leaf directories link_path
# below will manage (operational/odds_archive, backups, logs) -- doing
# so here as well as inside link_path's own migration step is exactly
# the bug this script's own local test caught: `mv realdir target`
# renames realdir to target only when target does NOT already exist; if
# it does (because something pre-created it), `mv` instead nests
# realdir INSIDE target (target/realdir/...), silently misplacing real
# data one level too deep. link_path is solely responsible for creating
# its own target, in the branch where it knows whether a migration
# happened.
mkdir -p "$SECRETS"
chmod 700 "$SECRETS"

# link_path <path relative to $APP> <target absolute path> [--dir]
# --dir: target is a directory (operational/odds_archive, backups,
# logs), not a single file -- affects only how the target is created
# when there's nothing real at $link to migrate.
link_path() {
    local rel="$1" target="$2" is_dir="${3:-}"
    local link="$APP/$rel"
    mkdir -p "$(dirname "$link")"
    if [ -L "$link" ]; then
        return 0  # already a symlink -- assume a prior run of this script set it up correctly
    fi
    if [ -e "$link" ]; then
        echo "  real data found at $link -- moving it to $target before linking (data preserved)"
        mkdir -p "$(dirname "$target")"
        # $target must NOT already exist for `mv` to rename-in-place
        # rather than nest $link inside it (see the note above).
        if [ -e "$target" ]; then
            echo "    ERROR: $target already exists -- refusing to risk nesting real data. Resolve manually." >&2
            exit 1
        fi
        mv "$link" "$target"
    elif [ "$is_dir" = "--dir" ]; then
        mkdir -p "$target"
    else
        mkdir -p "$(dirname "$target")"
    fi
    ln -s "$target" "$link"
    echo "  linked $rel -> $target"
}

link_path "nhl.db"                                             "$DATA/nhl.db"
link_path "operational/paper_bankroll.db"                      "$DATA/operational/paper_bankroll.db"
link_path "operational/auth_store.db"                          "$DATA/operational/auth_store.db"
link_path "operational/prospective_observations.db"            "$DATA/operational/prospective_observations.db"
link_path "operational/special_teams_history.db"                "$DATA/operational/special_teams_history.db"
link_path "operational/odds_archive"                           "$DATA/operational/odds_archive" --dir
link_path "operational/backups"                                "$BACKUPS" --dir
link_path "operational/logs"                                   "$LOGS" --dir
link_path "fantasy/storage/fantasy_store.db"                   "$DATA/fantasy/storage/fantasy_store.db"
link_path "fantasy/storage/yahoo_token.enc"                    "$DATA/fantasy/storage/yahoo_token.enc"
link_path "research/real_nhl_pbp/research_pbp.db"              "$DATA/research/real_nhl_pbp/research_pbp.db"
link_path "research/moneypuck_ingestion/research_moneypuck.db" "$DATA/research/moneypuck_ingestion/research_moneypuck.db"
link_path ".env"                                               "$SECRETS/.env"

# operational/odds_archive/live must exist even on a completely fresh
# deploy with nothing to migrate -- the app writes there before ever
# reading it.
mkdir -p "$DATA/operational/odds_archive/live"

echo "Layout provisioned. Nothing started -- see docs/VPS_CUTOVER_RUNBOOK.md for next steps."
