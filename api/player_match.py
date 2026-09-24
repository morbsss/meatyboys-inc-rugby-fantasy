"""Resolve an ESPN player to the SuperBru name the rest of the app keys on.

SuperBru is the source of truth for player identity: `players.name` holds its
"Surname,Initial" spelling, and `match_lineups.player_name` has to agree or the
lineup join finds nothing — no S/B/O badge, no auto-substitution, no score.

The two feeds disagree in one specific way. SuperBru lengthens the initial to
tell apart players who would otherwise collide at the same club:

    ESPN "Arthur Griffin"  -> Griffin,A    SuperBru: Griffin,Ar   (also Griffin,Al)
    ESPN "Oscar Williams"  -> Williams,O   SuperBru: Williams,Os  (also Williams,N/J)
    ESPN "Tom Curry"       -> Curry,T      SuperBru: Curry,TM     (also Curry,B)

So a plain equality check drops exactly the players most likely to matter — the
ones with a namesake in the same squad. Worse, Griffin,Al and Griffin,Ar are BOTH
props at Bath, so position can't separate them either; only the forename can,
which is why this works from ESPN's full name rather than the formatted one.

Nothing here guesses. Every rule must land on exactly one candidate at that club,
or the name is left unresolved for the caller to log.
"""

FORENAME_PREFIX = 'forename-prefix'
FIRST_LETTER = 'first-letter'
EXACT = 'exact'
SOLE_SURNAME = 'sole-surname'


# Shirt numbers 1-15 are fixed by the laws of the game, so a starter's number
# gives their position for free. Bench numbers are only convention — 16-23 vary by
# club and a replacement often covers two positions — so they return None rather
# than a guess. This is only ever a tiebreaker (see `resolve`), and a wrong
# tiebreaker picks the wrong player, which is worse than picking nobody.
_STARTER_POSITIONS = {
    1: 'PR', 2: 'HK', 3: 'PR', 4: 'LK', 5: 'LK',
    6: 'LF', 7: 'LF', 8: 'LF', 9: 'SH', 10: 'FH',
    11: 'OBK', 12: 'MID', 13: 'MID', 14: 'OBK', 15: 'OBK',
}


def position_for_jersey(jersey) -> str | None:
    """Position implied by a shirt number, for 1-15 only."""
    try:
        return _STARTER_POSITIONS.get(int(jersey))
    except (TypeError, ValueError):
        return None


def split_formatted(formatted: str) -> tuple[str, str]:
    """'Griffin,Ar' -> ('Griffin', 'Ar'). ('x', '') when there's no comma."""
    if not formatted or ',' not in formatted:
        return (formatted or '', '')
    surname, _, initials = formatted.rpartition(',')
    return (surname.strip(), initials.strip())


def forename_of(full_name: str) -> str:
    """ESPN's forename — the first word. Mirrors real_lineups.format_name, which
    treats everything after it as the surname so particles ('van', 'du') stay
    with the surname."""
    parts = (full_name or '').strip().split()
    return parts[0] if len(parts) >= 2 else ''


def _norm(s: str) -> str:
    return (s or '').replace("'", '').strip().lower()


def resolve(formatted: str, full_name: str, candidates: list[dict],
            position: str | None = None) -> tuple[dict | None, str | None]:
    """Pick the one candidate that is this player.

    `candidates` are the SuperBru player rows for the club, each at least
    {'name': 'Griffin,Ar', 'position': 'PR'}. Returns (row, rule) on a unique
    match, else (None, None).

    Ordered widest-evidence-first, and each step must be unique to win:

      1. exact          — the formatted names already agree (the common case)
      2. sole-surname   — only one player of that surname at the club, so the
                          initial can't be telling us anything more
      3. forename-prefix— SuperBru's initials are a prefix of ESPN's forename
                          ('Ar' of 'Arthur'), which is how it disambiguates
      4. first-letter   — initials start with the forename's first letter; picks
                          up SuperBru's OTHER style, multiple initials
                          ('Curry,TM' for 'Tom'), where no prefix can match

    A position hint is applied as a tiebreaker within 3 and 4 only. It is
    deliberately never used on its own: two same-position namesakes exist (both
    Bath Griffins are props), so position alone would pick one at random.
    """
    if not candidates:
        return (None, None)

    surname, initials = split_formatted(formatted)
    forename = forename_of(full_name)

    exact = [c for c in candidates if _norm(c.get('name')) == _norm(formatted)]
    if len(exact) == 1:
        return (exact[0], EXACT)

    same_surname = [c for c in candidates
                    if _norm(split_formatted(c.get('name', ''))[0]) == _norm(surname)]
    if not same_surname:
        return (None, None)
    if len(same_surname) == 1:
        return (same_surname[0], SOLE_SURNAME)

    def _unique(pool, rule):
        """One candidate wins outright, or position breaks a tie."""
        if len(pool) == 1:
            return (pool[0], rule)
        if len(pool) > 1 and position:
            narrowed = [c for c in pool if c.get('position') == position]
            if len(narrowed) == 1:
                return (narrowed[0], rule)
        return None

    if forename:
        fore = _norm(forename)
        prefix = [c for c in same_surname
                  if (ini := _norm(split_formatted(c.get('name', ''))[1]))
                  and fore.startswith(ini)]
        hit = _unique(prefix, FORENAME_PREFIX)
        if hit:
            return hit

        letter = [c for c in same_surname
                  if (ini := _norm(split_formatted(c.get('name', ''))[1]))
                  and ini[:1] == fore[:1]]
        hit = _unique(letter, FIRST_LETTER)
        if hit:
            return hit

    # Several namesakes and nothing separates them. Better a logged miss than a
    # coin flip that silently scores the wrong player.
    return (None, None)
