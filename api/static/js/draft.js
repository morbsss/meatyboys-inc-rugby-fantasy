/* =============================================================================
 * draft.js — The Draft page: commissioner setup, snake-draft board, pick pool.
 * Extracted from templates/draft.html. Shared helpers: common.js, leagues.js, base.js.
 * ========================================================================== */

// Position filter chips come from the shared league model (see leagues.js):
// meatyboys lists the FR unit; OFDS lists individual props/hookers.
const filterPositions = () => Leagues.positionFilters(state);
let posFilter = 'ALL';
let state = null;
let autoFiring = false;
let orderDraft = null;   // commissioner working copy of the order
let commishBuilt = false; // setup form already on screen (don't clobber on poll)

async function api(path, body) {
  const opt = body ? { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(body) }
                    : {};
  const res = await fetch(path, opt);
  return { ok: res.ok, data: await res.json() };
}

async function load() {
  const { data } = await api('/api/draft');
  state = data;
  render();
  // Drive auto-draft for an absent/expired pick (spec §6.2). Any open client
  // can trigger it; the server only allows it once the clock has expired.
  if (state.status === 'live' && state.auto_due && !autoFiring) {
    autoFiring = true;
    await api('/api/draft/autopick', {});
    autoFiring = false;
    load();
  }
}

function fmtClock() {
  if (state.status !== 'live' || !state.pick_deadline) return '';
  const ms = new Date(state.pick_deadline) - new Date();
  const s = Math.max(0, Math.round(ms / 1000));
  return `${s}s`;
}

function render() {
  renderStatus();
  renderCommish();
  renderPool();
  renderRoster();
  renderBoard();
}

function renderStatus() {
  const bar = document.getElementById('status-bar');
  const lg = state.league ? state.league.name : '';
  let pill = `<span class="pill ${state.status}">${state.status}</span>`;
  let mid = '';
  if (state.status === 'pending') {
    mid = `<span class="muted">Draft not started${state.draft_at ? ' · scheduled ' + new Date(state.draft_at).toLocaleString() : ''}.</span>`;
  } else if (state.status === 'live') {
    mid = `<span class="on-clock">On the clock: <span class="accent">${state.on_clock || '—'}</span></span>
           <span class="muted">Pick ${state.current_pick} / ${state.total_picks} · Round ${state.round}</span>`;
  } else if (state.status === 'complete') {
    mid = `<span class="on-clock">Draft complete 🎉</span>`;
  }
  const clk = state.status === 'live' ? `<span class="clock ${ (new Date(state.pick_deadline)-new Date())<10000 ? 'warn':''}">${fmtClock()}</span>` : '';
  bar.innerHTML = `${pill} ${mid} ${clk}`;
}

function renderCommish(force = false) {
  const wrap = document.getElementById('commish-panel');
  if (!state.is_commissioner || state.status === 'complete') {
    wrap.innerHTML = ''; commishBuilt = false; return;
  }

  if (state.status === 'live') {
    commishBuilt = false;
    wrap.innerHTML = `<div class="ofds-card commish">
      <div class="section-title">Commissioner</div>
      <div class="muted">Each pick has a 60-second clock — when it runs out the team on the
        clock is auto-drafted a valid player. You can also force the current pick now.</div>
      <div class="row-actions">
        <button class="ofds-btn ofds-btn--secondary ofds-btn--sm" onclick="autopickNow()">Auto-pick current</button>
      </div></div>`;
    return;
  }

  // pending → setup. Don't rebuild on background polls (that wiped the typed
  // date and dropped focus); only build on first render or an explicit action.
  if (commishBuilt && !force) return;
  const cur = document.getElementById('draft-at');
  const dateVal = cur ? cur.value : toLocalInput(state.draft_at);   // keep typed value across rebuilds
  if (orderDraft === null) orderDraft = state.order.slice();
  const rows = orderDraft.map((t, i) => `
    <div class="order-team">
      <span class="idx">${i+1}</span><span class="nm">${t}</span>
      <button class="ofds-btn ofds-btn--ghost ofds-btn--sm" onclick="moveTeam(${i},-1)" ${i===0?'disabled':''}>↑</button>
      <button class="ofds-btn ofds-btn--ghost ofds-btn--sm" onclick="moveTeam(${i},1)" ${i===orderDraft.length-1?'disabled':''}>↓</button>
    </div>`).join('');
  wrap.innerHTML = `<div class="ofds-card commish">
    <div class="section-title">Commissioner — set up the draft</div>
    <div style="margin-bottom:10px;">
      <label class="muted">Draft date &amp; time (must be before the season starts)</label><br>
      <input type="datetime-local" id="draft-at" value="${dateVal}">
    </div>
    <div class="muted">Snake order</div>
    ${rows}
    <div class="row-actions">
      <button class="ofds-btn ofds-btn--ghost ofds-btn--sm" onclick="shuffleOrder()">Shuffle</button>
      <button class="ofds-btn ofds-btn--primary ofds-btn--sm" onclick="saveSetup()">Save setup</button>
      <button class="ofds-btn ofds-btn--secondary ofds-btn--sm" onclick="startDraft()">Start draft</button>
    </div>
  </div>`;
  commishBuilt = true;
}

function toLocalInput(iso) {
  if (!iso) return '';
  const d = new Date(iso); if (isNaN(d)) return '';
  const pad = n => String(n).padStart(2,'0');
  return `${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

window.moveTeam = (i, dir) => {
  const j = i + dir;
  if (j < 0 || j >= orderDraft.length) return;
  [orderDraft[i], orderDraft[j]] = [orderDraft[j], orderDraft[i]];
  renderCommish(true);
};
window.shuffleOrder = () => {
  for (let i = orderDraft.length-1; i>0; i--) { const j = Math.floor(Math.random()*(i+1)); [orderDraft[i],orderDraft[j]]=[orderDraft[j],orderDraft[i]]; }
  renderCommish(true);
};
window.saveSetup = async () => {
  const at = document.getElementById('draft-at').value;
  const iso = at ? new Date(at).toISOString() : '';
  const { ok, data } = await api('/api/draft/setup', { draft_at: iso, order: orderDraft });
  ofdsToast(ok ? 'Draft setup saved' : (data.error || 'Failed'), ok ? 'ok' : 'err');
  if (ok) load();
};
window.startDraft = async () => {
  const { ok, data } = await api('/api/draft/start', {});
  ofdsToast(ok ? 'Draft started!' : (data.error || 'Failed'), ok ? 'ok' : 'err');
  if (ok) { orderDraft = null; load(); }
};
window.autopickNow = async () => {
  const { ok, data } = await api('/api/draft/autopick', {});
  ofdsToast(ok ? `Auto-picked ${data.player || ''}` : (data.error || 'Failed'), ok ? 'ok' : 'err');
  load();
};

function renderPool() {
  const filters = document.getElementById('pool-filters');
  filters.innerHTML = filterPositions().map(p =>
    `<button class="ofds-chip ${p===posFilter?'is-active':''}" onclick="setFilter('${p}')">${p}</button>`).join('');
  const list = document.getElementById('pool-list');
  const canPick = state.status === 'live' && state.is_on_clock;

  // Club front-row units (meatyboys only) — listed inline in the pool as
  // ordinary, OPTIONAL picks (max one per team). Hidden once you own one.
  let frHtml = '';
  if (Leagues.isMeatyboys(state) && !state.your_fr && (posFilter === 'ALL' || posFilter === 'FR')) {
    const clubs = (state.available_fr || []).slice(0, 40);
    if (clubs.length) {
      frHtml = 
          clubs.map(c => `<div class="player-row">
            <span class="ofds-pos">FR</span>
            <span class="nm">${esc(c.club)} FR</span>
            <span class="rk">${(c.rank||0).toFixed ? c.rank.toFixed(0) : c.rank}</span>
            ${canPick ? `<button class="ofds-btn ofds-btn--primary ofds-btn--sm" onclick="pickFr('${escAttr(c.club)}')">Draft</button>` : ''}
          </div>`).join('');
    }
  }

  let players = state.available || [];
  if (posFilter !== 'ALL') players = players.filter(p => p.position === posFilter);
  players = players.slice(0, 60);
  const playersHtml = players.map(p => `
    <div class="player-row">
      <span class="ofds-pos">${p.position}</span>
      <span class="nm">${esc(p.name)} <small>${esc(p.real_team)}</small></span>
      <span class="rk">${(p.rank||0).toFixed ? p.rank.toFixed(0) : p.rank}</span>
      ${canPick ? `<button class="ofds-btn ofds-btn--primary ofds-btn--sm" onclick="pick(${p.id})">Draft</button>` : ''}
    </div>`).join('');
  list.innerHTML = (frHtml + playersHtml) || '<div class="muted" style="padding:8px;">No players.</div>';
}
window.setFilter = (p) => { posFilter = p; renderPool(); };

window.pick = async (id) => {
  const { ok, data } = await api('/api/draft/pick', { player_id: id });
  ofdsToast(ok ? `Drafted ${data.player}` : (data.error || 'Failed'), ok ? 'ok' : 'err');
  load();
};
window.pickFr = async (club) => {
  const { ok, data } = await api('/api/draft/pick', { fr_club: club });
  ofdsToast(ok ? `Drafted ${data.player}` : (data.error || 'Failed'), ok ? 'ok' : 'err');
  load();
};

function esc(s){ return String(s ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }
function escAttr(s){ return esc(s).replace(/'/g,'&#39;'); }

function renderRoster() {
  const countEl = document.getElementById('roster-count');
  const listEl = document.getElementById('roster-list');
  if (!listEl) return;
  const roster = state.your_roster || [];
  const frOwned = Leagues.isMeatyboys(state) && !!state.your_fr;
  const target = state.draft_picks || 16;          // full squad size per team
  const picked = roster.length + (frOwned ? 1 : 0);
  if (countEl) countEl.textContent = state.your_team ? `${picked}/${target}` : '';

  let html = '';
  if (frOwned) html += `<div class="roster-item"><span class="ofds-pos">FR</span><span>${esc(state.your_fr)} FR</span></div>`;
  html += roster.map(p => `<div class="roster-item"><span class="ofds-pos">${esc(p.position)}</span><span>${esc(p.name)}</span></div>`).join('');
  if (!html) html = '<div class="muted">No players drafted yet.</div>';

  // Helper chips (advisory): unfilled position quotas (+ the optional FR unit
  // for meatyboys). For OFDS these are the exact remaining squad slots.
  const needChips = [];
  if (Leagues.isMeatyboys(state) && !frOwned) needChips.push('FR');
  Object.entries(state.your_needs || {}).filter(([, n]) => n > 0)
    .forEach(([slot, n]) => needChips.push(`${slot} ×${n}`));
  if (needChips.length) {
    html += `<div class="needs">${needChips.map(c => `<span class="need-chip">${esc(c)}</span>`).join('')}</div>`;
  }

  listEl.innerHTML = html;
}

function renderBoard() {
  const b = document.getElementById('board');
  const rows = (state.board || []).slice().reverse();
  b.innerHTML = rows.length
    ? rows.map(p => `<div class="board-row"><span class="pk">#${p.pick_number}</span>
        <span class="tm">${p.team_name}</span>
        <span>${p.name || '—'} <span class="ofds-pos">${p.position||''}</span></span>
        ${p.is_auto ? '<span class="auto">auto</span>' : ''}</div>`).join('')
    : '<div class="muted">No picks yet.</div>';
}

load();
setInterval(load, 1000);
