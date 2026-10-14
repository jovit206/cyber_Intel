/**
 * Performance & Reliability views (Write Concern Latency, Network Reliability).
 *
 * All measurements, formatting and health decisions come from the Python API
 * (/api/lab9/*). This file only fetches, shows loading/error states and renders.
 *
 * It registers its own click handlers on its two sidebar buttons, so these views
 * work regardless of which version of app.js the browser has loaded.
 */
(() => {
  const base = () => (window.AEGIS_API_BASE_URL || (window.location.protocol === 'file:' ? 'http://127.0.0.1:8000' : '')).replace(/\/+$/, '');

  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const show = (value, fallback = 'N/A') => (value === null || value === undefined || value === '' ? fallback : esc(value));
  const isObject = (v) => v !== null && typeof v === 'object' && !Array.isArray(v);
  const obj = (v) => (isObject(v) ? v : {});
  const arr = (v) => (Array.isArray(v) ? v : []);
  const count = (v) => (Number.isFinite(Number(v)) && v !== null && v !== '' ? Number(v).toLocaleString() : 'N/A');
  const words = (v) => show(String(v ?? '').replace(/_/g, ' '), 'UNKNOWN');
  const when = (v) => {
    if (!v) return 'N/A';
    const d = new Date(/(Z|[+-]\d\d:?\d\d)$/.test(v) ? v : `${v}Z`);
    return Number.isNaN(d.getTime()) ? esc(v) : esc(d.toLocaleString());
  };

  const REQUEST_TIMEOUT_MS = 10000;

  async function fetchJson(path, options = {}) {
    const { timeoutMs = REQUEST_TIMEOUT_MS, ...init } = options;
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
      let res;
      try {
        res = await fetch(`${base()}${path}`, { ...init, signal: controller.signal });
      } catch (err) {
        if (err.name === 'AbortError') throw new Error(`The request timed out after ${Math.round(timeoutMs / 1000)} seconds.`);
        throw new Error(`Could not reach the API (${err.message}).`);
      }
      let body = null;
      try { body = await res.json(); } catch { /* non-JSON or interrupted body */ }
      if (!res.ok) {
        const detail = body && (body.detail || body.error);
        throw new Error(`HTTP ${res.status}${detail ? `: ${typeof detail === 'string' ? detail : JSON.stringify(detail)}` : ''}`);
      }
      if (!isObject(body)) throw new Error('The API returned an unexpected response.');
      return body;
    } finally {
      clearTimeout(timer);
    }
  }

  const loading = (text) => `<div class="loading-state"><span class="rel-spinner" aria-hidden="true"></span><span>${esc(text)}</span></div>`;

  function renderError(container, title, err, retry) {
    container.innerHTML = `
      <div class="rel-card rel-error" role="alert">
        <h4>${esc(title)}</h4>
        <p>${esc(err.message || err)}</p>
        <button type="button" class="rel-btn secondary" data-retry>Retry</button>
      </div>`;
    const btn = container.querySelector('[data-retry]');
    if (btn) btn.addEventListener('click', retry);
  }

  // Ignore responses from a superseded request (fast tab switching / retry).
  const latest = { latency: 0, partition: 0 };
  // One load at a time per view; extra triggers while loading are ignored.
  const busy = { latency: false, partition: false };

  // ==========================================
  // WRITE CONCERN LATENCY
  // ==========================================
  function liveBenchmarkTable(results) {
    const rows = arr(results).map(r => `
      <tr>
        <td class="rel-strong">${show(r.label)}</td>
        <td class="num">${show(r.operations_display, count(r.operation_count))}</td>
        <td class="num rel-ok">${show(r.avg_display)}</td>
        <td class="num">${show(r.p50_display)}</td>
        <td class="num">${show(r.p99_display)}</td>
      </tr>`).join('');
    return `
      <div class="rel-table-wrap"><table class="rel-table">
        <thead><tr><th>Configuration</th><th class="num">Documents</th><th class="num">Avg</th><th class="num">P50</th><th class="num">P99</th></tr></thead>
        <tbody>${rows || '<tr><td colspan="5">No results returned.</td></tr>'}</tbody>
      </table></div>`;
  }

  async function runLiveBenchmark(container) {
    const button = container.querySelector('#btn-run-live-bench');
    const input = container.querySelector('#live-bench-docs');
    const status = container.querySelector('#live-bench-status');
    const out = container.querySelector('#live-bench-results');
    if (!button || !input || !status || !out) return;

    const docs = Math.min(200, Math.max(10, parseInt(input.value, 10) || 50));
    input.value = docs;
    button.disabled = true;
    status.className = '';
    status.textContent = `Running live benchmark (${docs} documents per configuration)...`;
    out.innerHTML = '';

    try {
      const data = await fetchJson('/api/lab9/run-latency', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ docs_per_config: docs, confirm: true }),
        timeoutMs: 120000,
      });
      status.className = 'rel-ok';
      status.textContent = `Completed ${new Date(data.executed_at).toLocaleTimeString()}`;
      out.innerHTML = `
        <div class="rel-card-head" style="margin-top:12px;"><h4>Live benchmark results</h4><span class="tag-badge green">LIVE</span></div>
        ${liveBenchmarkTable(data.results)}`;
    } catch (err) {
      status.className = 'rel-bad';
      status.textContent = `Benchmark failed: ${err.message}`;
    } finally {
      button.disabled = false;
    }
  }

  async function loadLatencyView() {
    const container = document.getElementById('latency-content');
    if (!container || busy.latency) return;
    busy.latency = true;
    const requestId = ++latest.latency;
    container.innerHTML = loading('Loading write concern latency measurements...');
    try {
      const data = await fetchJson('/api/lab9/latency');
      if (requestId === latest.latency) renderLatency(container, data);
    } catch (err) {
      if (requestId === latest.latency) renderError(container, 'Could not load write concern latency', err, loadLatencyView);
    } finally {
      busy.latency = false;
    }
  }

  const STATUS_PILLS = {
    RECORDED_HISTORICAL: ['info', 'Historical'],
    UNAVAILABLE: ['warn', 'No data'],
    DOCUMENTS_PRESENT_INSUFFICIENT_TIMESTAMPS: ['warn', 'Insufficient data'],
    ERROR: ['bad', 'Error'],
  };
  const statusPill = (status) => {
    const [tone, label] = STATUS_PILLS[status] || ['plain', String(status || 'Unknown').replace(/_/g, ' ')];
    return `<span class="rel-pill ${tone}" title="${esc(status)}">${esc(label)}</span>`;
  };

  function latencyBars(configs) {
    const measured = configs.filter(c => c && c.avg_ms !== null && c.avg_ms !== undefined && Number.isFinite(Number(c.avg_ms)));
    if (!measured.length) return '';
    const slowest = Math.max(...measured.map(c => Number(c.avg_ms)));
    const rows = measured.map(c => `
      <div class="rel-bar-row">
        <span class="rel-bar-label">${show(c.label)}</span>
        <div class="rel-bar-track" role="img" aria-label="${esc(c.label)} average ${esc(c.avg_display)}">
          <div class="rel-bar-fill" style="width:${slowest > 0 ? Math.max(1, (Number(c.avg_ms) / slowest) * 100).toFixed(1) : 0}%"></div>
        </div>
        <span class="rel-bar-value"><b>${show(c.avg_display)}</b> avg &middot; P99 ${show(c.p99_display)}</span>
      </div>`).join('');
    return `
      <div class="rel-card">
        <div class="rel-card-head"><h4>Average latency by configuration</h4><span class="rel-pill info plain">Historical</span></div>
        <div class="rel-bars">${rows}</div>
        <p class="rel-note">Bar length is relative to the slowest recorded configuration.</p>
      </div>`;
  }

  function savedRunsCard(data) {
    const runs = arr(data.saved_runs);
    if (!runs.length) {
      return `
        <div class="rel-card">
          <div class="rel-card-head"><h4>Saved experiment runs</h4><span class="rel-pill plain">None yet</span></div>
          <p class="rel-note">No saved runs. Run
            <span class="rel-mono">docker compose exec lab7-backend python labs/lab9/lab_01_write_concern_latency.py</span>
            to measure w:1, w:majority and w:all with and without journaling. The report is saved here as JSON and CSV.</p>
        </div>`;
    }
    return runs.map((run, index) => {
      const rows = arr(run.configurations).map(c => `
        <tr>
          <td class="rel-strong">${show(c.label)}</td>
          <td class="num">${count(c.acknowledged)} / ${count(c.documents_attempted)}</td>
          <td class="num">${count(c.timeouts)}</td>
          <td class="num">${count(c.errors)}</td>
          <td class="num">${c.total_elapsed_ms == null ? 'N/A' : esc(Number(c.total_elapsed_ms).toFixed(0))} ms</td>
          <td class="num rel-strong">${c.avg_ms == null ? 'N/A' : esc(Number(c.avg_ms).toFixed(2))} ms</td>
          <td class="num">${c.p50_ms == null ? 'N/A' : esc(Number(c.p50_ms).toFixed(2))} ms</td>
          <td class="num">${c.p99_ms == null ? 'N/A' : esc(Number(c.p99_ms).toFixed(2))} ms</td>
          <td>${statusPill(c.status === 'COMPLETED' ? 'RECORDED_HISTORICAL' : c.status)}</td>
        </tr>`).join('');
      return `
        <details class="rel-card"${index === 0 ? ' open' : ''}>
          <summary class="rel-card-head"><h4>Saved experiment run ${esc(run.run_id || '')}</h4>
            <span class="rel-pill info plain">Saved run &middot; ${when(run.finished_at)}</span></summary>
          <div class="rel-table-wrap" style="margin-top:10px;"><table class="rel-table">
            <thead><tr><th>Configuration</th><th class="num">Acknowledged</th><th class="num">Timeouts</th><th class="num">Errors</th>
              <th class="num">Total</th><th class="num">Avg</th><th class="num">P50</th><th class="num">P99</th><th>Status</th></tr></thead>
            <tbody>${rows}</tbody>
          </table></div>
          <ul class="rel-note" style="padding-left:18px;">${arr(run.interpretation).map(t => `<li>${esc(t)}</li>`).join('')}</ul>
          <p class="rel-note">${count(run.docs_per_configuration)} documents per configuration &middot; file ${show(run.file)}</p>
        </details>`;
    }).join('');
  }

  function renderLatency(container, data) {
    const configs = arr(data.configurations);
    const highlights = arr(data.highlights).map(h => `
      <div class="rel-card">
        <div class="rel-label">${show(h.title)}</div>
        <div class="rel-value">${show(h.value)}</div>
        <div class="rel-note">${show(h.detail, '')}</div>
      </div>`).join('');

    const rows = configs.map(c => `
      <tr>
        <td class="rel-strong">${show(c.label)}</td>
        <td>${show(c.desc)}</td>
        <td class="num">${show(c.operations_display, count(c.operation_count))}</td>
        <td class="num rel-strong">${show(c.avg_display)}</td>
        <td class="num">${show(c.p50_display)}</td>
        <td class="num">${show(c.p99_display)}</td>
        <td>${statusPill(c.status)}${c.error ? `<div class="rel-note rel-bad">${esc(c.error)}</div>` : ''}</td>
      </tr>`).join('');

    const empty = data.has_data
      ? ''
      : `<div class="rel-card"><p class="rel-note">No recorded benchmark data was found in <span class="rel-mono">${show(data.database, 'cap_latency_test')}</span>.
         Use the live runner below, or run <span class="rel-mono">labs/lab9/lab_01_write_concern_latency.py</span>.</p></div>`;

    const takeaways = arr(data.key_takeaways).map(t => `<li>${esc(t)}</li>`).join('');

    container.innerHTML = `
      <div class="rel-head">
        <div>
          <h3>Recorded write concern benchmark</h3>
          <p>Latency by durability level, measured earlier against <span class="rel-mono">${show(data.database, 'cap_latency_test')}</span>.</p>
        </div>
        <span class="tag-badge cyan">HISTORICAL &middot; retrieved ${when(data.retrieved_at)}</span>
      </div>
      ${empty}
      ${highlights ? `<div class="rel-grid">${highlights}</div>` : ''}
      ${latencyBars(configs)}
      <div class="rel-card">
        <h4>Configuration summary</h4>
        <div class="rel-table-wrap"><table class="rel-table">
          <thead><tr><th>Configuration</th><th>Durability level</th><th class="num">Operations</th><th class="num">Avg</th><th class="num">P50</th><th class="num">P99</th><th>Status</th></tr></thead>
          <tbody>${rows || '<tr><td colspan="7">No configurations returned.</td></tr>'}</tbody>
        </table></div>
        <p class="rel-note">${show(data.method, '')}</p>
      </div>
      ${data.w_all_note ? `<p class="rel-note" style="margin:-6px 0 14px;"><strong>About w:all.</strong> ${esc(data.w_all_note)}</p>` : ''}
      ${savedRunsCard(data)}
      ${takeaways ? `<div class="rel-card"><h4>Key takeaways</h4><ul class="rel-note" style="padding-left:18px;">${takeaways}</ul></div>` : ''}
      <div class="rel-card">
        <div class="rel-card-head"><h4>Run a live benchmark</h4><span class="tag-badge">ON DEMAND</span></div>
        <p class="rel-note" style="margin-bottom:12px;">
          Inserts documents into scratch collections (<span class="rel-mono">live_benchmark_*</span> in
          <span class="rel-mono">${show(data.database, 'cap_latency_test')}</span>) with each write concern. Runs only when you press the button.
        </p>
        <div class="rel-run">
          <label>Documents per configuration:
            <input id="live-bench-docs" class="rel-input" type="number" min="10" max="200" value="50">
          </label>
          <button id="btn-run-live-bench" type="button" class="rel-btn">Execute live benchmark</button>
          <span id="live-bench-status" class="rel-mono" aria-live="polite"></span>
        </div>
        <div id="live-bench-results"></div>
      </div>`;

    const button = container.querySelector('#btn-run-live-bench');
    if (button) button.addEventListener('click', () => runLiveBenchmark(container));
  }

  // ==========================================
  // NETWORK RELIABILITY
  // ==========================================
  function healthBadge(cr) {
    if (cr.error) return '<span class="rel-pill bad">Unknown &middot; check failed</span>';
    return cr.healthy ? '<span class="rel-pill ok">Healthy</span>' : '<span class="rel-pill bad">Degraded</span>';
  }

  function memberCard(m) {
    const isPrimary = m.state_str === 'PRIMARY';
    const healthy = m.health === 1;
    return `
      <div class="rel-member ${isPrimary ? 'primary' : ''}">
        <div class="rel-member-head">
          <span class="rel-member-name">${show(m.name)}</span>
          <span class="rel-pill ${isPrimary ? 'info' : 'plain'}">${show(m.state_str, 'UNKNOWN')}</span>
        </div>
        <dl>
          <dt>Health</dt><dd><span class="rel-pill ${healthy ? 'ok' : 'bad'}">${healthy ? 'Healthy' : 'Unreachable'}</span></dd>
          <dt>Uptime</dt><dd>${show(m.uptime_display)}</dd>
          <dt>Sync source</dt><dd>${show(m.sync_source, 'None (primary)')}</dd>
        </dl>
      </div>`;
  }

  function experimentCard(title, wc, data, countLabel, note) {
    const present = Number(data.document_count) > 0;
    return `
      <div class="rel-card">
        <div class="rel-card-head"><span class="rel-label">${esc(title)}</span><span class="tag-badge cyan">HISTORICAL</span></div>
        <div class="rel-value">${count(data.document_count)} <span style="font-size:13px;font-weight:600;color:var(--text-secondary);">${esc(countLabel)}</span></div>
        <div class="rel-note">
          <strong>Write concern:</strong> ${show(wc)}<br>
          <strong>Status:</strong> <span class="rel-pill ${data.error ? 'bad' : present ? 'ok' : 'warn'}">${words(data.status)}</span><br>
          <strong>First record:</strong> ${when(data.first_timestamp || data.timestamp)}<br>
          ${data.error ? `<span class="rel-bad">${esc(data.error)}</span><br>` : ''}
          ${show(data.explanation || data.note, '')}
        </div>
      </div>`;
  }

  function savedPartitionCard(saved) {
    const tests = arr(saved.write_tests).map(t => {
      const r = obj(t.result);
      return `<tr><td class="rel-strong">${show(t.label)}</td>
        <td class="num">${count(r.acknowledged)} / ${count(r.attempted)}</td>
        <td class="num">${count(r.timeouts)}</td><td class="num">${count(r.errors)}</td>
        <td>${arr(r.errorSamples).map(e => esc(e)).join('<br>') || '&mdash;'}</td></tr>`;
    }).join('');
    const verified = saved.recovery_verified === true;
    return `
      <div class="rel-card">
        <div class="rel-card-head"><h4>Saved partition experiment report</h4><span class="rel-pill info plain">Saved run &middot; ${when(saved.executedAt)}</span></div>
        <div class="rel-grid rel-grid-stats" style="margin-bottom:12px;">
          <div class="rel-stat"><span class="rel-label">Primary at test</span><div class="rel-stat-value">${show(saved.primary_at_test)}</div></div>
          <div class="rel-stat"><span class="rel-label">Isolated member</span><div class="rel-stat-value">${show(saved.secondary_isolated)}</div></div>
          <div class="rel-stat"><span class="rel-label">Isolation window</span><div class="rel-stat-note">${when(saved.isolation_started)}<br>&rarr; ${when(saved.isolation_ended)}</div></div>
          <div class="rel-stat"><span class="rel-label">Recovery</span><div style="margin-top:8px;"><span class="rel-pill ${verified ? 'ok' : 'warn'}">${verified ? 'Verified' : 'Not verified'}</span></div>
            <div class="rel-stat-note">${show(saved.replication_check, '')}</div></div>
        </div>
        ${tests ? `<div class="rel-table-wrap"><table class="rel-table"><thead><tr><th>Write test</th><th class="num">Acknowledged</th><th class="num">Timeouts</th><th class="num">Errors</th><th>Error samples</th></tr></thead><tbody>${tests}</tbody></table></div>` : ''}
        <p class="rel-note">Phase: ${show(saved.phase, 'single')} &middot; status ${show(saved.status)} &middot; final topology: ${show(saved.final_topology)}</p>
      </div>`;
  }

  function simulationCard(sim, primary) {
    const checks = obj(sim.static_checks);
    const checkRow = (label, ok) => `<li>${esc(label)}: <span class="${ok ? 'rel-ok' : 'rel-warn'}">${ok ? 'found' : 'not found'}</span></li>`;
    const checkList = sim.static_checks
      ? `<ul class="rel-note" style="padding-left:18px;margin-bottom:10px;">
           ${checkRow('Detects the current PRIMARY dynamically', checks.dynamic_primary_detection)}
           ${checkRow('Never selects the PRIMARY for isolation', checks.never_isolates_primary)}
           ${checkRow('Requires a typed confirmation', checks.requires_typed_confirmation)}
           ${checkRow('Restores network connectivity on exit (trap)', checks.network_restore_trap)}
           ${checkRow('Preserves network aliases on reconnect', checks.preserves_network_aliases)}
           ${checkRow('Offers a read-only --dry-run', checks.supports_dry_run)}
         </ul>
         <p class="rel-note" style="margin-bottom:10px;">${show(checks.method, '')}</p>`
      : `<p class="rel-note" style="margin-bottom:10px;">The script file is not available to the backend, so no static check was performed.</p>`;
    return `
      <div class="rel-card">
        <div class="rel-card-head"><h4>Partition simulation (manual only)</h4><span class="tag-badge">NOT RUN AUTOMATICALLY</span></div>
        <p class="rel-note" style="margin-bottom:10px;">
          This dashboard never runs the simulation or disconnects any node. The script isolates one <em>secondary</em>
          (${primary ? `current primary <span class="rel-mono">${esc(primary)}</span> stays connected` : 'primary stays connected'}),
          then reconnects it. Run it yourself, deliberately:
        </p>
        ${checkList}
        <div class="rel-code">${show(sim.dry_run_command, '')}\n${show(sim.manual_command, 'bash labs/lab9/lab_02_simulate_partition.sh')}</div>
        <p class="rel-note">${show(sim.run_from, '')}</p>
      </div>`;
  }

  async function loadPartitionView() {
    const container = document.getElementById('partition-content');
    if (!container || busy.partition) return;
    busy.partition = true;
    const requestId = ++latest.partition;
    container.innerHTML = loading('Loading network reliability data...');
    try {
      const data = await fetchJson('/api/lab9/partition');
      if (requestId === latest.partition) renderPartition(container, data);
    } catch (err) {
      if (requestId === latest.partition) renderError(container, 'Could not load network reliability data', err, loadPartitionView);
    } finally {
      busy.partition = false;
    }
  }

  function renderPartition(container, data) {

    const cr = obj(data.cluster_readiness);
    const baseline = obj(data.baseline_writes);
    const w1 = obj(data.partition_w1_writes);
    const majority = obj(data.partition_majority_writes);
    const members = arr(cr.members);
    const warnings = arr(cr.warnings).map(w => `<div class="rel-note rel-warn">${esc(w)}</div>`).join('');
    const saved = isObject(data.saved_experiment_log) ? data.saved_experiment_log : null;

    container.innerHTML = `
      <div class="rel-head">
        <div>
          <h3>Network partition experiment &amp; cluster status</h3>
          <p>What the recorded partition experiment wrote, alongside the replica set's current state.</p>
        </div>
        <span class="tag-badge cyan">LIVE STATUS &middot; HISTORICAL RECORDS</span>
      </div>

      <div class="rel-card">
        <div class="rel-card-head"><h4>Current cluster status</h4><span class="rel-pill ok">Live &middot; checked ${when(cr.checked_at)}</span></div>
        <div class="rel-grid rel-grid-stats" style="margin-bottom:14px;">
          <div class="rel-stat">
            <span class="rel-label">Replica set</span>
            <div class="rel-stat-value">${show(cr.set_name)}</div>
            <div class="rel-stat-note">${count(cr.member_count)} members</div>
          </div>
          <div class="rel-stat">
            <span class="rel-label">Primary node</span>
            <div class="rel-stat-value primary">${show(cr.detected_primary, 'None detected')}</div>
            <div class="rel-stat-note">Accepts all writes</div>
          </div>
          <div class="rel-stat">
            <span class="rel-label">Eligible secondary</span>
            <div class="rel-stat-value">${show(cr.eligible_partition_target, 'None')}</div>
            <div class="rel-stat-note">Target the manual script would isolate</div>
          </div>
          <div class="rel-stat">
            <span class="rel-label">Cluster health</span>
            <div style="margin-top:8px;">${healthBadge(cr)}</div>
            <div class="rel-stat-note">Docker network: <span class="rel-mono">${show(cr.mongo_net_network)}</span> (configured)</div>
          </div>
        </div>
        ${cr.error ? `<div class="rel-note rel-bad">Replica set status unavailable: ${esc(cr.error)}</div>` : ''}
        ${warnings}
        <div class="rel-members" style="margin-top:12px;">${members.map(memberCard).join('') || (cr.error ? '' : '<div class="rel-note">No member details returned.</div>')}</div>
      </div>

      <h4 style="font-size:14px;font-weight:700;color:var(--text-white);margin-bottom:10px;">Recorded experiment results (${show(data.database, 'lab9_partition')})</h4>
      ${data.has_historical_data ? '' : '<p class="rel-note" style="margin-bottom:12px;">No experiment records found yet. Run the manual simulation below to create them.</p>'}
      <div class="rel-grid">
        ${experimentCard('Phase 1: baseline writes', baseline.write_concern || 'w:1', baseline, 'documents', baseline.note)}
        ${experimentCard('Phase 2: writes during partition', w1.write_concern || 'w:1', w1, 'documents', w1.explanation)}
        ${experimentCard('Phase 3: majority writes during partition', majority.write_concern || 'w:majority', majority, 'documents', majority.explanation)}
      </div>
      <p class="rel-note" style="margin:-6px 0 18px;">Counts are documents stored in each collection. Whether a write was acknowledged is printed by the script and is not stored in the database.</p>

      ${saved ? savedPartitionCard(saved) : '<div class="rel-card"><div class="rel-card-head"><h4>Saved partition experiment report</h4><span class="rel-pill plain">None yet</span></div><p class="rel-note">No report found. The manual Lab 9.2 script writes one to <span class="rel-mono">labs/lab9/results/</span> when it completes.</p></div>'}

      ${simulationCard(obj(data.simulation), cr.detected_primary)}`;
  }

  // The sidebar buttons are wired here (once) so the views do not depend on app.js.
  const bindNavigation = () => {
    [['latency', loadLatencyView], ['partition', loadPartitionView]].forEach(([view, load]) => {
      const button = document.querySelector(`.sidebar-nav .nav-link[data-view="${view}"]`);
      if (button && !button.dataset.reliabilityBound) {
        button.dataset.reliabilityBound = '1';
        button.addEventListener('click', load);
      }
    });
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', bindNavigation);
  else bindNavigation();

  // Kept for callers that dispatch by view id; the in-flight guard makes repeat calls harmless.
  window.loadReliabilityView = (viewId) => {
    if (viewId === 'latency') loadLatencyView();
    if (viewId === 'partition') loadPartitionView();
  };
})();
