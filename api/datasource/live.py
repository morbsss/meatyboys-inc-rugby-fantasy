"""
Live data-source adapter (spec §3, §8.6 — best-effort).

Wraps the existing ESPN/SuperBru scraping behind the four interfaces. ESPN
publishes no official rugby API and SuperBru must be scraped, so the live
adapter is best-effort: the mock adapter is the contract of record and the app
must keep working when these endpoints can't be reached.

Per-competition source identifiers come from the league registry
(api/leagues.py). Only the Premiership has confirmed endpoints today; Super
Rugby falls back to empty results until its endpoints are wired.
"""

from ..leagues import LEAGUES
from .base import (
    PlayerSource, FixtureSource, LineupSource, ScoreSource,
    PlayerRecord, RoundRecord, MatchRecord, LineupEntry, ScoreRecord,
)

_SUPERBRU_URL = 'https://www.superbru.com/premrugbyfantasy/ajax/f_write_player_stats.php?'
_SUPERBRU_POS = {1: 'PR', 2: 'HK', 3: 'LK', 4: 'LF', 5: 'SH', 6: 'FH', 7: 'MID', 8: 'OBK'}
_HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'
    ),
}


def _config(competition: str) -> dict:
    for cfg in LEAGUES.values():
        if cfg['competition'] == competition:
            return cfg
    raise KeyError(f'No league configured for competition {competition!r}')


def _to_float(val) -> float:
    try:
        return float(str(val).replace('£', '').replace('m', '').strip())
    except (ValueError, TypeError):
        return 0.0


class LiveAdapter(PlayerSource, FixtureSource, LineupSource, ScoreSource):

    # --- §4.2 fixtures (official premiershiprugby.com feed) ---------------
    def fetch_rounds(self, competition: str) -> list[RoundRecord]:
        """Rounds come from data/prem_fixtures_2026_27.json (see api/prem_fixtures),
        not ESPN: it carries explicit round numbers, current club names and real
        venues, and doesn't rate-limit. Teams are emitted as canonical codes so
        they join straight to `players.team`."""
        cfg = _config(competition)
        if cfg['competition'] != 'premiership':
            return []
        from .. import prem_fixtures

        out: list[RoundRecord] = []
        for rd in prem_fixtures.rounds():
            window = prem_fixtures.round_window(rd['round'])
            if not window:
                continue
            matches = []
            for f in rd['fixtures']:
                home = prem_fixtures.resolve_team(f.get('home_team'))
                away = prem_fixtures.resolve_team(f.get('away_team'))
                # Play-off slots are 'TBC' until the regular season ends.
                if not home or not away:
                    continue
                matches.append(MatchRecord(home=home, away=away,
                                           kickoff=f['kickoff_utc']))
            out.append(RoundRecord(
                round_number=rd['round'],
                first_kickoff=window[0], last_kickoff=window[1],
                matches=matches,
            ))
        return out

    # --- §4.3 lineups (ESPN) ---------------------------------------------
    def fetch_lineups(self, competition: str, round_number: int) -> list[LineupEntry]:
        """Match-day lineups stay on ESPN — it's the only source that publishes
        them. Only the round-to-matches mapping and the team naming are taken
        from the fixtures file, so lineups land on the right round under the
        same team codes the rest of the app uses."""
        cfg = _config(competition)
        if cfg['competition'] != 'premiership':
            return []
        from ..real_lineups import fetch_json, extract_lineups, format_name
        from .. import prem_fixtures

        events = self._espn_round_events(cfg, round_number)
        entries: list[LineupEntry] = []
        for event in events:
            game_id = event['id']
            summary_url = (
                f'https://site.api.espn.com/apis/site/v2/sports/rugby'
                f'/{cfg["espn_league_id"]}/summary?event={game_id}'
            )
            try:
                teams = extract_lineups(fetch_json(summary_url))
            except Exception:
                continue
            for team in teams:
                # Prefer ESPN's abbreviation (already our code); fall back to
                # resolving its display name, which can lag a club rebrand.
                real_team = (prem_fixtures.resolve_team(team.get('abbreviation'))
                             or prem_fixtures.resolve_team(team.get('name'))
                             or team.get('name'))
                for p in team['players']:
                    if not p['name']:
                        continue
                    entries.append(LineupEntry(
                        player_name=format_name(p['name']), real_team=real_team,
                        jersey=p['jersey'], status='B' if p['is_bench'] else 'S',
                    ))
        return entries

    @staticmethod
    def _espn_round_events(cfg: dict, round_number: int) -> list[dict]:
        """ESPN events belonging to a round, selected by the round's kickoff
        window from the fixtures file.

        ESPN publishes no round numbers, so the previous approach split the
        season on gaps between match dates — which renumbers every later round
        the moment a match is rescheduled. Matching on the official round's date
        window keeps lineups aligned with the fixture list.
        """
        from .. import prem_fixtures
        from ..real_lineups import fetch_json
        from datetime import date, timedelta

        window = prem_fixtures.round_window(round_number)
        if not window:
            return []
        first = date.fromisoformat(window[0][:10]) - timedelta(days=1)
        last = date.fromisoformat(window[1][:10]) + timedelta(days=1)

        # One request PER DAY, not a `dates=start-end` range. ESPN rejects the
        # range form outright:
        #     ?dates=20260924-20260928  -> HTTP 400
        #     ?dates=20260925           -> 200, 2 events
        # The range failure used to be swallowed by a bare `except: return []`,
        # so the lineups job quietly recorded "0 entries" every run and
        # auto-substitution silently stopped working.
        events, seen, failures = [], set(), 0
        days = (last - first).days + 1
        for offset in range(days):
            day = first + timedelta(days=offset)
            url = (
                f'https://site.api.espn.com/apis/site/v2/sports/rugby'
                f'/{cfg["espn_league_id"]}/scoreboard?dates={day:%Y%m%d}'
            )
            try:
                data = fetch_json(url)
            except Exception:
                failures += 1
                continue
            for ev in data.get('events', []):
                # A fixture can appear on neighbouring days as the window is
                # padded by a day at each end; keep the first sighting only.
                if ev.get('id') and ev['id'] not in seen:
                    seen.add(ev['id'])
                    events.append(ev)

        # Every request failing is an outage, not an empty round — raise so the
        # scheduler logs an error against the job instead of a clean "0 entries".
        if failures == days and days:
            raise RuntimeError(
                f'ESPN scoreboard unreachable for all {days} days of round {round_number}')
        return events

    # --- §4.1 players / §4.4 scores (SuperBru) ---------------------------
    def _scrape_superbru(self, competition: str) -> list[dict]:
        cfg = _config(competition)
        table = cfg.get('superbru_table')
        if not table:
            return []
        import requests
        from bs4 import BeautifulSoup
        rows = []
        for page in range(1, 9):
            resp = requests.get(
                f'{_SUPERBRU_URL}pg={page}&tbl={table}',
                headers=_HEADERS, timeout=15, verify=False,
            )
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, 'html.parser')
            thead, tbody = soup.find('thead'), soup.find('tbody')
            if not tbody:
                continue
            # Header-driven column map: stat columns follow the Team + Player
            # headers, and each row has an empty colspan filler cell after the
            # name, so its stat cells start at index 3. This survives column-set
            # changes (e.g. a 'Kicking' column appearing/disappearing).
            labels = [th.get_text(strip=True) for th in thead.find_all('th')][2:] if thead else []
            for row in tbody.find_all('tr'):
                cells = [td.get_text(strip=True) for td in row.find_all('td')]
                if len(cells) < 4 or not cells[1]:
                    continue
                stats = dict(zip(labels, cells[3:3 + len(labels)]))
                rows.append({
                    'team': cells[0],
                    'name': cells[1],
                    'position': _SUPERBRU_POS[page],
                    'total_points': _to_float(stats.get('Points', 0)),
                    'price': _to_float(stats.get('Price', 0)) * 1_000_000,
                    'kicking': _to_float(stats.get('Kicking', 0)),
                    'points_per_game': stats.get('Points per game', ''),
                    'popularity': stats.get('Popularity', ''),
                    'form': stats.get('Form', ''),
                })
        return rows

    def fetch_players(self, competition: str) -> list[PlayerRecord]:
        return [
            PlayerRecord(name=r['name'], team=r['team'],
                         position=r['position'], price=r['price'])
            for r in self._scrape_superbru(competition) if r['name']
        ]

    def fetch_player_scores(self, competition: str, round_number: int) -> list[ScoreRecord]:
        # SuperBru exposes season totals (not per-round), matching the existing
        # cron behaviour; round_number is accepted for interface parity.
        return [
            ScoreRecord(
                name=r['name'], team=r['team'], position=r['position'],
                total_points=r['total_points'], price=r['price'], kicking=r['kicking'],
                points_per_game=r['points_per_game'], popularity=r['popularity'],
                form=r['form'],
            )
            for r in self._scrape_superbru(competition) if r['name']
        ]
