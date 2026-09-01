# Yahoo Fantasy Hockey Integration Guide

Written 2026-09-01 as part of the Yahoo Fantasy Hockey Master Sprint. This
is the reference for how the `fantasy/` package works, how to connect a
real Yahoo account, and what its real limitations are today.

## 1. Architecture at a glance

```
fantasy/
  yahoo/            OAuth 2.0, HTTP client, XML parsing, identity matching, caching
  league/           League settings model + fantasy scoring engine
  projections/      Reuses the betting engine's own frozen models for fantasy category values
  recommendations/  Lineup optimizer, waiver wire (+ 6 explicitly deferred engines)
  storage/          Append-only SQLite storage, isolated per user_key
```

`fantasy/` is a **read-only consumer** of the betting engine, never the
reverse. `tests/test_fantasy_betting_isolation.py` enforces this at the AST
level: it scans every file under `fantasy/` for an import of `operational`,
`research.live_sog_pricing`, or `research.player_props.decision_policy` and
fails the build if it ever finds one. Nothing in this sprint modified any
betting model, threshold, decision policy, overlay, pricing logic, Top
Conviction ranking, paper bankroll, or ledger.

Fantasy projections are built by calling the *same real, frozen* NHL models
the betting engine's own demo already uses (`dashboard.demo_data`'s
`_demo_context()` / `build_demo_roster()` / `build_demo_goalies()`,
themselves pre-existing betting-demo infrastructure) and reading each
model's raw expected-value output (`mu`, `expected_saves`) directly — never
the betting engine's BET/WATCH/WAIT/PASS decision or threshold-crossing
probability.

## 2. Yahoo API contract — what was actually verified this sprint

Base URL: `https://fantasysports.yahooapis.com/fantasy/v2`. Resource keys:
`game_key=<id>`, `league_key=<game_id>.l.<league_id>`,
`team_key=<game_id>.l.<league_id>.t.<team_id>`,
`player_key=<game_id>.p.<player_id>`. Responses are XML.

`fantasy/yahoo/contracts.py` is the single source of truth and draws an
explicit line between:

- **VERIFIED this session** (fetched live from `sports.yahoo.com/developer/docs/`
  and `developer.yahoo.com/oauth2/guide/flows_authcode/` on 2026-09-01): OAuth
  authorize/token endpoints, Game resource, League metadata/settings/standings.
- **NOT_VERIFIED_THIS_SESSION** (built against Yahoo's long-stable public
  contract, not independently re-fetched this session because a browser
  content-safety filter blocked deeper extraction from the docs page): Team,
  Roster, Player, available-players filter semantics, Scoreboard/Matchup,
  draftresults, transactions.

One real discrepancy was found and deliberately **not** followed: Yahoo's
own Fantasy Sports guide links a stale OAuth 1.0a PHP sample
(`gist.github.com/VerizonMediaOwner/...`) that contradicts the same guide's
explicit "requires OAuth 2.0" text. This integration follows the verified
`developer.yahoo.com/oauth2/guide/flows_authcode/` OAuth 2.0 flow instead.

## 3. OAuth 2.0 setup (Owner Auth Steps)

Nothing in this integration works against a real Yahoo account until a
Yahoo Developer Network app exists and its credentials are supplied via
environment variables. **As of this sprint, the owner's Yahoo API access
application has been submitted but is pending review** — this integration
ships fully built and tested against mocked fixtures, with the real
connection deferred until Yahoo approves access.

Once approved:

1. Register an app at Yahoo's Developer Network with the Fantasy Sports
   read scope and a redirect URI you control.
2. Set three environment variables (`.env`, never committed — see `.gitignore`) or Streamlit Cloud secrets:
   ```
   YAHOO_CLIENT_ID=...
   YAHOO_CLIENT_SECRET=...
   YAHOO_REDIRECT_URI=...
   ```
3. Open **Fantasy Settings** in the dashboard. It reads these three values
   via `fantasy/yahoo/oauth.py::load_app_credentials()` (env vars first,
   `st.secrets` fallback) and shows `CREDENTIALS CONFIGURED` once they're
   present, `OWNER_AUTH_REQUIRED` until then.
4. Click **Connect Yahoo Fantasy** to run the real authorization-code
   flow (`build_authorization_url` → user approves on Yahoo → Yahoo
   redirects back with a code → `exchange_code_for_token`).

Credentials and tokens are never logged, never written into any file
under version control, and never rendered in the UI — `TokenResponse`'s
`__repr__` is redacting by construction, and
`tests/test_fantasy_privacy.py` / `test_fantasy_oauth.py` enforce this at
the AST level (no `st.session_state`/`print`/`logging` access to token
fields anywhere in the module).

## 4. Multi-user safety

Nothing is hardcoded to one Yahoo user, league, or team. `user_key` is a
required parameter on every accessor in `fantasy/storage/fantasy_store.py`
(enforced by `inspect.signature` in `test_fantasy_privacy.py`), and league
settings are always parsed from whatever the connected account's real data
says (`parse_league_settings({})` yields empty tuples, never an invented
default category list).

## 5. Storage and caching

`fantasy/storage/fantasy_schema.sql` defines an append-only schema (every
table except `yahoo_tokens` and `user_selection`, which are per-user
upserts, only ever grows). `fantasy/yahoo/cache.py` is a small SQLite TTL
cache (settings/standings longer-lived, roster/matchup/available-players
short-lived).

**Honest limitation:** Streamlit Community Cloud's filesystem is
ephemeral. A redeploy or restart there wipes both SQLite files. This is
disclosed directly in both modules' docstrings — it is not genuinely
durable production persistence in that environment. Local/self-hosted
deployments do not have this limitation.

`.gitignore` excludes both database files
(`fantasy/storage/fantasy_store.db`, `fantasy/storage/fantasy_cache.db`)
so no per-user data or cached Yahoo content is ever committed.

## 6. Scoring engine

`fantasy/league/scoring.py` maps Yahoo's real stat display names to this
engine's own internal stat keys via `YAHOO_STAT_NAME_TO_INTERNAL`, each
tagged by the *real* status of that stat's projection model in
`research/model_registry.py`:

- **VALIDATED**: SOG, Goals, Assists, Blocks, Goalie Saves
- **PARTIAL**: Points (registry status is `EMPIRICAL_BASELINE_REMAINS_CHAMPION`, not `VALIDATED`)
- **NOT_AVAILABLE**: PIM, Hits, PPP, Wins, GA, SV%, Shutouts, and others with no real projection model in this engine

`points_value()` computes a weighted sum for points leagues and always
returns an explicit `missing_categories` list rather than silently
dropping stats it can't project. `category_contributions()` returns
per-category breakdowns for category/H2H leagues without collapsing them
to one scalar.

## 7. Player identity reconciliation

`fantasy/yahoo/identity.py::PlayerIdentityIndex` fails closed: a name only
resolves to `MATCHED` when it's uniquely normalized (accent-stripped,
suffix-stripped) across the index, or when a team disambiguates a
duplicate. Otherwise it returns `AMBIGUOUS` or `UNMATCHED` rather than
guessing. This is self-contained and does not import the betting engine's
own separate `research/live_sog_pricing/player_mapping.py`, to keep the
isolation boundary structural rather than incidental.

## 8. Lineup optimizer

`fantasy/recommendations/lineup_optimizer.py::optimize_lineup()` is a
**greedy value-maximizer** — sorts players by value, assigns each to its
best still-open eligible slot from the league's real roster positions. It
is explicitly documented as not an exhaustive/globally-optimal bipartite
matching search. IR slots are never auto-filled by value. Util/flex slots
correctly exclude goalies (a real bug found this sprint — see the Report's
Known Limitations / bugs-fixed section).

## 9. What's built vs. explicitly deferred this sprint

Built, tested, real: OAuth client, XML parser + typed extractors, HTTP
client (read-only by construction — no POST/PUT/DELETE method exists
anywhere on `YahooFantasyClient`), identity matching, league settings
parsing, scoring engine, fantasy projections (skater + goalie), lineup
optimizer, waiver wire ranking, append-only storage, TTL cache, Fantasy HQ
page, Fantasy Settings page.

**Explicitly `NOT_IMPLEMENTED_THIS_SPRINT`** (each stub module states the
real structural dependency it's blocked on):

| Module | Blocked on |
|---|---|
| `streaming.py` | real forward NHL schedule feed (doesn't exist anywhere in this project yet) |
| `drop_candidates.py` | real opponent/available-player pool (requires real Yahoo roster sync) |
| `trade_analyzer.py` | real opposing roster (requires real Yahoo connection) |
| `matchup_strategy.py` | real current matchup data (requires real Yahoo connection) |
| `draft_rankings.py` | real draft-status/ADP data (requires real Yahoo connection) |
| `category_value.py` | replacement-level modeling across a real full league player pool |

Also deferred: real roster/matchup/available-players sync (`fantasy/yahoo/sync.py`
has 2 of 7 planned sync steps implemented — league settings and standings
only), the daily fantasy self-evaluation module, a dedicated Fantasy
Performance page, and any automated Yahoo transaction (add/drop/trade) —
this sprint is read-and-recommend only, by design and by hard rule.

## 10. No automated Yahoo transactions

`YahooFantasyClient` has no method that can write to Yahoo — verified
structurally in `test_fantasy_client.py` (`hasattr` checks for
`post_resource`, `put_resource`, `delete_resource`, `add_player`,
`drop_player`, `propose_trade`, `accept_trade`, `set_lineup` all assert
`False`). No scheduler or background job was added. Every recommendation
this sprint produces is a suggestion rendered in the dashboard, never an
executed action.

## 11. Troubleshooting

- **"OWNER_AUTH_REQUIRED" won't go away** — `YAHOO_CLIENT_ID`/`YAHOO_CLIENT_SECRET`/`YAHOO_REDIRECT_URI` aren't set. This is expected until Yahoo approves the pending API access application.
- **Fantasy HQ always shows DEMO MODE even after connecting** — expected this sprint; real roster/matchup sync is `NOT_IMPLEMENTED_THIS_SPRINT` (see §9), so Fantasy HQ intentionally keeps showing the demo lineup regardless of connection status rather than half-implementing a real sync.
- **Yahoo data disappeared after a Streamlit Cloud redeploy** — expected; see §5's ephemeral-storage disclosure.
