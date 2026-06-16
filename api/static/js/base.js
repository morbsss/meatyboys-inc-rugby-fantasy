/* =============================================================================
 * base.js — the shared app chrome, loaded on every page (via base.html).
 *
 * Owns the bits that live in the shared layout, not any one page:
 *   • toast notifications (window.ofdsToast)
 *   • the signed-in user chip + the slide-up Profile sheet
 *   • profile actions: rename team, commissioner toggle, change password
 *   • logout
 *
 * These functions are intentionally GLOBAL: the profile sheet markup in
 * base.html wires them up with inline `onclick="..."` handlers.
 * ========================================================================== */

/** Show a transient toast. `kind` is '' | 'ok' | 'err' (styles the pill). */
window.ofdsToast = function (msg, kind) {
  const el = document.getElementById('ofds-toast');
  if (!el) return;
  el.textContent = msg;
  el.className = 'ofds-toast' + (kind ? ' ofds-toast--' + kind : '') + ' is-visible';
  clearTimeout(window.__ofdsToastT);
  window.__ofdsToastT = setTimeout(() => { el.classList.remove('is-visible'); }, 3000);
};

// The currently signed-in user (from /api/auth/user); null when logged out.
window.__ofdsUser = null;

// ---- Session + profile sheet ----------------------------------------------

/** Fetch the current user and paint the chip + profile sheet, or hide them. */
async function checkUserSession() {
  try {
    const res = await fetch('/api/auth/user');
    if (!res.ok) {
      document.getElementById('user-chip').style.display = 'none';
      document.getElementById('logout-btn').style.display = 'none';
      return;
    }
    const user = await res.json();
    const initial = (user.team_name || user.username || '?').trim().charAt(0).toUpperCase();

    document.getElementById('user-chip').style.display = 'flex';
    document.getElementById('user-avatar').textContent = initial;
    document.getElementById('user-info').textContent = user.team_name || user.username;
    document.getElementById('logout-btn').style.display = '';

    const avatarSheet = document.getElementById('user-avatar-sheet');
    const infoSheet = document.getElementById('user-info-sheet');
    if (avatarSheet) avatarSheet.textContent = initial;
    if (infoSheet) infoSheet.textContent = user.team_name ? `${user.username} · ${user.team_name}` : user.username;

    renderProfile(user);
  } catch (err) {
    console.error('Failed to check session:', err);
  }
}

/** Paint the profile sheet (team name, commissioner toggle) from a user. */
function renderProfile(user) {
  window.__ofdsUser = user;

  // Team name + (conditionally shown) rename control.
  const nameEl = document.getElementById('profile-team-name');
  if (nameEl) nameEl.textContent = user.team_name || '—';
  const editBtn = document.getElementById('profile-team-edit-btn');
  const lockedEl = document.getElementById('profile-team-locked');
  if (editBtn) editBtn.style.display = user.can_edit_team ? '' : 'none';
  if (lockedEl) lockedEl.style.display = user.can_edit_team ? 'none' : '';
  cancelTeamRename();
  cancelPwChange();

  // Commissioner toggle: ON = you hold the role; disabled when someone else does.
  const toggle = document.getElementById('profile-commish-toggle');
  const statusEl = document.getElementById('profile-commish-status');
  const heldByOther = !user.is_commissioner && !!user.commissioner_user_id;
  if (toggle) {
    toggle.setAttribute('aria-checked', user.is_commissioner ? 'true' : 'false');
    toggle.disabled = heldByOther;
  }
  if (statusEl) {
    if (user.is_commissioner) statusEl.textContent = 'You are the league commissioner.';
    else if (heldByOther) statusEl.textContent = `Held by ${user.commissioner_name} — they must step down first.`;
    else statusEl.textContent = 'No commissioner yet — toggle on to take the role.';
  }
}

// ---- Profile action: rename team ------------------------------------------

function startTeamRename() {
  document.getElementById('profile-team-view').style.display = 'none';
  document.getElementById('profile-team-edit').style.display = '';
  const input = document.getElementById('profile-team-input');
  input.value = (window.__ofdsUser && window.__ofdsUser.team_name) || '';
  input.focus();
}

function cancelTeamRename() {
  const view = document.getElementById('profile-team-view');
  const edit = document.getElementById('profile-team-edit');
  if (view) view.style.display = 'flex';
  if (edit) edit.style.display = 'none';
}

async function saveTeamRename() {
  const name = document.getElementById('profile-team-input').value.trim();
  if (!name) { ofdsToast('Enter a team name', 'error'); return; }
  const { ok, data } = await apiFetch('/api/auth/team-name', { team_name: name });
  if (ok) { ofdsToast('Team name updated'); await checkUserSession(); }
  else ofdsToast((data && data.error) || 'Could not rename team', 'error');
}

// ---- Profile action: commissioner toggle ----------------------------------

async function toggleCommissioner() {
  const user = window.__ofdsUser || {};
  const action = user.is_commissioner ? 'resign' : 'claim';
  if (action === 'resign' && !confirm('Step down as commissioner?')) return;
  const { ok, data } = await apiFetch('/api/league/commissioner', { action });
  if (ok) {
    ofdsToast(action === 'resign' ? 'You stepped down' : 'You are now the commissioner');
    await checkUserSession();
  } else ofdsToast((data && data.error) || 'Could not update commissioner', 'error');
}

// ---- Profile action: change password --------------------------------------

function startPwChange() {
  document.getElementById('profile-pw-edit-btn').style.display = 'none';
  document.getElementById('profile-pw-edit').style.display = '';
  document.getElementById('profile-pw-current').value = '';
  document.getElementById('profile-pw-new').value = '';
  document.getElementById('profile-pw-current').focus();
}

function cancelPwChange() {
  const edit = document.getElementById('profile-pw-edit');
  const btn = document.getElementById('profile-pw-edit-btn');
  if (edit) edit.style.display = 'none';
  if (btn) btn.style.display = '';
}

async function savePwChange() {
  const current = document.getElementById('profile-pw-current').value;
  const next = document.getElementById('profile-pw-new').value;
  if (!current) { ofdsToast('Enter your current password', 'error'); return; }
  if (next.length < 6) { ofdsToast('New password must be at least 6 characters', 'error'); return; }
  const { ok, data } = await apiFetch('/api/auth/password', { current_password: current, new_password: next });
  if (ok) { ofdsToast('Password updated'); cancelPwChange(); }
  else ofdsToast((data && data.error) || 'Could not change password', 'error');
}

// ---- Logout ----------------------------------------------------------------

async function logout() {
  if (!confirm('Sign out of OFDS Fantasy?')) return;
  try {
    await fetch('/api/auth/logout', { method: 'POST' });
    window.location.href = '/auth';
  } catch (err) {
    console.error('Logout failed:', err);
  }
}

// ---- Boot ------------------------------------------------------------------
checkUserSession();
