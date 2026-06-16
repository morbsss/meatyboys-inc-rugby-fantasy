/* =============================================================================
 * squad.js — the Squad page: set your starting line-up + bench, pick a captain.
 *
 * One page serves both competitions (see leagues.js); the server supplies the
 * model via /api/my-picks and we branch on it:
 *
 *   OFDS (positioned)  — a strict rugby XV: exact positions, like-for-like
 *                        bench swaps, exactly one captain.
 *   meatyboys (flex)   — an optional club FRONT-ROW UNIT + flexible individual
 *                        starters + an any-position bench.
 *
 * Layout of this file:  state → load → render dispatch → shared helpers →
 * interactions → save → [OFDS section] → [meatyboys section] → save bar → boot.
 * ========================================================================== */
(() => {
  'use strict';

  // ---- State -------------------------------------------------------------
  let MODEL = { fr_unit: false, positioned_bench: false, starters: {}, bench: {},
                bench_count: 0, starter_count: 0, labels: {}, order: [] };
  let picks = [];           // [{player_id, name, position, real_team, is_bench, is_captain, is_fr?}]
  let frClub = null;        // meatyboys only: the owned club front-row unit
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
    frClub = (Leagues.isMeatyboys(MODEL) && mp) ? mp.fr_club : null;
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
      : renderFlexibleSquad(starters, bench);    // meatyboys

    body.querySelectorAll('[data-act]').forEach((btn) =>
      btn.addEventListener('click', () => act(btn.dataset.act, btn.dataset.id)));
    renderSaveBar(starters, bench);
  }

  function renderEmpty(body) {
    body.innerHTML = `<div class="ofds-card empty"><h3>No squad yet</h3>
      <p>Your Squad will be ready after the draft.</p>
      <a class="ofds-btn ofds-btn--primary ofds-btn--sm" href="/draft">Go to the draft</a></div>`;
    el('legend').hidden = true;
    el('save-bar').hidden = true;
  }

  // ---- Shared rendering helpers -----------------------------------------
  const emptyRow = () =>
    `<div class="pl"><span class="nm" style="color:var(--ink-faint)">- empty -</span></div>`;
  const posLabel = (pos, n) => `${MODEL.labels[pos] || pos}${n > 1 ? ` ×${n}` : ''}`;
  const sectionTitle = (label, have, want) =>
    `<div class="sec-title"><span>${label}</span><span>${have}/${want}</span></div>`;

  function playerRow(p) {
    const stat = statusByPid[p.player_id];
    const dot = stat === 'S' ? 's' : (stat === 'B' ? 'b' : '');
    const dotTitle = stat === 'S' ? 'Starting' : (stat === 'B' ? 'On bench' : 'Not named');
    const disabled = isLocked ? 'disabled' : '';
    return `<div class="pl">
      <span class="status-dot ${dot}" title="${dotTitle}"></span>
      <span class="ofds-pos">${p.position}</span>
      <span class="nm"><b>${esc(p.name)}</b> <small>${esc(p.real_team || '')}</small></span>
      <span class="marks">
        <button class="ck-btn cap ${p.is_captain ? 'on' : ''}" data-act="cap" data-id="${p.player_id}" ${disabled} title="Captain">C</button>
        <button class="ofds-btn ofds-btn--ghost ofds-btn--sm bench-btn" data-act="bench" data-id="${p.player_id}" ${disabled}>${p.is_bench ? 'Start' : 'Bench'}</button>
      </span>
    </div>`;
  }

  // ---- Interactions ------------------------------------------------------
  function act(kind, id) {
    const p = picks.find((x) => String(x.player_id) === String(id));
    if (!p) return;
    if (kind === 'cap') setCaptain(p);
    else if (kind === 'bench') toggleBench(p);
    render();
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
    p.is_bench = !p.is_bench;               // meatyboys (or no counterpart): plain toggle
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
      window.ofdsToast('Squad saved', 'ok');
      original = snapshot();
      renderSaveBar(picks.filter((p) => !p.is_bench), picks.filter((p) => p.is_bench));
    } else {
      window.ofdsToast((data && data.error) || 'Save failed', 'err');
      btn.disabled = false;
    }
  }

  // =========================================================================
  // OFDS — strict full rugby XV (positioned starters + positioned bench)
  // =========================================================================

  function renderPositionedSquad(starters, bench) {
    return sectionTitle('Starting XV', starters.length, MODEL.starter_count)
      + `<div class="ofds-card">${fillPositionedSlots(starters, MODEL.starters)}</div>`
      + sectionTitle('Bench', bench.length, MODEL.bench_count)
      + `<div class="ofds-card">${fillPositionedSlots(bench, MODEL.bench)}</div>`;
  }

  // Render exactly `slotsByPos[pos]` rows per position; flag anything surplus.
  function fillPositionedSlots(group, slotsByPos) {
    const used = new Set();
    let html = '';
    (MODEL.order || []).forEach((pos) => {
      const n = slotsByPos[pos] || 0;
      if (!n) return;
      html += `<div class="slot-label">${posLabel(pos, n)}</div>`;
      for (let i = 0; i < n; i++) {
        const p = group.find((x) => x.position === pos && !used.has(x.player_id));
        if (p) { used.add(p.player_id); html += playerRow(p); } else html += emptyRow();
      }
    });
    const extra = group.filter((p) => !used.has(p.player_id));
    if (extra.length) {
      html += `<div class="slot-label" style="color:var(--danger)">Over the limit</div>`
        + extra.map(playerRow).join('');
    }
    return html;
  }

  // Swap the clicked player with the same-position player on the opposite side
  // (starter <-> bench). Returns true if a swap happened.
  function swapSamePosition(p) {
    const counterpart = picks.find((x) =>
      x.position === p.position && x.is_bench !== p.is_bench
      && String(x.player_id) !== String(p.player_id));
    if (!counterpart) return false;
    [p.is_bench, counterpart.is_bench] = [counterpart.is_bench, p.is_bench];
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
  // meatyboys — optional club FRONT-ROW UNIT + flexible individuals
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
    const starterTarget = MODEL.starter_count + (Leagues.isMeatyboys(MODEL) ? 1 : 0);
    let html = sectionTitle('Starters', starters.length, starterTarget) + `<div class="ofds-card">`;

    const used = new Set();
    const slots = (Leagues.isMeatyboys(MODEL) ? ['FR'] : [])
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

    html += sectionTitle('Bench', bench.length, MODEL.bench_count) + `<div class="ofds-card">`;
    html += bench.map(playerRow).join('')
      || `<div class="pl"><span class="nm" style="color:var(--ink-muted)">No bench players</span></div>`;
    html += `</div>`;
    return html;
  }

  // meatyboys validity: composition is advisory; only the squad cap + one captain.
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
