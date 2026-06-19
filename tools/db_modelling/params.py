import os
from pathlib import Path

try:
    from dotenv import load_dotenv
    from pathlib import Path as _Path
    load_dotenv(_Path(__file__).parent.parent / '.env')
except ImportError:
    pass

BASE_DIR = Path(__file__).parent.parent
DATA_DIR = BASE_DIR / 'data'
DB_PATH = Path(os.getenv('ANALYTICS_DB_PATH', str(DATA_DIR / 'analytics.db')))
FIXTURES_DIR = Path(__file__).parent / 'fixtures'

FRD_URL = 'http://www.fantasyrugbydraft.com/Web/Services/Action.asmx/Request'
FRD_EMAIL = os.getenv('FANTASY_RUGBY_EMAIL', '')
FRD_PASSWORD = os.getenv('FANTASY_RUGBY_PASSWORD', '')
FRD_LEAGUE_ID = os.getenv('FRD_LEAGUE_ID', '')
FRD_SEASON_ID = os.getenv('FRD_SEASON_ID', '')

CURRENT_SEASON = int(os.getenv('CURRENT_SEASON', '2026'))

# Used to calculate round number from today's date
SEASON_START_DATES = {
    2022: '2022-02-12',
    2023: '2023-02-11',
    2024: '2024-02-10',
    2025: '2025-02-15',
    2026: '2026-02-14',
}

PLAYER_HUB_PAGES = 23

PLAYER_REF_TABLE = 'ref_players'
FIXTURE_TABLE = 'ref_fixtures'
TEAM_NEWS_TABLE = 'player_team_news'

# Normalise team names from fixtures CSV to match FRD website names
TEAM_NAME_MAP = {
    'NSW Waratahs': 'Waratahs',
    'Queensland Reds': 'Reds',
    'Western Force': 'Force',
}

# Fantasy league manager team IDs (FRD API GUIDs, stable for the season)
MANAGER_TEAMS = {
    'BIG REDS':        'ce502d05-c35d-4582-82eb-b3e40182be69',
    'BUMBIOSE':        'caf426a8-f178-49ae-aa17-b3e40182f2ea',
    'BIG KATUNAS':     'e71eb8c9-0e5b-4f54-924d-b3e5015c6353',
    'PIZZA SAMU':      '5a4cfb32-64ac-40ed-b117-b3e5002dd388',
    'NED SHENANIGANS': '6c54b8cd-0411-4046-93f8-b3e6000c2459',
    'BUMBLEBLUES':     '713b2ceb-ac49-4056-bd10-b3e4017fcf92',
    'SCRUMCHOPS':      '85908f1c-5847-4eea-876a-b3e40015aa9d',
    'ONISLANDTIME':    '0e742ee4-7f5d-49f3-99eb-b3e6000a3c66',
    'FUNWOLVES':       '08804e9e-11de-4648-8aa0-b3e5014aebb9',
    'THE CHIEFS':      '0d516641-6430-4a4b-9f1f-b3e501717fee',
}
