# Owner steps — the token, the Odds API key and the credit decision, together (2026-10-12)

Everything that does not depend on these has been finished. Each step says what it unlocks and what I do afterwards. Nothing here is purchased or changed by me.

## 1. `LOG_WRITE_TOKEN` — unlocks hosted accounts, the Paper Parlay Builder submit and the hosted two-account test (about 5 minutes)

1. GitHub → your avatar → **Settings** → **Developer settings** → **Personal access tokens** → **Fine-grained tokens** → **Generate new token**.
2. Name `nhl-app-log-writer`; expiration 90 days; **Resource owner** `burnettj93-sys`; **Repository access** → *Only select repositories* → `nhl-betting-intelligence-engine`.
3. **Repository permissions** → **Issues: Read and write**. Everything else *No access*. **Generate token** and copy it (starts with `github_pat_`). Do not paste it anywhere except the next step.
4. Open the app → **Manage app** (bottom right) → ⋮ → **Settings** → **Secrets** and add one line:
   `LOG_WRITE_TOKEN = "github_pat_…the token…"` → **Save**, then ⋮ → **Reboot app** (about 7 minutes).
5. Tell me it is done. I confirm the My Bets page now shows **Create account** (no "not switched on" note) and the engine's order path answers, then we run the test in `docs/HOSTED_ACCOUNT_TEST.md`.
   Accounts and passcodes are typed by a person, never by me: you create `Qaone` and `Qatwo` (two minutes each, in two browser windows; copy each passcode when it is shown); I build, submit, close/reopen, wait for settlement and reconcile.

## 2. The Odds API key and the credit decision — one change covers both

The exposed key is only fixed by replacing it, and the credit decision changes the key if you upgrade, so decide first.

**Decision (pick one):**
* **Stay free (500 credits a cycle).** About 11 credits a day: one league-wide moneyline pull, morning shots ladders for part of the slate and a pregame look for one wave of 2–3 games. Tomorrow has 16 games; the free plan prices 2–3.
  The requested workflow (a morning and a pregame look for every game, shots only, plus 4 moneyline pulls and a tomorrow check, about 2N+5 a day) needs 277 credits for the 15 days on file, against 224 usable: **it runs out about 2026-10-24**.
* **Upgrade to the 20K tier ($30 a month, 20,000 credits).** Covers every game and several looks a day (the same workflow is about 2,300 a month). Not purchased; only you can buy it.

**Steps:**
1. At the-odds-api.com: if you upgrade, do it first; the upgraded plan issues its own key. Then generate a **new** key and make sure the **old one is revoked** (if the dashboard only adds keys, ask their support to deactivate the old one). Free plan: generate a new key and revoke the old one.
2. Put the new key in `/Users/johnburnett/Downloads/nhl_engine 2/.env` on the line `THE_ODDS_API_KEY=...` (the pinned release reads the same file). Do not paste it into chat, tickets, commits or tests. If the Streamlit app has a `THE_ODDS_API_KEY` secret, delete it (the app never calls the provider).
3. Tell me the plan you chose and ask me to verify. I run `python3 deploy/verify_odds_key.py` (free endpoint; prints only fingerprints) and need `rotation verified`; then I check the next scheduled pulls succeeded.
4. Also tell me the date your quota resets (the provider does not publish it; the planner assumes the 1st). I set it in `.env` as `NHL_ENGINE_ODDS_RESET_DAY` (the existing owner setting, a day 1–28) and the daily budget recomputes. If you upgraded, I raise the plan from "free" to the tier's allowance (`docs/ODDS_BUDGET_CONFIGURATIONS.md`, configuration B) so every game gets its morning and pregame look.

## 3. The Mac (only if you want the 8:00 AM look and the game windows to be reliable)

* Plugged in, lid open, from about 7:50 AM until the last game starts. The engine holds the Mac awake inside each window, but it cannot wake a sleeping Mac or stop a closed lid sleeping it.
* Optional one-time wake for tonight/tomorrow: `python3 -m operational.keep_awake --plan` prints the exact `sudo pmset schedule wake …` lines; optional `sudo pmset -c sleep 0` keeps it from idle-sleeping while plugged in.

## 4. Optional

* `OWNER_ACCESS_PASSPHRASE` in the app's Secrets, so you can open **Data Status** and **Diagnostics** on the hosted app (they are owner-only).
* **One phone check:** on an iPhone open the app link in Safari with a Private tab and confirm **Today** loads with no sign-in, then tap the menu and open **My Bets**.
* Lineups, injuries, starting goalies: no permitted source is configured. Name a source you are allowed to use (a licensed feed or API with credentials) and I will connect it; I will not scrape a site whose terms forbid it.
