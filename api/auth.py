"""Authentication helper functions for user management."""

from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime
from .db import DB_TYPE


def _is_postgres(conn) -> bool:
    return DB_TYPE == 'postgres'


def hash_password(password: str) -> str:
    """Hash a password using PBKDF2."""
    return generate_password_hash(password, method='pbkdf2:sha256')


def verify_password(password: str, hash_str: str) -> bool:
    """Verify a password against its hash."""
    return check_password_hash(hash_str, password)


def create_user(conn, username: str, password: str, league_id) -> dict:
    """Create a new user. The username IS the team name (no email); it's stored
    in both columns so login (username) and display (team_name) stay in sync."""
    ph = '%s' if _is_postgres(conn) else '?'
    cursor = conn.cursor()

    # Username/team name already taken? (case-insensitive; the two columns mirror.)
    cursor.execute(
        f'SELECT user_id FROM users WHERE LOWER(username) = LOWER({ph}) OR LOWER(team_name) = LOWER({ph})',
        (username, username))
    if cursor.fetchone():
        cursor.close()
        return {'error': 'That username is already taken'}

    password_hash = hash_password(password)
    created_at = datetime.utcnow().isoformat()

    try:
        cursor.execute(
            f'INSERT INTO users (username, password_hash, team_name, league_id, created_at) '
            f'VALUES ({ph}, {ph}, {ph}, {ph}, {ph})',
            (username, password_hash, username, league_id, created_at),
        )
        conn.commit()

        cursor.execute(
            f'SELECT user_id, username, team_name, league_id FROM users WHERE username = {ph}',
            (username,),
        )
        user = cursor.fetchone()
        cursor.close()
        u = user if isinstance(user, dict) else {
            'user_id': user[0], 'username': user[1], 'team_name': user[2], 'league_id': user[3],
        }
        return {
            'user_id': u['user_id'], 'username': u['username'],
            'team_name': u['team_name'], 'league_id': u['league_id'],
        }
    except Exception as e:
        cursor.close()
        conn.rollback()
        return {'error': str(e)}


def authenticate_user(conn, identifier: str, password: str) -> dict:
    """Authenticate by email (or legacy username) + password."""
    ph = '%s' if _is_postgres(conn) else '?'
    cursor = conn.cursor()
    cursor.execute(
        f'SELECT user_id, username, password_hash, team_name, league_id, must_change_password '
        f'FROM users WHERE LOWER(email) = LOWER({ph}) OR LOWER(username) = LOWER({ph})',
        (identifier, identifier),
    )
    user = cursor.fetchone()
    cursor.close()

    if not user:
        return {'error': 'Invalid username or password'}

    if isinstance(user, dict):
        user_id = user['user_id']
        user_password_hash = user['password_hash']
        team_name = user['team_name']
        username_val = user['username']
        league_id = user['league_id']
        must_change = user['must_change_password']
    else:
        (user_id, username_val, user_password_hash, team_name, league_id,
         must_change) = user

    if not verify_password(password, user_password_hash):
        return {'error': 'Invalid username or password'}

    return {
        'user_id': user_id,
        'username': username_val,
        'team_name': team_name,
        'league_id': league_id,
        # Set by a commissioner reset: the password just used is temporary, so
        # the front end sends them straight to the profile to choose a new one.
        'must_change_password': bool(must_change),
    }


def get_available_teams(conn, league_id=None) -> list:
    """Get list of teams (optionally scoped to a league) and whether claimed."""
    ph = '%s' if _is_postgres(conn) else '?'
    cursor = conn.cursor()

    if league_id is None:
        cursor.execute('''
            SELECT DISTINCT ts.team_name, u.username
            FROM team_selections ts
            LEFT JOIN users u ON u.team_name = ts.team_name
            ORDER BY ts.team_name
        ''')
    else:
        cursor.execute(f'''
            SELECT DISTINCT ts.team_name, u.username
            FROM team_selections ts
            LEFT JOIN users u ON u.team_name = ts.team_name
            WHERE ts.league_id = {ph}
            ORDER BY ts.team_name
        ''', (league_id,))

    teams = []
    for row in cursor.fetchall():
        if isinstance(row, dict):
            team_name = row['team_name']
            owner = row['username']
        else:
            team_name = row[0]
            owner = row[1]

        teams.append({
            'name': team_name,
            'owner': owner,
            'available': owner is None,
        })

    cursor.close()
    return teams
