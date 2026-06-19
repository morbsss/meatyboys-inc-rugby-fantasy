/* =============================================================================
 * matchup.js — The Match Up page: head-to-head line-ups for the round.
 * Extracted from templates/matchup.html. Shared helpers: common.js, leagues.js, base.js.
 * ========================================================================== */

let RESULTS = [], weekSel = null, fixtureIdx = 0, IS_MTYBY = false;

async function init() {
  const data = await (await fetch('/api/competition')).json();
  // meatyboys runs on wins (no bonus points); used to flip the line-up order.
  IS_MTYBY = data.bonus === false;
  // Only weeks with real fantasy-vs-fantasy fixtures (skip byes-only entries here).
  RESULTS = (data.results || []).map(w => ({
    week: w.week,
    matches: (w.matches || []).filter(m => !m.is_bye)
  })).filter(w => w.matches.length);
  if (!RESULTS.length) {
    document.getElementById('mu-card').innerHTML = '<div class="mu-empty">No fixtures yet.</div>';
    return;
  }
  weekSel = RESULTS[RESULTS.length - 1].week;   // default: latest week with fixtures
  buildWeekFilter();
  buildFixtureFilter();
  renderMatch();
}

function buildWeekFilter() {
  const wf = document.getElementById('week-filter');
  wf.innerHTML = RESULTS.map(w => `<option value="${w.week}">Round ${w.week}</option>`).join('');
  wf.value = String(weekSel);
  wf.onchange = () => { weekSel = +wf.value; fixtureIdx = 0; buildFixtureFilter(); renderMatch(); };
}

function currentWeek() { return RESULTS.find(w => w.week === weekSel) || RESULTS[0]; }

function buildFixtureFilter() {
  const ff = document.getElementById('fixture-filter');
  const ms = currentWeek().matches;
  ff.innerHTML = ms.map((m, i) => `<option value="${i}">${esc(m.home)} v ${esc(m.away)}</option>`).join('');
  ff.value = String(fixtureIdx);
  ff.onchange = () => { fixtureIdx = +ff.value; renderMatch(); };
}

async function renderMatch() {
  const m = currentWeek().matches[fixtureIdx];
  if (!m) return;
  const card = document.getElementById('mu-card');
  card.innerHTML = '<div class="mu-loading">Loading line-ups…</div>';
  const [home, away] = await Promise.all([teamPicks(m.home, weekSel), teamPicks(m.away, weekSel)]);

  const played = m.played;
  const hs = played ? (m.home_score ?? 0) : null;
  const as = played ? (m.away_score ?? 0) : null;
  // Higher score = win (green), lower score = lose (red).
  const homeCls = played ? (hs > as ? 'win' : (hs < as ? 'lose' : '')) : '';
  const awayCls = played ? (as > hs ? 'win' : (as < hs ? 'lose' : '')) : '';

  card.innerHTML = `
    <div class="mu-head">
      <span class="mu-team-name">${esc(m.home)}</span>
      <span class="mu-vs">vs</span>
      <span class="mu-team-name away">${esc(m.away)}</span>
    </div>
    <div class="mu-grid">
      <div class="mu-col home">${colHTML(home)}</div>
      <div class="mu-col away">${colHTML(away)}</div>
    </div>
    <div class="mu-total">
      <span class="v ${homeCls}">${played ? hs.toFixed(1) : '—'}</span>
      <span class="lbl">Total</span>
      <span class="v away ${awayCls}">${played ? as.toFixed(1) : '—'}</span>
    </div>`;

  // Tap a player → shared card with their recent points and opponents.
  card.querySelectorAll('.mu-p[data-player]').forEach(el =>
    el.addEventListener('click', () => mtybyPlayerCard(+el.dataset.player)));
}

async function teamPicks(name, round) {
  try {
    return await (await fetch(`/api/team-view?name=${encodeURIComponent(name)}&round=${round}`)).json();
  } catch { return {picks: [], fr_club: null}; }
}

// A player's round points, shown toward the centre divider (right-aligned in the
// home column, left-aligned in the away column via the row's reversed flow).
// The captain's points count double (C = captain ×2).
const ptsHTML = p => {
  if (p.points === null || p.points === undefined) return '';
  // meatyboys has no captain, so it never doubles (matches the backend total).
  const v = (p.is_captain && !IS_MTYBY) ? p.points * 2 : p.points;
  return `<span class="pts">${v.toFixed(1)}</span>`;
};

// The front-row UNIT's points for the round (no captain in meatyboys).
const frPtsHTML = v =>
  (v === null || v === undefined) ? '' : `<span class="pts">${v.toFixed(1)}</span>`;

// meatyboys line-up order, TOP → BOTTOM: backs first, front row last, with the
// club FR unit pinned to the very bottom. (OFDS keeps real jersey order.)
const MTYBY_ORDER = ['OBK', 'MID', 'FH', 'SH', 'LF', 'LK', 'HK', 'PR'];
const mtybyRank = pos => {
  const i = MTYBY_ORDER.indexOf(pos);
  return i === -1 ? MTYBY_ORDER.length : i;
};

// Put the front row in real jersey order — loosehead prop, hooker, tighthead
// prop (PR, HK, PR) — then the rest of the XV in their existing grouped order.
function orderStarters(starters) {
  const props = starters.filter(p => p.position === 'PR');
  const hooks = starters.filter(p => p.position === 'HK');
  const front = [props[0], hooks[0], props[1]].filter(Boolean);
  const seen = new Set(front);
  const rest = starters.filter(p => !seen.has(p) && p.position !== 'PR' && p.position !== 'HK');
  const spareFront = [...props, ...hooks].filter(p => !seen.has(p));   // defensive: odd counts
  return [...front, ...spareFront, ...rest];
}

function colHTML(team) {
  const all = (team.picks || []).filter(p => !p.is_bench);
  const playerRow = p => ({
    pid: p.player_id,
    inner: `<span class="mtyby-pos">${p.position}</span>`
      + `<span class="nm"><b>${esc(p.name)}</b>${(p.is_captain && !IS_MTYBY) ? '<span class="ck">C</span>' : ''}</span>`
      + ptsHTML(p),
  });
  const frRow = () => ({
    pid: null,
    inner: `<span class="mtyby-pos">FR</span><span class="nm"><b>${esc(team.fr_club)} FR</b></span>`
      + frPtsHTML(team.fr_points),
  });

  const rows = [];
  if (IS_MTYBY) {
    // Backs at the top, front row at the bottom; FR unit pinned last.
    const starters = all.slice().sort((a, b) => mtybyRank(a.position) - mtybyRank(b.position));
    starters.forEach(p => rows.push(playerRow(p)));
    if (team.fr_club) rows.push(frRow());
  } else {
    // OFDS: real jersey order with the front row at the top.
    if (team.fr_club) rows.push(frRow());
    orderStarters(all).forEach(p => rows.push(playerRow(p)));
  }

  return rows.map((row, i) =>
    `<div class="mu-p${i % 2 ? ' alt' : ''}${row.pid ? ' mu-clickable' : ''}"`
    + `${row.pid ? ` data-player="${row.pid}"` : ''}>${row.inner}</div>`).join('')
    || '<div class="mu-p">-</div>';
}

function esc(s){ return String(s ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }
init();
