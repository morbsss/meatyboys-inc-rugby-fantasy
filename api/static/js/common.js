/* =============================================================================
 * common.js — tiny utilities shared by every page's script.
 * Loaded once from base.html, before any page-specific script.
 * ========================================================================== */

/** HTML-escape a value for safe insertion into innerHTML. */
function esc(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

/** Escape for use inside a single-quoted HTML attribute. */
function escAttr(value) {
  return esc(value).replace(/'/g, '&#39;');
}

/**
 * Thin fetch wrapper. GET when no body is given, POST JSON otherwise.
 * Always resolves to { ok, status, data } (data is null for non-JSON replies).
 */
async function apiFetch(path, body) {
  const options = body
    ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }
    : {};
  const res = await fetch(path, options);
  let data = null;
  try { data = await res.json(); } catch (_) { /* response had no JSON body */ }
  return { ok: res.ok, status: res.status, data };
}

/* ---------------------------------------------------------------------------
 * Shared player card — a read-only popup with the player's per-round points
 * and who they played, fetched from /api/player/points. Used by the Player Hub
 * and Match Up (the Squad pitch has its own card with captain/bench actions).
 * ------------------------------------------------------------------------- */
function mtybyClosePlayerCard() {
  document.removeEventListener('keydown', _mtybyCardKey);
  const ex = document.querySelector('.pc-overlay');
  if (ex) ex.remove();
}
function _mtybyCardKey(e) { if (e.key === 'Escape') mtybyClosePlayerCard(); }

async function mtybyPlayerCard(playerId) {
  if (!playerId) return;
  return _mtybyShowCard('/api/player/points?id=' + encodeURIComponent(playerId));
}

// The meatyboys front-row UNIT (a club) shows its previous stats the same way an
// individual does, fetched by club rather than player id.
async function mtybyFrCard(club) {
  if (!club) return;
  return _mtybyShowCard('/api/player/points?fr=' + encodeURIComponent(club));
}

async function _mtybyShowCard(url) {
  mtybyClosePlayerCard();
  let p = null;
  try { p = await (await fetch(url)).json(); }
  catch (_) { return; }
  if (!p || p.error) return;

  const pts = p.recent_points || [];
  const rows = pts.length
    ? pts.map((r) => {
        const opp = r.opponent ? `${r.home ? 'v' : '@'} ${esc(r.opponent)}` : '';
        return `<div class="pc-pt"><span class="pc-rd">R${r.round}</span>`
          + `<span class="pc-opp">${opp}</span><b>${r.points}</b></div>`;
      }).join('')
    : `<div class="pc-none">No points from previous rounds yet.</div>`;

  const overlay = document.createElement('div');
  overlay.className = 'pc-overlay';
  overlay.innerHTML = `
    <div class="pc-card" role="dialog" aria-modal="true" aria-label="${esc(p.name)}">
      <button class="pc-x" data-pc="close" aria-label="Close">&times;</button>
      <div class="pc-head">
        <span class="mtyby-pos">${esc(p.position || '')}</span>
        <div class="pc-id">
          <div class="pc-name">${esc(p.name)}</div>
          <div class="pc-team">${esc(p.real_team || '')}</div>
        </div>
      </div>
      <div class="pc-sub">Previous rounds</div>
      <div class="pc-pts">${rows}</div>
    </div>`;
  overlay.addEventListener('click', (e) => {
    if (e.target === overlay || e.target.closest('[data-pc="close"]')) mtybyClosePlayerCard();
  });
  document.addEventListener('keydown', _mtybyCardKey);
  document.body.appendChild(overlay);
}
