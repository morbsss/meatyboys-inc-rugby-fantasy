/* =============================================================================
 * player-hub.js — The Player Hub: browse/sort players, pick up free agents, trade.
 * Extracted from templates/player_hub.html. Shared helpers: common.js, leagues.js, base.js.
 * ========================================================================== */

// Position filter chips come from the shared league model (see leagues.js):
// OFDS lists individual props/hookers; meatyboys lists the club FR unit.
const positionChips = () => Leagues.positionFilters(rosterModel);
let ALL = [], myRoster = [], myTeam = null, isLocked = false, teamsList = [], roundsList = [], maxRound = 0;
let rosterModel = null;
let likeForLike = false;   // positioned squads (OFDS) trade same-position only
let pos = 'ALL', q = '', teamFilter = 'ALL', roundSel = '', metric = 'total';
let sortKey = 'value', sortDir = -1;

async function init() {
  const params = new URLSearchParams();
  if (roundSel) params.set('round', roundSel);
  params.set('metric', metric);
  const [pl, mp, tr] = await Promise.all([
    fetch('/api/players?' + params.toString()).then(r => r.json()),
    fetch('/api/my-picks').then(r => r.ok ? r.json() : {picks:[]}),
    fetch('/api/trades').then(r => r.json()),
  ]);
  ALL = pl.players || [];
  roundsList = pl.rounds || [];
  maxRound = pl.round || 0;
  teamsList = pl.teams || [];
  myRoster = mp.picks || [];
  rosterModel = mp.roster_model || null;
  likeForLike = Leagues.isOfds(rosterModel);
  myTeam = tr.my_team;
  isLocked = !!tr.is_locked;
  document.getElementById('comp-sub').textContent =
    (pl.league ? pl.league.name + ' · ' : '') + ALL.length + ' players'
    + (metric === 'form' ? ' · form (last 3)' : '') + ' · round ' + maxRound
    + (isLocked ? ' · trades locked' : '');
  renderFilters();
  renderPending(tr);
  renderChips();
  render();
}

function renderFilters() {
  const tf = document.getElementById('team-filter');
  if (!tf.dataset.built || tf.dataset.teams !== teamsList.join(',')) {
    tf.innerHTML = `<option value="ALL">All players</option><option value="FREE">Free agents</option>`
      + teamsList.map(t => `<option value="${esc(t)}">${esc(t)}</option>`).join('');
    tf.dataset.built = '1'; tf.dataset.teams = teamsList.join(',');
  }
  tf.value = teamFilter;
  const rf = document.getElementById('round-filter');
  rf.innerHTML = `<option value="">Latest (R${maxRound})</option>`
    + roundsList.map(r => `<option value="${r}">Round ${r}</option>`).join('');
  rf.value = roundSel;
  document.getElementById('m-total').classList.toggle('on', metric === 'total');
  document.getElementById('m-form').classList.toggle('on', metric === 'form');
}

function renderPending(tr) {
  const card = document.getElementById('pending-card');
  const list = document.getElementById('pending-list');
  const inc = tr.incoming || [], out = tr.outgoing || [];
  if (!inc.length && !out.length) { card.hidden = true; return; }
  card.hidden = false;
  const pl = p => p ? `${esc(p.name)} <span class="ph-team">${p.position}</span>` : '—';
  let html = '';
  inc.forEach(t => {
    html += `<div class="offer">
      <span class="desc">From <b>${esc(t.from_team)}</b>: you get <span class="you-get">${pl(t.out_player)}</span>,
        give <span class="you-give">${pl(t.in_player)}</span></span>
      <button class="ofds-btn ofds-btn--primary ofds-btn--sm" data-act="accept" data-id="${t.id}" ${isLocked?'disabled':''}>Accept</button>
      <button class="ofds-btn ofds-btn--ghost ofds-btn--sm" data-act="reject" data-id="${t.id}">Reject</button>
    </div>`;
  });
  out.forEach(t => {
    html += `<div class="offer">
      <span class="desc">To <b>${esc(t.to_team)}</b>: you give <span class="you-give">${pl(t.out_player)}</span>,
        get <span class="you-get">${pl(t.in_player)}</span> <em>(awaiting reply)</em></span>
      <button class="ofds-btn ofds-btn--ghost ofds-btn--sm" data-act="cancel" data-id="${t.id}">Cancel</button>
    </div>`;
  });
  list.innerHTML = html;
  list.querySelectorAll('button[data-act]').forEach(b =>
    b.addEventListener('click', () => respond(b.dataset.act, +b.dataset.id, b)));
}

async function respond(action, id, btn) {
  if (action === 'accept' && !confirm('Accept this trade? Players will swap immediately.')) return;
  if (action === 'reject' && !confirm('Reject this trade offer?')) return;
  if (action === 'cancel' && !confirm('Cancel your trade offer?')) return;
  let url, body;
  if (action === 'cancel') { url = '/api/trades/cancel'; body = {trade_id: id}; }
  else { url = '/api/trades/respond'; body = {trade_id: id, action}; }
  const res = await fetch(url, {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body)});
  const data = await res.json();
  ofdsToast(res.ok ? `Trade ${data.status}` : (data.error || 'Failed'), res.ok ? 'ok' : 'err');
  if (res.ok) init();
}

function renderChips() {
  document.getElementById('pos-chips').innerHTML = positionChips().map(p =>
    `<button class="ofds-chip ${p === pos ? 'is-active' : ''}" data-pos="${p}">${p}</button>`).join('');
  document.querySelectorAll('#pos-chips .ofds-chip').forEach(c =>
    c.addEventListener('click', () => { pos = c.dataset.pos; renderChips(); render(); }));
}

function render() {
  let rows = ALL.slice();
  if (pos !== 'ALL') rows = rows.filter(r => r.position === pos);
  if (teamFilter === 'FREE') rows = rows.filter(r => !r.fantasy_team);
  else if (teamFilter !== 'ALL') rows = rows.filter(r => r.fantasy_team === teamFilter);
  if (q) { const s = q.toLowerCase(); rows = rows.filter(r =>
    (r.name||'').toLowerCase().includes(s) || (r.real_team||'').toLowerCase().includes(s)); }
  rows.sort((a, b) => {
    const va = a[sortKey] ?? 0, vb = b[sortKey] ?? 0;
    if (typeof va === 'number' && typeof vb === 'number') return (va - vb) * sortDir;
    return String(va).localeCompare(String(vb)) * sortDir;
  });

  const el = document.getElementById('table');
  if (!rows.length) { el.className = 'ph-empty'; el.textContent = 'No players match.'; return; }
  el.className = '';
  const valLabel = metric === 'form' ? 'Form' : 'Pts';
  const th = (key, label, cls='') =>
    `<th class="${cls} ${sortKey === key ? 'sorted' : ''}" data-key="${key}">${label}${sortKey === key ? (sortDir < 0 ? ' ▾' : ' ▴') : ''}</th>`;
  el.innerHTML = `<table class="ph"><thead><tr>
      ${th('name','Player','c-name')} ${th('position','Pos')} ${th('value', valLabel)}
      ${th('fantasy_team','Owner')} <th class="c-act"></th>
    </tr></thead><tbody>${rows.slice(0,80).map(rowHTML).join('')}</tbody></table>`;
  el.querySelectorAll('th[data-key]').forEach(h => h.addEventListener('click', () => {
    const k = h.dataset.key;
    if (sortKey === k) sortDir *= -1; else { sortKey = k; sortDir = (k==='name'||k==='position'||k==='fantasy_team') ? 1 : -1; }
    render();
  }));
  el.querySelectorAll('button[data-trade]').forEach(b =>
    b.addEventListener('click', () => openTrade(+b.dataset.trade)));
}

function actionCell(r) {
  if (r.is_fr) return `<span class="ph-team">club unit</span>`;   // FR units not individually tradeable (yet)
  if (!myTeam) return '';
  if (r.fantasy_team === myTeam) return `<span class="yours-tag">Yours</span>`;
  if (!r.fantasy_team) return `<button class="ofds-btn ofds-btn--primary ofds-btn--sm" data-trade="${r.player_id}">Pick up</button>`;
  return `<button class="ofds-btn ofds-btn--secondary ofds-btn--sm" data-trade="${r.player_id}">Trade</button>`;
}

function fmtVal(v) { return metric === 'form' ? (v ?? 0).toFixed(1) : (v ?? 0).toFixed(0); }

function rowHTML(r) {
  const owner = r.fantasy_team
    ? `<span class="ph-owner" style="color:var(--danger)">${esc(r.fantasy_team)}</span>`
    : `<span class="ph-owner" style="color:var(--success)">Free</span>`;
  return `<tr>
    <td class="c-name"><span class="ph-name">${esc(r.name)}</span> <span class="ph-team">${esc(r.real_team||'')}</span></td>
    <td><span class="ofds-pos">${r.position}</span></td>
    <td class="ph-val">${fmtVal(r.value)}</td>
    <td>${owner}</td>
    <td class="c-act">${actionCell(r)}</td>
  </tr>`;
}

function openTrade(playerId) {
  const target = ALL.find(p => p.player_id === playerId);
  if (!target || !myTeam) return;
  const isFree = !target.fantasy_team;
  document.getElementById('ts-title').textContent =
    (isFree ? 'Pick up ' : 'Trade for ') + target.name + ' (' + target.position + ')';
  // Like-for-like squads (OFDS) can only swap same-position players, so only
  // show those; flexible squads (meatyboys) show everyone, same-position first.
  let mine = myRoster.slice();
  if (likeForLike) mine = mine.filter(p => p.position === target.position);
  else mine.sort((a,b) => (a.position===target.position?0:1) - (b.position===target.position?0:1));

  document.getElementById('ts-note').textContent = likeForLike
    ? `Choose one of your ${target.position} players to ${isFree ? 'drop' : 'offer'}.`
    : (isFree ? 'Choose one of your players to drop to free agents.'
              : `Choose one of your players to offer ${target.fantasy_team}. They must accept.`);

  const body = document.getElementById('ts-body');
  if (!mine.length) {
    body.innerHTML = `<div class="ts-row"><span class="nm">You have no ${target.position} player to swap.</span></div>`;
  } else {
    body.innerHTML = mine.map(p =>
      `<div class="ts-row" data-mine="${p.player_id}">
        <span class="ofds-pos">${p.position}</span>
        <span class="nm"><b>${esc(p.name)}</b> <span class="ph-team">${esc(p.real_team||'')}</span></span>
        <span class="ofds-btn ofds-btn--ghost ofds-btn--sm">${isFree?'Drop':'Offer'}</span>
      </div>`).join('');
    body.querySelectorAll('.ts-row').forEach(row =>
      row.addEventListener('click', () => doTrade(target, +row.dataset.mine, isFree)));
  }
  document.getElementById('trade-sheet').classList.add('is-open');
  document.body.classList.add('ofds-no-scroll');
}

async function doTrade(target, myId, isFree) {
  const mine = myRoster.find(p => p.player_id === myId);
  const msg = isFree
    ? `Pick up ${target.name} and drop ${mine ? mine.name : 'your player'}?`
    : `Offer ${mine ? mine.name : 'your player'} to ${target.fantasy_team} for ${target.name}?`;
  if (!confirm(msg)) return;
  closeTradeSheet();
  let res;
  if (isFree) {
    res = await fetch('/api/trades/free-agent', {method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({drop_id: myId, add_id: target.player_id})});
  } else {
    res = await fetch('/api/trades/propose', {method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({to_team: target.fantasy_team, give_id: myId, receive_id: target.player_id})});
  }
  const data = await res.json();
  ofdsToast(res.ok ? (isFree ? 'Picked up' : 'Trade proposed') : (data.error || 'Failed'), res.ok ? 'ok' : 'err');
  if (res.ok) init();
}

function closeTradeSheet() {
  document.getElementById('trade-sheet').classList.remove('is-open');
  document.body.classList.remove('ofds-no-scroll');
}
window.closeTradeSheet = closeTradeSheet;
document.addEventListener('keydown', e => { if (e.key === 'Escape') closeTradeSheet(); });

document.getElementById('search').addEventListener('input', e => { q = e.target.value; render(); });
document.getElementById('team-filter').addEventListener('change', e => { teamFilter = e.target.value; render(); });
document.getElementById('round-filter').addEventListener('change', e => { roundSel = e.target.value; init(); });
document.querySelectorAll('.metric-toggle button').forEach(b =>
  b.addEventListener('click', () => { metric = b.dataset.metric; init(); }));

function esc(s){ return String(s ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }
init();
