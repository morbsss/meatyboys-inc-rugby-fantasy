# db_modelling — reference data/modelling pipeline

The original offline pipeline that fetches Fantasy Rugby Draft (FRD) data and
builds player/win projections against the `super_rugby_2026.db` schema. Kept
here as **reference**; it is not part of the deployed app.

The modelling that the app actually uses (opposition deltas, season/3-game
averages, Gamma/Weibull medians, the GBM, and head-to-head win probabilities)
has been **ported to [`api/predict.py`](../../api/predict.py)**, which runs
against this app's own schema (`weekly_stats` / `real_fixtures` /
`team_selections`) and writes the `player_predictions` / `matchup_predictions`
tables the Analysis page reads. Prefer editing `api/predict.py` for app changes.

## Layout
- `a_fetch_players.py`, `b_fetch_scores.py`, `b2_fetch_lineups.py` — FRD API fetch
- `c_map_players.py`, `c_apply_mappings.py` — map FRD players to our IDs
- `d_opposition_deltas.py`, `d_player_summary.py` — derived per-position stats
- `e_helpers.py`, `e_predictions.py` — feature engineering + GBM player projections
- `f_picks.py`, `f_win_predictions.py` — squad picks + Gamma win probabilities
- `g_live.py` — live in-round scoring
- `db_init.py`, `params.py`, `run_extract.py`, `migrate_historical.py`,
  `save_fixtures.py`, `save_matchups.py` — schema/config/orchestration
- `fixtures/`, `review/` — sample fixtures + player-mapping review CSVs
- `super_rugby_2026.db` — source data (gitignored; local reference only)

Paths in `params.py` are resolved relative to this folder, so the scripts stay
self-contained after the move.
