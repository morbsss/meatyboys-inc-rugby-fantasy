/* =============================================================================
 * matchup.js — The Match Up page: head-to-head line-ups for the round.
 * Extracted from templates/matchup.html. Shared helpers: common.js, leagues.js, base.js.
 * ========================================================================== */

let RESULTS = [], weekSel = null, fixtureIdx = 0;

async function init() {
  const data = await (await fetch('/api/competition')).json();
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
  const v = p.is_captain ? p.points * 2 : p.points;
  return `<span class="pts">${v.toFixed(1)}</span>`;
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
  const starters = orderStarters((team.picks || []).filter(p => !p.is_bench));
  const rows = [];
  if (team.fr_club) {
    rows.push(`<span class="ofds-pos">FR</span><span class="nm"><b>${esc(team.fr_club)} FR</b></span>`);
  }
  starters.forEach(p => rows.push(
    `<span class="ofds-pos">${p.position}</span>`
    + `<span class="nm"><b>${esc(p.name)}</b>${p.is_captain ? '<span class="ck">C</span>' : ''}</span>`
    + ptsHTML(p)));

  let html = ``;
  html += rows.map((inner, i) => `<div class="mu-p${i % 2 ? ' alt' : ''}">${inner}</div>`).join('')
    || '<div class="mu-p">-</div>';
  return html;
}

function esc(s){ return String(s ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }
init();
