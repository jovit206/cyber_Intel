/* Shard evaluation and hotspot measurements. */
document.addEventListener('DOMContentLoaded', () => {
  const byId = id => document.getElementById(id);
  const statusBox = byId('lab11-cluster-status');
  const matrixBox = byId('lab11-matrix');
  const measurementsBox = byId('lab11-measurements');
  const evaluationBox = byId('lab11-dataset-summary');
  const jobMessage = byId('lab11-job-status');
  let loaded = false;
  let polling = false;

  function esc(value) {
    return String(value ?? '').replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[char]);
  }

  async function api(path, options = {}) {
    const response = await fetch(path, { credentials: 'same-origin', ...options });
    const type = response.headers.get('content-type') || '';
    const body = type.includes('json') ? await response.json() : await response.text();
    if (!response.ok) {
      const detail = body && typeof body === 'object' ? body.detail : null;
      throw new Error(typeof detail === 'string' ? detail : `Request failed (${response.status}).`);
    }
    return body;
  }

  function formatBytes(value) {
    const n = Number(value);
    if (!Number.isFinite(n)) return 'Unavailable';
    if (n < 1024) return `${n.toLocaleString()} B`;
    const units = ['KB', 'MB', 'GB', 'TB'];
    let amount = n / 1024;
    let index = 0;
    while (amount >= 1024 && index < units.length - 1) { amount /= 1024; index += 1; }
    return `${amount.toFixed(1)} ${units[index]}`;
  }

  function formatDate(value) {
    if (!value) return 'Not recorded';
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? 'Not recorded' : new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short', timeZone: 'UTC' }).format(date) + ' UTC';
  }

  function metric(label, value, note = '') {
    return `<article class="lab11-metric"><span>${esc(label)}</span><strong>${esc(value)}</strong>${note ? `<small>${esc(note)}</small>` : ''}</article>`;
  }

  function setPageState(available, message) {
    const pill = byId('lab11-page-status');
    pill.textContent = available ? 'Cluster available' : 'Cluster unavailable';
    pill.className = `rel-pill ${available ? 'ok' : 'bad'}`;
    byId('lab11-cluster-state').textContent = available ? 'Available' : 'Unavailable';
    byId('lab11-cluster-state').className = `rel-pill ${available ? 'ok' : 'bad'}`;
    if (!available && message) jobMessage.textContent = message;
  }

  async function loadStatus() {
    statusBox.innerHTML = '<div class="loading-state">Checking mongos and replica-set status…</div>';
    try {
      const data = await api('/api/chapter11/status');
      const config = data.config_replica_set || {};
      const collections = data.collections || {};
      setPageState(data.available, data.message);
      const shards = (data.shards || []).map(shard => shard.name).join(', ') || 'None registered';
      const cfgState = config.available ? `${config.name || 'Config replica set'} · ${config.healthy ? 'healthy' : 'degraded'}` : 'Unavailable';
      const memberSummary = config.members?.length
        ? config.members.map(member => `${member.name}: ${member.state || 'unknown'} (health ${member.health ?? 'unknown'})`).join(' · ')
        : 'Member details unavailable';
      const datasets = Object.entries(collections).map(([name, item]) => `${name}: ${item.available ? Number(item.documents).toLocaleString() : 'not created'}`).join(' · ') || 'No measurement collections';
      const test = data.latest_test || {};
      const sharded = Object.entries(data.sharded_collections || {}).filter(([, enabled]) => enabled).map(([name]) => name).join(', ') || 'No benchmark collections sharded';
      statusBox.innerHTML = `<div class="lab11-status-metrics">${metric('Cluster', data.available ? 'Available' : 'Unavailable', `MongoDB ${data.mongodb_version || 'version unavailable'}`)}${metric('Shards', data.available ? data.shard_count : '—', shards)}${metric('Config replica set', config.healthy ? 'Healthy' : 'Unavailable / degraded', memberSummary)}${metric('mongos', data.mongos_connected ? 'Connected' : 'Unavailable', 'Application and analysis access routes through mongos')}${metric('Sharding', data.sharding_enabled ? 'Enabled' : 'Unavailable', sharded)}${metric('Dataset sizes', data.available ? 'Measured live' : 'Unavailable', datasets)}${metric('Latest dashboard test', test.status === 'passed' ? 'Passed' : test.status === 'failed' ? 'Failed' : 'Not run here', test.finished_at ? formatDate(test.finished_at) : 'No test run recorded by this backend instance')}</div>`;
      const active = data.active_job;
      if (active && ['queued', 'running'].includes(active.status)) {
        jobMessage.textContent = `${active.phase || 'Measurement running'}…`;
        setRunButtonsDisabled(true);
      }
    } catch (error) {
      setPageState(false, error.message);
      statusBox.innerHTML = `<div class="empty-prompt lab11-unavailable">The sharding cluster status is temporarily unavailable. ${esc(error.message)}</div>`;
    }
  }

  function renderMatrix(report) {
    if (!report || !Array.isArray(report.matrix)) throw new Error('The measured decision matrix is unavailable.');
    const rows = report.matrix;
    const winner = report.recommendation;
    const columns = [
      ['candidate', 'Candidate'], ['cardinality', 'Cardinality'], ['distribution', 'Distribution'],
      ['query_targeting', 'Query targeting'], ['hotspot_risk', 'Hotspot risk'], ['range_efficiency', 'Range queries'], ['fit', 'Verdict'],
    ];
    matrixBox.innerHTML = `<div class="lab11-table-scroll"><table class="lab11-table"><thead><tr>${columns.map(([, title]) => `<th scope="col">${esc(title)}</th>`).join('')}</tr></thead><tbody>${rows.map(row => `<tr class="${row.candidate === winner ? 'lab11-recommended-row' : ''}">${columns.map(([key]) => `<td>${esc(row[key])}${key === 'candidate' && row.candidate === winner ? '<span class="lab11-recommended-badge">Recommended</span>' : ''}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
    byId('lab11-recommendation').innerHTML = `<strong>Recommendation for this workload: ${esc(winner)}.</strong> ${esc(report.reason || '')}`;
  }

  function renderEvaluation(report) {
    if (!report || !report.dataset || !report.collections || !Array.isArray(report.matrix)) throw new Error('Measured transaction dataset results are unavailable.');
    const cards = Object.entries(report.collections);
    evaluationBox.innerHTML = [
      metric('Transaction records', Number(report.dataset.record_count).toLocaleString(), 'Persisted source records used only for this evaluation'),
      metric('Unique transaction IDs', Number(report.dataset.unique_transaction_ids).toLocaleString()),
      metric('Candidate keys measured', cards.length),
      metric('Recommended for this workload', report.recommendation)
    ].join('');
    byId('lab11-evaluation-captured').textContent = `Measured ${formatDate(report.captured_at_utc)} · ${report.measurement_note || ''}`;
    const labels = cards.map(([, item]) => item.candidate);
    const cardinalities = cards.map(([, item]) => Number(item.cardinality?.distinct_values || 0));
    const cardinalityCanvas = byId('lab11-cardinality-chart');
    const distributionCanvas = byId('lab11-distribution-chart');
    if (window.Chart) {
      [cardinalityCanvas, distributionCanvas].forEach(canvas => { if (canvas._lab11Chart) canvas._lab11Chart.destroy(); });
      cardinalityCanvas._lab11Chart = new Chart(cardinalityCanvas, { type: 'bar', data: { labels, datasets: [{ label: 'Distinct values in source records', data: cardinalities, backgroundColor: ['#54745b','#b98545','#75889a','#8a6e9e'] }] }, options: { responsive: true, plugins: { legend: { display: false } }, scales: { y: { beginAtZero: true, ticks: { precision: 0 } } } } });
      const shardNames = [...new Set(cards.flatMap(([, item]) => (item.per_shard || []).map(shard => shard.shard)))];
      distributionCanvas._lab11Chart = new Chart(distributionCanvas, { type: 'bar', data: { labels, datasets: shardNames.map((shard, index) => ({ label: shard, data: cards.map(([, item]) => Number((item.per_shard || []).find(value => value.shard === shard)?.owned_documents || 0)), backgroundColor: ['#54745b','#b98545','#75889a'][index % 3] })) }, options: { responsive: true, scales: { x: { stacked: true }, y: { stacked: true, beginAtZero: true, ticks: { precision: 0 } } } } });
    } else {
      byId('lab11-cardinality-chart').outerHTML = `<div class="empty-prompt">${labels.map((label, i) => `${esc(label)}: ${cardinalities[i].toLocaleString()} distinct`).join(' · ')}</div>`;
    }
  }

  function renderMeasurements(report) {
    if (!report || !report.collections) throw new Error('The generated measurements report is empty.');
    byId('lab11-measurement-captured').textContent = `Actual cluster snapshot captured ${formatDate(report.captured_at_utc)} · MongoDB ${report.mongodb_version || 'version unavailable'} · ${Number(report.requested_documents_per_collection || 0).toLocaleString()} requested documents per collection. These are measured values, not live estimates.`;
    const sections = Object.entries(report.collections).map(([name, details]) => {
      const shards = Array.isArray(details.per_shard) ? details.per_shard : [];
      const totalOwned = shards.reduce((sum, shard) => sum + Number(shard.owned_documents || 0), 0);
      const maxCount = Math.max(1, ...shards.map(shard => Number(shard.owned_documents || 0)));
      const distribution = shards.length ? `<div class="lab11-shard-bars">${shards.map(shard => {
        const count = Number(shard.owned_documents || 0);
        const percent = totalOwned ? count / totalOwned * 100 : 0;
        return `<div class="lab11-shard-bar-row"><div class="lab11-shard-bar-label"><strong>${esc(shard.shard)}</strong><span>${count.toLocaleString()} documents · ${percent.toFixed(1)}%</span></div><div class="lab11-bar-track" role="img" aria-label="${esc(shard.shard)} owns ${count} documents, ${percent.toFixed(1)} percent"><span style="width:${Math.min(100, count / maxCount * 100)}%"></span></div><small>Storage ${formatBytes(shard.storage_size_bytes)} · logical ${formatBytes(shard.logical_size_bytes)} · indexes ${formatBytes(shard.total_index_size_bytes)}</small></div>`;
      }).join('')}</div>` : '<div class="empty-prompt">Per-shard measurements are unavailable.</div>';
      const queries = Array.isArray(details.queries) ? details.queries : [];
      const queryRows = queries.map(query => `<tr><td>${esc(query.label)}</td><td>${esc((query.targeted_shards || []).join(', ') || 'Unknown')}</td><td><span class="lab11-query-pill ${query.scatter_gather === true ? 'broadcast' : 'targeted'}">${query.scatter_gather === true ? 'Scatter / gather' : query.scatter_gather === false ? 'Targeted' : 'Unknown'}</span></td><td>${esc(query.n_returned ?? '—')}</td><td>${esc(query.docs_examined ?? '—')}</td><td>${esc(query.execution_millis ?? '—')}</td></tr>`).join('');
      return `<article class="lab11-collection-card"><header><div><span class="eyebrow">MEASURED COLLECTION</span><h3>${esc(name)}</h3></div><span class="rel-pill info">${esc(JSON.stringify(details.shard_key))}</span></header><div class="lab11-collection-metrics">${metric('Stored documents', Number(details.total_documents || 0).toLocaleString())}${metric('Distinct users', Number(details.distinct_users || 0).toLocaleString())}${metric('Insert / upsert time', details.write_seconds == null ? 'Unavailable' : `${Number(details.write_seconds).toFixed(3)} s`)}${metric('Owned documents measured', totalOwned.toLocaleString())}</div><h4>Per-shard distribution and storage</h4>${distribution}<h4>Query routing and execution</h4>${queryRows ? `<div class="lab11-table-scroll"><table class="lab11-table lab11-query-table"><thead><tr><th>Query</th><th>Targeted shards</th><th>Routing</th><th>Returned</th><th>Examined</th><th>Time (ms)</th></tr></thead><tbody>${queryRows}</tbody></table></div>` : '<div class="empty-prompt">No explain measurements are available.</div>'}</article>`;
    });
    measurementsBox.innerHTML = sections.join('');
    byId('lab11-explain-json').textContent = JSON.stringify(Object.fromEntries(Object.entries(report.collections).map(([name, value]) => [name, (value.queries || []).map(query => ({ label: query.label, raw_explain: query.raw_explain }))])), null, 2);
  }

  async function loadReports() {
    matrixBox.innerHTML = '<div class="loading-state">Loading measured decision matrix…</div>';
    measurementsBox.innerHTML = '<div class="loading-state">Loading actual measurement snapshot…</div>';
    const [matrix, measurements] = await Promise.allSettled([
      api('/api/chapter11/reports/decision-matrix'),
      api('/api/chapter11/reports/measurements'),
    ]);
    if (matrix.status === 'fulfilled') {
      try { renderEvaluation(matrix.value); renderMatrix(matrix.value); }
      catch (error) { matrixBox.innerHTML = `<div class="empty-prompt">${esc(error.message)}</div>`; }
    } else {
      matrixBox.innerHTML = `<div class="empty-prompt">Transaction dataset evaluation is unavailable. Start the dedicated cluster and refresh this evaluation.</div>`;
      evaluationBox.innerHTML = '<div class="empty-prompt">Measured transaction dataset results are unavailable.</div>';
      byId('lab11-evaluation-captured').textContent = 'No current evaluation measurement is available.';
      byId('lab11-recommendation').textContent = '';
    }
    if (measurements.status === 'fulfilled') {
      try { renderMeasurements(measurements.value); }
      catch (error) { measurementsBox.innerHTML = `<div class="empty-prompt">${esc(error.message)}</div>`; }
    } else {
      measurementsBox.innerHTML = `<div class="empty-prompt">No saved measurements are available yet. Start the dedicated sharding cluster and refresh hotspot measurements. ${esc(measurements.reason.message)}</div>`;
      byId('lab11-measurement-captured').textContent = 'Measurement snapshot unavailable.';
      byId('lab11-explain-json').textContent = 'No raw explain output is available.';
    }
  }

  function setRunButtonsDisabled(disabled) {
    byId('lab11-run-1').disabled = disabled;
    byId('lab11-run-2').disabled = disabled;
  }

  async function startRun(lab) {
    if (polling) return;
    polling = true;
    setRunButtonsDisabled(true);
    jobMessage.className = 'lab11-job-status is-running';
    jobMessage.textContent = 'Queueing measurement job…';
    try {
      const job = await api('/api/chapter11/runs', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ lab }) });
      await pollRun(job.job_id);
    } catch (error) {
      jobMessage.className = 'lab11-job-status is-error';
      jobMessage.textContent = error.message;
      polling = false;
      setRunButtonsDisabled(false);
    }
  }

  async function pollRun(jobId) {
    try {
      const job = await api(`/api/chapter11/runs/${encodeURIComponent(jobId)}`);
      jobMessage.className = `lab11-job-status ${job.status === 'failed' ? 'is-error' : job.status === 'completed' ? 'is-success' : 'is-running'}`;
      jobMessage.textContent = job.error || `${job.phase || job.status}${job.status === 'running' || job.status === 'queued' ? '…' : ''}`;
      if (job.status === 'queued' || job.status === 'running') {
        window.setTimeout(() => pollRun(jobId), 1100);
        return;
      }
      polling = false;
      setRunButtonsDisabled(false);
      if (job.output) jobMessage.textContent += ` ${job.output.split('\n').filter(Boolean).slice(-2).join(' · ')}`;
      await Promise.all([loadStatus(), loadReports()]);
    } catch (error) {
      polling = false;
      setRunButtonsDisabled(false);
      jobMessage.className = 'lab11-job-status is-error';
      jobMessage.textContent = error.message;
    }
  }

  const strategyChoices = [
    ['range_customer_id', 'Range customer / entity ID'], ['hashed_customer_id', 'Hashed customer / entity ID'],
    ['range_order_id', 'Range order ID'], ['range_event_time', 'Range event / order date'], ['hashed_event_time', 'Hashed event / order time'], ['hashed_indicator', 'Hashed indicator'],
  ];
  for (const id of ['lab11-compare-a', 'lab11-compare-b']) {
    byId(id).innerHTML = strategyChoices.map(([value, label]) => `<option value="${value}">${esc(label)}</option>`).join('');
  }
  byId('lab11-compare-a').value = 'hashed_customer_id';
  byId('lab11-compare-b').value = 'range_event_time';

  const profiles = {
    ecommerce_orders: { patterns: ['customer_lookup', 'order_lookup', 'date_range'], writes: 'moderate', ranges: true, recent: false, entity: true, a: 'range_customer_id', b: 'range_order_id' },
    customer_transactions: { patterns: ['customer_lookup', 'entity_history', 'date_range'], writes: 'high', ranges: true, recent: true, entity: true, a: 'hashed_customer_id', b: 'range_customer_id' },
    time_series: { patterns: ['time_range', 'date_range'], writes: 'high', ranges: true, recent: true, entity: false, a: 'range_event_time', b: 'hashed_event_time' },
    threat_intelligence: { patterns: ['indicator_lookup', 'date_range'], writes: 'high', ranges: false, recent: true, entity: false, a: 'hashed_indicator', b: 'range_event_time' },
  };
  byId('lab11-profile').addEventListener('change', event => {
    const preset = profiles[event.target.value];
    if (!preset) return;
    document.querySelectorAll('#lab11-advisor-form input[name="query_patterns"]').forEach(input => { input.checked = preset.patterns.includes(input.value); });
    byId('lab11-write-frequency').value = preset.writes;
    byId('lab11-ranges-important').checked = preset.ranges;
    byId('lab11-recent-writes').checked = preset.recent;
    byId('lab11-entity-scoped').checked = preset.entity;
    byId('lab11-compare-a').value = preset.a;
    byId('lab11-compare-b').value = preset.b;
  });

  byId('lab11-advisor-form').addEventListener('submit', async event => {
    event.preventDefault();
    const result = byId('lab11-advisor-result');
    result.innerHTML = '<div class="loading-state">Applying the documented workload rules…</div>';
    const payload = {
      profile: byId('lab11-profile').value,
      expected_documents: Number(byId('lab11-documents').value),
      write_frequency: byId('lab11-write-frequency').value,
      query_patterns: [...document.querySelectorAll('#lab11-advisor-form input[name="query_patterns"]:checked')].map(input => input.value),
      range_queries_important: byId('lab11-ranges-important').checked,
      recent_write_concentration: byId('lab11-recent-writes').checked,
      entity_scoped_queries: byId('lab11-entity-scoped').checked,
      compare_a: byId('lab11-compare-a').value,
      compare_b: byId('lab11-compare-b').value,
    };
    try {
      const data = await api('/api/chapter11/advisor', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
      const renderStrategy = strategy => strategy ? `<article class="lab11-strategy-card ${strategy.id === data.recommended.id ? 'is-recommended' : ''}"><div class="lab11-strategy-title"><div><h4>${esc(strategy.label)}</h4><code>${esc(strategy.key)}</code></div><span class="lab11-fit-score">${strategy.fit_score}<small>fit</small></span></div><dl><div><dt>Distribution</dt><dd>${esc(strategy.distribution)}</dd></div><div><dt>Query targeting</dt><dd>${esc(strategy.query_targeting)}</dd></div><div><dt>Range suitability</dt><dd>${esc(strategy.range_suitability)}</dd></div><div><dt>Hotspot risk</dt><dd>${esc(strategy.hotspot_risk)}</dd></div><div><dt>Verdict</dt><dd>${esc(strategy.verdict)}</dd></div></dl><details><summary>Rule breakdown</summary><ul>${(strategy.rule_breakdown || []).map(rule => `<li><strong>${esc(rule.rule)} (${rule.adjustment > 0 ? '+' : ''}${esc(rule.adjustment)})</strong> — ${esc(rule.reason)}</li>`).join('') || '<li>No profile-specific adjustments applied.</li>'}</ul></details></article>` : '';
      const compared = (data.comparison || []).map(renderStrategy).join('');
      result.innerHTML = `<div class="lab11-advisor-pick"><span class="eyebrow">RULE-BASED RECOMMENDATION</span><h3>${esc(data.recommended.label)}</h3><code>${esc(data.recommended.key)}</code><p>${esc(data.recommended.verdict)}</p></div><div class="lab11-comparison-grid">${compared}</div><p class="lab11-education-warning">${esc(data.educational_notice)}</p>`;
    } catch (error) {
      result.innerHTML = `<div class="empty-prompt lab11-unavailable">Recommendation unavailable: ${esc(error.message)}</div>`;
    }
  });

  byId('lab11-check-status').addEventListener('click', loadStatus);
  document.querySelectorAll('.lab11-tab-button').forEach(button => button.addEventListener('click', () => {
    document.querySelectorAll('.lab11-tab-button').forEach(item => { const active = item === button; item.classList.toggle('is-active', active); item.setAttribute('aria-selected', String(active)); });
    document.querySelectorAll('.lab11-tab-panel').forEach(panel => { panel.hidden = panel.id !== button.getAttribute('aria-controls'); });
  }));
  byId('lab11-refresh-results').addEventListener('click', () => Promise.all([loadStatus(), loadReports()]));
  byId('lab11-view-explain').addEventListener('click', () => {
    const details = document.querySelector('.lab11-raw-explain');
    details.open = true;
    details.scrollIntoView({ behavior: 'smooth', block: 'center' });
  });
  byId('lab11-run-1').addEventListener('click', () => startRun('lab11_1'));
  byId('lab11-run-2').addEventListener('click', () => startRun('lab11_2'));
  document.querySelector('.sidebar-nav [data-view="lab11"]').addEventListener('click', async () => {
    if (loaded) return;
    loaded = true;
    try {
      const access = await api('/api/chapter11/capabilities');
      byId('lab11-run-1').hidden = !access.can_run_experiments;
      byId('lab11-run-2').hidden = !access.can_run_experiments;
      if (!access.can_run_experiments) jobMessage.textContent = 'Administrator access is required to refresh measurements.';
    } catch (error) {
      jobMessage.textContent = error.message;
    }
    await Promise.all([loadStatus(), loadReports()]);
  });
});
