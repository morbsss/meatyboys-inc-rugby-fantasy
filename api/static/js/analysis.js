/* =============================================================================
 * analysis.js — reads precomputed model predictions from /api/analysis
 * (written offline by api/predict.py) and renders matchup win probabilities
 * plus the player-prediction table. Pure read/render; no compute here.
 * ========================================================================== */

let AN = null;                 // raw payload
let posF = 'ALL', mgrF = 'ALL', luF = 'ALL';
let matchupFilter = null;      // { i, teams: [home, away] }

const LU_META = { S: ['Starting', 's'], B: ['Bench', 'b'], O: ['Out', 'o'] };

function num(v) {
  return (v === null || v === undefined) ? '—' : (+v).toFixed(1);
}

async function initAnalysis() {
  let data;
  try {
    const res = await fetch('/api/analysis');
    data = await res.json();
  } catch (e) {
    document.getElementById('an-table').textContent = 'Could not load predictions.';
    return;
  }
  AN = data;

  // const roundEl = document.getElementById('an-round');
  // roundEl.textContent = data.round ? '- Round ' + data.round : '';

  if (!data.players || !data.players.length) {
    document.getElementById('an-matchups').innerHTML =
      '<div class="an-empty">No matchups available yet.</div>';
    document.getElementById('an-hint').style.display = 'none';
    const t = document.getElementById('an-table');
    t.className = 'an-empty';
    t.textContent = 'No predictions yet — the analysis job has not run for this league.';
    return;
  }

  buildFilters();
  renderMatchups();
  render();
}

function renderPosChips() {
  const wrap = document.getElementById('f-pos');
  const chips = ['ALL', ...(AN.positions || [])];
  wrap.innerHTML = chips.map(p =>
    `<button type="button" class="mtyby-chip ${p === posF ? 'is-active' : ''}" data-pos="${escAttr(p)}">${p === 'ALL' ? 'All' : esc(p)}</button>`
  ).join('');
  wrap.querySelectorAll('.mtyby-chip').forEach(c => c.addEventListener('click', () => {
    posF = c.dataset.pos;
    renderPosChips();
    render();
  }));
}

function buildFilters() {
  renderPosChips();

  const mgr = document.getElementById('f-mgr');
  mgr.innerHTML = '<option value="ALL">All managers</option>' +
    (AN.managers || []).map(m => `<option value="${escAttr(m)}">${esc(m)}</option>`).join('') +
    '<option value="FREE">Free agents</option>';
  mgr.onchange = e => {
    mgrF = e.target.value;
    matchupFilter = null;
    syncMatchupActive();
    render();
  };

  document.getElementById('f-lineup').onchange = e => { luF = e.target.value; render(); };
}

function probClass(prob, isLeader) {
  if (!isLeader) return 'trail';
  return prob >= 55 ? 'lead-strong' : 'lead-close';
}

function renderMatchups() {
  const el = document.getElementById('an-matchups');
  if (!AN.matchups || !AN.matchups.length) {
    el.innerHTML = '<div class="an-empty">No matchups available yet.</div>';
    document.getElementById('an-hint').style.display = 'none';
    return;
  }
  el.innerHTML = AN.matchups.map((m, i) => {
    const homeLead = (m.home_prob || 0) >= (m.away_prob || 0);
    const hCls = probClass(m.home_prob, homeLead);
    const aCls = probClass(m.away_prob, !homeLead);
    return `
      <div class="an-matchup" data-i="${i}" role="button" tabindex="0">
        <div class="an-mu-top">
          <div class="an-mu-side">
            <span class="an-mu-team">${esc(m.home_team)}</span>
            <span class="an-mu-pct ${hCls}">${(m.home_prob || 0).toFixed(1)}%</span>
          </div>
          <span class="an-vs">vs</span>
          <div class="an-mu-side an-mu-side--right">
            <span class="an-mu-team">${esc(m.away_team)}</span>
            <span class="an-mu-pct ${aCls}">${(m.away_prob || 0).toFixed(1)}%</span>
          </div>
        </div>
        <div class="an-bar"><div class="an-bar-fill ${homeLead ? 'is-home-lead' : ''}" style="width:${m.home_prob || 0}%"></div></div>
      </div>`;
  }).join('');

  el.querySelectorAll('.an-matchup').forEach(card => {
    const toggle = () => {
      const i = +card.dataset.i;
      const m = AN.matchups[i];
      if (matchupFilter && matchupFilter.i === i) {
        matchupFilter = null;
      } else {
        matchupFilter = { i, teams: [m.home_team, m.away_team] };
      }
      mgrF = 'ALL';
      document.getElementById('f-mgr').value = 'ALL';
      syncMatchupActive();
      render();
    };
    card.addEventListener('click', toggle);
    card.addEventListener('keydown', e => {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggle(); }
    });
  });
}

function syncMatchupActive() {
  document.querySelectorAll('.an-matchup').forEach(c => {
    c.classList.toggle('active', matchupFilter && +c.dataset.i === matchupFilter.i);
  });
}

function lineupCell(r) {
  const meta = LU_META[r.lineup];
  return meta
    ? `<span class="an-lu an-lu--${meta[1]}">${meta[0]}</span>`
    : '<span class="an-na">—</span>';
}

function rowHTML(r) {
  let vs = '—';
  if (r.opponent) vs = `${r.home ? 'v' : '@'} ${esc(r.opponent)}`;
  const owner = r.fantasy_team
    ? esc(r.fantasy_team)
    : '<span class="an-na">Free agent</span>';
  return `
    <tr class="${r.is_fr ? 'an-fr-row' : ''}">
      <td class="c-name">${esc(r.name)}</td>
      <td>${esc(r.position || '')}</td>
      <td class="c-team">${esc(r.real_team || '')}</td>
      <td class="c-vs">${vs}</td>
      <td class="c-owner">${owner}</td>
      <td><span class="an-na">None</span></td>
      <td>${lineupCell(r)}</td>
      <td>${num(r.score)}</td>
      <td class="an-strong">${num(r.proj)}</td>
      <td>${num(r.gbm)}</td>
      <td>${num(r.avg3)}</td>
      <td>${num(r.ssn_avg)}</td>
      <td>${num(r.gamma_p50)}</td>
      <td>${num(r.weibull_p50)}</td>
    </tr>`;
}

function render() {
  let rows = AN.players.slice();
  if (posF !== 'ALL') rows = rows.filter(r => r.position === posF);
  if (mgrF === 'FREE') rows = rows.filter(r => !r.fantasy_team);
  else if (mgrF !== 'ALL') rows = rows.filter(r => r.fantasy_team === mgrF);
  if (luF !== 'ALL') rows = rows.filter(r => r.lineup === luF);
  if (matchupFilter) rows = rows.filter(r => matchupFilter.teams.includes(r.fantasy_team));

  document.getElementById('an-count').textContent = `${rows.length} player${rows.length === 1 ? '' : 's'}`;

  const wrap = document.getElementById('an-table');
  if (!rows.length) {
    wrap.className = 'an-empty';
    wrap.textContent = 'No players match these filters.';
    return;
  }
  wrap.className = '';
  wrap.innerHTML = `
    <table class="an-table">
      <thead>
        <tr>
          <th class="c-name">Player</th>
          <th>Pos</th>
          <th class="c-team">Team</th>
          <th class="c-vs">vs</th>
          <th class="c-owner">Owner</th>
          <th>News</th>
          <th>Lineup</th>
          <th>Score</th>
          <th>Proj</th>
          <th>GBM</th>
          <th>3G Avg</th>
          <th>Ssn Avg</th>
          <th>γ p50</th>
          <th>W p50</th>
        </tr>
      </thead>
      <tbody>${rows.map(rowHTML).join('')}</tbody>
    </table>`;
}

document.addEventListener('DOMContentLoaded', initAnalysis);
