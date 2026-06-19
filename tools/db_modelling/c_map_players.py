"""
c_map_players.py
----------------
Maps historical player IDs (all prior seasons) to their canonical (current season) equivalents.

Run once per season, or whenever new historical data is added:
    cd db_modelling
    python c_map_players.py

What it does:
  - Auto-applies exact name matches directly into player_id_map
  - Adds identity mappings for current-season players (they map to themselves)
  - Writes review files to db_modelling/review/ for everything it can't auto-apply:
      player_mapping_fuzzy.csv   — high-similarity matches needing Y/N confirmation
      player_mapping_nomatch.csv — no match found; fill in canonical_playerid if known
      player_2026_reference.csv  — full 2026 player list to help with manual lookup

After reviewing, run:
    python c_apply_mappings.py
"""
import re
import sqlite3
from pathlib import Path

import pandas as pd
from rapidfuzz import fuzz, process

import params

FUZZY_THRESHOLD = 85
REVIEW_DIR = Path(__file__).parent / 'review'


def _normalise(name: str) -> str:
    """Strip to lowercase a-z only — ignores punctuation, spacing, accents."""
    return re.sub(r'[^a-z]', '', str(name).lower())


def _get_players(con, season_year):
    return pd.read_sql(
        'SELECT DISTINCT playerid, playername, position FROM detailed_scores WHERE season_year = ?',
        con, params=(season_year,)
    )


def _already_mapped(con, season_year):
    return set(
        pd.read_sql(
            'SELECT source_playerid FROM player_id_map WHERE source_season_year = ?',
            con, params=(season_year,)
        )['source_playerid']
    )


def _insert_rows(con, rows):
    if not rows:
        return
    pd.DataFrame(rows).to_sql('player_id_map', con, if_exists='append', index=False)
    con.commit()


def map_season(con, source_year, canonical_year, canonical_players):
    """
    Map one historical season to the canonical season.
    Returns (fuzzy_rows, nomatch_rows) for writing to review files.
    """
    source_players = _get_players(con, source_year)
    if source_players.empty:
        print(f'  {source_year}: no players in detailed_scores, skipping')
        return [], []

    already = _already_mapped(con, source_year)

    canonical_players = canonical_players.copy()
    canonical_players['name_norm'] = canonical_players['playername'].apply(_normalise)
    curr_lookup = dict(zip(canonical_players['name_norm'], canonical_players['playerid']))
    curr_display = dict(zip(canonical_players['name_norm'], canonical_players['playername']))
    curr_names = list(curr_lookup.keys())

    exact_rows, fuzzy_rows, nomatch_rows = [], [], []

    for _, row in source_players.iterrows():
        pid = row['playerid']
        name = row['playername']
        norm = _normalise(name)

        if pid in already:
            continue

        if norm in curr_lookup:
            exact_rows.append({
                'source_season_year':    source_year,
                'source_playerid':       pid,
                'source_name':           name,
                'canonical_playerid':    curr_lookup[norm],
                'canonical_season_year': canonical_year,
                'canonical_name':        curr_display[norm],
                'match_type':            'exact',
                'match_score':           100.0,
            })
        else:
            result = process.extractOne(norm, curr_names, scorer=fuzz.ratio) if curr_names else None
            if result and result[1] >= FUZZY_THRESHOLD:
                matched_norm = result[0]
                fuzzy_rows.append({
                    'source_season_year':    source_year,
                    'source_playerid':       pid,
                    'source_name':           name,
                    'canonical_playerid':    curr_lookup[matched_norm],
                    'canonical_season_year': canonical_year,
                    'canonical_name':        curr_display[matched_norm],
                    'match_score':           round(result[1], 1),
                    'confirmed':             '',
                })
            else:
                nomatch_rows.append({
                    'source_season_year': source_year,
                    'source_playerid':    pid,
                    'source_name':        name,
                    'position':           row.get('position', ''),
                    'canonical_playerid': '',
                    'notes':              '',
                })

    _insert_rows(con, exact_rows)

    print(f'  {source_year}: {len(exact_rows)} exact (auto-applied), '
          f'{len(fuzzy_rows)} fuzzy, {len(nomatch_rows)} unmatched')

    return fuzzy_rows, nomatch_rows


def main():
    REVIEW_DIR.mkdir(exist_ok=True)

    con = sqlite3.connect(params.DB_PATH)
    con.execute('PRAGMA journal_mode=WAL')
    db_init_module = __import__('db_init')
    db_init_module.init_db()

    canonical_year = params.CURRENT_SEASON
    canonical_players = _get_players(con, canonical_year)
    print(f'Canonical season: {canonical_year} ({len(canonical_players)} players)\n')

    # Identity mappings — current-season players map to themselves
    already_canonical = _already_mapped(con, canonical_year)
    identity_rows = []
    for _, row in canonical_players.iterrows():
        if row['playerid'] not in already_canonical:
            identity_rows.append({
                'source_season_year':    canonical_year,
                'source_playerid':       row['playerid'],
                'source_name':           row['playername'],
                'canonical_playerid':    row['playerid'],
                'canonical_season_year': canonical_year,
                'canonical_name':        row['playername'],
                'match_type':            'identity',
                'match_score':           100.0,
            })
    _insert_rows(con, identity_rows)
    if identity_rows:
        print(f'Added {len(identity_rows)} identity mappings for {canonical_year}\n')

    historical_years = sorted(y for y in params.SEASON_START_DATES if y < canonical_year)

    all_fuzzy, all_nomatch = [], []
    for year in historical_years:
        fuzzy, nomatch = map_season(con, year, canonical_year, canonical_players)
        all_fuzzy.extend(fuzzy)
        all_nomatch.extend(nomatch)

    con.close()

    # Write review files
    ref_path = REVIEW_DIR / f'player_{canonical_year}_reference.csv'
    canonical_players[['playerid', 'playername', 'position']].sort_values('playername').to_csv(ref_path, index=False)

    if all_fuzzy:
        fuzzy_path = REVIEW_DIR / 'player_mapping_fuzzy.csv'
        pd.DataFrame(all_fuzzy).to_csv(fuzzy_path, index=False)
        print(f'\nFuzzy review → {fuzzy_path} ({len(all_fuzzy)} rows)')
        print('  Fill in "confirmed" column: Y to accept, N to reject')

    if all_nomatch:
        nomatch_path = REVIEW_DIR / 'player_mapping_nomatch.csv'
        pd.DataFrame(all_nomatch).to_csv(nomatch_path, index=False)
        print(f'No-match review → {nomatch_path} ({len(all_nomatch)} rows)')
        print(f'  Fill in "canonical_playerid" using {ref_path.name}, or leave blank if no equivalent')

    print(f'\n2026 reference list → {ref_path}')
    print('\nNext step: python c_apply_mappings.py  (after reviewing CSVs)')


if __name__ == '__main__':
    main()
