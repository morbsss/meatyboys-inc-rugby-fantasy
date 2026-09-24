#!/usr/bin/env python3
"""Rewrite Premiership club names in a DB to canonical 3-letter codes.

The live app stores clubs by code — LiveAdapter.fetch_rounds resolves every club
through prem_fixtures.resolve_team, so production holds 'BAT', not 'Bath Rugby'.
The mock seed used to write display names instead, so a DB built under
DATA_SOURCE=mock described the same clubs by a different key and anything joining
on one silently found nothing: jersey art, the real-fixtures strip, lineup joins,
auto-substitution.

api/datasource/generate_seed.py now seeds codes, so freshly built mock DBs are
already correct. This migrates a DB that already exists, in place, without
touching the local state in it (users, draft picks, selections).

    python tools/normalise_club_codes.py mock_fantasy.db            # dry run
    python tools/normalise_club_codes.py mock_fantasy.db --apply    # with backup

Only leagues whose competition is 'premiership' are touched; Super Rugby has no
code vocabulary and keeps its display names. Safe to re-run: rows already holding
a code resolve to themselves and are left alone.
"""
import argparse
import os
import shutil
import sqlite3
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api import prem_fixtures                                    # noqa: E402

# Every column that identifies a real club. team_front_row.club and
# draft_picks.fr_club are deliberately absent: those are the meatyboys
# front-row-unit feature and only ever hold Super Rugby clubs.
CLUB_COLUMNS = [
    ('players', 'team'),
    ('match_lineups', 'real_team'),
    ('real_fixtures', 'home_team'),
    ('real_fixtures', 'away_team'),
]


def _premiership_league_ids(con):
    try:
        return [r[0] for r in con.execute(
            "SELECT league_id FROM leagues WHERE competition = 'premiership'")]
    except sqlite3.Error:
        return []


def _table_has(con, table, column):
    try:
        return column in {r[1] for r in con.execute(f'PRAGMA table_info({table})')}
    except sqlite3.Error:
        return False


def plan(con, league_ids):
    """[(table, column, old, new, rows)] for every value needing a rewrite."""
    todo, unresolved = [], []
    ph = ','.join('?' * len(league_ids))
    for table, column in CLUB_COLUMNS:
        if not _table_has(con, table, column):
            continue
        rows = con.execute(
            f'SELECT "{column}" AS v, COUNT(*) AS n FROM "{table}" '
            f'WHERE league_id IN ({ph}) AND "{column}" IS NOT NULL '
            f'GROUP BY "{column}"', league_ids).fetchall()
        for v, n in rows:
            code = prem_fixtures.resolve_team(v)
            if code is None:
                unresolved.append((table, column, v, n))
            elif code != v:
                todo.append((table, column, v, code, n))
    return todo, unresolved


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('db', help='path to the SQLite database')
    ap.add_argument('--apply', action='store_true',
                    help='write the changes (default: dry run)')
    ap.add_argument('--no-backup', action='store_true',
                    help='skip the timestamped backup copy')
    args = ap.parse_args(argv)

    if not os.path.exists(args.db):
        ap.error(f'no such database: {args.db}')

    con = sqlite3.connect(args.db)
    league_ids = _premiership_league_ids(con)
    if not league_ids:
        print('No premiership league in this DB — nothing to do.')
        return 0
    print(f'premiership league_id(s): {league_ids}')

    todo, unresolved = plan(con, league_ids)

    if unresolved:
        print('\nUNRESOLVED — no canonical code for these values:')
        for table, column, v, n in unresolved:
            print(f'  {table}.{column}: {v!r} ({n} rows)')
        print('  Add an alias to prem_fixtures.EXTRA_ALIASES, then re-run.')

    if not todo:
        print('\nEvery club already stored as a canonical code. Nothing to change.')
        return 1 if unresolved else 0

    print(f'\n{len(todo)} rewrite(s){"" if args.apply else " (dry run)"}:')
    total = 0
    for table, column, old, new, n in sorted(todo):
        print(f'  {table}.{column}: {old!r} -> {new!r}  ({n} rows)')
        total += n
    print(f'  {total} rows total')

    if not args.apply:
        print('\nRe-run with --apply to write.')
        return 0

    if not args.no_backup:
        # Keep the .db extension LAST, matching the repo's existing backups
        # (fantasy_2026_27.backup-preseason.db). .gitignore only has `*.db`, so a
        # name ending in the timestamp would be an untracked 3MB database sitting
        # in `git status` waiting to be committed by accident.
        stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
        stem, ext = os.path.splitext(args.db)
        backup = f'{stem}.backup-clubcodes-{stamp}{ext}'
        shutil.copy2(args.db, backup)
        print(f'\nbacked up to {backup}')

    ph = ','.join('?' * len(league_ids))
    with con:                       # one transaction: all of it, or none
        for table, column, old, new, _n in todo:
            con.execute(
                f'UPDATE "{table}" SET "{column}" = ? '
                f'WHERE league_id IN ({ph}) AND "{column}" = ?',
                [new, *league_ids, old])

    left, still_unresolved = plan(con, league_ids)
    print(f'applied. remaining rewrites: {len(left)}; unresolved: {len(still_unresolved)}')
    return 0 if not left else 1


if __name__ == '__main__':
    raise SystemExit(main())
