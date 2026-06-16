/* =============================================================================
 * auth.js — The sign-in / create-account screen (pre-login; no shared chrome).
 * Extracted from templates/auth.html. Shared helpers: common.js, leagues.js, base.js.
 * ========================================================================== */

const html = document.documentElement;
const DEFAULT_THEME = 'forest';
let selectedLeague = null;   // { slug, theme, ... }

function applyTheme(t) { html.setAttribute('data-theme', t); }

// Tab switching also drives the theming state (spec §7):
//  - register tab with no league chosen → grayscale
//  - a league chosen → that league's theme
//  - login tab → default theme
document.querySelectorAll('.tab').forEach(tab => {
  tab.addEventListener('click', () => {
    const tabName = tab.dataset.tab;
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
    tab.classList.add('active');
    document.getElementById(tabName).classList.add('active');
    document.getElementById(tabName + '-alert').innerHTML = '';
    if (selectedLeague?.theme && (tabName === 'register' || tabName === 'login')) {
      applyTheme(DEFAULT_THEME);
    } else {
      applyTheme(selectedLeague ? selectedLeague.theme : 'grayscale');
    }
  });
});

// Load the two joinable leagues and render chooser cards.
async function loadLeagues() {
  try {
    const res = await fetch('/api/leagues');
    const leagues = await res.json();
    const wrap = document.getElementById('league-cards');
    wrap.innerHTML = '';
    leagues.forEach(lg => {
      const card = document.createElement('button');
      card.type = 'button';
      card.className = 'league-card';
      card.dataset.slug = lg.slug;
      card.dataset.theme = lg.theme;
      card.innerHTML =
        `<span class="lc-comp">${lg.comp_name}</span>` +
        `<span class="lc-name">${lg.name}</span>` +
        `<span class="lc-dot"></span>`;
      card.addEventListener('click', () => selectLeague(lg, card));
      wrap.appendChild(card);
    });
  } catch (err) { console.error('Failed to load leagues:', err); }
}

function selectLeague(lg, card) {
  selectedLeague = lg;
  document.querySelectorAll('.league-card').forEach(c => c.classList.remove('selected'));
  card.classList.add('selected');
  applyTheme(lg.theme);                                   // live theme preview
  document.getElementById('register-team-step').classList.add('revealed');
}

// Login
document.getElementById('login-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const email = document.getElementById('login-email').value.trim();
  const password = document.getElementById('login-password').value;
  const remember = document.getElementById('login-remember').checked;
  const btn = document.getElementById('login-btn');
  const alertDiv = document.getElementById('login-alert');
  btn.disabled = true; btn.innerHTML = '<span class="loading-spinner"></span> Signing in...';
  try {
    const res = await fetch('/api/auth/login', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, password, remember })
    });
    const data = await res.json();
    if (res.ok) {
      alertDiv.innerHTML = `<div class="alert alert-info">Signed in. Redirecting...</div>`;
      setTimeout(() => window.location.href = '/', 400);
    } else {
      alertDiv.innerHTML = `<div class="alert alert-error">${data.error || 'Sign in failed'}</div>`;
    }
  } catch (err) {
    alertDiv.innerHTML = `<div class="alert alert-error">Network error: ${err.message}</div>`;
  } finally { btn.disabled = false; btn.innerHTML = 'Sign in'; }
});

// Register
document.getElementById('register-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const email = document.getElementById('register-email').value.trim();
  const password = document.getElementById('register-password').value;
  const passwordConfirm = document.getElementById('register-password-confirm').value;
  const team = document.getElementById('register-team').value.trim();
  const alertDiv = document.getElementById('register-alert');

  if (!selectedLeague) {
    alertDiv.innerHTML = `<div class="alert alert-error">Choose a league to join first.</div>`;
    return;
  }
  if (password !== passwordConfirm) {
    alertDiv.innerHTML = `<div class="alert alert-error">Passwords do not match.</div>`;
    return;
  }
  if (!team) {
    alertDiv.innerHTML = `<div class="alert alert-error">Give your team a name.</div>`;
    return;
  }

  const btn = document.getElementById('register-btn');
  btn.disabled = true; btn.innerHTML = '<span class="loading-spinner"></span> Creating account...';
  try {
    const res = await fetch('/api/auth/register', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, password, team_name: team, league: selectedLeague.slug })
    });
    const data = await res.json();
    if (res.ok) {
      alertDiv.innerHTML = `<div class="alert alert-info">Account created. Redirecting...</div>`;
      setTimeout(() => window.location.href = '/', 400);
    } else {
      alertDiv.innerHTML = `<div class="alert alert-error">${data.error || 'Registration failed'}</div>`;
    }
  } catch (err) {
    alertDiv.innerHTML = `<div class="alert alert-error">Network error: ${err.message}</div>`;
  } finally { btn.disabled = false; btn.innerHTML = 'Create account'; }
});

loadLeagues();
