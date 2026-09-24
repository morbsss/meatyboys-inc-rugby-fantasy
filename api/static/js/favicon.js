/* =============================================================================
 * favicon.js — animate the favicon in browsers that refuse to.
 *
 * Firefox plays an animated GIF favicon. Chrome, Edge and Safari draw its first
 * frame and stop, and no markup changes that. The workaround is to repaint the
 * icon ourselves: slice a sprite sheet onto a 32x32 canvas and swap the
 * <link rel="icon"> href frame by frame.
 *
 * Costs are real, so they're all paid once or not at all:
 *
 *   - every frame is rendered to a data URL ONCE at startup, so the loop does
 *     nothing per tick but assign a string — no canvas work, no encoding;
 *   - the sheet is a single 6.7 KB request, not one per frame;
 *   - it runs at 12.5 fps (the sheet is every 2nd source frame), which is plenty
 *     for something drawn at 16 px;
 *   - it stops dead while the tab is hidden, so a backgrounded tab costs nothing;
 *   - it never starts if the visitor asked for reduced motion.
 *
 * Failure is always silent and always leaves the static icon in place: the <link>
 * tags in base.html already point at favicon.png / favicon.gif, so a blocked
 * sheet, an old browser or a thrown exception just means the icon doesn't move.
 * ========================================================================== */
(() => {
  'use strict';

  const SPRITE = '/static/img/favicon-sprite.png';
  const META = '/static/img/favicon-sprite.json';

  // A spinning icon in the corner of the eye is exactly what this setting is
  // for. Honour it before doing any work at all.
  const reduced = window.matchMedia
    && window.matchMedia('(prefers-reduced-motion: reduce)');
  if (reduced && reduced.matches) return;

  let frames = [];          // data URLs, one per frame
  let link = null;
  let timer = null;
  let i = 0;

  /** The <link> we drive. Appended last so it wins over the static tags —
   *  browsers use the final `rel=icon` they understand. */
  function iconLink() {
    let el = document.querySelector('link[rel="icon"][data-animated]');
    if (!el) {
      el = document.createElement('link');
      el.rel = 'icon';
      el.type = 'image/png';
      el.setAttribute('data-animated', '');
      document.head.appendChild(el);
    }
    return el;
  }

  function step() {
    // Safari keeps the OLD icon unless the element itself changes, so replace
    // the node rather than only reassigning href.
    const next = link.cloneNode();
    next.href = frames[i];
    link.replaceWith(next);
    link = next;
    i = (i + 1) % frames.length;
  }

  function play(delay) {
    if (timer) return;
    timer = setInterval(step, delay);
  }

  function pause() {
    clearInterval(timer);
    timer = null;
  }

  /** Slice the sheet into one data URL per frame. */
  function render(sheet, meta) {
    const canvas = document.createElement('canvas');
    canvas.width = meta.cell;
    canvas.height = meta.cell;
    const ctx = canvas.getContext('2d');
    if (!ctx) return [];

    const out = [];
    for (let n = 0; n < meta.count; n += 1) {
      ctx.clearRect(0, 0, meta.cell, meta.cell);
      ctx.drawImage(
        sheet,
        (n % meta.cols) * meta.cell, Math.floor(n / meta.cols) * meta.cell,
        meta.cell, meta.cell,          // source rect
        0, 0, meta.cell, meta.cell,    // destination rect
      );
      out.push(canvas.toDataURL('image/png'));
    }
    return out;
  }

  async function start() {
    const meta = await fetch(META).then((r) => (r.ok ? r.json() : null));
    if (!meta || !meta.count || meta.count < 2) return;

    const sheet = await new Promise((resolve, reject) => {
      const img = new Image();
      img.onload = () => resolve(img);
      img.onerror = reject;
      img.src = SPRITE;
    });

    frames = render(sheet, meta);
    if (frames.length < 2) return;

    link = iconLink();
    link.href = frames[0];

    // A hidden tab's icon isn't on screen; don't burn cycles drawing it.
    document.addEventListener('visibilitychange', () => {
      if (document.hidden) pause(); else play(meta.delay);
    });
    if (!document.hidden) play(meta.delay);

    // Stop cleanly on navigation rather than leaving an interval running
    // against a detached document.
    window.addEventListener('pagehide', pause);
  }

  // Wait for a quiet moment: the icon is decoration, and the page's own data
  // fetches matter more than this does.
  const boot = () => start().catch(() => { /* static icon stands */ });
  if (document.readyState === 'complete') boot();
  else window.addEventListener('load', boot);
})();
