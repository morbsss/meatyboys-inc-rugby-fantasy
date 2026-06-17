/* =============================================================================
 * squad.js — the Squad page: set your starting line-up + bench, pick a captain.
 *
 * One page serves both competitions (see leagues.js); the server supplies the
 * model via /api/my-picks and we branch on it:
 *
 *   OFDS (positioned)  — a strict rugby XV: exact positions, like-for-like
 *                        bench swaps, exactly one captain.
 *   mtyby (flex)   — an optional club FRONT-ROW UNIT + flexible individual
 *                        starters + an any-position bench.
 *
 * Layout of this file:  state → load → render dispatch → shared helpers →
 * interactions → save → [OFDS section] → [mtyby section] → save bar → boot.
 * ========================================================================== */
(() => {
  'use strict';

  // ---- State -------------------------------------------------------------
  let MODEL = { fr_unit: false, positioned_bench: false, starters: {}, bench: {},
                bench_count: 0, starter_count: 0, labels: {}, order: [] };
  let picks = [];           // [{player_id, name, position, real_team, is_bench, is_captain, is_fr?}]
  let frClub = null;        // mtyby only: the owned club front-row unit
  let statusByPid = {};     // player_id -> 'S' | 'B' | null  (real-match lineup status)
  let isLocked = false;
  let original = '';        // snapshot of the saved line-up, for change detection

  const el = (id) => document.getElementById(id);

  // ---- Load --------------------------------------------------------------
  async function init() {
    const [mp, st] = await Promise.all([
      fetch('/api/my-picks').then((r) => (r.ok ? r.json() : null)),
      fetch('/api/state').then((r) => r.json()),
    ]);

    isLocked = !!st.is_locked;
    el('round-badge').textContent = st.round ?? '—';
    el('team-name').textContent = (mp && mp.team_name) || '';
    (st.players || []).forEach((p) => { statusByPid[p.player_id] = p.lineup_status; });

    el('lock-pill').classList.toggle('is-locked', isLocked);
    el('lock-text').textContent = isLocked ? 'Locked' : 'Open';

    MODEL = (mp && mp.roster_model) || MODEL;
    picks = ((mp && mp.picks) || []).map((p) => ({
      ...p, is_bench: !!p.is_bench, is_captain: !!p.is_captain,
    }));
    frClub = (Leagues.isMtyby(MODEL) && mp) ? mp.fr_club : null;
    if (frClub) addFrontRowUnitPick(mp);

    original = snapshot();
    render();
  }

  const snapshot = () =>
    JSON.stringify(picks.map((p) => [p.player_id, p.is_bench, p.is_captain]).sort());

  // ---- Render dispatch ---------------------------------------------------
  function render() {
    const body = el('squad-body');
    if (!picks.length) { renderEmpty(body); return; }
    el('legend').hidden = false;
    el('save-bar').hidden = false;

    const starters = picks.filter((p) => !p.is_bench);
    const bench = picks.filter((p) => p.is_bench);

    body.innerHTML = Leagues.isOfds(MODEL)
      ? renderPositionedSquad(starters, bench)   // OFDS
      : renderFlexibleSquad(starters, bench);    // mtyby

    body.querySelectorAll('[data-act]').forEach((btn) =>
      btn.addEventListener('click', () => act(btn.dataset.act, btn.dataset.id)));
    renderSaveBar(starters, bench);
  }

  function renderEmpty(body) {
    body.innerHTML = `<div class="mtyby-card empty"><h3>No squad yet</h3>
      <p>Your Squad will be ready after the draft.</p>
      <a class="mtyby-btn mtyby-btn--primary mtyby-btn--sm" href="/draft">Go to the draft</a></div>`;
    el('legend').hidden = true;
    el('save-bar').hidden = true;
  }

  // ---- Shared rendering helpers -----------------------------------------
  const emptyRow = () =>
    `<div class="pl"><span class="nm" style="color:var(--ink-faint)">- empty -</span></div>`;
  const posLabel = (pos, n) => `${MODEL.labels[pos] || pos}${n > 1 ? ` x${n}` : ''}`;
  const sectionTitle = (label, have, want) =>
    `<div class="sec-title"><span>${label}</span><span>${have}/${want}</span></div>`;

  function playerRow(p) {
    const stat = statusByPid[p.player_id];
    const dot = stat === 'S' ? 's' : (stat === 'B' ? 'b' : '');
    const dotTitle = stat === 'S' ? 'Starting' : (stat === 'B' ? 'On bench' : 'Not named');
    const disabled = isLocked ? 'disabled' : '';
    return `<div class="pl">
      <span class="status-dot ${dot}" title="${dotTitle}"></span>
      <span class="mtyby-pos">${p.position}</span>
      <span class="nm"><b>${esc(p.name)}</b> <small>${esc(p.real_team || '')}</small></span>
      <span class="marks">
        <button class="ck-btn cap ${p.is_captain ? 'on' : ''}" data-act="cap" data-id="${p.player_id}" ${disabled} title="Captain">C</button>
        <button class="mtyby-btn mtyby-btn--ghost mtyby-btn--sm bench-btn" data-act="bench" data-id="${p.player_id}" ${disabled}>${p.is_bench ? 'Start' : 'Bench'}</button>
      </span>
    </div>`;
  }

  // ---- Interactions ------------------------------------------------------
  function act(kind, id) {
    const p = picks.find((x) => String(x.player_id) === String(id));
    if (!p) return;
    if (kind === 'info') { openPlayerCard(p); return; }   // pitch/bench tap → card
    if (kind === 'cap') setCaptain(p);
    else if (kind === 'bench') toggleBench(p);
    render();
  }

  // Tapping a player on the pitch (or bench) opens a card with their recent
  // per-round points and the captain / bench actions.
  function openPlayerCard(p) {
    closePlayerCard();
    const pts = p.recent_points || [];
    const ptsHtml = pts.length
      ? pts.map((r) => {
          const opp = r.opponent ? `${r.home ? 'v' : '@'} ${esc(r.opponent)}` : '';
          return `<div class="pc-pt"><span class="pc-rd">R${r.round}</span>`
            + `<span class="pc-opp">${opp}</span><b>${r.points}</b></div>`;
        }).join('')
      : `<div class="pc-none">No points from previous rounds yet.</div>`;
    const onField = !p.is_bench;
    const disabled = isLocked ? 'disabled' : '';

    const overlay = document.createElement('div');
    overlay.className = 'pc-overlay';
    overlay.innerHTML = `
      <div class="pc-card" role="dialog" aria-modal="true" aria-label="${esc(p.name)}">
        <button class="pc-x" data-pc="close" aria-label="Close">&times;</button>
        <div class="pc-head">
          <span class="mtyby-pos">${p.position}</span>
          <div class="pc-id">
            <div class="pc-name">${esc(p.name)}${p.is_captain ? ' <span class="pc-cap">C</span>' : ''}</div>
            <div class="pc-team">${esc(p.real_team || '')}</div>
          </div>
        </div>
        <div class="pc-sub">Previous rounds</div>
        <div class="pc-pts">${ptsHtml}</div>
        <div class="pc-actions">
          <button class="mtyby-btn mtyby-btn--secondary mtyby-btn--sm" data-pc="cap" ${disabled}>
            ${p.is_captain ? 'Remove captain' : 'Make captain'}</button>
          <button class="mtyby-btn mtyby-btn--primary mtyby-btn--sm" data-pc="bench" ${disabled}>
            ${onField ? 'Move to bench' : 'Move to starting XV'}</button>
        </div>
      </div>`;

    overlay.addEventListener('click', (e) => {
      const hit = e.target.closest('[data-pc]');
      if (e.target === overlay || (hit && hit.dataset.pc === 'close')) { closePlayerCard(); return; }
      if (!hit) return;
      if (hit.dataset.pc === 'cap') setCaptain(p);
      else if (hit.dataset.pc === 'bench') toggleBench(p);
      closePlayerCard();
      render();
    });
    document.addEventListener('keydown', onCardKey);
    document.body.appendChild(overlay);
  }

  function onCardKey(e) { if (e.key === 'Escape') closePlayerCard(); }
  function closePlayerCard() {
    document.removeEventListener('keydown', onCardKey);
    const ex = document.querySelector('.pc-overlay');
    if (ex) ex.remove();
  }

  function setCaptain(p) {
    const makeCaptain = !p.is_captain;     // tapping the current captain clears it
    picks.forEach((x) => { x.is_captain = false; });
    p.is_captain = makeCaptain;
  }

  function toggleBench(p) {
    // OFDS: a benched starter is swapped with the same-position player on the
    // other side, so the XV always stays a valid 15 (see "OFDS section").
    if (Leagues.isOfds(MODEL) && swapSamePosition(p)) return;
    p.is_bench = !p.is_bench;               // mtyby (or no counterpart): plain toggle
  }

  // ---- Save --------------------------------------------------------------
  async function save() {
    const btn = el('save-btn');
    btn.disabled = true;

    // The FR unit is sent separately; player_ids/bench/jerseys are individuals.
    const realPicks = picks.filter((p) => !p.is_fr);
    const frPick = picks.find((p) => p.is_fr);

    // Jerseys: starters first, then bench (cosmetic; the server stores is_bench).
    const ordered = realPicks.filter((p) => !p.is_bench).concat(realPicks.filter((p) => p.is_bench));
    const jerseys = {};
    ordered.forEach((p, i) => { jerseys[p.player_id] = i + 1; });
    const captain = realPicks.find((p) => p.is_captain);

    const teamName = el('team-name').textContent || 'me';
    const { ok, data } = await apiFetch(`/api/team/${encodeURIComponent(teamName)}/picks`, {
      player_ids: realPicks.map((p) => p.player_id),
      bench_ids: realPicks.filter((p) => p.is_bench).map((p) => p.player_id),
      jerseys,
      captain_id: captain && captain.player_id,
      fr_is_captain: frPick ? frPick.is_captain : false,
      fr_is_bench: frPick ? frPick.is_bench : false,
    });

    if (ok) {
      window.mtybyToast('Squad saved', 'ok');
      original = snapshot();
      renderSaveBar(picks.filter((p) => !p.is_bench), picks.filter((p) => p.is_bench));
    } else {
      window.mtybyToast((data && data.error) || 'Save failed', 'err');
      btn.disabled = false;
    }
  }

  // =========================================================================
  // OFDS — strict full rugby XV, laid out on a rugby-union pitch
  // =========================================================================

  // The starting XV in 1–15 jersey order, each placed where it stands on the
  // field: forwards packed near the top (own line), the back-line spreading
  // toward the bottom. x/y are percentages of the pitch (token centre).
  const OFDS_FORMATION = [
    { pos: 'PR',  num: 1,  x: 30, y: 15 },   // loose-head prop
    { pos: 'HK',  num: 2,  x: 50, y: 12 },   // hooker
    { pos: 'PR',  num: 3,  x: 70, y: 15 },   // tight-head prop
    { pos: 'LK',  num: 4,  x: 41, y: 27 },   // lock
    { pos: 'LK',  num: 5,  x: 59, y: 27 },   // lock
    { pos: 'LF',  num: 6,  x: 24, y: 38 },   // blind-side flanker
    { pos: 'LF',  num: 7,  x: 76, y: 38 },   // open-side flanker
    { pos: 'LF',  num: 8,  x: 50, y: 43 },   // number 8
    { pos: 'SH',  num: 9,  x: 20, y: 55 },   // scrum-half
    { pos: 'FH',  num: 10, x: 30, y: 65 },   // fly-half
    { pos: 'MID', num: 12, x: 45, y: 70 },   // inside centre
    { pos: 'MID', num: 13, x: 65, y: 75 },   // outside centre
    { pos: 'OBK', num: 11, x: 16, y: 80 },   // left wing
    { pos: 'OBK', num: 14, x: 84, y: 80 },   // right wing
    { pos: 'OBK', num: 15, x: 50, y: 85 },   // full-back
  ];

  // Horizontal field lines (% from top) — try lines + posts, 22s, dashed 10s,
  // and the halfway line, mirroring a real rugby-union pitch.
  const PITCH_LINES = [
    { y: 7,  dash: false, posts: true },
    { y: 12, dash: true },
    { y: 29, dash: false },
    { y: 40, dash: true },
    { y: 50, dash: false },
    { y: 60, dash: true },
    { y: 71, dash: false },
    { y: 87, dash: true },
    { y: 93, dash: false, posts: true },
  ];

  // Pitch on the left with the XV in formation; replacements stacked in a
  // vertical column on the right (like a matchday bench beside the field).
  function renderPositionedSquad(starters, bench) {
    const usedS = new Set();
    const tokens = OFDS_FORMATION.map((slot) => {
      const p = starters.find((x) => x.position === slot.pos && !usedS.has(x.player_id));
      if (p) { usedS.add(p.player_id); return fieldToken(p, slot); }
      return emptyFieldToken(slot);
    }).join('');

    const markings = PITCH_LINES.map((l) =>
      `<div class="pitch-line${l.dash ? ' dash' : ''}" style="top:${l.y}%"></div>`
      + (l.posts ? `<div class="pitch-posts pitch-posts--${l.y < 50 ? 'top' : 'bottom'}" style="top:${l.y}%"><i></i></div>` : '')
    ).join('');

    // The Starting XV title sits inside the pitch near the top (label top-left,
    // count top-right) rather than as a heading above the field.
    const head = `<div class="sec-title pitch-head"><span>Starting XV</span>`
      + `<span>${starters.length}/${MODEL.starter_count}</span></div>`;

    const benchExtra = [];
    let html = `<div class="field-layout">`
      +   `<div class="pitch-wrap"><div class="pitch" role="img"`
      +     ` aria-label="Starting XV in rugby formation">${head}${markings}${tokens}</div></div>`
      +   `<aside class="bench-col">`
      +     `<div class="bench-col-head"><span>Replacements</span>`
      +       `<span>${bench.length}/${MODEL.bench_count}</span></div>`
      +     renderBenchChips(bench, benchExtra)
      +   `</aside>`
      + `</div>`;

    // Surplus / wrong-position players (an in-progress XV) — flagged full width.
    html += overLimitCard(starters.filter((p) => !usedS.has(p.player_id)));
    html += overLimitCard(benchExtra);
    return html;
  }

  // Replacement jersey order: the hooker covers the front row first (16), then
  // the props (17/18), then the rest forward-to-back — as on a real bench.
  const BENCH_ORDER = ['HK', 'PR', 'LK', 'LF', 'SH', 'FH', 'MID', 'OBK'];

  // Replacements 16–23, in bench order, as a vertical bench column; any player
  // beyond a position's bench quota is pushed onto `extraOut`.
  function renderBenchChips(bench, extraOut) {
    const used = new Set();
    let num = 16;
    let chips = '';
    BENCH_ORDER.forEach((pos) => {
      const n = (MODEL.bench || {})[pos] || 0;
      for (let i = 0; i < n; i++, num++) {
        const p = bench.find((x) => x.position === pos && !used.has(x.player_id));
        if (p) { used.add(p.player_id); chips += benchToken(p, num); }
        else chips += emptyBenchToken(num, pos);
      }
    });
    bench.filter((p) => !used.has(p.player_id)).forEach((p) => extraOut.push(p));
    return chips;
  }

  // A full-width warning card listing players beyond their position's quota.
  function overLimitCard(extra) {
    if (!extra.length) return '';
    return `<div class="mtyby-card"><div class="slot-label" style="color:var(--danger)">Over the limit</div>`
      + extra.map(playerRow).join('') + `</div>`;
  }

  // One player standing on the field: tap the shirt to open their card (recent
  // points + captain / bench actions). Shirt carries the jersey number, captain
  // badge and a real-match lineup dot.
  function fieldToken(p, slot) {
    return `<div class="fp${p.is_captain ? ' is-cap' : ''}" style="left:${slot.x}%;top:${slot.y}%">
      <button class="fp-shirt" data-act="info" data-id="${p.player_id}"
        title="${esc(p.name)} — tap for points & options">
        ${slot.num}${statusDotHtml(p)}${p.is_captain ? '<span class="fp-c">C</span>' : ''}
      </button>
      <div class="fp-name">${esc(p.name)}</div>
    </div>`;
  }

  function emptyFieldToken(slot) {
    return `<div class="fp fp--empty" style="left:${slot.x}%;top:${slot.y}%">
      <div class="fp-shirt fp-shirt--empty">${slot.num}</div>
      <div class="fp-name">${MODEL.labels[slot.pos] || slot.pos}</div>
    </div>`;
  }

  function benchToken(p, num) {
    return `<div class="bp${p.is_captain ? ' is-cap' : ''}">
      <button class="bp-shirt" data-act="info" data-id="${p.player_id}"
        title="${esc(p.name)} — tap for points & options">
        ${num}${statusDotHtml(p)}${p.is_captain ? '<span class="fp-c">C</span>' : ''}
      </button>
      <div class="bp-name"><b>${esc(p.name)}</b><small>${esc(p.real_team || '')}</small></div>
    </div>`;
  }

  function emptyBenchToken(num, pos) {
    return `<div class="bp bp--empty">
      <div class="bp-shirt bp-shirt--empty">${num}</div>
      <div class="bp-name"><b>${MODEL.labels[pos] || pos}</b><small>—</small></div>
    </div>`;
  }

  // Real-match lineup status, shown as a small dot on the shirt (shared by the
  // field tokens and bench chips); mirrors the legend on the page.
  function statusDotHtml(p) {
    const stat = statusByPid[p.player_id];
    const cls = stat === 'S' ? 's' : (stat === 'B' ? 'b' : '');
    const title = stat === 'S' ? 'Starting' : (stat === 'B' ? 'On bench' : 'Not named');
    return `<span class="fp-dot ${cls}" title="${title}"></span>`;
  }

  // Swap the clicked player with the same-position player on the opposite side
  // (starter <-> bench). It's a true like-for-like: the two also trade places in
  // `picks`, so the player coming on inherits the exact field slot — and jersey
  // number — of the one going off (and vice-versa). Returns true if swapped.
  function swapSamePosition(p) {
    const i = picks.findIndex((x) => x === p);
    const j = picks.findIndex((x) =>
      x.position === p.position && x.is_bench !== p.is_bench
      && String(x.player_id) !== String(p.player_id));
    if (j === -1) return false;
    [picks[i].is_bench, picks[j].is_bench] = [picks[j].is_bench, picks[i].is_bench];
    [picks[i], picks[j]] = [picks[j], picks[i]];
    return true;
  }

  const countByPos = (arr) =>
    arr.reduce((m, p) => { m[p.position] = (m[p.position] || 0) + 1; return m; }, {});
  function sameCounts(have, want) {
    const keys = new Set([...Object.keys(have), ...Object.keys(want)]);
    for (const k of keys) if ((have[k] || 0) !== (want[k] || 0)) return false;
    return true;
  }
  const fmtCounts = (obj) => (MODEL.order || Object.keys(obj))
    .filter((p) => obj[p]).map((p) => `${obj[p]}x ${MODEL.labels[p] || p}`).join(', ');

  // OFDS validity: exact starter positions, exact bench positions, one captain.
  function positionedValidity(starters, bench, captains) {
    const startersOk = sameCounts(countByPos(starters), MODEL.starters);
    const benchOk = sameCounts(countByPos(bench), MODEL.bench);
    let msg = '';
    if (captains !== 1) msg = 'Pick exactly one captain.';
    else if (!startersOk) msg = `Starting XV must be ${fmtCounts(MODEL.starters)}.`;
    else if (!benchOk) msg = `Bench must be ${fmtCounts(MODEL.bench)}.`;
    return { valid: captains === 1 && startersOk && benchOk, msg };
  }

  // =========================================================================
  // mtyby — optional club FRONT-ROW UNIT + flexible individuals
  // =========================================================================

  // The front-row unit is shown as a synthetic, captainable/benchable pick.
  function addFrontRowUnitPick(mp) {
    picks.push({
      player_id: 'FR', is_fr: true, position: 'FR',
      name: `${frClub} FR`, real_team: frClub,
      is_bench: !!(mp && mp.fr_is_bench), is_captain: !!(mp && mp.fr_is_captain),
    });
  }

  function renderFlexibleSquad(starters, bench) {
    const starterTarget = MODEL.starter_count + (Leagues.isMtyby(MODEL) ? 1 : 0);
    let html = sectionTitle('Starters', starters.length, starterTarget) + `<div class="mtyby-card">`;

    const used = new Set();
    const slots = (Leagues.isMtyby(MODEL) ? ['FR'] : [])
      .concat((MODEL.order || []).filter((p) => MODEL.starters[p]));
    slots.forEach((pos) => {
      const n = pos === 'FR' ? 1 : (MODEL.starters[pos] || 0);
      html += `<div class="slot-label">${pos === 'FR' ? 'Front Row' : posLabel(pos, n)}</div>`;
      const inSlot = starters.filter((p) => p.position === pos && !used.has(p.player_id)).slice(0, n);
      inSlot.forEach((p) => used.add(p.player_id));
      html += inSlot.map(playerRow).join('') || emptyRow();
    });
    const leftover = starters.filter((p) => !used.has(p.player_id));
    if (leftover.length) {
      html += `<div class="slot-label" style="color:var(--danger)">Extra (move to bench)</div>`
        + leftover.map(playerRow).join('');
    }
    html += `</div>`;

    html += sectionTitle('Bench', bench.length, MODEL.bench_count) + `<div class="mtyby-card">`;
    html += bench.map(playerRow).join('')
      || `<div class="pl"><span class="nm" style="color:var(--ink-muted)">No bench players</span></div>`;
    html += `</div>`;
    return html;
  }

  // mtyby validity: composition is advisory; only the squad cap + one captain.
  function flexibleValidity(captains) {
    const total = picks.filter((p) => !p.is_fr).length;
    const cap = MODEL.starter_count + MODEL.bench_count;
    let msg = '';
    if (captains !== 1) msg = 'Pick exactly one captain.';
    else if (total > cap) msg = `Too many players (max ${cap}).`;
    return { valid: captains === 1 && total <= cap, msg };
  }

  // ---- Save bar (dispatch to the right league's validation) --------------
  function renderSaveBar(starters, bench) {
    const captains = picks.filter((p) => p.is_captain).length;
    const changed = snapshot() !== original;
    const status = el('save-status');
    const { valid, msg } = Leagues.isOfds(MODEL)
      ? positionedValidity(starters, bench, captains)
      : flexibleValidity(captains);

    if (isLocked) status.innerHTML = '<span class="bad">Squad locked — a game has kicked off.</span>';
    else if (msg) status.innerHTML = `<span class="bad">${esc(msg)}</span>`;
    else status.textContent = changed ? 'Unsaved changes' : 'Squad up to date';

    el('save-btn').disabled = !(valid && !isLocked && changed);
  }

  // ---- Boot --------------------------------------------------------------
  el('reset-btn').addEventListener('click', init);
  el('save-btn').addEventListener('click', save);
  init();
})();
