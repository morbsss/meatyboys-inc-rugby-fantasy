"""
Legacy Vercel Cron handler for syncing the round schedule.

Runs every Monday at 09:00 UTC via Vercel Cron.
Endpoint: GET /api/cron/sync-rounds

The live deployment uses the unified scheduler at /api/cron/tick instead; this
endpoint is kept for the Vercel path. It now delegates to the same league-aware
ingestion as the tick, so rounds come from the official fixtures file
(data/prem_fixtures_2026_27.json via api/prem_fixtures) rather than ESPN, land
against a league_id, and populate real_fixtures alongside the round calendar.
"""

import os
from flask import Flask, jsonify, request

from ..db import get_connection, ensure_schema
from ..ingest import ingest_rounds
from ..leagues import LEAGUES, DEFAULT_LEAGUE

app = Flask(__name__)

CRON_SECRET = os.getenv('CRON_SECRET', '')


def _cron_auth_ok():
    if not CRON_SECRET:
        return True
    return request.headers.get('Authorization') == f'Bearer {CRON_SECRET}'


@app.route('/api/cron/sync-rounds')
def sync_rounds_cron():
    if not _cron_auth_ok():
        return jsonify({'error': 'Unauthorized'}), 401

    conn = get_connection()
    ensure_schema(conn)
    cur = conn.cursor()
    cur.execute('SELECT league_id FROM leagues WHERE slug = ?', (DEFAULT_LEAGUE,))
    row = cur.fetchone()
    cur.close()
    if not row:
        conn.close()
        return jsonify({'error': f'league {DEFAULT_LEAGUE} not found'}), 500
    league_id = row['league_id'] if isinstance(row, dict) else row[0]

    try:
        n = ingest_rounds(conn, league_id, LEAGUES[DEFAULT_LEAGUE]['competition'])
    except Exception as e:
        conn.close()
        return jsonify({'error': str(e)}), 500

    conn.close()
    return jsonify({'status': 'ok', 'rounds_synced': n})


if __name__ == '__main__':
    app.run(debug=True, port=5002)
