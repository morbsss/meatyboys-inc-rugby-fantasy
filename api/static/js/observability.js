/* =============================================================================
 * observability.js - commissioner-only ingestion health.
 *
 * Reading order on the page is deliberate and matches how you actually debug:
 *
 *   1. is the cron arriving at all?          (nothing else matters if not)
 *   2. what is wrong right now?              (checks, worst first)
 *   3. what did each job last do?            (per-job status)
 *   4. what did those jobs actually WRITE?   (row counts - catches "ok / 0 rows")
 *   5. the raw run log                       (the sequence, when a summary won't do)
 *
 * All the judgement lives server-side in api/observability.py so it can be tested
 * without a browser; this file renders what it is told.
 * ========================================================================== */
(() => {
  'use strict';

  let DATA = null;
  const el = (id) => document.getElementById(id);

  // ---- formatting --------------------------------------------------------
  function ago(seconds) {
    if (seconds === null || seconds === undefined) return 'never';
    const s = Math.max(0, Math.round(seconds));
    if (s < 90) return `${s}s ago`;
    const m = Math.round(s / 60);
    if (m < 90) return `${m}m ago`;
    const h = s / 3600;
    if (h < 48) return `${h.toFixed(1)}h ago`;
    return `${Math.round(h / 24)}d ago`;
  }

  function stamp(iso) {
    if (!iso) return '-';
    const d = new Date(iso);
    if (isNaN(d)) return iso;
    // Local time, because the person reading this is debugging now, not
    // reconstructing UTC in their head.
    return d.toLocaleString(undefined, {
      month: 'short', day: '2-digit', hour: '2-digit', minute: '2-digit',
    });
  }

  function dur(seconds) {
    if (!seconds) return '-';
    if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
    if (seconds < 86400) return `${Math.round(seconds / 3600)}h`;
    return `${Math.round(seconds / 86400)}d`;
  }

  const esc = (s) => String(s ?? '')
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

  // ---- liveness ----------------------------------------------------------
  function renderTick(d) {
    const w = (d.tick_warnings || [])[0];
    // Only a real outage turns this red. An unknown-liveness notice (no
    // heartbeat recorded yet) is amber, because jobs are logged only when due and
    // a quiet spell is not a failure.
    const state = !w ? 'is-ok' : (w.level === 'info' ? 'is-unknown' : 'is-bad');
    el('ob-tick').className = 'ob-tick ' + state;
    el('ob-tick-title').textContent = w ? w.title : 'Cron is running';
    el('ob-tick-sub').textContent = w
      ? w.detail
      : `Last tick ${ago(d.tick_age_seconds)} · expected every `
        + `${Math.round(d.tick_interval_seconds / 60)} min`
        + (d.tick_exact ? '' : ' (from the last job run)');
  }

  // ---- checks ------------------------------------------------------------
  function alertsHTML(all) {
    if (!all.length) {
      return `<div class="ob-alert is-ok">
        <b>No problems detected</b>
        <small>Every job is inside its expected cadence and has written rows.</small>
      </div>`;
    }
    return all.map((w) => `<div class="ob-alert is-${esc(w.level)}">
      <b>${esc(w.title)}</b>
      <small>${esc(w.detail)}</small>
      <code>${esc(w.scope || '')}${w.scope ? ' · ' : ''}${esc(w.code)}</code>
    </div>`).join('');
  }

  // ---- per-job -----------------------------------------------------------
  // A job's colour comes from its LAST OUTCOME, not from whether it ran: a job
  // that keeps succeeding at nothing is green here and caught by the checks and
  // the row counts instead. Keeping those signals separate is the point.
  function jobState(j) {
    if (j.never_run) return j.kind === 'rollover' ? 'idle' : 'warn';
    if (j.status && j.status !== 'ok') return 'error';
    if (j.failures_24h) return 'warn';
    return 'ok';
  }

  function jobRowHTML(j) {
    const state = jobState(j);
    const fail = j.last_failure
      ? `<div class="ob-jf">Last failure ${esc(stamp(j.last_failure.run_at))}:
           <span>${esc(j.last_failure.detail)}</span></div>`
      : '';
    return `<tr class="ob-j is-${state}">
      <td class="ob-j-name">
        <span class="ob-dot"></span>
        <div>
          <b>${esc(j.job)}</b>
          <small>writes ${esc(j.writes)}</small>
        </div>
      </td>
      <td class="ob-j-when">
        ${j.never_run ? '<i>never run</i>'
                      : `${esc(ago(j.last_run_age))}<small>${esc(stamp(j.last_run))}</small>`}
      </td>
      <td class="ob-j-cad">
        ${j.cadence_seconds ? `every ${esc(dur(j.cadence_seconds))}` : '-'}
        <small>${esc(j.expect)}</small>
      </td>
      <td class="ob-j-24">
        ${j.runs_24h}${j.failures_24h ? ` <b class="bad">${j.failures_24h} failed</b>` : ''}
      </td>
      <td class="ob-j-detail">
        ${esc(j.detail ?? '-')}
        ${fail}
      </td>
    </tr>`;
  }

  // ---- outputs -----------------------------------------------------------
  function outputsHTML(outs, round) {
    return outs.map((o) => {
      const extra = o.extra
        ? Object.entries(o.extra).map(([k, v]) => `${esc(k)}: ${v}`).join(' · ')
        : '';
      return `<div class="ob-out ${o.count ? '' : 'is-empty'}">
        <div class="ob-out-n">${o.count}</div>
        <div class="ob-out-b">
          <b>${esc(o.label)}</b>
          <small>${esc(o.unit)}${o.scoped === 'round' ? ` · round ${round}` : ''}
                 · by ${esc(o.written_by)}</small>
          ${extra ? `<small class="ob-out-x">${extra}</small>` : ''}
          ${o.updated ? `<small class="ob-out-x">updated ${esc(stamp(o.updated))}</small>` : ''}
        </div>
      </div>`;
    }).join('');
  }

  // ---- league block ------------------------------------------------------
  function leagueHTML(L) {
    const badges = [
      L.live_now ? '<span class="ob-badge is-live">match live</span>' : '',
      L.in_lineup_window ? '<span class="ob-badge">team-sheet window open</span>' : '',
    ].filter(Boolean).join('');

    return `<section class="ob-card">
      <div class="ob-card-head">
        <h2>${esc(L.name)} <span class="ob-slug">${esc(L.slug)}</span></h2>
        <div class="ob-meta">
          round ${L.active_round} · ${esc(L.timezone)} ${esc(stamp(L.local_time))}
          ${badges}
        </div>
      </div>

      <div class="ob-log-wrap">
        <table class="ob-jobs">
          <thead><tr>
            <th>Job</th><th>Last run</th><th>Cadence</th><th>24h</th><th>Last detail</th>
          </tr></thead>
          <tbody>${L.jobs.map(jobRowHTML).join('')}</tbody>
        </table>
      </div>

      <h3 class="ob-sub">What it wrote</h3>
      <p class="ob-note">Row counts, not job status - this is what catches a job
        reporting success while producing nothing.</p>
      <div class="ob-outs">${outputsHTML(L.outputs, L.active_round)}</div>
    </section>`;
  }

  // ---- run log -----------------------------------------------------------
  function fillFilters() {
    const leagues = [...new Set(DATA.recent.map((r) => r.slug).filter(Boolean))];
    const jobs = [...new Set(DATA.recent.map((r) => r.job))].sort();
    el('ob-f-league').innerHTML = '<option value="ALL">All leagues</option>'
      + leagues.map((s) => `<option value="${esc(s)}">${esc(s)}</option>`).join('');
    el('ob-f-job').innerHTML = '<option value="ALL">All jobs</option>'
      + jobs.map((j) => `<option value="${esc(j)}">${esc(j)}</option>`).join('');
  }

  function renderLog() {
    const fl = el('ob-f-league').value;
    const fj = el('ob-f-job').value;
    const fs = el('ob-f-status').value;
    const rows = DATA.recent.filter((r) =>
      (fl === 'ALL' || r.slug === fl)
      && (fj === 'ALL' || r.job === fj)
      && (fs === 'ALL' || (fs === 'error' ? r.status !== 'ok' : r.status === 'ok')));

    el('ob-log-body').innerHTML = rows.length
      ? rows.map((r) => `<tr class="${r.status === 'ok' ? '' : 'is-error'}">
          <td class="ob-w">${esc(stamp(r.run_at))}</td>
          <td>${esc(r.slug ?? r.league_id)}</td>
          <td><b>${esc(r.job)}</b></td>
          <td>${r.round_number ?? '-'}</td>
          <td><span class="ob-st is-${r.status === 'ok' ? 'ok' : 'error'}">${esc(r.status)}</span></td>
          <td class="ob-d">${esc(r.detail)}</td>
        </tr>`).join('')
      : `<tr><td colspan="6" class="ob-empty">No runs match this filter.</td></tr>`;

    el('ob-log-foot').textContent =
      `${rows.length} of ${DATA.recent.length} most recent runs`;
  }

  // ---- boot --------------------------------------------------------------
  async function load() {
    let d;
    try {
      const res = await fetch('/api/observability');
      if (res.status === 403) {
        el('ob-alerts').innerHTML = `<div class="ob-alert is-error">
          <b>Commissioner only</b><small>This page is not available to you.</small></div>`;
        return;
      }
      d = await res.json();
    } catch {
      el('ob-alerts').innerHTML = `<div class="ob-alert is-error">
        <b>Could not load pipeline health</b>
        <small>The app answered nothing - which is itself a signal.</small></div>`;
      return;
    }
    DATA = d;

    renderTick(d);
    // Tick problems first, then every league's checks, each labelled with which
    // league it came from.
    const all = (d.tick_warnings || []).concat(
      ...d.leagues.map((L) => (L.warnings || []).map((w) => ({ ...w, scope: L.slug }))));
    el('ob-alerts').innerHTML = alertsHTML(all);
    el('ob-leagues').innerHTML = d.leagues.map(leagueHTML).join('');
    fillFilters();
    renderLog();
  }

  ['ob-f-league', 'ob-f-job', 'ob-f-status'].forEach((id) =>
    el(id).addEventListener('change', renderLog));
  el('ob-refresh').addEventListener('click', load);

  load();
})();
