/* =============================================================================
 * transfers.js - The Transfers page: completed trades & free-agent move history.
 * Extracted from templates/transfers.html. Shared helpers: common.js, leagues.js, base.js.
 * ========================================================================== */

async function init() {
  let data;
  try { data = await (await fetch('/api/trades')).json(); }
  catch { document.getElementById('feed').innerHTML = '<div class="loading">Failed to load - refresh to retry.</div>'; return; }

  renderPending(data);

  const feed = document.getElementById('feed');
  const hist = data.history || [];
  if (!hist.length) {
    feed.innerHTML = `<div class="empty"><h3>No trades yet</h3>
      <p>Free-agent pickups and team-to-team trades will appear here.</p>
      <a class="mtyby-btn mtyby-btn--primary mtyby-btn--sm" href="/players">Make a move in the Player Hub</a></div>`;
    return;
  }
  feed.innerHTML = hist.map(rowHTML).join('');
}

// Pending offers: incoming (accept/decline) + outgoing (cancel, awaiting reply).
function renderPending(data) {
  const card = document.getElementById('pending-card');
  const list = document.getElementById('pending-list');
  if (!card || !list) return;
  const inc = data.incoming || [], out = data.outgoing || [];
  const locked = !!data.is_locked;
  if (!inc.length && !out.length) { card.hidden = true; return; }
  card.hidden = false;

  let html = '';
  inc.forEach(t => {
    html += `<div class="offer">
      <span class="desc"><span class="badge tr">Offer</span>
        <b>${esc(t.from_team)}</b> wants to offer <span class="in">${plyr(t.out_player)}</span>
        for <span class="out">${plyr(t.in_player)}</span></span>
      <span class="offer-actions">
        <button class="mtyby-btn mtyby-btn--primary mtyby-btn--sm" data-act="accept" data-id="${t.id}" ${locked ? 'disabled' : ''}>Accept</button>
        <button class="mtyby-btn mtyby-btn--ghost mtyby-btn--sm" data-act="reject" data-id="${t.id}">Decline</button>
      </span>
    </div>`;
  });
  out.forEach(t => {
    html += `<div class="offer">
      <span class="desc"><span class="badge fa">Sent</span>
        To <b>${esc(t.to_team)}</b>: you give <span class="out">${plyr(t.out_player)}</span>,
        get <span class="in">${plyr(t.in_player)}</span> <em>(awaiting reply)</em></span>
      <span class="offer-actions">
        <button class="mtyby-btn mtyby-btn--ghost mtyby-btn--sm" data-act="cancel" data-id="${t.id}">Cancel</button>
      </span>
    </div>`;
  });
  list.innerHTML = html;
  list.querySelectorAll('button[data-act]').forEach(b =>
    b.addEventListener('click', () => respond(b.dataset.act, +b.dataset.id)));
}

async function respond(action, id) {
  const msg = action === 'accept' ? 'Accept this trade? Players swap immediately.'
            : action === 'reject' ? 'Decline this trade offer?'
            : 'Cancel your trade offer?';
  if (!confirm(msg)) return;
  const url  = action === 'cancel' ? '/api/trades/cancel' : '/api/trades/respond';
  const body = action === 'cancel' ? { trade_id: id } : { trade_id: id, action };
  const res  = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  let data = {};
  try { data = await res.json(); } catch (_) { /* no body */ }
  if (typeof mtybyToast === 'function') {
    mtybyToast(res.ok ? `Trade ${data.status || 'updated'}` : (data.error || 'Failed'), res.ok ? 'ok' : 'err');
  }
  if (res.ok) {
    init();
    if (typeof checkTradeOffers === 'function') checkTradeOffers();   // refresh the header badge
  }
}

function plyr(p) { return p ? `${esc(p.name)} <span class="pos">${p.position}</span>` : '-'; }

function rowHTML(t) {
  const when = t.resolved_at || t.created_at;
  const whenStr = when ? new Date(when).toLocaleString('en-GB', {day:'numeric', month:'short', hour:'2-digit', minute:'2-digit'}) : '';
  if (t.type === 'free_agent') {
    // from_team picked up in_player, dropped out_player (→ free agents)
    return `<div class="trade">
      <span class="badge fa">Free agent</span>
      <div class="body">
        <div class="teams">${esc(t.from_team)}</div>
        <div class="move">In <span class="in">${plyr(t.in_player)}</span> · Out <span class="out">${plyr(t.out_player)}</span></div>
        <div class="when">${whenStr}</div>
      </div></div>`;
  }
  // player_trade: from_team gave out_player, received in_player from to_team
  return `<div class="trade">
    <span class="badge tr">Trade</span>
    <div class="body">
      <div class="teams">${esc(t.from_team)}<span class="arrow">⇄</span>${esc(t.to_team || '')}</div>
      <div class="move"><b>${esc(t.from_team)}</b> traded <span class="out">${plyr(t.out_player)}</span>
      for <span class="in">${plyr(t.in_player)}</span></div>
      <div class="when">${whenStr}</div>
    </div></div>`;
}

function esc(s){ return String(s ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }
init();
