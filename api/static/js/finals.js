/* =============================================================================
 * finals.js — The Finals page: playoff bracket.
 * Extracted from templates/finals.html. Shared helpers: common.js, leagues.js, base.js.
 * ========================================================================== */

let TABLE = [];          // league standings — used for seed numbers

async function init() {
  let data;
  try {
    const res = await fetch('/api/competition');
    data = await res.json();
  } catch (err) {
    document.getElementById('brackets').innerHTML =
      '<div class="finals-loading">Failed to load — refresh to retry.</div>';
    return;
  }
  const badge = document.getElementById('round-badge');
  if (badge) badge.textContent = data.max_round ?? '—';
  TABLE = data.table || [];
  renderPlayoffs(data.playoffs || null);
}

function renderPlayoffs(pl) {
  const brackets = document.getElementById('brackets');
  const status = document.getElementById('finals-status');
  if (!pl || (!pl.championship && !pl.sacko)) {
    status.textContent = '';
    brackets.innerHTML = '<div class="finals-empty"><h3>No finals yet</h3><p>The playoff brackets appear once the league has enough teams.</p></div>';
    return;
  }

  status.className = 'finals-status' + (pl.complete ? ' is-complete' : '');
  status.innerHTML = pl.complete
    ? '<span class="dot"></span>Regular season complete — playoff seeds locked.'
    : '<span class="dot"></span>Provisional seeds — finalised after Round 15.';

  let html = '';
  if (pl.championship) html += bracketHtml('Championship', '🏆', 'champ', pl.championship);
  if (pl.sacko)        html += bracketHtml('Sacko', '🥄', 'sacko', pl.sacko);
  brackets.innerHTML = html;
}

function bracketHtml(title, icon, kind, br) {
  const semis = br.semis.map((s, i) => tieHtml(`Semi-final ${i + 1}`, kind, s, false)).join('');
  const finalTie = tieHtml('Grand Final', kind, finalAsTie(br.final), true);
  const semiNote  = kind === 'sacko' ? ' · losers advance' : '';
  return `<section class="bracket bracket--${kind}">
    <div class="bracket-title">${icon} ${esc(title)}</div>
    <div class="bracket-row-label">Semi-finals · Wk 16-17 (aggregate)${semiNote}</div>
    <div class="semis-row">${semis}</div>
    <div class="bracket-row-label center"></div>
    <div class="final-row"><div class="final-wrap">${finalTie}</div></div>
  </section>`;
}

// Normalise a final into the same shape a semi side-render expects.
function finalAsTie(f) {
  return {
    home: f.home, away: f.away,
    home_agg: f.home_score, away_agg: f.away_score,
    played: f.played, winner: f.champion, _final: true,
  };
}

function tieHtml(label, kind, t, isFinal) {
  const head = `<div class="tie-head"><span>${esc(label)}</span>${
    t.played ? '' : '<span class="tie-pending">' + (isFinal ? 'TBD' : 'awaiting') + '</span>'
  }</div>`;
  return `<div class="tie ${isFinal ? 'tie--final' : ''}">
    ${head}
    ${tieSide(kind, t, 'home', isFinal)}
    ${tieSide(kind, t, 'away', isFinal)}
  </div>`;
}

function tieSide(kind, t, side, isFinal) {
  const name = t[side];
  const isWin = t.played && t.winner && t.winner === name;
  if (!name) {
    const from = (kind === 'sacko' ? 'Loser ' : 'Winner ') + (side === 'home' ? 'SF1' : 'SF2');
    return `<div class="tie-side"><span class="tie-seed">·</span><span class="tie-name tie-tbd">${from}</span><span class="tie-score tie-tbd">—</span></div>`;
  }
  const seedNo = seedNumber(name);
  const agg = t[side + '_agg'];
  const legs = (!isFinal && t.played)
    ? `<span class="legs">${fmt(t[side + '_leg1'])}+${fmt(t[side + '_leg2'])}</span>`
    : '';
  const score = t.played ? `${legs}${fmt(agg)}` : '<span class="tie-tbd">—</span>';
  const trophy = isWin && isFinal ? `<span class="tie-trophy">${kind === 'champ' ? '🏆' : '🥄'}</span>` : '';
  return `<div class="tie-side ${isWin ? 'is-winner' : ''}">
    <span class="tie-seed">${seedNo || ''}</span>
    <span class="tie-name">${esc(name)}${trophy}</span>
    <span class="tie-score">${score}</span>
  </div>`;
}

// Seed number = league-table position (1-based) of the team.
function seedNumber(name) {
  const idx = TABLE.findIndex(t => t.name === name);
  return idx >= 0 ? idx + 1 : '';
}

function fmt(v) { return (v ?? 0).toFixed(1); }
function esc(s) {
  return String(s ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

init();
