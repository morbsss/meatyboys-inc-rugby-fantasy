# OFDS — weekly lockouts, squad locking and transfers

How the **Owen Farrell Disappreciation Society** league (`ofds`, league_id 2,
English Premiership, `Europe/London`) opens and closes each week.

Everything here comes from the code that enforces it — `api/index.py` for the
lock, `api/scheduler.py` for the ingestion windows, `api/leagues.py` for league
config. If you change a rule in the engine, update this file with it.

---

## 1. The weekly cycle

There is **one boundary**: Tuesday 12:00, in the league's own timezone. At that
moment the round rolls over, its league table and fixtures go final, and squads
and transfers reopen for the next round. The squad locks again at the round's
first kickoff.

```
Tue 12:00  ──────── OPEN ────────  Fri 19:45  ─────── LOCKED ───────  Tue 12:00
           pick your XV,                      fixtures play,                    round N final,
           transfers, trades                  Mon: scores finalised             round N+1 opens
```

| Window | Current round | Squad + transfers |
|---|---|---|
| Tue 12:00 → Fri first kickoff | N | **open** |
| First kickoff → last fixture | N | locked |
| Last fixture → Tue 12:00 | **still N** | locked |
| Tue 12:00 onward | N+1 | **open** |

Two functions in `api/index.py` decide all of it:

- **`get_next_round`** — the lowest round whose *rollover* (the Tuesday noon
  after its last kickoff) is still in the future.
- **`is_locked`** — `now >= first_kickoff(current round)`.

Because the round stays current until Tuesday, `is_locked` needs no cool-down
logic of its own: it simply stays true through the weekend and Monday.

### The lock is round-wide, not per-player

It trips at the **first** kickoff of the round. If your players all play on
Sunday you are still frozen from Friday evening, along with everyone else. This
is the most common source of "why can't I edit my team?".

### Why Tuesday noon

The authoritative scoring scrape (`finalize`) runs **Monday 12:00** league-local
(`api/scheduler.py`). Rolling over 24 hours later means scores have settled
before anyone can change a squad, and a completed round can never be reopened.

---

## 2. What the lock gates

All three go through `is_locked` and return HTTP 403:

| Action | Endpoint | Message |
|---|---|---|
| Save squad / line-up | `POST /api/team/<name>` | *Deadline has passed — picks are locked until next round.* |
| Free-agent pickup | `POST /api/trades/free-agent` | *Trades are locked — a game in this round has kicked off.* |
| Accept a trade | `POST /api/trades/respond` | *Trades are locked — a game in this round has kicked off.* |

**Rejecting** a trade is deliberately *not* gated — you can always decline an
offer, even mid-round.

`GET /api/state` publishes `is_locked`, `cutoff` (next lock = first kickoff) and
`reopen` (the Tuesday rollover) so the squad screen can show a countdown.

### The development override

`ALLOW_UNRESTRICTED_EDITS=true` disables locking entirely — `is_locked` returns
`False` no matter what. It is `true` in `.env.local` and **`false` in
`.env.production`**. If the lock "isn't working" locally, this is why.

### Team names are not currently locked

`TEAM_LOCK_ENABLED = False` in `api/index.py`. The rule exists (names freeze 3
days before the season starts) but is switched off, so names stay editable.

---

## 3. The 2026-27 OFDS calendar

Generated from `data/prem_fixtures_2026_27.json`. `*` marks rounds whose kickoff
times the league has **not yet confirmed** — see the caveat below.

| Round | Locks (first kickoff) | Last fixture | Reopens (rollover) | Locked | Open |
|---|---|---|---|---|---|
| 1 | Fri 25 Sep 19:45 BST | Sun 27 Sep 15:00 BST | Tue 29 Sep 12:00 BST | 88h | 80h |
| 2 | Fri 02 Oct 19:45 BST | Sun 04 Oct 15:00 BST | Tue 06 Oct 12:00 BST | 88h | 80h |
| 3 | Fri 09 Oct 19:45 BST | Sun 11 Oct 15:00 BST | Tue 13 Oct 12:00 BST | 88h | 248h |
| 4 | Fri 23 Oct 19:45 BST | Sun 25 Oct 15:00 GMT | Tue 27 Oct 12:00 GMT | 89h | 80h |
| 5 | Fri 30 Oct 19:45 GMT | Sat 31 Oct 17:30 GMT | Tue 03 Nov 12:00 GMT | 88h | 752h |
| 6 | Fri 04 Dec 19:45 GMT | Sun 06 Dec 15:00 GMT | Tue 08 Dec 12:00 GMT | 88h | 248h |
| 7 | Fri 18 Dec 19:45 GMT | Sun 20 Dec 15:00 GMT | Tue 22 Dec 12:00 GMT | 88h | 99h |
| 8 | Sat 26 Dec 15:00 GMT | Mon 28 Dec 17:00 GMT | Tue 29 Dec 12:00 GMT | 69h | 80h |
| 9 | Fri 01 Jan 19:45 GMT | Sun 03 Jan 15:00 GMT | Tue 05 Jan 12:00 GMT | 88h | 435h |
| 10 * | Sat 23 Jan 15:00 GMT | Sat 23 Jan 15:00 GMT | Tue 26 Jan 12:00 GMT | 69h | 1275h |
| 11 * | Sat 20 Mar 15:00 GMT | Sat 20 Mar 15:00 GMT | Tue 23 Mar 12:00 GMT | 69h | 99h |
| 12 * | Sat 27 Mar 15:00 GMT | Sat 27 Mar 15:00 GMT | Tue 30 Mar 12:00 BST | 68h | 435h |
| 13 * | Sat 17 Apr 15:00 BST | Sat 17 Apr 15:00 BST | Tue 20 Apr 12:00 BST | 69h | 99h |
| 14 * | Sat 24 Apr 15:00 BST | Sat 24 Apr 15:00 BST | Tue 27 Apr 12:00 BST | 69h | 267h |
| 15 * | Sat 08 May 15:00 BST | Sat 08 May 15:00 BST | Tue 11 May 12:00 BST | 69h | 99h |
| 16 * | Sat 15 May 15:00 BST | Sat 15 May 15:00 BST | Tue 18 May 12:00 BST | 69h | 267h |
| 17 * | Sat 29 May 15:00 BST | Sat 29 May 15:00 BST | Tue 01 Jun 12:00 BST | 69h | 99h |
| 18 * | Sat 05 Jun 15:00 BST | Sat 05 Jun 15:00 BST | Tue 08 Jun 12:00 BST | 69h | 99h |
| PO * | Sat 12 Jun 15:00 BST | Sat 12 Jun 15:00 BST | Tue 15 Jun 12:00 BST | 69h | 99h |
| F | Sat 19 Jun 15:00 BST | Sat 19 Jun 15:00 BST | Tue 22 Jun 12:00 BST | 69h | — |

Worth noticing:

- A normal week is **~88h locked / ~80h open** — open Tuesday lunchtime to
  Friday evening.
- **Long breaks**: 752h open after round 5 (the autumn internationals) and
  1275h after round 10. The squad is fully editable throughout.
- **Round 8 locks on Boxing Day**, a Saturday, not a Friday.
- Times are shown league-local. The rollover is *noon to managers* year round —
  the BST→GMT switch (round 4) and GMT→BST (round 12) are handled automatically.

### Caveat: unconfirmed kickoff times

Rounds 10–20 are marked `time_confirmed: false` in the fixture feed. Every
fixture in those rounds carries the same placeholder slot, so the "first" and
"last" kickoff are identical and the table above shows a single time. Once the
league confirms real times, the daily `sync_rounds` job picks them up and these
rounds spread out on their own — no code change needed.

---

## 4. Season shape

`api/competition.py`: **15 regular rounds**, semi-final legs at 16 and 17, grand
final at 18. The real Premiership runs 18 regular rounds plus play-offs (19) and
a final (20), so the fantasy finals are played during real rounds 16–18 and real
rounds 19–20 are not used by the fantasy competition.

Scoring (OFDS uses league points with bonuses): win 4, draw 2, loss 0, bonus
point 1 — awarded to the winner at a margin ≥ 27 and to the loser at ≤ 11.

---

## 5. Ingestion schedule

`api/scheduler.py` decides what runs when, evaluated in `Europe/London`:

| Job | When | Targets |
|---|---|---|
| `sync_rounds` | daily | refreshes the calendar + fixture list |
| `lineups` | Thu 14:00 → Sun 18:00, every 2h | the current round |
| `live_scoring` | every 3 min while a match is live | the current round |
| `finalize` | Mon 12:00, once per round | the current round |

A cron on the VM pings `/api/cron/tick` every 10 minutes; the scheduler decides
what is actually due. All writes are idempotent upserts.

Because the round no longer rolls over at its last kickoff, `finalize` on Monday
targets the round that has just been played (previously it had to compensate
with `active_round - 1`).

---

## 6. Known issue: round 8 finalize

**Round 8's last fixture is Monday 28 Dec 17:00, but `finalize` fires Monday at
12:00 — five hours earlier.** It then records itself as done for that round and
never re-runs, so round 8's final match never receives its authoritative scoring
pass. Live scoring does cover the match, so totals are close but not guaranteed
final.

This affects only rounds whose last fixture falls on a Monday. The fix is to
gate `finalize` on "all fixtures in the round have finished" rather than on a
weekday. Not yet implemented.

---

## Appendix: current live state (audited 2026-09-21)

At the time of writing, production (`45.32.106.113`) is **not** behaving as
documented above. Delete this section once resolved.

**A. Production runs the mock data source.** `DATA_SOURCE` is unset in the VM's
`.env`, and `api/datasource/__init__.py` defaults to `mock`. The mock calendar is
synthesised from `SEASON_START['premiership'] = '2026-02-27'`, giving 18
synthetic Friday→Sunday rounds spanning Feb–Jun **2026** — all in the past.

**B. OFDS is therefore locked shut.** With no round having a future rollover,
`get_next_round` falls back to `MAX(weekly_stats.round) + 1` = 1, and round 1's
kickoff is seven months past, so `is_locked` is permanently `True`. Verified:

```
DATA_SOURCE: mock   ALLOW_UNRESTRICTED: False
OFDS (league 2)  get_next_round: 1  IS_LOCKED: True
                 next_lock_time: 2026-02-27T19:00:00+00:00
```

The daily `sync_rounds` job rewrites the calendar from mock every 24h, so
editing the `rounds` table by hand does not survive. The fix is
`DATA_SOURCE=live`, which reads the real calendar from
`data/prem_fixtures_2026_27.json`. Regression test:
`tests/test_lockout.py::test_exhausted_calendar_locks_the_league_shut`.

**C. Mixed real and mock data.** `players` and `draft_picks` are real
(`Ravouvou,K` / `BRI`) but `match_lineups` are mock synthetic (`Holloway,P` /
`Bath`) — the team codes don't even match, so lineup joins and auto-subs cannot
resolve. Needs the mock-derived `real_fixtures` and `match_lineups` rows purged
after the switch.

**D. Duplicate cron entries.** `deploy.sh` filtered on the wrong line when
reinstalling its crontab entry, so the VM accumulated 18 copies (6 using a stale
`CRON_SECRET` and returning 401). Fixed in `deploy.sh`; the VM's crontab needs
reinstalling once.
