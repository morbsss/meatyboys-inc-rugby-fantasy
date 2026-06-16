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
