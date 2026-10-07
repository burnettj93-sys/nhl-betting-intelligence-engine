# The Odds API key: exposure and rotation

**Status: NOT RESOLVED until a person rotates the key.** A live `THE_ODDS_API_KEY` value was committed as a string
literal in two test files (commits `6335ce3` and `134abee`) in a **public** GitHub repository. The current files no longer
contain it, but git history does, and anyone can read it. Deleting it from files does not revoke it.
`python3 deploy/verify_odds_key.py` (free endpoint, never prints a key) showed on 2026-10-07 that the exposed key is the
configured key and still active.

## Minimum action (about five minutes)

1. In your account at the-odds-api.com, generate a **new** API key and make sure the **old one is revoked/deactivated**
   (if the dashboard only lets you add keys, ask their support to deactivate the old one). The old key is the one in
   `.env` today.
2. Put the new key in `/Users/johnburnett/Downloads/nhl_engine 2/.env` on the line `THE_ODDS_API_KEY=...`
   (file mode is already 600 and the file is gitignored). The pinned release checkout at
   `~/nhl_engine_release/.env` is a symlink to that file, so there is nothing else to change on this Mac. Do not paste the
   key into chat, tickets, commits or test files.
3. If the Streamlit Cloud app has a `THE_ODDS_API_KEY` in its Secrets, replace it there too, or better delete it: the Cloud
   app only reads the published snapshot and never calls the odds provider.
4. Run `python3 deploy/verify_odds_key.py`. It must print `rotation verified` (new key works, every exposed key is rejected).
5. Confirm the scheduled integrations: the next prop sweep / trader log entries show `status: SUCCESS` and
   `price_refresh.status: OK`.

## What not to do

* Do not rewrite or force-push history to hide the key: rotation is the fix, and the experiment's history must stay intact.
* Consider making the repository private; that limits future exposure but does not undo the past one.
* Keep secrets out of tests: the two files now use an obviously fake `deadbeef...` value.
