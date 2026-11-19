/* Cross-Source Analysis page. Independent of app.js; talks to /api/cross-source. */
document.addEventListener('DOMContentLoaded', () => {
  const root = document.getElementById('view-cross');
  if (!root) return;
  const api = p => `${(window.AEGIS_API_BASE_URL || '').replace(/\/+$/, '')}/api/cross-source${p}`;
  const $ = id => document.getElementById(id);
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const LABELS = { cve: 'CVEs', malware: 'Malware', actor: 'Threat actors', ioc: 'Common indicators', tool: 'Tools', galaxy: 'Other clusters' };
  const state = { cat: 'all', min: 2, src: '', q: '', sort: 'source_count', page: 1, pages: 1, loaded: false };
  let names = {};

  const get = async (path, opts) => {
    const r = await fetch(api(path), opts);
    const body = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(body.detail || `HTTP ${r.status}`);
    return body;
  };
  const counts = c => Object.keys(LABELS).filter(k => c[k]).map(k => `${c[k]} ${LABELS[k]}`).join(' · ') || 'Nothing in common yet';

  async function loadSummary() {
    try {
      const m = await get('/summary');
      names = Object.fromEntries(m.sources.map(s => [s.key, s.name]));
      $('cs-status').innerHTML = `Records analyzed: <b>${m.records_analyzed.toLocaleString()}</b> (${m.attributes_analyzed.toLocaleString()} indicators) · Sources analyzed: <b>${m.sources.length}</b> (${m.sources.map(s => esc(s.name)).join(', ')}) · Last analyzed: <b>${esc(new Date(m.analyzed_at).toLocaleString())}</b>`;
      $('cs-totals').innerHTML = Object.keys(LABELS).map(k => `<div class="cs-card"><b>${m.totals[k] || 0}</b>${LABELS[k]} found in 2+ sources</div>`).join('');
      $('cs-overlap').innerHTML = m.overlap.length ? m.overlap.map(o => `<div class="cs-pair"><b>${esc(o.a_name)} ↔ ${esc(o.b_name)}</b><br>${esc(counts(o.counts))}
        <br><button class="pill" data-a="${esc(o.a)}" data-b="${esc(o.b)}">View common data</button></div>`).join('') : '<div class="empty-prompt">No information is shared between sources yet.</div>';
      const opts = m.sources.map(s => `<option value="${esc(s.key)}">${esc(s.name)}</option>`).join('');
      $('cs-a').innerHTML = opts; $('cs-b').innerHTML = opts; if (m.sources[1]) $('cs-b').value = m.sources[1].key;
      $('cs-src').innerHTML = `<option value="">All sources</option>${opts}`;
      return true;
    } catch (e) {
      $('cs-status').textContent = e.message; $('cs-totals').innerHTML = ''; $('cs-overlap').innerHTML = ''; $('cs-rows').innerHTML = '';
      return false;
    }
  }

  const badges = s => s.map(x => `<span class="tag-badge cyan">✓ ${esc(x.name)}</span>`).join(' ');

  let rowsReq = 0;
  async function loadRows() {
    const req = ++rowsReq;
    const qs = new URLSearchParams({ category: state.cat, min_sources: state.min, sources: state.src, q: state.q, sort: state.sort, page: state.page, page_size: 25 });
    try {
      const d = await get(`/common?${qs}`);
      if (req !== rowsReq) return;
      state.pages = Math.max(1, Math.ceil(d.total / d.page_size));
      $('cs-common-title').textContent = `Common data (${d.total.toLocaleString()})`;
      $('cs-rows').innerHTML = d.items.map((r, i) => `<tr><td>${esc(r.type)}</td><td>${esc(r.value)}</td><td>${badges(r.sources)}</td>
        <td class="${r.source_count >= 3 ? 'cs-strong' : ''}">${r.source_count}</td><td>${r.record_count}</td><td>${esc(r.match_type)}</td>
        <td><button class="pill" data-i="${i}">View evidence</button></td></tr>`).join('') || '<tr><td colspan="7">Nothing found for these filters.</td></tr>';
      $('cs-rows').dataset.items = JSON.stringify(d.items.map(r => [r.type, r.key]));
      $('cs-page').textContent = `Page ${d.page} of ${state.pages}`;
    } catch (e) { $('cs-rows').innerHTML = `<tr><td colspan="7">${esc(e.message)}</td></tr>`; }
  }

  async function showEvidence(type, key) {
    const e = await get(`/evidence?type=${encodeURIComponent(type)}&key=${encodeURIComponent(key)}`);
    $('drawer-content').innerHTML = `<h2>${esc(e.value)}</h2><p class="cs-strong">Common across ${e.source_count} sources · ${e.record_count} records</p>
      ${e.sources.map((s, n) => `<div class="cs-src"><b>SOURCE ${n + 1}: ${esc(s.name)}</b><br>Records: ${s.record_count}
        <ul>${s.records.map((r, ri) => `<li${ri >= 15 ? ' hidden data-more' : ''}>${esc(r.title)}<br><small>${esc(r.date || '')} · UUID ${esc(r.misp_uuid)}${r.reference && /^https?:\/\//.test(r.reference) ? ` · <a href="${esc(r.reference)}" target="_blank" rel="noopener noreferrer">open</a>` : ''}</small></li>`).join('')}</ul>
        ${s.records.length > 15 ? `<button class="pill" data-more-btn>Show all ${s.records.length}${s.record_count > s.records.length ? ` of ${s.record_count}` : ''} records</button>` : ''}</div>`).join('')}
      <h3>Why is this common?</h3><p>${esc(e.why)} This does not mean the records are duplicates.</p>
      <details><summary>Technical details</summary><pre>${esc(JSON.stringify({ sources: e.sources.map(s => ({ source_feed: s.url, records: s.records.map(r => ({ misp_uuid: r.misp_uuid, record_id: r.record_id, field: r.field })) })), ...e.technical }, null, 2))}</pre></details>`;
    $('drawer-content').querySelectorAll('[data-more-btn]').forEach(b => b.addEventListener('click', () => {
      b.parentElement.querySelectorAll('[data-more]').forEach(li => { li.hidden = false; }); b.remove();
    }));
    $('drawer-backdrop').classList.remove('hidden');
  }

  async function compare() {
    const a = $('cs-a').value, b = $('cs-b').value, out = $('cs-compare-result');
    if (a === b) { out.innerHTML = '<div class="empty-prompt">Pick two different sources.</div>'; return; }
    try {
      const d = await get(`/compare?a=${encodeURIComponent(a)}&b=${encodeURIComponent(b)}&page_size=10`);
      out.innerHTML = `<h4>${esc(d.a.name)} ↔ ${esc(d.b.name)}</h4><p>${esc(counts(d.counts))}</p>
        <button class="pill" id="cs-cmp-go">View all common data</button>
        <ul>${d.items.map(r => `<li>${esc(r.type)}: ${esc(r.value)} (${r.record_count} records)</li>`).join('')}</ul>`;
      $('cs-cmp-go').onclick = () => setSource(`${a},${b}`);
    } catch (e) { out.innerHTML = `<div class="empty-prompt">${esc(e.message)}</div>`; }
  }

  function setSource(v) {
    state.src = v; state.page = 1;
    if (!$('cs-src').querySelector(`option[value="${v}"]`)) $('cs-src').insertAdjacentHTML('beforeend', `<option value="${esc(v)}">${esc(v.split(',').map(k => names[k] || k).join(' ↔ '))}</option>`);
    $('cs-src').value = v; loadRows(); $('cs-common-title').scrollIntoView({ behavior: 'smooth' });
  }

  const pills = (id, fn) => $(id).addEventListener('click', e => {
    const b = e.target.closest('.pill'); if (!b) return;
    $(id).querySelectorAll('.pill').forEach(p => p.classList.toggle('active', p === b)); fn(b.dataset.v); state.page = 1; loadRows();
  });
  pills('cs-cat', v => state.cat = v);
  pills('cs-min', v => state.min = +v);
  $('cs-src').addEventListener('change', e => { state.src = e.target.value; state.page = 1; loadRows(); });
  $('cs-sort').addEventListener('change', e => { state.sort = e.target.value; state.page = 1; loadRows(); });
  let timer; $('cs-q').addEventListener('input', e => { clearTimeout(timer); timer = setTimeout(() => { state.q = e.target.value; state.page = 1; loadRows(); }, 300); });
  $('cs-prev').addEventListener('click', () => { if (state.page > 1) { state.page--; loadRows(); } });
  $('cs-next').addEventListener('click', () => { if (state.page < state.pages) { state.page++; loadRows(); } });
  $('cs-compare').addEventListener('click', compare);
  $('cs-overlap').addEventListener('click', e => { const b = e.target.closest('[data-a]'); if (b) setSource(`${b.dataset.a},${b.dataset.b}`); });
  $('cs-rows').addEventListener('click', e => {
    const b = e.target.closest('[data-i]'); if (!b) return;
    const [t, k] = JSON.parse($('cs-rows').dataset.items)[+b.dataset.i];
    showEvidence(t, k).catch(err => { $('cs-status').textContent = err.message; });
  });
  $('cs-refresh').addEventListener('click', async () => {
    const b = $('cs-refresh'); b.disabled = true; b.textContent = 'Analyzing...';
    try { await get('/refresh', { method: 'POST' }); await init(true); }
    catch (e) { $('cs-status').textContent = e.message; }
    finally { b.disabled = false; b.textContent = 'Refresh Analysis'; }
  });

  async function init(force) {
    if (state.loaded && !force) return;
    if (await loadSummary()) { state.loaded = true; loadRows(); }
  }
  document.querySelector('[data-view="cross"]').addEventListener('click', () => init(false));
});
