(() => {
  const tabs = ['Overview', 'System Architecture', 'Database Comparison', 'Threat Intelligence', 'False-Positive Detection', 'Benchmarking', 'Performance Analytics', 'Scalability Assessment', 'Comparative Report', 'Threat Communities', 'Settings'];
  const esc = value => String(value ?? '').replace(/[&<>"']/g, ch => ({ '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;' })[ch]);
  const api = async (url, options = {}) => {
    const response = await fetch(url, { credentials: 'same-origin', ...options, headers: { ...(options.body ? { 'Content-Type': 'application/json' } : {}), ...(options.headers || {}) } });
    if (!response.ok) { const body = await response.json().catch(() => ({})); throw Error(typeof body.detail === 'string' ? body.detail : body.detail?.message || `Request failed (${response.status})`); }
    return response.json();
  };
  const stateLabel = state => `<span class="vector-message is-${state === 'connected' || state === 'ready' ? 'success' : 'info'}">${esc(state || 'unknown')}</span>`;
  let activeRoot = null;
  let activeTab = 0;
  let statusData = null;
  let communityData = [];

  function rootTemplate() {
    return `<div class="vector-workspace chapter18-workspace"><header class="vector-workspace-heading"><div><span class="portfolio-kicker">PROJECT 7 · LABS 18.1–18.2</span><h2>Comparative threat intelligence</h2><p>One normalized dataset, three database models, measured operations and analyst-led classifications.</p></div><button class="xs-btn secondary" data-c18-refresh>Refresh</button></header>
      <nav class="c18-tabs" role="tablist" aria-label="Chapter 18 project sections">${tabs.map((name, i) => `<button type="button" role="tab" aria-selected="${i === activeTab}" data-c18-tab="${i}">${esc(name)}</button>`).join('')}</nav>
      <div data-c18-content aria-live="polite"></div><div data-c18-message aria-live="polite"></div></div>`;
  }
  function setMessage(root, message, bad = false) { root.querySelector('[data-c18-message]').innerHTML = message ? `<div class="vector-message is-${bad ? 'error' : 'success'}">${esc(message)}</div>` : ''; }
  function cards(status = statusData?.databases || {}) {
    return `<div class="vector-status-grid">${['mongodb','cassandra','neo4j'].map(name => { const item = status[name] || {}; const note = item.message || item.reason || (item.schema_ready ? 'Chapter 18 schema ready' : item.status === 'connected' ? 'Chapter 18 schema not ready' : item.version || 'Status not available'); return `<article class="vector-status-card"><span>${name}</span><strong>${esc(item.status || 'checking')}</strong><small>${esc(note)}${item.record_count !== undefined ? ` · ${Number(item.record_count).toLocaleString()} records` : ''}</small></article>`; }).join('')}</div>`;
  }
  function panel(index) {
    if (index === 0) return `<h3>System status</h3>${cards()}<p>Imported record capacity: 100,000 records. Only imported source records are included in comparisons.</p><button class="xs-btn" data-c18-schema>Prepare isolated MongoDB schema</button>`;
    if (index === 1) return `<h3>System architecture</h3><ol class="vector-stage-list"><li>Validate and normalize incoming threat records once.</li><li>Persist to MongoDB; write matching query tables in configured Cassandra and explicit relationships in Neo4j.</li><li>FastAPI reports adapter readiness and measured operations; admins review evidence.</li></ol><p>MongoDB database and Cassandra keyspace default to <code>threat_intel_lab18</code>; Neo4j uses Chapter 18 Threat labels.</p>`;
    if (index === 2) return `<h3>Database comparison</h3>${cards()}<p>Same imported records feed all configured adapters. An unavailable database is shown as unavailable or not tested; no sample latency is substituted.</p><button class="xs-btn secondary" data-c18-status>Refresh database status</button>`;
    if (index === 3) return `<h3>Threat intelligence</h3><form data-c18-search><input name="q" placeholder="Search ID, indicator, target" maxlength="256"><input name="source" placeholder="Exact source" maxlength="120"><button class="xs-btn">Search</button><button type="button" class="xs-btn secondary" data-c18-import-open>Import JSON</button></form><div data-c18-import hidden><p>Paste a JSON array of normalized records (up to 10,000 records per import).</p><textarea rows="7" data-c18-json placeholder='[{"threat_id":"...","source":"...","from_source":"...","threat_type":"...","timestamp":"2026-01-01T00:00:00Z"}]'></textarea><button class="xs-btn" data-c18-import>Import records</button></div><div data-c18-table>Load records with Search.</div>`;
    if (index === 4) return `<h3>False-positive analyst review</h3><p>Investigations follow explicit related-threat identifiers, report unresolved links and exact transaction cross-references, and require an analyst decision. Shared indicators and transaction matches are evidence only.</p><form data-c18-investigate><input name="threat_id" required placeholder="Imported seed threat ID"><button class="xs-btn">Build investigation</button></form><div data-c18-investigations></div><form data-c18-review><select name="subject_type"><option value="threat">Threat</option><option value="investigation">Investigation</option></select><input name="subject_id" required placeholder="Threat or investigation ID"><select name="decision"><option value="needs_review">Needs review</option><option value="confirmed_threat">Keep as real threat</option><option value="false_positive">Confirm false positive</option></select><input name="reason" required maxlength="2000" placeholder="Analyst note: evidence and rationale"><button class="xs-btn">Save analyst review</button></form><p>Review records are evidence for analysts. Current benchmark exports do not exclude false-positive decisions.</p><h4>Analyst review history</h4><div data-c18-reviews></div>`;
    if (index === 5) return `<h3>Benchmarking</h3><p>Runs four database query patterns ten times each and reports median/p95 from actual operations. Requires at least 100,000 imported records and all filters must match existing records.</p><form data-c18-benchmark><label>Source<input name="source" required maxlength="120"></label><label>Target<input name="target" maxlength="2048"></label><label>Indicator<input name="indicator" maxlength="2048"></label><label>From (your local time)<input name="from_datetime" type="datetime-local" required></label><label>To (your local time)<input name="to_datetime" type="datetime-local" required></label><label>Seed threat ID (optional)<input name="seed_threat_id" maxlength="256"></label><button class="xs-btn">Run measured benchmark</button></form><div data-c18-benchmark-result></div>`;
    if (index === 6) return `<h3>Performance analytics</h3><button class="xs-btn secondary" data-c18-analytics>Load stored-data analytics</button><div data-c18-analytics-result></div><div data-c18-history></div>`;
    if (index === 7) return `<h3>Scalability assessment</h3><p>MongoDB uses indexed document queries; Cassandra uses bounded day and shard partitions; Neo4j follows explicit edges. The import request is capped at 10,000 records, and the system target is 100,000. Actual capacity and latency depend on configured cluster resources.</p><div data-c18-scale>${cards()}</div>`;
    if (index === 8) return `<h3>Comparative report</h3><p>Choose a completed benchmark run to download a CSV or PDF summary. Reports distinguish measured, unavailable and untested operations.</p><div data-c18-reports>Loading benchmark history…</div>`;
    if (index === 9) return `<h3>Threat relationship communities</h3><p>Graphs show only explicit imported <code>related_threats</code> links returned by stored investigations. Shared attributes remain evidence candidates, not graph edges. Family classification and statistical Z-scores are not calculated by this API.</p><div data-c18-communities><p>Loading stored investigations…</p></div><div class="c18-graph-scroll" data-c18-community-graph></div><div data-c18-community-evidence></div>`;
    return `<h3>Settings and source analytics</h3><p>Optional database drivers are installed from <code>chapter-18/requirements-chapter18.txt</code>. Configure variables on the backend only. Secrets are never returned to this page.</p><ul><li>LAB18_MONGO_DATABASE</li><li>LAB18_CASSANDRA_HOSTS, PORT, KEYSPACE, DATACENTER, REPLICATION_FACTOR, USERNAME, PASSWORD</li><li>LAB18_NEO4J_URI, USERNAME, PASSWORD, DATABASE</li></ul><button class="xs-btn secondary" data-c18-status>Check configuration</button><h4>Imported source counts</h4><button class="xs-btn secondary" data-c18-sources>Load measured source counts</button><div data-c18-sources-result></div><p class="c18-unavailable-note">Precision, recall, false-positive rates, cross-database votes, source-pair anomaly scores, and Neo4j Browser connectivity are not provided by the current Chapter 18 APIs.</p>`;
  }
  function graphSvg(item) {
    const nodes = item.nodes || [];
    if (!nodes.length) return '<p>No explicit related threat records were stored in this investigation.</p>';
    const columns = Math.min(5, Math.max(1, Math.ceil(Math.sqrt(nodes.length))));
    const spacingX = 150, spacingY = 112, margin = 65;
    const rows = Math.ceil(nodes.length / columns);
    const width = margin * 2 + (columns - 1) * spacingX;
    const height = margin * 2 + (rows - 1) * spacingY;
    const positions = Object.fromEntries(nodes.map((id, index) => [id, { x: margin + (index % columns) * spacingX, y: margin + Math.floor(index / columns) * spacingY }]));
    const edges = item.edges || [];
    const lines = edges.filter(edge => positions[edge.from] && positions[edge.to]).map(edge => {
      const from = positions[edge.from], to = positions[edge.to];
      return `<line x1="${from.x}" y1="${from.y}" x2="${to.x}" y2="${to.y}" marker-end="url(#c18-arrow)"/>`;
    }).join('');
    const circles = nodes.map(id => {
      const point = positions[id], short = id.length > 15 ? `${id.slice(0, 12)}…` : id;
      return `<g><title>${esc(id)}</title><circle cx="${point.x}" cy="${point.y}" r="29"/><text x="${point.x}" y="${point.y + 4}">${esc(short)}</text></g>`;
    }).join('');
    return `<svg class="c18-network-graph" viewBox="0 0 ${width} ${height}" role="img" aria-label="Explicit threat relationship graph for seed ${esc(item.seed_threat_id)}"><defs><marker id="c18-arrow" markerWidth="8" markerHeight="8" refX="6" refY="3" orient="auto"><path d="M0,0 L0,6 L7,3 z"/></marker></defs><g class="c18-graph-edges">${lines}</g><g class="c18-graph-nodes">${circles}</g></svg>`;
  }
  function benchmarkCharts(results = {}) {
    const backends = ['mongodb', 'cassandra', 'neo4j'];
    const operations = [...new Set(backends.flatMap(name => Object.keys(results[name] || {})))];
    const charts = operations.map(operation => {
      const measured = backends.map(name => ({ name, metrics: results[name]?.[operation] }))
        .filter(row => row.metrics?.status === 'measured' && Number.isFinite(Number(row.metrics?.latency_ms?.p50)) && Number.isFinite(Number(row.metrics?.latency_ms?.p95)));
      if (!measured.length) return '';
      const max = Math.max(...measured.map(row => Number(row.metrics.latency_ms.p95)), .001);
      const width = 650, left = 112, right = 34, rowHeight = 42, top = 24, height = top + measured.length * rowHeight + 10;
      const bars = measured.map((row, index) => {
        const y = top + index * rowHeight;
        const p50 = Number(row.metrics.latency_ms.p50), p95 = Number(row.metrics.latency_ms.p95);
        const p50Width = Math.max(p50 > 0 ? 1 : 0, (p50 / max) * (width - left - right));
        const p95Width = Math.max(p95 > 0 ? 1 : 0, (p95 / max) * (width - left - right));
        return `<text x="${left - 9}" y="${y + 16}" text-anchor="end" class="c18-chart-label">${esc(row.name)}</text><rect x="${left}" y="${y + 1}" width="${p50Width}" height="11" rx="4" class="c18-chart-p50"><title>${esc(row.name)} p50 ${p50} ms</title></rect><text x="${left + p50Width + 5}" y="${y + 11}" class="c18-chart-value">${p50} ms</text><rect x="${left}" y="${y + 18}" width="${p95Width}" height="11" rx="4" class="c18-chart-p95"><title>${esc(row.name)} p95 ${p95} ms</title></rect><text x="${left + p95Width + 5}" y="${y + 28}" class="c18-chart-value">${p95} ms</text>`;
      }).join('');
      return `<figure class="c18-benchmark-chart"><figcaption>${esc(operation)} · measured latency</figcaption><p><span class="c18-chart-key p50">p50</span> <span class="c18-chart-key p95">p95</span> · actual API benchmark results in milliseconds</p><svg viewBox="0 0 ${width} ${height}" role="img" aria-label="Measured p50 and p95 latency in milliseconds by database for ${esc(operation)}">${bars}</svg></figure>`;
    }).filter(Boolean);
    return charts.length ? charts.join('') : '<p>No measured benchmark results are available to chart. Unavailable and untested operations are not plotted.</p>';
  }
  async function loadCommunities(root) {
    const response = await api('/api/chapter18/investigations?limit=50');
    communityData = response.items || [];
    const list = root.querySelector('[data-c18-communities]');
    list.innerHTML = communityData.length ? communityData.map((item, index) => `<button type="button" class="c18-community-chip" data-c18-community-select="${index}">Seed ${esc(item.seed_threat_id)} · ${Number(item.evidence?.node_count || 0)} threats · ${Number(item.evidence?.explicit_relationship_edge_count || 0)} explicit links</button>`).join('') : '<p>No investigations exist. Create an investigation from False-Positive Detection to view its explicit relationship graph.</p>';
    if (communityData.length) renderCommunity(root, 0);
  }
  function renderCommunity(root, index) {
    const item = communityData[index];
    if (!item) return;
    root.querySelector('[data-c18-community-graph]').innerHTML = graphSvg(item);
    root.querySelector('[data-c18-community-evidence]').innerHTML = `<p><strong>Investigation ${esc(item.investigation_id)}</strong> · Seed ${esc(item.seed_threat_id)} · ${Number(item.evidence?.node_count || 0)} threat records · ${Number(item.evidence?.explicit_relationship_edge_count || 0)} explicit links.</p><p>Separate shared-attribute candidates: ${esc((item.evidence?.shared_attribute_candidate_ids || []).join(', ') || 'none')}. These candidates are not rendered as relationship edges.</p>`;
  }
  async function refreshStatus(root) {
    try { statusData = await api('/api/chapter18/status'); const box = root.querySelector('[data-c18-content]'); box.innerHTML = panel(activeTab); }
    catch (error) { setMessage(root, error.message, true); }
  }
  async function loadThreats(root, form) {
    const params = new URLSearchParams(new FormData(form));
    const data = await api(`/api/chapter18/threats?${params}`);
    root.querySelector('[data-c18-table]').innerHTML = `<p>${data.total} records · showing ${data.items.length}</p><div class="c18-table-wrap"><table><thead><tr><th>Threat ID</th><th>Source</th><th>Type</th><th>Indicator / target</th><th>Time (UTC)</th><th>Review</th></tr></thead><tbody>${data.items.map(row => `<tr><td>${esc(row.threat_id)}</td><td>${esc(row.from_source)}</td><td>${esc(row.threat_type)}</td><td>${esc(row.indicator || row.target || '—')}</td><td>${esc(row.timestamp)}</td><td><button class="xs-btn" data-c18-seed="${esc(row.threat_id)}">Investigate</button></td></tr>`).join('')}</tbody></table></div>`;
  }
  async function loadHistory(root) {
    const data = await api('/api/chapter18/benchmarks?limit=30');
    const rows = data.items || [];
    root.querySelector('[data-c18-history]').innerHTML = rows.length ? `<h4>Recent measured runs</h4>${rows.map(run => `<p>${esc(run.created_at)} · ${esc(run.status)} · <code>${esc(run.run_id)}</code></p>`).join('')}` : '<p>No benchmark runs stored yet.</p>';
    const target = root.querySelector('[data-c18-reports]');
    if (target) target.innerHTML = rows.length ? rows.map(run => `<article class="vector-panel"><strong>${esc(run.created_at)} · ${esc(run.status)}</strong><p>${esc(run.run_id)}</p><a href="/api/chapter18/benchmarks/${encodeURIComponent(run.run_id)}/report?format=csv">CSV</a> · <a href="/api/chapter18/benchmarks/${encodeURIComponent(run.run_id)}/report?format=pdf">PDF</a></article>`).join('') : '<p>No reports yet.</p>';
  }
  async function activate(root, index) {
    activeTab = index;
    root.querySelectorAll('[data-c18-tab]').forEach(button => button.setAttribute('aria-selected', String(Number(button.dataset.c18Tab) === index)));
    root.querySelector('[data-c18-content]').innerHTML = panel(index);
    setMessage(root, '');
    if ([0, 2, 7, 10].includes(index)) await refreshStatus(root);
    if (index === 4) { const [data, reviews] = await Promise.all([api('/api/chapter18/investigations'), api('/api/chapter18/reviews?limit=50')]); root.querySelector('[data-c18-investigations]').innerHTML = (data.items || []).map(item => `<article class="vector-panel"><strong>${esc(item.status)} · Seed ${esc(item.seed_threat_id)}</strong><p>${item.evidence.node_count} explicit graph nodes · ${item.evidence.explicit_relationship_edge_count} explicit links · ${item.evidence.shared_attribute_candidate_ids?.length || 0} shared-attribute candidates · ${item.transaction_matches?.length || 0} exact transaction matches</p><p>Shared indicators: ${esc((item.evidence.shared_indicator_values || []).join(', ') || 'none')} · Shared targets: ${esc((item.evidence.shared_target_values || []).join(', ') || 'none')}</p><p>${esc(item.evidence.shared_attribute_interpretation || '')}</p><small>${esc(item.investigation_id)}</small></article>`).join('') || '<p>No investigations yet.</p>'; root.querySelector('[data-c18-reviews]').innerHTML = (reviews.items || []).map(item => `<article class="vector-panel"><strong>${esc(item.decision)} · ${esc(item.subject_type)} ${esc(item.subject_id)}</strong><p>${esc(item.reason)}</p><small>${esc(item.created_at)} · ${esc(item.analyst_id)}</small></article>`).join('') || '<p>No analyst reviews recorded.</p>'; }
    if (index === 6 || index === 8) await loadHistory(root);
    if (index === 9) await loadCommunities(root);
  }
  function bind(root) {
    root.addEventListener('click', async event => {
      const button = event.target.closest('button'); if (!button) return;
      try {
        if (button.dataset.c18Tab !== undefined) return activate(root, Number(button.dataset.c18Tab));
        if (button.hasAttribute('data-c18-refresh') || button.hasAttribute('data-c18-status')) return refreshStatus(root);
        if (button.hasAttribute('data-c18-schema')) { const result = await api('/api/chapter18/schema', { method: 'POST' }); statusData = { databases: result.optional_databases }; await activate(root, activeTab); setMessage(root, 'MongoDB Chapter 18 collections and indexes are ready.'); }
        if (button.hasAttribute('data-c18-import-open')) root.querySelector('[data-c18-import]').hidden = false;
        if (button.hasAttribute('data-c18-import')) { const records = JSON.parse(root.querySelector('[data-c18-json]').value); const result = await api('/api/chapter18/imports', { method: 'POST', body: JSON.stringify({ records }) }); setMessage(root, `Import ${result.status}: ${result.unique_records} unique records; ${result.mongo.status} MongoDB, ${result.cassandra.status} Cassandra, ${result.neo4j.status} Neo4j.`); }
        if (button.dataset.c18Seed) { activeTab = 4; await activate(root, 4); root.querySelector('[name="threat_id"]').value = button.dataset.c18Seed; }
        if (button.hasAttribute('data-c18-analytics')) { const data = await api('/api/chapter18/analytics'); root.querySelector('[data-c18-analytics-result]').innerHTML = `<p>30-day imported volume: ${(data.daily || []).reduce((sum, item) => sum + item.count, 0)} records · ${data.dataset_note}</p><pre>${esc(JSON.stringify(data.daily, null, 2))}</pre>`; }
        if (button.dataset.c18CommunitySelect !== undefined) renderCommunity(root, Number(button.dataset.c18CommunitySelect));
        if (button.hasAttribute('data-c18-sources')) {
          const data = await api('/api/chapter18/analytics');
          const rows = data.sources || [];
          root.querySelector('[data-c18-sources-result]').innerHTML = rows.length
            ? `<p>${rows.length} imported source groups · stored counts only. ${esc(data.dataset_note)}</p><div class="c18-table-wrap"><table><thead><tr><th>Source</th><th>Imported records</th><th>Latest record</th></tr></thead><tbody>${rows.map(row => `<tr><td>${esc(row.source || 'Not recorded')}</td><td>${Number(row.count).toLocaleString()}</td><td>${esc(row.latest || 'Not recorded')}</td></tr>`).join('')}</tbody></table></div>`
            : '<p>No imported source records are available yet.</p>';
        }
      } catch (error) { setMessage(root, error.message, true); }
    });
    root.addEventListener('submit', async event => {
      const form = event.target; event.preventDefault();
      try {
        if (form.matches('[data-c18-search]')) await loadThreats(root, form);
        if (form.matches('[data-c18-investigate]')) { const data = await api('/api/chapter18/investigations', { method: 'POST', body: JSON.stringify({ threat_id: new FormData(form).get('threat_id') }) }); await activate(root, 4); setMessage(root, `Investigation created: ${data.investigation_id}; ${data.evidence.node_count} explicit graph records, ${data.evidence.explicit_relationship_edge_count} explicit links, ${data.evidence.shared_attribute_candidate_ids.length} shared-attribute candidates, and ${data.transaction_matches.length} exact transaction matches. All findings require analyst review.`); }
        if (form.matches('[data-c18-review]')) {
          const data = Object.fromEntries(new FormData(form));
          if (data.decision === 'false_positive' && !window.confirm('Confirm this analyst false-positive decision? The current exports will still include the imported source record.')) return;
          if (data.decision === 'confirmed_threat' && !window.confirm('Keep this record classified as a real threat?')) return;
          await api('/api/chapter18/reviews', { method: 'POST', body: JSON.stringify(data) });
          setMessage(root, 'Analyst decision and note recorded. Current exports do not filter false-positive decisions.');
          await activate(root, 4);
        }
        if (form.matches('[data-c18-benchmark]')) { const data = Object.fromEntries(new FormData(form)); for (const key of ['from_datetime','to_datetime']) data[key] = new Date(data[key]).toISOString(); for (const key of ['target','indicator','seed_threat_id']) if (!data[key]) delete data[key]; const result = await api('/api/chapter18/benchmarks', { method: 'POST', body: JSON.stringify(data) }); root.querySelector('[data-c18-benchmark-result]').innerHTML = `<h4>Measured latency charts · ${esc(result.status)}</h4><div class="c18-benchmark-charts">${benchmarkCharts(result.results)}</div><details><summary>Full API benchmark response</summary><pre>${esc(JSON.stringify(result, null, 2))}</pre></details>`; }
      } catch (error) { setMessage(root, error.message, true); }
    });
  }
  document.addEventListener('portfolio:project-opened', event => {
    if (event.detail?.id !== 'chapter18') return;
    const root = event.detail?.root || document.getElementById('vector-project-workspace'); if (!root) return;
    activeRoot = root; activeTab = 0; root.innerHTML = rootTemplate(); bind(root); activate(root, 0);
  });
  document.addEventListener('portfolio:project-closed', () => { activeRoot = null; });
})();
