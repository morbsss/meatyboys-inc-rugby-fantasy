/* =============================================================================
 * leagues.js — the two competitions, and the shape of each squad model.
 *
 * The SERVER is the source of truth: squad/draft/player responses each carry a
 * `roster_model`. These helpers just make the mtyby-vs-OFDS distinction
 * explicit and readable on the front end, so league-specific UI branches read
 * as `Leagues.isOfds(model)` rather than poking at raw flags.
 *
 *   mtyby  (Super Rugby)  — an OPTIONAL club FRONT-ROW UNIT + flexible
 *                               individual starters + an any-position bench.
 *   ofds       (Premiership)  — a STRICT full rugby XV: 15 positioned starters
 *                               (2 PR, 1 HK, 2 LK, 3 LF, 1 SH, 1 FH, 2 MID,
 *                               3 OBK) + 8 positioned bench (one per position),
 *                               like-for-like trades, real-lineup auto-subs.
 * ========================================================================== */

const Leagues = {
  /** OFDS-style: fixed positions, like-for-like trades, real-lineup auto-sub. */
  isOfds(model) { return !!(model && model.positioned_bench); },

  /** mtyby-style: owns a club front-row unit + flexible composition. */
  isMtyby(model) { return !!(model && model.fr_unit); },

  /** Alias kept for readability where the *bench shape* is what matters. */
  isPositioned(model) { return this.isOfds(model); },

  /** Position filter chips for the player pool / hub, per model. */
  positionFilters(model) {
    return this.isMtyby(model)
      ? ['ALL', 'FR', 'LK', 'LF', 'SH', 'FH', 'MID', 'OBK']
      : ['ALL', 'PR', 'HK', 'LK', 'LF', 'SH', 'FH', 'MID', 'OBK'];
  },
};
