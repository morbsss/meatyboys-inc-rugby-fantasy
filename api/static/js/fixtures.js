/* =============================================================================
 * fixtures.js — The Fixtures page: the season schedule.
 * Extracted from templates/fixtures.html. Shared helpers: common.js, leagues.js, base.js.
 * ========================================================================== */

let maxRound   = 0;
let activeWeek = 'ALL';
let regularRounds = 15;

async function init() {
  const res  = await fetch('/api/competition');
  const data = await res.json();
  regularRounds = data.regular_rounds || 15;
  // Regular season only — playoff (semi/final) fixtures live on the Finals page.
  allResults = (data.results || []).filter(r => r.week <= regularRounds);
  maxRound   = data.max_round;

  const badge = document.getElementById('round-badge');
  if (badge) badge.textContent = `Round ${maxRound}`;
  buildChips();
  render();
}

function buildChips() {
  const bar = document.getElementById('filter-bar');
  const allChip = bar.querySelector('[data-week="ALL"]');
  allChip.addEventListener('click', () => setFilter('ALL'));

  allResults.forEach(({ week }) => {
    const played = week <= maxRound;
    const btn    = document.createElement('button');
    btn.className   = `round-chip ${played ? 'played' : 'upcoming'}`;
    btn.dataset.week = String(week);
    btn.textContent  = `Wk ${week}`;
    btn.addEventListener('click', () => setFilter(week));
    bar.appendChild(btn);
  });
}

function setFilter(week) {
  activeWeek = week;
  document.querySelectorAll('.round-chip').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.week === String(week));
  });
  render();
}

function render() {
  const wrap  = document.getElementById('fixtures-wrap');
  const weeks = activeWeek === 'ALL'
    ? [...allResults].reverse()
    : allResults.filter(r => r.week === activeWeek);

  if (!weeks.length) {
    wrap.innerHTML = '<div class="loading">No fixtures for this round.</div>';
    return;
  }

  const cards = weeks.map(({ week, matches }) => makeWeekCard(week, matches)).join('');
  wrap.innerHTML = activeWeek === 'ALL'
    ? `<div class="weeks-grid">${cards}</div>`
    : `<div class="single-round">${cards}</div>`;
}

function makeWeekCard(week, matches) {
  // Show the bye (team vs round average) as the last row of each week.
  matches = matches.slice().sort((a, b) => (a.is_bye ? 1 : 0) - (b.is_bye ? 1 : 0));
  const hasData     = matches.some(m => m.played);
  const isUpcoming  = week > maxRound;
  const statusLabel = hasData ? 'Played' : isUpcoming ? 'Upcoming' : 'No data';
  const statusCls   = hasData ? 'played' : 'upcoming';

  const rows = matches.map(m => {
    if (m.is_bye) {
      if (!m.played) {
        return `<div class="bye-row">${esc(m.team)} — BYE</div>`;
      }
      const tWin = m.wins, tLose = m.loses;
      return `<div class="match-row">
        <span class="match-home ${tWin ? 'winner' : ''}">
          <span class="tn">${esc(m.team)}</span>
          ${m.team_bp ? `<span class="bp-tag">BP</span>` : ``}
        </span>
        <div class="match-score-wrap">
          <span class="match-score ${tWin ? 'winner' : ''}">${m.team_score.toFixed(1)}</span>
          <span class="match-vsep"> v </span>
          <span class="match-score bye-avg-score">${m.bye_score.toFixed(1)}</span>
        </div>
        <span class="match-away bye-label">Bye avg</span>
      </div>`;
    }
    if (!m.played) {
      return `<div class="upcoming-row">
        <span>${esc(m.home)}</span>
        <span class="upcoming-badge">Upcoming</span>
        <span class="away-name">${esc(m.away)}</span>
      </div>`;
    }
    const hWin = m.home_wins, aWin = m.away_wins;
    return `<div class="match-row">
      <span class="match-home ${hWin ? 'winner' : ''}">
        <span class="tn">${esc(m.home)}</span>
        ${m.home_bp ? `<span class="bp-tag">BP</span>` : ``}
      </span>
      <div class="match-score-wrap">
        <span class="match-score ${hWin ? 'winner' : ''}">${m.home_score.toFixed(1)}</span>
        <span class="match-vsep"> v </span>
        <span class="match-score ${aWin ? 'winner' : ''}">${m.away_score.toFixed(1)}</span>
      </div>
      <span class="match-away ${aWin ? 'winner' : ''}">
        ${m.away_bp ? `<span class="bp-tag">BP</span>` : ``}
        <span class="tn">${esc(m.away)}</span>
      </span>
    </div>`;
  }).join('');

  return `<div class="week-card">
    <div class="week-card-header">
      <span>Week ${week}</span>
      <span class="week-status ${statusCls}">${statusLabel}</span>
    </div>
    ${rows}
  </div>`;
}

function esc(s) {
  return String(s ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

init();
