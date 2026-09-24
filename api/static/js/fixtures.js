/* =============================================================================
 * fixtures.js - The Fixtures page: the season schedule.
 * Extracted from templates/fixtures.html. Shared helpers: common.js, leagues.js, base.js.
 * ========================================================================== */

let maxRound   = 0;
let activeWeek = 'ALL';
let regularRounds = 15;

async function init() {
  const res  = await fetch('/api/competition');
  const data = await res.json();
  regularRounds = data.regular_rounds || 15;
  // Regular season only - playoff (semi/final) fixtures live on the Finals page.
  allResults = (data.results || []).filter(r => r.week <= regularRounds);
  maxRound   = data.max_round;

  const badge = document.getElementById('round-badge');
  if (badge) badge.textContent = `Round ${maxRound}`;
  buildChips();
  render();
}

/* Round filter, built twice: chips for desktop, a select for mobile.
 *
 * Both live in the DOM and CSS shows exactly one - no resize listener, and
 * because both route through setFilter() they can never drift apart. Fifteen
 * "Wk n" chips wrap to three rows on a phone and push the fixtures off screen;
 * one dropdown is a single row. */
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

  const sel = document.getElementById('round-select');
  if (sel) {
    sel.innerHTML = `<option value="ALL">All rounds</option>`
      + allResults.map(({ week }) =>
          `<option value="${week}">Week ${week}${week > maxRound ? ' - upcoming' : ''}</option>`).join('');
    sel.value = String(activeWeek);
    sel.addEventListener('change', e =>
      setFilter(e.target.value === 'ALL' ? 'ALL' : Number(e.target.value)));
  }
}

function setFilter(week) {
  activeWeek = week;
  document.querySelectorAll('.round-chip').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.week === String(week));
  });
  // Keep the other control in step, so switching orientation keeps the filter.
  const sel = document.getElementById('round-select');
  if (sel) sel.value = String(week);
  render();
}

function render() {
  const wrap = document.getElementById('fixtures-wrap');
  // The API sends weeks ascending; "All" reads round 1 → 15, like a fixture list.
  const weeks = activeWeek === 'ALL'
    ? allResults
    : allResults.filter(r => r.week === activeWeek);

  if (!weeks.length) {
    wrap.innerHTML = '<div class="loading">No fixtures for this round.</div>';
    return;
  }

  // One container for both views, so a single week's card is laid out on the
  // same grid track as in "All" and keeps its size. Filtering to one round used
  // to swap in a narrower centred wrapper, which visibly shrank the table.
  const cards = weeks.map(w => makeWeekCard(w.week, w.matches, w)).join('');
  wrap.innerHTML = `<div class="weeks-grid">${cards}</div>`;
}

function makeWeekCard(week, matches, timing) {
  // Show the bye (team vs round average) as the last row of each week.
  matches = matches.slice().sort((a, b) => (a.is_bye ? 1 : 0) - (b.is_bye ? 1 : 0));
  // Header, top-right: the round's date, or a live indicator while it's being
  // played. The old Played/Upcoming badge duplicated what the rows already show
  // (scores vs "Upcoming") and told you nothing about when the round is.
  // date_label is formatted server-side in the league's timezone - see
  // _round_timing in api/index.py. Don't reformat it from the ISO kickoff here:
  // the browser's zone would shift an evening fixture onto the wrong day.
  const isLive = !!(timing && timing.is_live);
  const dateStr = (timing && timing.date_label) || '';
  const headerRight = isLive
    ? `<span class="week-live"><span class="live-dot" aria-hidden="true"></span>Live</span>`
    : dateStr
      ? `<span class="week-date">${esc(dateStr)}</span>`
      : '';

  const rows = matches.map(m => {
    if (m.is_bye) {
      if (!m.played) {
        return `<div class="bye-row">${esc(m.team)} - BYE</div>`;
      }
      const tWin = m.wins, tLose = m.loses;
      return `<div class="match-row">
        <span class="match-home ${tWin ? 'winner' : ''}">
          <span class="tn">${esc(m.team)}</span>
          ${m.team_bp ? `<span class="bp-tag">BP</span>` : ``}
        </span>
        <div class="match-score-wrap">
          <span class="match-score ${tWin ? 'winner' : ''}">${m.team_score.toFixed(1)}</span>${m.team_bp ? `<span class="bp-star" title="Bonus point">*</span>` : ``}
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
        <span class="match-score ${hWin ? 'winner' : ''}">${m.home_score.toFixed(1)}</span>${m.home_bp ? `<span class="bp-star" title="Bonus point">*</span>` : ``}
        <span class="match-vsep"> v </span>
        <span class="match-score ${aWin ? 'winner' : ''}">${m.away_score.toFixed(1)}</span>${m.away_bp ? `<span class="bp-star" title="Bonus point">*</span>` : ``}
      </div>
      <span class="match-away ${aWin ? 'winner' : ''}">
        ${m.away_bp ? `<span class="bp-tag">BP</span>` : ``}
        <span class="tn">${esc(m.away)}</span>
      </span>
    </div>`;
  }).join('');

  return `<div class="week-card${isLive ? ' is-live' : ''}">
    <div class="week-card-header">
      <span>Week ${week}</span>
      ${headerRight}
    </div>
    ${rows}
    ${realFixturesHTML(timing && timing.real)}
  </div>`;
}

/** The actual club matches the fantasy round is scored from.
 *
 * Shown under the fantasy head-to-heads so you can see which real games your
 * players are in, and when. Rendered only when the league has a scraped
 * fixture list - Super Rugby has none, so the block is simply absent there.
 */
function realFixturesHTML(real) {
  if (!real || !real.length) return '';
  const rows = real.map(f => {
    const played = f.home_score !== null && f.home_score !== undefined;
    const mid = played
      ? `<span class="rf-score">${esc(String(f.home_score))}–${esc(String(f.away_score))}</span>`
      : `<span class="rf-when${f.confirmed ? '' : ' is-tbc'}">${esc(f.confirmed ? (f.kickoff || '') : 'Time TBC')}</span>`;
    return `<div class="rf-row"${f.venue ? ` title="${esc(f.venue)}"` : ''}>
      <span class="rf-team">${esc(f.home || 'TBC')}</span>
      ${mid}
      <span class="rf-team rf-team--away">${esc(f.away || 'TBC')}</span>
    </div>`;
  }).join('');
  return `<details class="rf-block">
    <summary class="rf-head">Premiership fixtures<span class="rf-count">${real.length}</span></summary>
    ${rows}
  </details>`;
}

function esc(s) {
  return String(s ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

init();
