/* =============================================================================
 * base.js — the shared app chrome, loaded on every page (via base.html).
 *
 * Owns the bits that live in the shared layout, not any one page:
 *   • toast notifications (window.mtybyToast)
 *   • the signed-in user chip + the slide-up Profile sheet
 *   • profile actions: rename team, commissioner toggle, change password
 *   • logout
 *
 * These functions are intentionally GLOBAL: the profile sheet markup in
 * base.html wires them up with inline `onclick="..."` handlers.
 * ========================================================================== */

/** Show a transient toast. `kind` is '' | 'ok' | 'err' (styles the pill). */
window.mtybyToast = function (msg, kind) {
  const el = document.getElementById('mtyby-toast');
  if (!el) return;
  el.textContent = msg;
  el.className = 'mtyby-toast' + (kind ? ' mtyby-toast--' + kind : '') + ' is-visible';
  clearTimeout(window.__mtybyToastT);
  window.__mtybyToastT = setTimeout(() => { el.classList.remove('is-visible'); }, 3000);
};

// The currently signed-in user (from /api/auth/user); null when logged out.
window.__mtybyUser = null;

// ---- "More" menu (bottom nav) ---------------------------------------------

/** Open/close the overflow menu. The nav button's icon swaps bars <-> X via
 *  the `is-open` class, so the same control closes what it opened. */
function toggleMoreMenu() {
  const menu = document.getElementById('more-menu');
  if (!menu) return;
  menu.hidden ? openMoreMenu() : closeMoreMenu();
}

function openMoreMenu() {
  const menu = document.getElementById('more-menu');
  const back = document.getElementById('more-backdrop');
  const btn = document.getElementById('more-btn');
  if (!menu) return;
  menu.hidden = false;
  if (back) back.hidden = false;
  if (btn) { btn.classList.add('is-open'); btn.setAttribute('aria-expanded', 'true'); }
}

function closeMoreMenu() {
  const menu = document.getElementById('more-menu');
  const back = document.getElementById('more-backdrop');
  const btn = document.getElementById('more-btn');
  if (!menu) return;
  menu.hidden = true;
  if (back) back.hidden = true;
  if (btn) { btn.classList.remove('is-open'); btn.setAttribute('aria-expanded', 'false'); }
}

// Escape closes it, like the other overlays.
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') closeMoreMenu();
});

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

    const roundEl = document.getElementById('header-round');
    if (roundEl && user.current_round != null) {
      // Round 0 = pre-season (draft not finished / no rounds scored yet).
      roundEl.textContent = user.current_round > 0 ? `Round ${user.current_round}` : 'Pre-season';
      roundEl.style.display = '';
    } else if (roundEl) {
      roundEl.style.display = 'none';
    }

    const avatarSheet = document.getElementById('user-avatar-sheet');
    const infoSheet = document.getElementById('user-info-sheet');
    if (avatarSheet) avatarSheet.textContent = initial;
    // Show "username · team" once the team has its own name; until then (team
    // still defaults to the username) just show the username to avoid "a · a".
    if (infoSheet) infoSheet.textContent =
      (user.team_name && user.team_name !== user.username)
        ? `${user.username} · ${user.team_name}` : user.username;

    renderProfile(user);
  } catch (err) {
    console.error('Failed to check session:', err);
  }
}

/** Paint the profile sheet (username, team name, commissioner toggle) from a user. */
function renderProfile(user) {
  window.__mtybyUser = user;

  // Username (login) — always changeable.
  const unameEl = document.getElementById('profile-username');
  if (unameEl) unameEl.textContent = user.username || '—';

  // Team name + (conditionally shown) create/rename control. Until the manager
  // names their team it defaults to their username, so show a placeholder and a
  // "Name your team" prompt; once named, it's a "Rename".
  const named = !!(user.team_name && user.team_name !== user.username);
  const nameEl = document.getElementById('profile-team-name');
  if (nameEl) {
    nameEl.textContent = named ? user.team_name : 'Not named yet';
    nameEl.style.color = named ? 'var(--ink)' : 'var(--ink-faint)';
  }
  const editBtn = document.getElementById('profile-team-edit-btn');
  const lockedEl = document.getElementById('profile-team-locked');
  const hintEl = document.getElementById('profile-team-hint');
  if (editBtn) {
    editBtn.textContent = named ? 'Rename' : 'Name your team';
    editBtn.style.display = user.can_edit_team ? '' : 'none';
  }
  if (lockedEl) lockedEl.style.display = user.can_edit_team ? 'none' : '';
  if (hintEl) hintEl.style.display = user.can_edit_team ? '' : 'none';
  cancelTeamRename();
  cancelUsernameChange();
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

  // Commissioner-only: the "reset a member's password" control.
  const resetBox = document.getElementById('profile-commish-reset');
  if (resetBox) {
    if (user.is_commissioner) { resetBox.style.display = ''; populateResetMembers(); }
    else { resetBox.style.display = 'none'; }
  }

  applyForcedPasswordChange(!!user.must_change_password);
}

// ---- Forced password change (after a commissioner reset) -------------------

// True while the signed-in user is on a temporary password. The profile sheet
// is pinned open until they set a real one.
window.__mtybyForcePw = false;

function applyForcedPasswordChange(forced) {
  window.__mtybyForcePw = forced;
  const banner = document.getElementById('profile-force-pw');
  const sheet = document.getElementById('profile-sheet');
  if (banner) banner.hidden = !forced;
  // The sheet isn't rendered on the auth page (base.html gates it on
  // current_page != 'auth'); the redirect to / paints it a moment later.
  if (!forced || !sheet) return;

  // Pin the profile open on the password field: this is the "take them to the
  // profile" step of the reset flow.
  sheet.classList.add('is-open');
  startPwChange();
  const cancel = document.querySelector('#profile-pw-edit button[onclick="cancelPwChange()"]');
  if (cancel) cancel.style.display = 'none';   // nothing to go back to
  const cur = document.getElementById('profile-pw-current');
  if (cur) cur.placeholder = 'Temporary password';
}

/** Close the profile sheet, unless a temporary password still needs replacing. */
function dismissProfileSheet() {
  const sheet = document.getElementById('profile-sheet');
  if (window.__mtybyForcePw) {
    mtybyToast('Set a new password to continue', 'err');
    const nw = document.getElementById('profile-pw-new');
    if (nw) nw.focus();
    return;
  }
  if (sheet) sheet.classList.remove('is-open');
}

// Fill the member picker from /api/league/members — every signed-up manager in
// the commissioner's league. (It used to read /api/auth/teams, which only lists
// teams that already have squad selections, so brand-new members were missing.)
async function populateResetMembers() {
  const sel = document.getElementById('reset-member-select');
  if (!sel) return;
  try {
    const res = await fetch('/api/league/members');
    if (!res.ok) throw new Error('members');
    const members = await res.json();
    sel.innerHTML = (members && members.length)
      ? members.map((m) => {
          // team_name defaults to username until they name a team; don't show "a (a)".
          const label = (m.team_name && m.team_name !== m.username)
            ? `${m.username} · ${m.team_name}` : m.username;
          const flag = m.awaiting_reset ? ' — reset pending' : '';
          return `<option value="${escAttr(String(m.user_id))}">${esc(label + flag)}</option>`;
        }).join('')
      : '<option value="">No other members yet</option>';
  } catch (_) {
    sel.innerHTML = '<option value="">Could not load members</option>';
  }
}

// Issue a member a temporary password and show it for the commissioner to pass on.
async function resetMemberPassword() {
  const sel = document.getElementById('reset-member-select');
  const out = document.getElementById('reset-member-result');
  const userId = sel && sel.value;
  if (!userId) { mtybyToast('Pick a member first', 'err'); return; }
  const { ok, data } = await apiFetch('/api/league/reset-member-password',
                                      { user_id: Number(userId) });
  if (ok && data) {
    const who = (data.team_name && data.team_name !== data.username)
      ? `${data.username} · ${data.team_name}` : data.username;
    out.innerHTML =
      `Temporary password for <b>${esc(who)}</b>: `
      + `<code id="reset-temp-pw" style="background:var(--surface-2); padding:2px 6px; border-radius:4px; font-weight:700; color:var(--ink);">${esc(data.temp_password)}</code> `
      + `<button type="button" class="mtyby-btn mtyby-btn--ghost mtyby-btn--sm" onclick="copyTempPassword()">Copy</button>`
      + `<br>Send it to them. They'll be taken straight to their profile to set a `
      + `new password when they sign in with it.`;
    populateResetMembers();   // refresh the "reset pending" markers
  } else {
    mtybyToast((data && data.error) || 'Could not reset password', 'err');
  }
}

/** Copy the freshly issued temporary password so it can be pasted to the member. */
function copyTempPassword() {
  const el = document.getElementById('reset-temp-pw');
  if (!el) return;
  const text = el.textContent || '';
  const done = () => mtybyToast('Temporary password copied');
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(done, () => mtybyToast('Copy failed', 'err'));
  } else {
    // http:// origins (the VM before HTTPS) don't expose the async clipboard.
    const ta = document.createElement('textarea');
    ta.value = text; document.body.appendChild(ta); ta.select();
    try { document.execCommand('copy'); done(); } catch (_) { mtybyToast('Copy failed', 'err'); }
    document.body.removeChild(ta);
  }
}

// ---- Profile action: rename team ------------------------------------------

function startTeamRename() {
  document.getElementById('profile-team-view').style.display = 'none';
  document.getElementById('profile-team-edit').style.display = '';
  const input = document.getElementById('profile-team-input');
  const u = window.__mtybyUser || {};
  // Start empty when the team is still unnamed (team_name defaults to username),
  // so the manager types a fresh name rather than editing their login.
  input.value = (u.team_name && u.team_name !== u.username) ? u.team_name : '';
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
  if (!name) { mtybyToast('Enter a team name', 'error'); return; }
  const { ok, data } = await apiFetch('/api/auth/team-name', { team_name: name });
  if (ok) { mtybyToast('Team name updated'); await checkUserSession(); }
  else mtybyToast((data && data.error) || 'Could not rename team', 'error');
}

// ---- Profile action: change username (login) ------------------------------

function startUsernameChange() {
  document.getElementById('profile-username-view').style.display = 'none';
  document.getElementById('profile-username-edit').style.display = '';
  const input = document.getElementById('profile-username-input');
  input.value = (window.__mtybyUser && window.__mtybyUser.username) || '';
  input.focus();
}

function cancelUsernameChange() {
  const view = document.getElementById('profile-username-view');
  const edit = document.getElementById('profile-username-edit');
  if (view) view.style.display = 'flex';
  if (edit) edit.style.display = 'none';
}

async function saveUsernameChange() {
  const name = document.getElementById('profile-username-input').value.trim();
  if (!name) { mtybyToast('Enter a username', 'error'); return; }
  const { ok, data } = await apiFetch('/api/auth/username', { username: name });
  if (ok) { mtybyToast('Username updated'); await checkUserSession(); }
  else mtybyToast((data && data.error) || 'Could not change username', 'error');
}

// ---- Profile action: commissioner toggle ----------------------------------

async function toggleCommissioner() {
  const user = window.__mtybyUser || {};
  const action = user.is_commissioner ? 'resign' : 'claim';
  if (action === 'resign' && !confirm('Step down as commissioner?')) return;
  const { ok, data } = await apiFetch('/api/league/commissioner', { action });
  if (ok) {
    mtybyToast(action === 'resign' ? 'You stepped down' : 'You are now the commissioner');
    await checkUserSession();
  } else mtybyToast((data && data.error) || 'Could not update commissioner', 'error');
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
  if (window.__mtybyForcePw) return;   // the form must stay open until it's done
  const edit = document.getElementById('profile-pw-edit');
  const btn = document.getElementById('profile-pw-edit-btn');
  if (edit) edit.style.display = 'none';
  if (btn) btn.style.display = '';
}

async function savePwChange() {
  const current = document.getElementById('profile-pw-current').value;
  const next = document.getElementById('profile-pw-new').value;
  if (!current) { mtybyToast('Enter your current password', 'error'); return; }
  if (next.length < 6) { mtybyToast('New password must be at least 6 characters', 'error'); return; }
  const { ok, data } = await apiFetch('/api/auth/password', { current_password: current, new_password: next });
  if (ok) {
    mtybyToast('Password updated');
    if (window.__mtybyForcePw) {
      // Forced reset is over: unpin the sheet and repaint from the server.
      applyForcedPasswordChange(false);
      const cancel = document.querySelector('#profile-pw-edit button[onclick="cancelPwChange()"]');
      if (cancel) cancel.style.display = '';
      document.getElementById('profile-sheet').classList.remove('is-open');
      checkUserSession();
    }
    cancelPwChange();
  } else mtybyToast((data && data.error) || 'Could not change password', 'error');
}

// ---- Trade-offer notification ---------------------------------------------

/** Show the header trade badge when another team has an offer pending for you. */
async function checkTradeOffers() {
  const el = document.getElementById('trade-notif');
  if (!el) return;
  try {
    const res = await fetch('/api/trades');
    if (!res.ok) { el.style.display = 'none'; return; }
    const data = await res.json();
    const n = (data.incoming || []).length;
    const cnt = document.getElementById('trade-count');
    if (n > 0) {
      el.style.display = '';
      if (cnt) cnt.textContent = n;
      el.title = `${n} trade offer${n === 1 ? '' : 's'} — tap to review`;
    } else {
      el.style.display = 'none';
    }
  } catch (err) { /* offline / not logged in — leave hidden */ }
}
window.checkTradeOffers = checkTradeOffers;

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
checkTradeOffers();
