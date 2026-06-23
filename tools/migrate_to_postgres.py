"""Copy a local SQLite app DB into a PostgreSQL database (e.g. Railway Postgres).

Schema-agnostic: it lets the app's own `ensure_schema` build the Postgres tables
(the exact path used in production), then copies every table's rows across —
ordered by foreign-key dependencies, intersecting only the columns both sides
share, and resetting SERIAL sequences afterwards so future inserts don't collide.

Usage:
    python tools/migrate_to_postgres.py --source fantasy_2025_26.db \
        --database-url postgresql://user:pass@host:5432/dbname

The target's matching tables are REPLACED (rows deleted first). Pass --keep to
append instead. DATABASE_URL env var is used if --database-url is omitted.
"""
import argparse
import os
import sqlite3
import sys

# Make the repo root importable so `import api.db` works when run as a script.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

SKIP_TABLES = {'sqlite_sequence'}


def _sqlite_tables(scon):
    rows = scon.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()
    return [r[0] for r in rows if r[0] not in SKIP_TABLES]


def _sqlite_columns(scon, table):
    return [r[1] for r in scon.execute(f'PRAGMA table_info("{table}")')]


def _fk_deps(scon, table):
    """Tables that `table` references via FK (its parents)."""
    return {r[2] for r in scon.execute(f'PRAGMA foreign_key_list("{table}")')}


def _topo_order(scon, tables):
    """Parents before children, so inserts satisfy FK constraints."""
    tset = set(tables)
    deps = {t: (_fk_deps(scon, t) & tset) - {t} for t in tables}   # ignore self-refs
    order, placed = [], set()
    while len(order) < len(tables):
        progressed = False
        for t in tables:
            if t not in placed and deps[t] <= placed:
                order.append(t); placed.add(t); progressed = True
        if not progressed:                                   # cycle — append the rest
            order += [t for t in tables if t not in placed]
            break
    return order


def _pg_columns(pcur, table):
    pcur.execute(
        'SELECT column_name FROM information_schema.columns WHERE table_name = %s',
        (table,))
    return {r[0] for r in pcur.fetchall()}


def _pg_serial_columns(pcur, table):
    pcur.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = %s AND column_default LIKE 'nextval(%%'", (table,))
    return [r[0] for r in pcur.fetchall()]


def migrate(sqlite_path, database_url, keep=False):
    if not os.path.exists(sqlite_path):
        raise SystemExit(f'source DB not found: {sqlite_path}')

    # ensure_schema branches on DB_TYPE, read at import time — set it first.
    os.environ['DB_TYPE'] = 'postgres'
    os.environ['DATABASE_URL'] = database_url
    import psycopg2
    from psycopg2.extras import execute_values
    from api.db import ensure_schema

    scon = sqlite3.connect(sqlite_path)
    scon.row_factory = sqlite3.Row
    pcon = psycopg2.connect(database_url)
    try:
        print(f'• building schema on target …')
        ensure_schema(pcon)                      # creates tables (+ seeds leagues)
        pcur = pcon.cursor()

        tables = _sqlite_tables(scon)
        order = _topo_order(scon, tables)
        print(f'• {len(order)} tables, order: {", ".join(order)}')

        if not keep:                             # clear target, children first
            for t in reversed(order):
                pcur.execute(f'DELETE FROM "{t}"')

        counts = {}
        for t in order:
            cols = [c for c in _sqlite_columns(scon, t) if c in _pg_columns(pcur, t)]
            if not cols:
                counts[t] = 0
                continue
            collist = ', '.join(f'"{c}"' for c in cols)
            rows = scon.execute(f'SELECT {collist} FROM "{t}"').fetchall()
            if rows:
                data = [tuple(r[c] for c in cols) for r in rows]
                execute_values(pcur, f'INSERT INTO "{t}" ({collist}) VALUES %s', data)
            counts[t] = len(rows)

        # Reset SERIAL sequences so the next insert continues past the copied ids.
        for t in order:
            for col in _pg_serial_columns(pcur, t):
                pcur.execute(
                    f'SELECT setval(pg_get_serial_sequence(%s, %s), '
                    f'  COALESCE((SELECT MAX("{col}") FROM "{t}"), 1), '
                    f'  (SELECT COUNT(*) > 0 FROM "{t}"))', (t, col))

        pcon.commit()

        # Verify: source vs target row counts.
        print('\n  table                          sqlite   postgres')
        ok = True
        for t in order:
            pcur.execute(f'SELECT COUNT(*) FROM "{t}"')
            pg_n = pcur.fetchone()[0]
            sq_n = counts[t]
            flag = '' if pg_n == sq_n else '   <-- MISMATCH'
            if pg_n != sq_n:
                ok = False
            print(f'  {t:30} {sq_n:>6}   {pg_n:>8}{flag}')
        print('\n' + ('✓ migration complete — all counts match.' if ok
                      else '✗ migration finished with mismatches (see above).'))
        return ok
    finally:
        scon.close()
        pcon.close()


def main():
    ap = argparse.ArgumentParser(description='Copy a SQLite app DB into PostgreSQL.')
    ap.add_argument('--source', default='fantasy_2025_26.db', help='source SQLite file')
    ap.add_argument('--database-url', default=os.getenv('DATABASE_URL'),
                    help='target Postgres URL (or set DATABASE_URL)')
    ap.add_argument('--keep', action='store_true',
                    help='append instead of replacing existing target rows')
    args = ap.parse_args()
    if not args.database_url:
        raise SystemExit('provide --database-url or set DATABASE_URL')
    ok = migrate(args.source, args.database_url, keep=args.keep)
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()
