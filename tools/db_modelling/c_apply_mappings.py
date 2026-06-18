"""
c_apply_mappings.py
-------------------
Ingests reviewed mapping CSVs and builds master_players.

Run after reviewing the files in db_modelling/review/:
    cd db_modelling
    python c_apply_mappings.py

What it does:
  1. Applies confirmed fuzzy matches (confirmed = Y) → match_type 'fuzzy'
  2. Records rejected fuzzy matches (confirmed = N) → match_type 'none', no canonical ID
  3. Applies manual mappings from nomatch CSV (canonical_playerid filled in) → match_type 'manual'
  4. Records confirmed no-equivalents (canonical_playerid left blank) → match_type 'none'
  5. Rebuilds master_players from the completed map

Future seasons: re-run c_map_players.py with updated CURRENT_SEASON, then re-run this script.
Existing mappings (exact/fuzzy/manual) from prior runs are preserved and chained automatically.
"""
import sqlite3
from pathlib import Path

import pandas as pd

import params

REVIEW_DIR = Path(__file__).parent / 'review'


def _upsert(con, rows, match_type):
    """Delete existing map rows for these source keys, then insert."""
    if not rows:
        return 0
    df = pd.DataFrame(rows)
    for _, r in df.iterrows():
        con.execute(
            'DELETE FROM player_id_map WHERE source_season_year=? AND source_playerid=?',
            (r['source_season_year'], r['source_playerid'])
        )
    df.to_sql('player_id_map', con, if_exists='append', index=False)
    con.commit()
    return len(df)


def apply_fuzzy(con):
    path = REVIEW_DIR / 'player_mapping_fuzzy.csv'
    if not path.exists():
        print('No fuzzy review file found — skipping')
        return

    df = pd.read_csv(path, dtype=str).fillna('')
    confirmed = df[df['confirmed'].str.upper() == 'Y']
    rejected  = df[df['confirmed'].str.upper() == 'N']
    pending   = df[~df['confirmed'].str.upper().isin(['Y', 'N'])]

    if not pending.empty:
        print(f'  Warning: {len(pending)} fuzzy rows have no Y/N — skipping them')

    accept_rows = []
    for _, r in confirmed.iterrows():
        accept_rows.append({
            'source_season_year':    int(r['source_season_year']),
            'source_playerid':       r['source_playerid'],
            'source_name':           r['source_name'],
            'canonical_playerid':    r['canonical_playerid'],
            'canonical_season_year': int(r['canonical_season_year']),
            'canonical_name':        r['canonical_name'],
            'match_type':            'fuzzy',
            'match_score':           float(r['match_score']),
        })

    reject_rows = []
    for _, r in rejected.iterrows():
        reject_rows.append({
            'source_season_year':    int(r['source_season_year']),
            'source_playerid':       r['source_playerid'],
            'source_name':           r['source_name'],
            'canonical_playerid':    None,
            'canonical_season_year': None,
            'canonical_name':        None,
            'match_type':            'none',
            'match_score':           None,
        })

    n = _upsert(con, accept_rows, 'fuzzy') + _upsert(con, reject_rows, 'none')
    print(f'  Fuzzy: {len(confirmed)} accepted, {len(rejected)} rejected')


def apply_manual(con):
    path = REVIEW_DIR / 'player_mapping_nomatch.csv'
    if not path.exists():
        print('No nomatch review file found — skipping')
        return

    df = pd.read_csv(path, dtype=str).fillna('')
    has_id  = df[df['canonical_playerid'].str.strip() != '']
    no_id   = df[df['canonical_playerid'].str.strip() == '']

    manual_rows = []
    for _, r in has_id.iterrows():
        manual_rows.append({
            'source_season_year':    int(r['source_season_year']),
            'source_playerid':       r['source_playerid'],
            'source_name':           r['source_name'],
            'canonical_playerid':    r['canonical_playerid'].strip(),
            'canonical_season_year': params.CURRENT_SEASON,
            'canonical_name':        '',
            'match_type':            'manual',
            'match_score':           None,
        })

    none_rows = []
    for _, r in no_id.iterrows():
        none_rows.append({
            'source_season_year':    int(r['source_season_year']),
            'source_playerid':       r['source_playerid'],
            'source_name':           r['source_name'],
            'canonical_playerid':    None,
            'canonical_season_year': None,
            'canonical_name':        None,
            'match_type':            'none',
            'match_score':           None,
        })

    _upsert(con, manual_rows, 'manual')
    _upsert(con, none_rows, 'none')
    print(f'  Manual: {len(manual_rows)} mapped, {len(no_id)} confirmed no-equivalent')


def build_master_players(con):
    """
    Rebuild master_players from the completed player_id_map.

    For players WITH a canonical ID: one row per canonical_playerid,
    spanning the earliest to latest season seen.

    For players with no canonical equivalent (match_type='none'):
    include their most recent season's entry as a standalone record,
    using their source ID as the de-facto canonical. Players appearing
    across multiple seasons with no mapping are deduplicated by normalised name.
    """
    import re

    def _norm(s):
        return re.sub(r'[^a-z]', '', str(s).lower())

    # Mapped players
    mapped = pd.read_sql('''
        SELECT
            m.canonical_playerid,
            m.canonical_season_year,
            m.canonical_name      AS playername,
            MIN(m.source_season_year) AS first_season,
            MAX(m.source_season_year) AS last_season
        FROM player_id_map m
        WHERE m.canonical_playerid IS NOT NULL
        GROUP BY m.canonical_playerid
    ''', con)

    # Enrich with position from detailed_scores (canonical season preferred)
    if not mapped.empty:
        pos = pd.read_sql('''
            SELECT DISTINCT playerid, position
            FROM detailed_scores
        ''', con).drop_duplicates('playerid')
        mapped = mapped.merge(pos, left_on='canonical_playerid', right_on='playerid', how='left')
        mapped = mapped.drop(columns=['playerid'], errors='ignore')

    # Unmatched players — deduplicate by normalised name, keep most recent
    unmatched = pd.read_sql('''
        SELECT source_playerid, source_name, source_season_year
        FROM player_id_map
        WHERE match_type = 'none'
        ORDER BY source_season_year DESC
    ''', con)

    standalone_rows = []
    seen_norms = set()
    for _, r in unmatched.iterrows():
        norm = _norm(r['source_name'])
        if norm in seen_norms:
            continue
        seen_norms.add(norm)
        standalone_rows.append({
            'canonical_playerid':    r['source_playerid'],
            'canonical_season_year': int(r['source_season_year']),
            'playername':            r['source_name'],
            'position':              None,
            'first_season':          int(r['source_season_year']),
            'last_season':           int(r['source_season_year']),
        })
    standalone = pd.DataFrame(standalone_rows) if standalone_rows else pd.DataFrame()

    con.execute('DELETE FROM master_players')
    if not mapped.empty:
        mapped.to_sql('master_players', con, if_exists='append', index=False)
    if not standalone.empty:
        standalone.to_sql('master_players', con, if_exists='append', index=False)
    con.commit()

    total = con.execute('SELECT COUNT(*) FROM master_players').fetchone()[0]
    print(f'  master_players rebuilt: {total} unique players '
          f'({len(mapped)} mapped, {len(standalone)} standalone)')


def main():
    con = sqlite3.connect(params.DB_PATH)
    con.execute('PRAGMA journal_mode=WAL')

    print('Applying fuzzy review...')
    apply_fuzzy(con)

    print('Applying manual mappings...')
    apply_manual(con)

    print('Building master_players...')
    build_master_players(con)

    con.close()
    print('\nDone.')


if __name__ == '__main__':
    main()
