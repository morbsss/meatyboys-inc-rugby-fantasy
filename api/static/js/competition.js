/* =============================================================================
 * competition.js — The League Table / Competition page: standings, weekly results, playoffs.
 * Extracted from templates/competition.html. Shared helpers: common.js, leagues.js, base.js.
 * ========================================================================== */

/* ============================================================
   STATE
   ============================================================ */
let TABLE = [];          // league table rows (sorted)
let RESULTS = [];        // [{week, matches:[...]}] — source for weekly scores
let HISTORY = [];        // [{round, order:[names]}] — per-round standings
let MOVE = {};           // team → {dir:'up'|'down'|'same', delta}
let TEAM_COLORS = {};
let champEnd = 0;        // championship = indices [0, champEnd)
let sackoStart = 0;      // sacko        = indices [sackoStart, n)

// Previous championships per team → one 🏆 each (hard-coded for now).
const CHAMPS = {
  'Seldom': 1,
  'Pizza Morahana': 2,
  'Dulwich Panthers': 1,
  'Chessums Cheerleaders': 1,
};

// Previous Sacko (wooden-spoon) finishes per team → one 🍆 each, shown under
// the trophies (hard-coded for now).
const SACKOS = {
  'Pizza Morahana': 1,
  'Bread XV': 1,
  'George XV': 2,
};

/* ============================================================
   BOOT
   ============================================================ */
async function init() {
  let data;
  try {
    const res = await fetch('/api/competition');
    data = await res.json();
  } catch (err) {
    document.getElementById('table-wrap').innerHTML =
      '<div class="lt-loading">Failed to load — refresh to retry.</div>';
    return;
  }
  const badge = document.getElementById('round-badge');
  if (badge) badge.textContent = data.max_round ?? '—';
  TABLE    = data.table    || [];
  RESULTS  = data.results  || [];
  HISTORY  = data.position_history || [];
  computeMovement();
  renderTable(TABLE);
  renderPositionChart();
}

// Movement vs last round, derived from the per-round standings (spec §7).
function computeMovement() {
  MOVE = {};
  if (!HISTORY.length) return;
  const last = HISTORY[HISTORY.length - 1].order;
  const prev = HISTORY.length >= 2 ? HISTORY[HISTORY.length - 2].order : null;
  last.forEach((name, i) => {
    if (!prev) { MOVE[name] = { dir: 'same', delta: 0 }; return; }
    const pj = prev.indexOf(name);
    const delta = pj < 0 ? 0 : pj - i;   // >0 = climbed (was lower-ranked before)
    MOVE[name] = { dir: delta > 0 ? 'up' : (delta < 0 ? 'down' : 'same'), delta: Math.abs(delta) };
  });
}

/* ============================================================
   LEAGUE TABLE
   ============================================================ */
function renderTable(table) {
  const wrap = document.getElementById('table-wrap');
  if (!table.length) {
    wrap.innerHTML = '<div class="lt-empty"><h3>No standings yet</h3><p>The table fills in once results are in.</p></div>';
    return;
  }

  // Top 4 = Championship, bottom 4 = Sacko. Any team in between (e.g. a
  // 9th-placed team) sits ungrouped between the two bands.
  const n = table.length;
  champEnd   = Math.min(4, n);
  sackoStart = Math.max(champEnd, n - 4);
  const sackoCount = n - sackoStart;

  const rows = table.map((t, i) => {
    const pd    = t.points_diff;
    const pdStr = pd >= 0 ? `+${pd.toFixed(1)}` : pd.toFixed(1);
    const pdCls = pd >= 0 ? 'lt-pd-pos' : 'lt-pd-neg';

    // Side-spine cell: rendered once per band via rowspan; middle rows get an
    // empty spacer; rows covered by a rowspan above emit nothing.
    let side = '';
    if (i === 0) {
      side = `<td rowspan="${champEnd}" class="c-side lt-side lt-side--champ"><span>Championship</span></td>`;
    } else if (i === sackoStart && sackoCount > 0) {
      side = `<td rowspan="${sackoCount}" class="c-side lt-side lt-side--sacko"><span>Sacko</span></td>`;
    } else if (i >= champEnd && i < sackoStart) {
      side = `<td class="c-side lt-side lt-side--none"></td>`;
    }

    const cls = ['lt-row'];
    if (i === 0) cls.push('is-leader');
    if (i < champEnd) cls.push('in-champ');
    else if (i >= sackoStart) cls.push('in-sacko');

    const mv = MOVE[t.name] || { dir: 'same', delta: 0 };
    const mvIcon = mv.dir === 'up' ? '▲' : (mv.dir === 'down' ? '▼' : '–');
    const mvTitle = mv.dir === 'same' ? 'No change' : `${mv.dir === 'up' ? 'Up' : 'Down'} ${mv.delta} vs last round`;

    return `<tr class="${cls.join(' ')}" onclick="openTeamSheet('${escAttr(t.name)}')">
      ${side}
      <td class="c-rank lt-rank">${i + 1}</td>
      <td class="c-move"><span class="lt-move lt-move--${mv.dir}" title="${mvTitle}" aria-label="${mvTitle}">${mvIcon}</span></td>
      <td class="c-team"><span class="lt-team">${esc(t.name)}</span></td>
      <td>${t.played}</td>
      <td>${t.won}</td>
      <td class="lt-muted">${t.drawn}</td>
      <td class="lt-muted">${t.lost}</td>
      <td class="c-hide lt-muted">${t.bonus_points}</td>
      <td class="c-hide lt-muted">${t.points_for.toFixed(1)}</td>
      <td class="c-hide lt-muted">${t.points_against.toFixed(1)}</td>
      <td class="c-hide ${pdCls}">${pdStr}</td>
      <td class="lt-pts">${t.league_points}</td>
      <td class="c-champs lt-champs" title="${(CHAMPS[t.name] || 0)} championship${(CHAMPS[t.name] || 0) === 1 ? '' : 's'}, ${(SACKOS[t.name] || 0)} sacko${(SACKOS[t.name] || 0) === 1 ? '' : 's'}"><span class="lt-trophies">${'🏆'.repeat(CHAMPS[t.name] || 0)}</span><span class="lt-sackos">${'🍆'.repeat(SACKOS[t.name] || 0)}</span></td>
      <td class="c-chev"><svg class="lt-chev" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="m9 18 6-6-6-6"/></svg></td>
    </tr>`;
  }).join('');

  wrap.innerHTML = `
    <div class="lt-wrap">
      <table class="lt">
        <thead><tr>
          <th class="c-side"></th>
          <th class="c-rank">#</th><th class="c-move"></th><th class="c-team">Team</th>
          <th>P</th><th>W</th><th>D</th><th>L</th>
          <th class="c-hide">BP</th><th class="c-hide">PF</th><th class="c-hide">PA</th>
          <th class="c-hide">PD</th><th>Pts</th><th class="c-champs">Champs</th><th class="c-chev"></th>
        </tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>`;

  document.getElementById('lt-legend').hidden = false;
}

/* ============================================================
   POSITION-HISTORY CHART (spec §7) — one rank line per team
   ============================================================ */
const PH_PALETTE = ['#0B3B2E', '#E89B2C', '#2E7D4F', '#B33A2E', '#1B5340',
                    '#C97A1F', '#173A8A', '#8B5E00', '#5C6063', '#9E0C24'];

function renderPositionChart() {
  const card = document.getElementById('ph-card');
  if (HISTORY.length < 2) { card.hidden = true; return; }
  card.hidden = false;

  const teams = HISTORY[HISTORY.length - 1].order;   // current ranking
  const n = teams.length, m = HISTORY.length;
  TEAM_COLORS = {};
  teams.forEach((t, i) => TEAM_COLORS[t] = PH_PALETTE[i % PH_PALETTE.length]);

  const W = 520, H = 260, padL = 26, padR = 12, padT = 14, padB = 26;
  const plotW = W - padL - padR, plotH = H - padT - padB;
  const x = i => padL + (m === 1 ? plotW / 2 : plotW * i / (m - 1));
  const y = rank => padT + plotH * ((rank - 1) / Math.max(1, n - 1));   // rank 1 at top
  const rankAt = (name, h) => { const i = h.order.indexOf(name); return i < 0 ? n : i + 1; };

  let grid = '';
  for (let r = 1; r <= n; r++) {
    grid += `<line class="ph-grid" x1="${padL}" y1="${y(r).toFixed(1)}" x2="${W - padR}" y2="${y(r).toFixed(1)}"/>`
          + `<text class="ph-ylabel" x="${padL - 4}" y="${(y(r) + 3).toFixed(1)}" text-anchor="end">${r}</text>`;
  }
  const step = Math.ceil(m / 8);
  const xlabels = HISTORY.map((h, i) =>
    (i % step === 0 || i === m - 1)
      ? `<text class="ph-xlabel" x="${x(i).toFixed(1)}" y="${H - 8}" text-anchor="middle">R${h.round}</text>`
      : '').join('');
  const lines = teams.map(t => {
    const pts = HISTORY.map((h, i) => `${x(i).toFixed(1)},${y(rankAt(t, h)).toFixed(1)}`).join(' ');
    return `<polyline class="ph-line" data-team="${escAttr(t)}" points="${pts}" stroke="${TEAM_COLORS[t]}"/>`;
  }).join('');

  document.getElementById('ph-chart-wrap').innerHTML =
    `<svg class="ph-chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="Each team's league position by round">
      ${grid}
      <line class="ph-axis" x1="${padL}" y1="${padT}" x2="${padL}" y2="${(padT + plotH).toFixed(1)}"/>
      ${lines}${xlabels}
    </svg>`;

  const legend = document.getElementById('ph-legend');
  legend.innerHTML = teams.map(t =>
    `<span class="ph-key" data-team="${escAttr(t)}"><i style="background:${TEAM_COLORS[t]}"></i>${esc(t)}</span>`
  ).join('');
  legend.querySelectorAll('.ph-key').forEach(k =>
    k.addEventListener('click', () => highlightTeam(k.dataset.team)));
}

function highlightTeam(name) {
  const lines = document.querySelectorAll('.ph-line');
  const keys = document.querySelectorAll('.ph-key');
  const alreadyOn = [...lines].some(l => l.dataset.team === name && l.classList.contains('hl'));
  lines.forEach(l => l.classList.remove('hl', 'dim'));
  keys.forEach(k => k.classList.remove('on'));
  if (alreadyOn) return;   // toggle off → reset to all-visible
  lines.forEach(l => l.classList.add(l.dataset.team === name ? 'hl' : 'dim'));
  keys.forEach(k => { if (k.dataset.team === name) k.classList.add('on'); });
}

/* ============================================================
   TEAM DETAIL SHEET — weekly points + average
   ============================================================ */
function openTeamSheet(name) {
  const idx  = TABLE.findIndex(t => t.name === name);
  const t    = idx >= 0 ? TABLE[idx] : null;
  const rank = idx >= 0 ? idx + 1 : '—';
  const bracket = idx < 0 ? '' :
    (idx < champEnd ? 'champ' : (idx >= sackoStart ? 'sacko' : ''));

  // Rank badge
  const rankEl = document.getElementById('ts-rank');
  rankEl.textContent = rank;
  rankEl.className = 'ts-rank'
    + (idx === 0 ? ' is-leader' : (bracket ? ` is-${bracket}` : ''));

  document.getElementById('team-sheet-name').textContent = name;

  // Weekly scores series
  const series = weeklySeries(name);
  const ys  = series.map(s => s.points);
  const avg = ys.length ? ys.reduce((a, b) => a + b, 0) / ys.length : 0;
  const hi  = ys.length ? Math.max(...ys) : 0;
  const lo  = ys.length ? Math.min(...ys) : 0;

  // Record line
  const recEl = document.getElementById('ts-record');
  if (t) {
    const tag = bracket === 'champ'
      ? '<span class="ts-bracket-tag is-champ">🏆 Championship</span>'
      : bracket === 'sacko'
        ? '<span class="ts-bracket-tag is-sacko">🥄 Sacko</span>'
        : '';
    recEl.innerHTML = `<span>${t.played} played · ${t.won}W ${t.drawn}D ${t.lost}L</span>${tag}`;
  } else {
    recEl.textContent = '';
  }

  // Stat tiles — summarise the weekly scoring shown in the chart
  const statsEl = document.getElementById('ts-stats');
  statsEl.innerHTML = `
    <div class="ts-stat"><div class="ts-stat-value">${t ? t.league_points : '—'}</div><div class="ts-stat-label">Points</div></div>
    <div class="ts-stat"><div class="ts-stat-value amber">${ys.length ? avg.toFixed(1) : '—'}</div><div class="ts-stat-label">Avg / rd</div></div>
    <div class="ts-stat"><div class="ts-stat-value">${ys.length ? hi.toFixed(0) : '—'}</div><div class="ts-stat-label">High</div></div>
    <div class="ts-stat"><div class="ts-stat-value">${ys.length ? lo.toFixed(0) : '—'}</div><div class="ts-stat-label">Low</div></div>`;

  // Chart
  const body = document.getElementById('ts-body');
  if (!series.length) {
    body.innerHTML = '<div class="lt-loading">No weekly scores yet.</div>';
  } else {
    body.innerHTML =
      `<div class="ts-section-title">Weekly points</div>`
      + buildChart(series, avg)
      + `<div class="ts-chart-caption">
           <span class="key"><i></i> Weekly points</span>
           <span class="key avg"><i></i> Average (${avg.toFixed(1)})</span>
         </div>`;
  }

  openSheet();
}

// Derive a team's per-round score from the match results.
function weeklySeries(name) {
  const out = [];
  (RESULTS || []).forEach(wk => {
    let pts = null;
    for (const m of (wk.matches || [])) {
      if (m.is_bye) {
        if (m.team === name && m.played) pts = m.team_score;
      } else if (m.played) {
        if (m.home === name)      pts = m.home_score;
        else if (m.away === name) pts = m.away_score;
      }
      if (pts !== null) break;
    }
    if (pts !== null) out.push({ round: wk.week, points: pts });
  });
  return out;
}

// Build an inline SVG line chart with a dashed average line.
function buildChart(series, avg) {
  const W = 500, H = 230, padL = 38, padR = 16, padT = 16, padB = 30;
  const n = series.length;
  const plotW = W - padL - padR;
  const plotH = H - padT - padB;

  const ys = series.map(s => s.points);
  const maxY = Math.max(...ys, avg, 1);
  const niceMax = Math.max(10, Math.ceil(maxY / 10) * 10);

  const x = i => padL + (n === 1 ? plotW / 2 : plotW * i / (n - 1));
  const y = v => padT + plotH * (1 - v / niceMax);
  const yB = y(0);

  // Gridlines + y labels at 0, half, max
  const ticks = [0, niceMax / 2, niceMax];
  const grid = ticks.map(v =>
    `<line class="ts-grid" x1="${padL}" y1="${y(v).toFixed(1)}" x2="${W - padR}" y2="${y(v).toFixed(1)}"/>
     <text class="ts-ylabel" x="${padL - 6}" y="${(y(v) + 3).toFixed(1)}" text-anchor="end">${v}</text>`
  ).join('');

  // X labels (thin them out if there are many rounds)
  const step = Math.ceil(n / 8);
  const xlabels = series.map((s, i) =>
    (i % step === 0 || i === n - 1)
      ? `<text class="ts-xlabel" x="${x(i).toFixed(1)}" y="${H - 10}" text-anchor="middle">R${s.round}</text>`
      : ''
  ).join('');

  // Area + line
  let areaD = `M ${x(0).toFixed(1)},${yB.toFixed(1)} `;
  series.forEach((s, i) => { areaD += `L ${x(i).toFixed(1)},${y(s.points).toFixed(1)} `; });
  areaD += `L ${x(n - 1).toFixed(1)},${yB.toFixed(1)} Z`;
  const lineD = series.map((s, i) => `${x(i).toFixed(1)},${y(s.points).toFixed(1)}`).join(' ');

  // Dots (highlight the best week)
  const hi = Math.max(...ys);
  const dots = series.map((s, i) =>
    `<circle class="ts-dot${s.points === hi ? ' ts-dot--hi' : ''}" cx="${x(i).toFixed(1)}" cy="${y(s.points).toFixed(1)}" r="3.5">`
    + `<title>Round ${s.round}: ${(+s.points).toFixed(1)} pts</title></circle>`
  ).join('');

  // Average line + label
  const avgY = y(avg).toFixed(1);
  const avgLine = `<line class="ts-avg" x1="${padL}" y1="${avgY}" x2="${W - padR}" y2="${avgY}"/>
    <text class="ts-avg-label" x="${W - padR}" y="${(y(avg) - 5).toFixed(1)}" text-anchor="end">Avg ${avg.toFixed(0)}</text>`;

  return `<svg class="ts-chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="Weekly points with average line">
    ${grid}
    <line class="ts-axis" x1="${padL}" y1="${padT}" x2="${padL}" y2="${yB.toFixed(1)}"/>
    <line class="ts-axis" x1="${padL}" y1="${yB.toFixed(1)}" x2="${W - padR}" y2="${yB.toFixed(1)}"/>
    <path class="ts-area" d="${areaD}"/>
    ${n > 1 ? `<polyline class="ts-line" points="${lineD}"/>` : ''}
    ${avgLine}
    ${dots}
    ${xlabels}
  </svg>`;
}

/* ============================================================
   SHEET OPEN / CLOSE
   ============================================================ */
function openSheet() {
  document.getElementById('team-sheet').classList.add('is-open');
  document.body.classList.add('mtyby-no-scroll');
}
function closeTeamSheet() {
  document.getElementById('team-sheet').classList.remove('is-open');
  document.body.classList.remove('mtyby-no-scroll');
}
window.openTeamSheet = openTeamSheet;
window.closeTeamSheet = closeTeamSheet;
document.addEventListener('keydown', e => { if (e.key === 'Escape') closeTeamSheet(); });

/* ============================================================
   HELPERS
   ============================================================ */
function esc(s) {
  return String(s ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}
function escAttr(s) {
  return esc(s).replace(/'/g,'&#39;').replace(/"/g,'&quot;');
}

init();
