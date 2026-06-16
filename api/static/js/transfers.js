/* =============================================================================
 * transfers.js — The Transfers page: completed trades & free-agent move history.
 * Extracted from templates/transfers.html. Shared helpers: common.js, leagues.js, base.js.
 * ========================================================================== */

async function init() {
  let data;
  try { data = await (await fetch('/api/trades')).json(); }
  catch { document.getElementById('feed').innerHTML = '<div class="loading">Failed to load — refresh to retry.</div>'; return; }

  const feed = document.getElementById('feed');
  const hist = data.history || [];
  if (!hist.length) {
    feed.innerHTML = `<div class="empty"><h3>No trades yet</h3>
      <p>Free-agent pickups and team-to-team trades will appear here.</p>
      <a class="ofds-btn ofds-btn--primary ofds-btn--sm" href="/players">Make a move in the Player Hub</a></div>`;
    return;
  }
  feed.innerHTML = hist.map(rowHTML).join('');
}

function plyr(p) { return p ? `${esc(p.name)} <span class="pos">${p.position}</span>` : '—'; }

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
      <div class="move"><b>${esc(t.from_team)}</b> got <span class="in">${plyr(t.in_player)}</span>,
        gave <span class="out">${plyr(t.out_player)}</span></div>
      <div class="when">${whenStr}</div>
    </div></div>`;
}

function esc(s){ return String(s ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }
init();
