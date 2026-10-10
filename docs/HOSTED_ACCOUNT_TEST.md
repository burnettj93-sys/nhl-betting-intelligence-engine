# Hosted two-account test (runs once `LOG_WRITE_TOKEN` exists)

**Why it is not done yet.** The hosted app cannot create accounts or submit slips without the write credential: My Bets says "Creating accounts is not switched on yet (the app has no write credential)". That is
the only thing missing. Everything else was proved with the real pages and the real engine (`tests/test_two_account_workflow.py`), with the transport (a GitHub issue per order) simulated.

## 1. Configure `LOG_WRITE_TOKEN` (about five minutes; yours to do)

1. GitHub → your avatar → **Settings** → **Developer settings** → **Personal access tokens** → **Fine-grained tokens** → **Generate new token**.
2. **Token name** `nhl-app-log-writer`; **Expiration** 90 days (put a reminder in your calendar).
3. **Resource owner** `burnettj93-sys`; **Repository access** → *Only select repositories* → `nhl-betting-intelligence-engine`.
4. **Repository permissions** → **Issues: Read and write**. Leave every other permission *No access* (Metadata: Read-only is added automatically). No account or organisation permissions.
5. **Generate token** and copy it (it starts with `github_pat_`). Do not send it to me or paste it anywhere else.
6. Open the app in your browser → **Manage app** (bottom right) → ⋮ → **Settings** → **Secrets**, and add one line:

   ```toml
   LOG_WRITE_TOKEN = "github_pat_…the token…"
   ```
7. **Save**, then ⋮ → **Reboot app** (about 5–8 minutes). My Bets then shows a **Create account** button instead of the "not switched on" note.

If the token ever leaks: GitHub → Settings → Developer settings → revoke it, make a new one, replace the secret. The engine only processes orders opened by your account, so a token from any other account does nothing.

## 2. The test (two people, two accounts; account creation and passcodes are typed by a person, not by me)

Use two throw-away surnames so nothing real is confused: **Qaone** and **Qatwo**. (Accounts are published by design and cannot be deleted; they are labelled as tests here and cost nothing.)

| Step | Who | What | Evidence I collect |
|---|---|---|---|
| 1 | You (browser A, signed out or Private) | My Bets → *Create an account* → `Qaone` → **Create account**. Copy the passcode shown once and press "I have saved it". | Within 2–10 minutes `Qaone` appears with $500.00 available (the engine answers the order every 2 minutes; the page refreshes with the next publish). |
| 2 | You (browser B, another window) | The same for `Qatwo`. | `Qatwo` appears with its own $500.00. |
| 3 | You, as Qaone | Parlay Builder → pick one fresh leg → stake $40 → **Submit to Qaone**. | `Qaone`: available $460.00, open stake $40.00, one open bet. |
| 4 | You, as Qatwo | Build a slip from two players in different games (an *Estimated* price) → stake $25 → submit. | `Qatwo`: available $475.00, open $25.00. Qaone unchanged. |
| 5 | You | Close the browsers; open the app in a fresh window; My Bets → *Open my account* → name and passcode. | Both accounts and their open bets are still there (persistence). |
| 6 | Me (engine side, read-only) | `python3 deploy/verify_personal_workflow.py` and a hash of every model-ledger table before and after. | Two last-name accounts created through the hosted path, each with a builder bet, signatures valid, the model ledger byte-identical, every account reconciles. Result PENDING until a bet has settled. |
| 7 | Automatic, after the games finish | The settlement job settles each bet into its own account. | `Qaone` and `Qatwo` show their own settled result and cash; the other account and the model book do not change. Result PASS. |

Also checked by me in the same run: the model book's cash, tickets and results (Today / Paper Performance) are identical before and after; a wrong passcode is refused; a stake above the cash is refused.
