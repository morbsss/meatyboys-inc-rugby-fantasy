/* =============================================================================
 * player-hub.js — The Player Hub: browse/sort players, pick up free agents, trade.
 * Extracted from templates/player_hub.html. Shared helpers: common.js, leagues.js, base.js.
 * ========================================================================== */

// Position filter chips come from the shared league model (see leagues.js):
// OFDS lists individual props/hookers; mtyby lists the club FR unit.
const positionChips = () => Leagues.positionFilters(rosterModel);
let ALL = [], myRoster = [], myTeam = null, isLocked = false, teamsList = [], roundsList = [], maxRound = 0;
let myFrClub = null;       // the front-row club this team owns (mtyby), or null
let rosterModel = null;
let likeForLike = false;   // positioned squads (OFDS) trade same-position only
let pos = 'ALL', q = '', teamFilter = 'ALL', lineupFilter = 'ALL', roundSel = '', metric = 'total';
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
  myFrClub = mp.fr_club || null;            // my front-row unit (a tradeable asset)
  rosterModel = mp.roster_model || null;
  likeForLike = Leagues.isOfds(rosterModel);
  myTeam = tr.my_team;
  isLocked = !!tr.is_locked;
  document.getElementById('comp-sub').textContent =
    (pl.league ? pl.league.name + ' · ' : '') + ALL.length + ' players'
    + (metric === 'form' ? ' · form (last 3)' : '')
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
  rf.innerHTML = `<option value="">Total</option>`
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
      <button class="mtyby-btn mtyby-btn--primary mtyby-btn--sm" data-act="accept" data-id="${t.id}" ${isLocked?'disabled':''}>Accept</button>
      <button class="mtyby-btn mtyby-btn--ghost mtyby-btn--sm" data-act="reject" data-id="${t.id}">Reject</button>
    </div>`;
  });
  out.forEach(t => {
    html += `<div class="offer">
      <span class="desc">To <b>${esc(t.to_team)}</b>: you give <span class="you-give">${pl(t.out_player)}</span>,
        get <span class="you-get">${pl(t.in_player)}</span> <em>(awaiting reply)</em></span>
      <button class="mtyby-btn mtyby-btn--ghost mtyby-btn--sm" data-act="cancel" data-id="${t.id}">Cancel</button>
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
  mtybyToast(res.ok ? `Trade ${data.status}` : (data.error || 'Failed'), res.ok ? 'ok' : 'err');
  if (res.ok) init();
}

function renderChips() {
  document.getElementById('pos-chips').innerHTML = positionChips().map(p =>
    `<button class="mtyby-chip ${p === pos ? 'is-active' : ''}" data-pos="${p}">${p}</button>`).join('');
  document.querySelectorAll('#pos-chips .mtyby-chip').forEach(c =>
    c.addEventListener('click', () => { pos = c.dataset.pos; renderChips(); render(); }));
}

function render() {
  let rows = ALL.slice();
  if (pos !== 'ALL') rows = rows.filter(r => r.position === pos);
  if (teamFilter === 'FREE') rows = rows.filter(r => !r.fantasy_team);
  else if (teamFilter !== 'ALL') rows = rows.filter(r => r.fantasy_team === teamFilter);
  if (lineupFilter !== 'ALL') rows = rows.filter(r =>
    lineupFilter === 'UNK' ? !r.lineup : r.lineup === lineupFilter);
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
  const valLabel = metric === 'form' ? 'Form' : (roundSel ? 'Rd Pts' : 'Pts');
  const th = (key, label, cls='') =>
    `<th class="${cls} ${sortKey === key ? 'sorted' : ''}" data-key="${key}">${label}${sortKey === key ? (sortDir < 0 ? ' ▾' : ' ▴') : ''}</th>`;
  el.innerHTML = `<table class="ph"><thead><tr>
      ${th('name','Player','c-name')} ${th('position','Position')} <th class="c-next">Next</th> ${th('fantasy_team','Owner','c-owner')}
      ${th('value', valLabel)} <th class="c-lineup">Line Up</th> <th class="c-act"></th>
    </tr></thead><tbody>${rows.slice(0,80).map(rowHTML).join('')}</tbody></table>`;
  el.querySelectorAll('th[data-key]').forEach(h => h.addEventListener('click', () => {
    const k = h.dataset.key;
    if (sortKey === k) sortDir *= -1; else { sortKey = k; sortDir = (k==='name'||k==='position'||k==='fantasy_team') ? 1 : -1; }
    render();
  }));
  el.querySelectorAll('button[data-trade]').forEach(b =>
    b.addEventListener('click', () => openTrade(+b.dataset.trade)));
  el.querySelectorAll('button[data-trade-fr]').forEach(b =>
    b.addEventListener('click', () => openTradeFr(b.dataset.tradeFr)));
  // Tap a row (but not its action button) → player card with recent points.
  el.querySelectorAll('tr[data-player]').forEach(tr =>
    tr.addEventListener('click', (e) => {
      if (e.target.closest('button, [data-trade], [data-trade-fr]')) return;
      mtybyPlayerCard(+tr.dataset.player);
    }));
}

function actionCell(r) {
  if (!myTeam) return r.is_fr ? `<span class="ph-team">club unit</span>` : '';
  if (r.fantasy_team === myTeam) return `<span class="yours-tag">Yours</span>`;
  // FR units trade by club; individuals by player_id.
  const ref = r.is_fr ? `data-trade-fr="${escAttr(r.real_team)}"` : `data-trade="${r.player_id}"`;
  if (!r.fantasy_team) return `<button class="mtyby-btn mtyby-btn--primary mtyby-btn--sm" ${ref}>Pick up</button>`;
  return `<button class="mtyby-btn mtyby-btn--secondary mtyby-btn--sm" ${ref}>Trade</button>`;
}

// Season total shows whole points; per-round and form values keep one decimal.
function fmtVal(v) { return (metric === 'form' || roundSel) ? (v ?? 0).toFixed(1) : (v ?? 0).toFixed(0); }

// Who the player's real team faces next (v = home, @ = away).
function nextHTML(r) {
  return r.next
    ? `<span class="ph-next">${r.next.home ? 'v' : '@'} ${esc(r.next.opp)}</span>`
    : `<span class="ph-na">—</span>`;
}

// Real matchday status for the upcoming round: Starting / Bench / Out.
const LINEUP_LABEL = { S: ['Starting', 's'], B: ['Bench', 'b'], O: ['Out', 'o'] };
function lineupHTML(r) {
  const e = LINEUP_LABEL[r.lineup];
  return e ? `<span class="lu lu--${e[1]}">${e[0]}</span>` : `<span class="ph-na">—</span>`;
}

function rowHTML(r) {
  const owner = r.fantasy_team
    ? `<span class="ph-owner" style="color:var(--danger)">${esc(r.fantasy_team)}</span>`
    : `<span class="ph-owner" style="color:var(--success)">Free</span>`;
  return `<tr${r.player_id ? ` data-player="${r.player_id}" class="ph-clickable"` : ''}>
    <td class="c-name"><span class="ph-name">${esc(r.name)}</span> <span class="ph-team">${esc(r.real_team||'')}</span></td>
    <td><span class="mtyby-pos">${r.position}</span></td>
    <td class="c-next">${nextHTML(r)}</td>
    <td class="c-owner">${owner}</td>
    <td class="ph-val">${fmtVal(r.value)}</td>
    
    <td class="c-lineup">${lineupHTML(r)}</td>
    <td class="c-act">${actionCell(r)}</td>
  </tr>`;
}

// An asset is either an individual (from myRoster / ALL) or the FR unit, which
// carries is_fr + real_team (the club). Both flow through one trade sheet.
function openTrade(playerId) { openTradeSheet(ALL.find(p => p.player_id === playerId)); }
function openTradeFr(club)   { openTradeSheet(ALL.find(p => p.is_fr && p.real_team === club)); }
const assetName = a => a.is_fr ? `${a.real_team} FR` : a.name;

function openTradeSheet(target) {
  if (!target || !myTeam) return;
  const isFree = !target.fantasy_team;
  document.getElementById('ts-title').textContent =
    (isFree ? 'Pick up ' : 'Trade for ') + assetName(target) + (target.is_fr ? '' : ` (${target.position})`);

  // My givable assets: individuals (like-for-like only for OFDS) + my FR unit
  // (mtyby is any-to-any, so the FR can be offered for anyone).
  let mine = myRoster.slice();
  if (likeForLike) mine = mine.filter(p => p.position === target.position);
  else mine.sort((a, b) => (a.position === target.position ? 0 : 1) - (b.position === target.position ? 0 : 1));
  if (!likeForLike && myFrClub) {
    mine.push({ is_fr: true, real_team: myFrClub, name: `${myFrClub} FR`, position: 'FR' });
  }

  document.getElementById('ts-note').textContent = isFree
    ? 'Choose one of your players to drop.'
    : `Choose one of your players to offer ${target.fantasy_team}. They must accept.`;

  const body = document.getElementById('ts-body');
  if (!mine.length) {
    body.innerHTML = `<div class="ts-row"><span class="nm">You have nothing to swap.</span></div>`;
  } else {
    body.innerHTML = mine.map((p, i) =>
      `<div class="ts-row" data-i="${i}">
        <span class="mtyby-pos">${p.position}</span>
        <span class="nm"><b>${esc(p.name)}</b> <span class="ph-team">${esc(p.is_fr ? '' : (p.real_team || ''))}</span></span>
        <span class="mtyby-btn mtyby-btn--ghost mtyby-btn--sm">${isFree ? 'Drop' : 'Offer'}</span>
      </div>`).join('');
    body.querySelectorAll('.ts-row').forEach(row =>
      row.addEventListener('click', () => doTrade(target, mine[+row.dataset.i], isFree)));
  }
  document.getElementById('trade-sheet').classList.add('is-open');
  document.body.classList.add('mtyby-no-scroll');
}

async function doTrade(target, give, isFree) {
  const msg = isFree
    ? `Pick up ${assetName(target)} and drop ${assetName(give)}?`
    : `Offer ${assetName(give)} to ${target.fantasy_team} for ${assetName(target)}?`;
  if (!confirm(msg)) return;
  closeTradeSheet();
  const body = {};
  let res;
  if (isFree) {
    if (give.is_fr) body.drop_fr = true; else body.drop_id = give.player_id;
    if (target.is_fr) body.add_fr = target.real_team; else body.add_id = target.player_id;
    res = await fetch('/api/trades/free-agent', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  } else {
    body.to_team = target.fantasy_team;
    if (give.is_fr) body.give_fr = true; else body.give_id = give.player_id;
    if (target.is_fr) body.receive_fr = target.real_team; else body.receive_id = target.player_id;
    res = await fetch('/api/trades/propose', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  }
  const data = await res.json();
  mtybyToast(res.ok ? (isFree ? 'Picked up' : 'Trade proposed') : (data.error || 'Failed'), res.ok ? 'ok' : 'err');
  if (res.ok) init();
}

function closeTradeSheet() {
  document.getElementById('trade-sheet').classList.remove('is-open');
  document.body.classList.remove('mtyby-no-scroll');
}
window.closeTradeSheet = closeTradeSheet;
document.addEventListener('keydown', e => { if (e.key === 'Escape') closeTradeSheet(); });

document.getElementById('search').addEventListener('input', e => { q = e.target.value; render(); });
document.getElementById('team-filter').addEventListener('change', e => { teamFilter = e.target.value; render(); });
document.getElementById('lineup-filter').addEventListener('change', e => { lineupFilter = e.target.value; render(); });
document.getElementById('round-filter').addEventListener('change', e => { roundSel = e.target.value; init(); });
document.querySelectorAll('.metric-toggle button').forEach(b =>
  b.addEventListener('click', () => { metric = b.dataset.metric; init(); }));

function esc(s){ return String(s ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }
init();
