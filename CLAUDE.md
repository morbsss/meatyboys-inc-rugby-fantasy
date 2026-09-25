# CLAUDE.md

Orientation for working on this repo, and the traps that have actually bitten.

Two rugby fantasy leagues on one Flask app: **`ofds`** (English Premiership) and
**`meatyboys`** (Super Rugby Pacific). Every table is scoped by `league_id` and
every schedule decision is evaluated in the league's own IANA timezone.

## Stack

Flask + SQLite, no build step, no frontend framework - plain ES5-ish JS modules
and hand-written CSS per page. One VM (Vultr, 1 vCPU / 1 GB), gunicorn behind
nginx, a single `cron` entry driving everything.

```
api/index.py          routes + most domain glue (large; navigate by the ==== banners)
api/competition.py    scoring, standings, effective XV
api/scheduler.py      WHEN jobs run - pure functions, no DB
api/ingest.py         WHAT jobs do - writes, all idempotent
api/datasource/       mock|live adapters behind one interface
api/predict.py        the analysis model, runs OUT of process
api/observability.py  pipeline health checks
api/static/{css,js}/  one file per page, same basename as the template
tests/                375 tests - the specification of record
tools/                generators and one-off operational scripts
docs/history/         the archived original build brief
```

## Commands

```bash
python -m pytest tests -q          # the whole suite, ~6s. Run it.
.\dev.ps1                          # Flask dev server on :5000, mock data
.\dev.ps1 seed-mock                # rebuild mock_fantasy.db from the seed
.\dev.ps1 reset-draft              # wipe rosters/picks/trades, draft back to pending
.\dev.ps1 logins                   # print local test logins (password: devdev)
python tools/generate_jerseys.py   # club kit SVGs -> api/static/img/jerseys/
python tools/generate_favicon.py   # favicons from img/animal.gif
```

`.env.local` supplies `DB_PATH` (`mock_fantasy.db`) and
`ALLOW_UNRESTRICTED_EDITS=true`. `DATA_SOURCE` defaults to **`mock`**, so local
work never touches SuperBru or ESPN.

## Deploys are CI-only

Pushing to `main` triggers `.github/workflows/deploy.yml`, which runs the tests,
rsyncs, and runs `deploy.sh` on the VM. **`deploy.sh` refuses to run without
`CI_DEPLOY=1`** and `deploy.ps1` is retired. Do not deploy by hand; if you think
you must, that is the break-glass `ALLOW_MANUAL_DEPLOY=1` and it should be a
deliberate, stated decision.

Generated assets (jersey SVGs, favicons, seed JSON) are **committed**, not built
on the VM. If you change a generator, re-run it and commit the output.

## The one rule everything hangs off

**Tuesday 12:00, league-local, is the single break.** At that moment the round
rolls to N+1, round N's table and fixtures go final, and squads reopen. Picks
lock again at the round's **first** kickoff - league-wide, so a manager holding
only Sunday players is frozen from Friday evening.

```
Tue 12:00 ──open──> first kickoff ──locked──> Tue 12:00 ──open──> …
```

`is_locked` is just `now >= first_kickoff(next_round)`; the behaviour comes from
`get_next_round` rolling at the rollover rather than at the last kickoff. See
`_rollover_at` in `api/index.py`, `is_finalize_time` in `api/scheduler.py`, and
README §1 (§5 is the ingestion schedule). The archived brief says Monday - it is wrong.

## Jobs

One cron entry (`*/5`) hits `/api/cron/tick`; `scheduler.due_jobs()` decides
what runs. Adding a job means touching **four** places: `INTERVALS`, `due_jobs`,
`_run_job`, and the `last_runs` dict in `cron_tick`.

| Job | Cadence | Writes |
|---|---|---|
| `sync_rounds` | daily | `rounds`, `real_fixtures` |
| `sync_players` | daily | `players` (signings + transfers) |
| `lineups` | 2h, inside the team-sheet window | `match_lineups` |
| `live_scoring` | 5 min while a match is live | `weekly_stats` |
| `finalize` | once, at the rollover | `weekly_stats` (authoritative) |
| `predict` | 2h, or 5 min while a match is live | prediction tables |

**A job missing from `last_runs` looks like it has never run**, so its interval
floor always passes and it fires on *every* tick. That nearly shipped an 8-page
SuperBru scrape every 10 minutes.

`/observability` (maintainer only - `OBSERVABILITY_USERS`, default `morbsss`)
shows job status, failures, and row counts.

## Data sources

**SuperBru is the source of truth for player identity** (`players.name` is its
`Surname,Initial` spelling). ESPN supplies team sheets only.

- A **transfer** is the same `(name, position)` at a different club → update the
  row in place. Identity is `(league, name, position)`, **never including the
  club** - the club in the key is why a transfer used to insert a duplicate.
- SuperBru lengthens the initial to separate namesakes at one club:
  `Griffin,Ar` vs `Griffin,Al`, `Curry,TM` vs `Curry,B`. ESPN sends
  `Griffin,A`. `api/player_match.py` reconciles them using ESPN's **forename**
  - both Bath Griffins are props, so position cannot.
- Ambiguity resolves to `None` and gets logged. Two real players can share
  surname, initial and position (`Wilson,T` plays for Bristol *and* Sale), so a
  guess writes someone else's points into a squad.

## Conventions

- **Tests are the spec.** Most carry a docstring saying *why* the rule exists and
  what broke without it. Keep that up - it is the only documentation here that
  cannot drift, because drift makes it fail.
- **Comments explain why, not what.** Density is deliberate: the repo goes quiet
  for months at a time, and the reasoning is what does not survive the gap.
- All timestamps stored **UTC**; convert only at display/scheduling boundaries.
- `spec §N` in a comment refers to `docs/history/agent.md`, the archived June
  build brief. It is **historical and partly superseded** - see its header for
  the divergences. Current rules live in the tests and README.
- Python deps split three ways on purpose: `api/requirements.txt` (web runtime,
  stays light), `requirements-analysis.txt` (the out-of-process model),
  `requirements-dev.txt` (tests + build tooling, e.g. Pillow). The web process
  must not grow numpy or an imaging library.

## Traps

Each of these has cost real time here.

- **`status='ok'` is not health.** Every outage this pipeline has had logged a
  success - `lineups` wrote `ok / 0 entries` for weeks behind an ESPN 403, and
  `predict` wrote `ok / launched` while producing no rows. Check what a job
  *wrote*, not whether it returned.
- **`job_runs` only gets a row when a job fires**, so a quiet weekday is not a
  dead cron. Liveness comes from `pipeline_heartbeat`.
- **Never send a spoofed browser User-Agent to ESPN** - Akamai 403s it. urllib's
  default works. And ESPN rejects `dates=start-end` ranges with a 400; request
  one day at a time.
- **Compute everything before `conn.close()`.** Returning a lazily-built value
  after closing the connection has broken this endpoint twice.
- **Pre-season, `weekly_stats` is empty and `last_round` is 0.** Inner-joining it
  eliminates every player. Use `LEFT JOIN` for anything that must work before
  the first match.
- **Mock and live must key clubs identically** (`BAT`, not `Bath Rugby`), or
  anything joining on a club silently finds nothing under `DATA_SOURCE=mock`.
- **`[hidden]` loses to an author `display`.** Anything shipping hidden needs an
  explicit `[hidden] { display: none !important }`.
- **CSS grid: set `grid-row` whenever you set `grid-column`**, or sparse
  auto-placement will stack the cells onto new rows.
- **`container` query units (`cqw`) only resolve inside a query container.** The
  squad pitch is one; the bench beside it is not.
- Windows: `MAX_PATH` will break a venv built under a long temp path.
