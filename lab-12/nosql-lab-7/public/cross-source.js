/* Cross-Source Analysis page. Independent of app.js; talks to /api/cross-source.
 *
 * Presentation only: the matching itself happens on the server. Every number shown
 * here comes from the API response; nothing is estimated or scored client-side.
 */
document.addEventListener('DOMContentLoaded', () => {
  const root = document.getElementById('view-cross');
  if (!root) return;
  const $ = id => document.getElementById(id);
  const REQUIRED = ['cs-status', 'cs-state', 'cs-body', 'cs-totals', 'cs-q', 'cs-src', 'cs-sort', 'cs-clear',
    'cs-cat', 'cs-min', 'cs-common-title', 'cs-count', 'cs-rows', 'cs-prev', 'cs-next', 'cs-page',
    'cs-overlap', 'cs-a', 'cs-b', 'cs-compare', 'cs-compare-result', 'cs-refresh'];
  const missing = REQUIRED.filter(id => !$(id));
  if (missing.length) { console.warn('Cross-Source page markup is out of date; missing:', missing.join(', ')); return; }

  const api = p => `${(window.AEGIS_API_BASE_URL || '').replace(/\/+$/, '')}/api/cross-source${p}`;
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const num = n => Number(n || 0).toLocaleString();
  const plural = (n, one, many) => `${num(n)} ${n === 1 ? one : (many || `${one}s`)}`;

  const TYPES = { cve: 'CVE', malware: 'Malware', actor: 'Threat actor', ioc: 'Indicator', tool: 'Tool', galaxy: 'Other cluster' };
  const TYPE_PLURALS = { cve: 'CVEs', malware: 'Malware', actor: 'Threat actors', ioc: 'Indicators', tool: 'Tools', galaxy: 'Other clusters' };
  const DEFAULTS = { cat: 'all', min: 2, src: '', q: '', sort: 'source_count', page: 1 };
  const PAGE_SIZE = 20;
  const state = { ...DEFAULTS, pages: 1, total: 0, loaded: false };
  let summary = null;
  let names = {};
  let currentItems = [];

  // ---------------------------------------------------------------- requests
  class ApiError extends Error {
    constructor(message, status) { super(message); this.status = status; }
  }

  async function get(path, opts = {}) {
    const { timeoutMs = 15000, ...init } = opts;
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
      let res;
      try {
        res = await fetch(api(path), { ...init, signal: controller.signal });
      } catch (err) {
        if (err.name === 'AbortError') throw new ApiError(`The request timed out after ${Math.round(timeoutMs / 1000)} seconds.`, 0);
        throw new ApiError(`Could not reach the API (${err.message}).`, 0);
      }
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new ApiError(body.detail || body.error || `HTTP ${res.status}`, res.status);
      return body;
    } finally {
      clearTimeout(timer);
    }
  }

  // ------------------------------------------------------------------ helpers
  const when = v => {
    if (!v) return '';
    const d = new Date(/(Z|[+-]\d\d:?\d\d)$/.test(v) ? v : `${v}Z`);
    return Number.isNaN(d.getTime()) ? String(v) : d.toLocaleString();
  };
  const filtersActive = () => state.cat !== DEFAULTS.cat || state.min !== DEFAULTS.min
    || state.src !== DEFAULTS.src || state.q.trim() !== '' || state.sort !== DEFAULTS.sort;

  function showState(kind, title, text, actions = []) {
    $('cs-body').hidden = true;
    $('cs-state').innerHTML = `
      <div class="xs-state ${kind === 'error' ? 'error' : ''}" role="${kind === 'error' ? 'alert' : 'status'}">
        <h3>${esc(title)}</h3>
        <p>${esc(text)}</p>
        <div class="xs-state-actions">${actions.map((a, i) => `<button type="button" class="xs-btn ${a.secondary ? 'secondary' : ''}" data-state-action="${i}">${esc(a.label)}</button>`).join('')}</div>
      </div>`;
    $('cs-state').querySelectorAll('[data-state-action]').forEach(b =>
      b.addEventListener('click', () => actions[+b.dataset.stateAction].run()));
  }

  function showBody() {
    $('cs-state').innerHTML = '';
    $('cs-body').hidden = false;
  }

  // ------------------------------------------------------------------ summary
  async function loadSummary() {
    $('cs-status').textContent = 'Loading cross-source analysis...';
    try {
      summary = await get('/summary');
    } catch (e) {
      $('cs-status').textContent = '';
      if (e.status === 404 && /not been run/i.test(e.message)) {
        showState('empty', 'No analysis has been run yet',
          'The analysis compares the records stored from every source. Run it once to see what the sources have in common.',
          [{ label: 'Run analysis', run: refresh }]);
      } else {
        showState('error', 'Could not load the cross-source analysis', e.message,
          [{ label: 'Retry', run: () => init(true) }]);
      }
      return false;
    }

    const sources = Array.isArray(summary.sources) ? summary.sources : [];
    names = Object.fromEntries(sources.map(s => [s.key, s.name]));
    $('cs-status').innerHTML = `Analyzed <b>${num(summary.records_analyzed)}</b> records (${num(summary.attributes_analyzed)} indicators) from `
      + `<b>${plural(sources.length, 'source')}</b>${sources.length ? ` (${sources.map(s => esc(s.name)).join(', ')})` : ''}`
      + (summary.analyzed_at ? ` &middot; snapshot taken ${esc(when(summary.analyzed_at))}` : '');
    return true;
  }

  async function renderCards() {
    const totals = summary.totals || {};
    const sources = summary.sources || [];
    const overlap = summary.overlap || [];
    const total = Number(totals.total ?? Object.keys(TYPES).reduce((n, k) => n + Number(totals[k] || 0), 0));
    const represented = new Set(overlap.flatMap(o => [o.a, o.b])).size;

    let strong = 0;
    if (sources.length >= 3 && total > 0) {
      try { strong = (await get('/common?min_sources=3&page_size=1')).total; } catch { strong = null; }
    }

    const card = (label, value, hint, tone = '') => `
      <div class="xs-card-stat ${tone}">
        <span class="xs-stat-label">${esc(label)}</span>
        <span class="xs-stat-value">${esc(value)}</span>
        <span class="xs-stat-hint">${esc(hint)}</span>
      </div>`;
    $('cs-totals').innerHTML = [
      card('Total matches', num(total), 'Items found in 2+ sources', total ? 'accent' : ''),
      card('Found in 3+ sources', strong === null ? 'Unavailable' : num(strong), 'Strongest corroboration. The analysis records no confidence score.'),
      card('Sources represented', `${represented} of ${sources.length}`, 'Sources that share at least one item'),
      card('Records analyzed', num(summary.records_analyzed), `${num(summary.attributes_analyzed)} indicators compared`),
    ].join('');
  }

  function renderFilters() {
    const totals = summary.totals || {};
    const sources = summary.sources || [];
    const overlap = summary.overlap || [];
    const total = Number(totals.total || 0);

    const chip = (value, label, active, disabled) =>
      `<button type="button" class="xs-chip${active ? ' active' : ''}" data-v="${esc(value)}"${disabled ? ' disabled' : ''} aria-pressed="${active}">${label}</button>`;
    $('cs-cat').innerHTML = chip('all', `All <span>${num(total)}</span>`, state.cat === 'all', false)
      + Object.keys(TYPES).map(k => chip(k, `${esc(TYPE_PLURALS[k])} <span>${num(totals[k])}</span>`, state.cat === k, !totals[k])).join('');

    const maxMin = Math.min(4, Math.max(2, sources.length));
    $('cs-min').innerHTML = Array.from({ length: maxMin - 1 }, (_, i) => i + 2)
      .map(n => chip(String(n), `${n}+ sources`, state.min === n, false)).join('');

    const options = [`<option value="">All sources</option>`]
      .concat(sources.map(s => `<option value="${esc(s.key)}">${esc(s.name)}</option>`))
      .concat(overlap.map(o => {
        const n = Object.values(o.counts || {}).reduce((a, b) => a + Number(b || 0), 0);
        return `<option value="${esc(`${o.a},${o.b}`)}">${esc(o.a_name || o.a)} ↔ ${esc(o.b_name || o.b)} (${n})</option>`;
      }));
    $('cs-src').innerHTML = options.join('');
    $('cs-src').value = state.src;
    $('cs-sort').value = state.sort;
    $('cs-q').value = state.q;

    const opts = sources.map(s => `<option value="${esc(s.key)}">${esc(s.name)}</option>`).join('');
    $('cs-a').innerHTML = opts; $('cs-b').innerHTML = opts;
    if (sources[1]) $('cs-b').value = sources[1].key;

    $('cs-overlap').innerHTML = overlap.length ? overlap.map(o => {
      const parts = Object.keys(TYPES).filter(k => o.counts && o.counts[k]).map(k => `${num(o.counts[k])} ${TYPE_PLURALS[k]}`);
      return `<div class="xs-overlap-row">
        <div><b>${esc(o.a_name || o.a)} ↔ ${esc(o.b_name || o.b)}</b><div class="xs-muted">${esc(parts.join(' · ') || 'Nothing in common yet')}</div></div>
        <button type="button" class="xs-btn secondary" data-a="${esc(o.a)}" data-b="${esc(o.b)}">View matches</button>
      </div>`;
    }).join('') : '<p class="xs-muted">No information is shared between sources yet.</p>';
  }

  // --------------------------------------------------------------------- list
  let rowsReq = 0;

  function syncClear() { $('cs-clear').hidden = !filtersActive(); }

  function rowsMessage(title, text, actions = [], error = false) {
    $('cs-rows').innerHTML = `
      <div class="xs-state ${error ? 'error' : ''}" role="${error ? 'alert' : 'status'}">
        <h3>${esc(title)}</h3><p>${esc(text)}</p>
        <div class="xs-state-actions">${actions.map((a, i) => `<button type="button" class="xs-btn ${a.secondary ? 'secondary' : ''}" data-row-action="${i}">${esc(a.label)}</button>`).join('')}</div>
      </div>`;
    $('cs-rows').querySelectorAll('[data-row-action]').forEach(b => b.addEventListener('click', () => actions[+b.dataset.rowAction].run()));
    $('cs-count').textContent = '';
    $('cs-page').textContent = '';
    $('cs-prev').disabled = true; $('cs-next').disabled = true;
  }

  async function loadRows() {
    const req = ++rowsReq;
    syncClear();
    $('cs-rows').innerHTML = '<div class="xs-loading"><span class="rel-spinner" aria-hidden="true"></span><span>Loading matches...</span></div>';
    const qs = new URLSearchParams({ category: state.cat, min_sources: state.min, sources: state.src, q: state.q.trim(), sort: state.sort, page: state.page, page_size: PAGE_SIZE });
    let d;
    try {
      d = await get(`/common?${qs}`);
    } catch (e) {
      if (req === rowsReq) rowsMessage('Could not load matches', e.message, [{ label: 'Retry', run: loadRows }], true);
      return;
    }
    if (req !== rowsReq) return;

    currentItems = d.items || [];
    state.total = d.total || 0;
    state.pages = Math.max(1, Math.ceil(state.total / (d.page_size || PAGE_SIZE)));
    if (!currentItems.length) {
      if (filtersActive()) {
        rowsMessage('No matches for these filters', 'Try a broader search, a different source, or clear the filters.',
          [{ label: 'Clear filters', run: clearFilters }]);
      } else {
        rowsMessage('No shared intelligence found', 'The analysis ran, but no CVE, malware, actor, indicator or other item appears in more than one source yet.');
      }
      return;
    }

    const from = (d.page - 1) * d.page_size + 1;
    $('cs-count').textContent = `Showing ${num(from)}–${num(from + currentItems.length - 1)} of ${num(state.total)}`;
    $('cs-page').textContent = `Page ${d.page} of ${state.pages}`;
    $('cs-prev').disabled = d.page <= 1;
    $('cs-next').disabled = d.page >= state.pages;

    $('cs-rows').innerHTML = currentItems.map((r, i) => {
      const strong = r.source_count >= 3;
      const srcs = (r.sources || []).map(s => `<span class="xs-source" title="${esc(`${num(s.records)} record(s)`)}">${esc(s.name)} <em>${num(s.records)}</em></span>`).join('');
      return `
        <article class="xs-match">
          <div class="xs-match-main">
            <div class="xs-match-top">
              <span class="xs-type">${esc(TYPES[r.category] || r.type)}</span>
              <span class="xs-corroboration ${strong ? 'strong' : ''}">Corroborated by ${plural(r.source_count, 'source')}</span>
            </div>
            <h4 class="xs-value">${esc(r.value)}</h4>
            <div class="xs-sources">${srcs}</div>
            <div class="xs-muted">${plural(r.record_count, 'record')} · ${esc(r.match_type || 'match')}</div>
          </div>
          <button type="button" class="xs-btn secondary" data-i="${i}">Details</button>
        </article>`;
    }).join('');
  }

  // ------------------------------------------------------------------ details
  async function showDetails(item) {
    const drawer = $('drawer-backdrop'), content = $('drawer-content');
    $('drawer-title').textContent = item.value;
    $('drawer-type').textContent = (TYPES[item.category] || item.type || 'MATCH').toUpperCase();
    content.innerHTML = '<div class="loading-state">Loading evidence...</div>';
    drawer.classList.remove('hidden');
    let e;
    try {
      e = await get(`/evidence?type=${encodeURIComponent(item.type)}&key=${encodeURIComponent(item.key)}`);
    } catch (err) {
      content.innerHTML = `<div class="xs-state error"><h3>Could not load the evidence</h3><p>${esc(err.message)}</p>
        <div class="xs-state-actions"><button type="button" class="xs-btn" id="cs-evidence-retry">Retry</button></div></div>`;
      $('cs-evidence-retry').addEventListener('click', () => showDetails(item));
      return;
    }
    const dates = (e.sources || []).flatMap(s => (s.records || []).map(r => r.date)).filter(Boolean).sort();
    const range = dates.length ? (dates[0] === dates[dates.length - 1] ? dates[0] : `${dates[0]} → ${dates[dates.length - 1]}`) : 'Not provided by the sources';
    const showCap = 15;
    content.innerHTML = `
      <p class="xs-corroboration ${e.source_count >= 3 ? 'strong' : ''}">Found in ${plural(e.source_count, 'source')} · ${plural(e.record_count, 'record')}</p>
      <dl class="xs-facts">
        <div><dt>Type</dt><dd>${esc(TYPES[e.category] || e.type)}</dd></div>
        <div><dt>Record dates</dt><dd>${esc(range)}</dd></div>
        <div><dt>Matching</dt><dd>${esc((e.technical && e.technical.matching) || 'n/a')}</dd></div>
        <div><dt>Analysis snapshot</dt><dd>${esc(when(e.technical && e.technical.analyzed_at) || 'n/a')}</dd></div>
      </dl>
      ${(e.sources || []).map((s, n) => `
        <div class="xs-evidence">
          <b>Source ${n + 1}: ${esc(s.name)}</b> <span class="xs-muted">${plural(s.record_count, 'record')}</span>
          <ul>${(s.records || []).map((r, ri) => `<li${ri >= showCap ? ' hidden data-more' : ''}>${esc(r.title)}<br><small>${esc(r.date || 'no date')} · ${esc(r.misp_uuid)}${r.reference && /^https?:\/\//.test(r.reference) ? ` · <a href="${esc(r.reference)}" target="_blank" rel="noopener noreferrer">open source record</a>` : ''}</small></li>`).join('')}</ul>
          ${(s.records || []).length > showCap ? `<button type="button" class="xs-btn secondary" data-more-btn>Show all ${num(s.records.length)}${s.record_count > s.records.length ? ` of ${num(s.record_count)}` : ''} records</button>` : ''}
        </div>`).join('')}
      <h3 class="xs-h3">Why is this a match?</h3>
      <p>${esc(e.why || '')} This does not mean the records are duplicates.</p>
      <details><summary>Technical details</summary><pre>${esc(JSON.stringify({
        sources: (e.sources || []).map(s => ({ source_feed: s.url, records: (s.records || []).map(r => ({ misp_uuid: r.misp_uuid, record_id: r.record_id, field: r.field })) })),
        ...(e.technical || {}),
      }, null, 2))}</pre></details>`;
    content.querySelectorAll('[data-more-btn]').forEach(b => b.addEventListener('click', () => {
      b.parentElement.querySelectorAll('[data-more]').forEach(li => { li.hidden = false; });
      b.remove();
    }));
  }

  // ---------------------------------------------------------------- compare
  async function compare() {
    const a = $('cs-a').value, b = $('cs-b').value, out = $('cs-compare-result');
    if (!a || !b || a === b) { out.innerHTML = '<p class="xs-muted">Pick two different sources.</p>'; return; }
    out.innerHTML = '<div class="xs-loading"><span class="rel-spinner" aria-hidden="true"></span><span>Comparing...</span></div>';
    try {
      const d = await get(`/compare?a=${encodeURIComponent(a)}&b=${encodeURIComponent(b)}&page_size=10`);
      const parts = Object.keys(TYPES).filter(k => d.counts && d.counts[k]).map(k => `${num(d.counts[k])} ${TYPE_PLURALS[k]}`);
      out.innerHTML = `<h4 class="xs-h3">${esc(d.a.name)} ↔ ${esc(d.b.name)}</h4>
        <p class="xs-muted">${esc(parts.join(' · ') || 'Nothing in common yet')}</p>
        ${d.items.length ? `<ul class="xs-compare-list">${d.items.map(r => `<li>${esc(TYPES[r.category] || r.type)}: ${esc(r.value)} <span class="xs-muted">(${plural(r.record_count, 'record')})</span></li>`).join('')}</ul>
        <button type="button" class="xs-btn secondary" id="cs-cmp-go">View all in the list</button>` : ''}`;
      const go = $('cs-cmp-go');
      if (go) go.addEventListener('click', () => setSource(`${a},${b}`));
    } catch (e) {
      out.innerHTML = `<div class="xs-state error"><p>${esc(e.message)}</p></div>`;
    }
  }

  // ---------------------------------------------------------------- actions
  function setSource(value) {
    state.src = value; state.page = 1;
    $('cs-src').value = value;
    loadRows();
    $('cs-common-title').scrollIntoView({ behavior: 'smooth' });
  }

  function clearFilters() {
    Object.assign(state, DEFAULTS);
    renderFilters();
    loadRows();
  }

  async function refresh() {
    const b = $('cs-refresh');
    b.disabled = true; b.textContent = 'Analyzing...';
    try {
      await get('/refresh', { method: 'POST', timeoutMs: 180000 });
      await init(true);
    } catch (e) {
      showState('error', 'Could not refresh the analysis', e.message, [{ label: 'Retry', run: refresh }]);
    } finally {
      b.disabled = false; b.textContent = 'Refresh Analysis';
    }
  }

  async function init(force) {
    if (state.loaded && !force) return;
    if (!(await loadSummary())) { state.loaded = false; return; }
    state.loaded = true;
    if (!Number((summary.totals || {}).total || 0) && !(summary.overlap || []).length) {
      showState('empty', 'No shared intelligence yet',
        `${num(summary.records_analyzed)} records from ${plural((summary.sources || []).length, 'source')} were analyzed and nothing appears in more than one source. `
        + 'This is a valid result, not an error. New feed data is picked up when you refresh the analysis.',
        [{ label: 'Refresh analysis', run: refresh }]);
      return;
    }
    showBody();
    renderFilters();
    await Promise.all([renderCards(), loadRows()]);
  }

  // --------------------------------------------------------------- listeners
  // All bound once, on elements that exist for the life of the page.
  const chipGroup = (id, apply) => $(id).addEventListener('click', e => {
    const b = e.target.closest('.xs-chip');
    if (!b || b.disabled) return;
    apply(b.dataset.v);
    state.page = 1;
    $(id).querySelectorAll('.xs-chip').forEach(c => {
      const on = c === b;
      c.classList.toggle('active', on);
      c.setAttribute('aria-pressed', String(on));
    });
    loadRows();
  });
  chipGroup('cs-cat', v => { state.cat = v; });
  chipGroup('cs-min', v => { state.min = Number(v); });
  $('cs-src').addEventListener('change', e => { state.src = e.target.value; state.page = 1; loadRows(); });
  $('cs-sort').addEventListener('change', e => { state.sort = e.target.value; state.page = 1; loadRows(); });
  let searchTimer;
  $('cs-q').addEventListener('input', e => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => { state.q = e.target.value; state.page = 1; loadRows(); }, 300);
  });
  $('cs-clear').addEventListener('click', clearFilters);
  $('cs-prev').addEventListener('click', () => { if (state.page > 1) { state.page--; loadRows(); } });
  $('cs-next').addEventListener('click', () => { if (state.page < state.pages) { state.page++; loadRows(); } });
  $('cs-compare').addEventListener('click', compare);
  $('cs-overlap').addEventListener('click', e => {
    const b = e.target.closest('[data-a]');
    if (b) setSource(`${b.dataset.a},${b.dataset.b}`);
  });
  $('cs-rows').addEventListener('click', e => {
    const b = e.target.closest('[data-i]');
    if (b && currentItems[+b.dataset.i]) showDetails(currentItems[+b.dataset.i]);
  });
  $('cs-refresh').addEventListener('click', refresh);
  const navButton = document.querySelector('[data-view="cross"]');
  if (navButton) navButton.addEventListener('click', () => init(false));
});
