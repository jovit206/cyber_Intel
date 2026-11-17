(() => {
  const root = document.getElementById('labs-operations');
  if (!root) return;
  const $ = selector => root.querySelector(selector);
  const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]));
  const chapters = [
    { id: '13', title: 'Deployment & Infrastructure', tech: 'MongoDB Replica Sets · Kubernetes · Atlas', description: 'Observe the configured replica set, monitor measured database activity, and inspect the documented Kubernetes and Atlas deployment targets.', endpoint: '/api/ops/overview' },
    { id: '14', title: 'Monitoring & Backup', tech: 'Metrics · Alerts · Recovery', description: 'Review live database health, monitoring signals, backup workflows, and recovery verification.', endpoint: '/api/operations/overview' },
    { id: '15', title: 'Enterprise Management', tech: 'Organisations · Cases · Access', description: 'Understand organisation-scoped access, memberships, and case workflows.', endpoint: '/api/cases/overview' },
    { id: '16', title: 'Database Operations', tech: 'Distributed storage · Graphs', description: 'Explore distributed database operations, audit evidence, and graph-based investigations.', endpoint: '/api/chapter16/status' },
    { id: '17', title: 'AI Research & Intelligence', tech: 'MongoDB Vector Search · Embeddings', description: 'Index threat reports, ask source-grounded questions, search semantically, and run vector benchmarks.', endpoint: '/api/rag/status', project: 'rag' },
    { id: '18', title: 'Threat Intelligence', tech: 'MongoDB · Cassandra · Neo4j', description: 'Review imported threat records and investigations across configured database services.', endpoint: '/api/chapter18/status', project: 'chapter18' },
  ];
  const tabs = {
    '13': ['Overview', 'Replica Set', 'Failover', 'Monitoring', 'Kubernetes', 'MongoDB Atlas'],
    '14': ['Overview', 'Live Metrics', 'Alerts', 'Backups', 'Recovery Verification'],
    '15': ['Overview', 'Cases', 'Organisations', 'Members', 'Rate Limits'],
    '16': ['Operations', 'Databases', 'Audit Log', 'Fraud Review', 'Graph Paths'],
    '17': ['Overview', 'Ask CTI Verify', 'Document Index', 'Vector Search', 'Benchmarking', 'History'],
    '18': ['Overview', 'Database Comparison', 'Threat Intelligence', 'False-Positive Detection', 'Benchmarks', 'Threat Communities', 'Comparative Report', 'Settings'],
  };
  const exerciseLandingTabs = { '13': 0, '14': 0, '15': 1, '16': 3, '17': 1, '18': 2 };
  let operationData = null, currentChapter = null, currentTab = 0, standaloneMode = false;

  function moveWorkspace(targetId, standalone) {
    const target = document.getElementById(targetId);
    if (target && root.parentElement !== target) target.append(root);
    standaloneMode = standalone;
    root.classList.toggle('is-exercise-standalone', standalone);
  }

  async function get(path, options = {}) {
    const response = await fetch(path, { credentials: 'same-origin', headers: { Accept: 'application/json', ...(options.body ? { 'Content-Type': 'application/json' } : {}), ...(options.headers || {}) }, ...options });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) { const error = new Error(data.detail || `Request failed (${response.status})`); error.status = response.status; error.retryAfter = response.headers.get('Retry-After'); throw error; }
    return data;
  }
  function setMessage(message, error = false) {
    const box = $('[data-labs-message]');
    box.textContent = message || '';
    box.classList.toggle('is-error', Boolean(error));
    box.hidden = !message;
  }
  async function refresh() {
    setMessage('');
    const checks = await Promise.all(chapters.filter(item => item.endpoint).map(async item => {
      try { return [item.id, { data: await get(item.endpoint) }]; }
      catch (error) { return [item.id, { error: error.message }]; }
    }));
    const results = Object.fromEntries(checks);
    operationData = results['14']?.data || null;
    root.dataset.checks = JSON.stringify(Object.fromEntries(checks.map(([id, value]) => [id, value.error ? null : value.data])));
    root.dataset.checkErrors = JSON.stringify(Object.fromEntries(checks.filter(([, value]) => value.error).map(([id, value]) => [id, value.error])));
    root.dataset.checksLoaded = 'true';
    if (currentChapter && !$('[data-labs-workspace]').hidden) renderTab();
  }
  function unavailable(text) {
    return `<div class="labs-empty"><span class="labs-status neutral">Not configured in this application</span><h3>Live service integration unavailable</h3><p>${esc(text)}</p><p>A separate service deployment may provide this operation, but it is not connected to this application. No sample values or simulated status are shown here.</p></div>`;
  }
  const value = (item, unit = '') => item === null || item === undefined ? 'Not reported' : `${esc(item)}${unit}`;
  function dataTable(headers, rows, empty) {
    if (!rows?.length) return `<p class="labs-data-empty">${esc(empty)}</p>`;
    return `<div class="labs-table-wrap"><table class="labs-table"><thead><tr>${headers.map(label => `<th>${esc(label)}</th>`).join('')}</tr></thead><tbody>${rows.map(row => `<tr>${row.map(cell => `<td>${esc(cell ?? 'Not reported')}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
  }
  function hitRatioChart(history) {
    const points = (history || []).filter(point => point.hit_ratio !== null && point.hit_ratio !== undefined && point.hit_ratio !== '' && Number.isFinite(Number(point.hit_ratio))).slice(-20);
    if (!points.length) return '<p class="labs-data-empty">No measured WiredTiger cache hit ratio samples are available yet.</p>';
    const width = 640, height = 230, left = 54, right = 18, top = 16, bottom = 42;
    const chartWidth = width - left - right, chartHeight = height - top - bottom;
    const coords = points.map((point, index) => ({
      x: left + (points.length === 1 ? chartWidth / 2 : index * chartWidth / (points.length - 1)),
      y: top + (1 - Math.min(1, Math.max(0, Number(point.hit_ratio)))) * chartHeight,
      point,
    }));
    const grid = [0, .5, 1].map(value => {
      const y = top + (1 - value) * chartHeight;
      return `<line x1="${left}" y1="${y}" x2="${width - right}" y2="${y}" class="labs-chart-grid"/><text x="${left - 9}" y="${y + 4}" text-anchor="end" class="labs-chart-label">${Math.round(value * 100)}%</text>`;
    }).join('');
    const polyline = coords.map(point => `${point.x},${point.y}`).join(' ');
    const marks = coords.map(({ x, y, point }) => `<circle cx="${x}" cy="${y}" r="4"><title>${esc(point.timestamp)} · ${(Number(point.hit_ratio) * 100).toFixed(1)}%</title></circle>`).join('');
    const firstTime = esc(points[0].timestamp || 'First sample'), lastTime = esc(points.at(-1).timestamp || 'Latest sample');
    return `<figure class="labs-chart-figure"><figcaption>WiredTiger cache hit ratio · actual API samples</figcaption><svg class="labs-line-chart" viewBox="0 0 ${width} ${height}" role="img" aria-label="WiredTiger cache hit ratio from ${points.length} live API samples, plotted as percent">${grid}<polyline points="${polyline}" class="labs-chart-line"/>${marks}<text x="${left}" y="${height - 10}" class="labs-chart-label">${firstTime}</text><text x="${width - right}" y="${height - 10}" text-anchor="end" class="labs-chart-label">${lastTime}</text></svg></figure>`;
  }
  async function showApiResult(path, render) {
    const target = $('[data-labs-detail]');
    target.innerHTML = '<p class="loading-state">Loading live data…</p>';
    try { render(await get(path), target); }
    catch (error) { target.innerHTML = `<div class="labs-empty"><span class="labs-status bad">Request failed</span><p>${esc(error.message)}</p></div>`; }
  }
  function renderOrganizations(target, data) {
    const items = data.organizations || [];
    target.innerHTML = dataTable(['Organisation','Description','Created'], items.map(org => [org.name, org.description || '—', org.created_at]), 'No organisations exist. Create one to begin.') + `<select hidden data-org-cache>${items.map(org => `<option value="${esc(org.organization_id)}">${esc(org.name)}</option>`).join('')}</select>`;
    const options = `<option value="">Select organisation</option>${items.map(org => `<option value="${esc(org.organization_id)}">${esc(org.name)}</option>`).join('')}`;
    root.querySelectorAll('[data-org-options]').forEach(select => { const previous = select.value; select.innerHTML = options; if (items.some(org => org.organization_id === previous)) select.value = previous; });
    root.dataset.organizations = JSON.stringify(items);
  }
  async function loadCaseWorkspace(tab = currentTab) {
    const getOrganizations = async () => {
      const data = await get('/api/cases/organizations');
      const target = $('[data-case-organizations-list]') || $('[data-case-list]');
      if (target) renderOrganizations(target, data);
      return data.organizations || [];
    };
    try {
      if (tab === 0 || tab === 4) {
        const data = await get('/api/cases/overview');
        root.dataset.caseOverview = JSON.stringify(data);
        const box = $('[data-case-rate-summary]');
        if (box) box.innerHTML = `<div class="labs-metric-row"><article><span>Refused requests</span><strong>${value(data.rate_limit_refusals)}</strong></article></div>`;
        if (tab === 0) $('[data-labs-content]').innerHTML = overview(chapters.find(row => row.id === '15'));
      }
      if (tab === 2) {
        const data = await get('/api/cases/overview');
        root.dataset.caseOverview = JSON.stringify(data);
      }
      if (tab === 1) {
        const organizations = await getOrganizations();
        const result = await get('/api/cases');
        const target = $('[data-case-list]');
        if (!organizations.length && !result.cases?.length) { target.innerHTML = '<p class="labs-data-empty">Ask an administrator to add you to an organisation.</p>'; return; }
        target.innerHTML = `<div class="labs-toolbar"><label>Filter status<select data-case-filter><option value="">All statuses</option><option>open</option><option>in_progress</option><option>closed</option></select></label><button class="xs-btn secondary" type="button" data-case-reload>Apply filter</button></div>${caseTable(result.cases || [])}`;
      }
      if (tab === 2) {
        await getOrganizations();
        const stats = JSON.parse(root.dataset.caseOverview || '{}').organizations || [];
        const target = $('[data-case-organizations-list]');
        target.innerHTML = dataTable(['Organisation','Members','Cases','Open','In progress','Closed'], stats.map(row => [row.name, row.members, row.cases, row.case_status_counts?.open, row.case_status_counts?.in_progress, row.case_status_counts?.closed]), 'No organisation records.');
      }
      if (tab === 3) await getOrganizations();
    } catch (error) {
      const target = $('[data-case-list]') || $('[data-case-organizations-list]') || $('[data-case-members-list]');
      if (target) target.innerHTML = `<p class="labs-message is-error">${esc(rateMessage(error))}</p>`;
    }
  }
  function rateMessage(error) {
    return error.status === 429 ? `Rate limit reached. Retry in ${error.retryAfter || 'the interval'} seconds.` : error.message;
  }
  function caseTable(items) {
    if (!items.length) return '<p class="labs-data-empty">No cases match this filter.</p>';
    return `<div class="labs-table-wrap"><table class="labs-table"><thead><tr><th>Title</th><th>Status</th><th>Assigned to</th><th>Linked threats</th><th>Updated</th><th></th></tr></thead><tbody>${items.map(row => `<tr><td>${esc(row.title)}</td><td>${esc(row.status)}</td><td>${esc(row.assigned_to || 'Unassigned')}</td><td>${value(row.linked_threat_count)}</td><td>${esc(row.updated_at || 'Not reported')}</td><td><button class="xs-btn secondary" type="button" data-case-detail="${esc(row.case_id)}">Details</button></td></tr>`).join('')}</tbody></table></div>`;
  }
  function replicaSet(data) {
    const members = data?.members || [];
    return `<div class="labs-explainer"><p>Live app replica set · ${esc(data?.set_name || 'name not reported')} · checked ${esc(data?.timestamp || 'timestamp not reported')}</p><p>Primary: <strong>${esc(data?.primary?.name || 'No primary reported')}</strong> · members: <strong>${value(data?.member_count)}</strong> · health: <strong>${data?.healthy === true ? 'Healthy' : data?.healthy === false ? 'Degraded' : 'Not reported'}</strong></p>${dataTable(['Member','State','Health','Heartbeat ping','Sync source'], members.map(member => [member.name, member.state_str, member.health === 1 ? 'Healthy' : member.health === 0 ? 'Unhealthy' : 'Not reported', value(member.ping_ms, ' ms'), member.sync_source_host]), 'The API returned no replica set members.')}</div>`;
  }
  function overview(chapter) {
    const data = JSON.parse(root.dataset.checks || '{}')[chapter.id];
    if (chapter.id === '17') return `<div class="labs-explainer"><h3>What this module does</h3><p>Retrieves threat report passages for a question and exposes document indexing and vector search tools.</p><p><strong>Document chunks:</strong> ${data?.documents?.embedded_chunks ?? 'Not reported'} · <strong>Vector index:</strong> ${data?.vector_search?.ready === true ? 'Ready' : data?.vector_search?.ready === false ? 'Not ready' : 'Not reported'}</p><p><strong>Embedding model:</strong> ${esc(data?.configuration?.embedding_model || 'Unavailable')} · <strong>Answer model:</strong> ${esc(data?.configuration?.llm_model || 'Unavailable')}</p><p><a href="#" data-labs-project="rag">Open the interactive RAG workspace</a>. Answers identify their retrieval or generation mode and show passages returned by the backend.</p></div>`;
    if (chapter.id === '18') {
      const dbs = data?.databases || {};
      return `<div class="labs-explainer"><h3>What this module does</h3><p>Stores threat records in MongoDB and checks optional Cassandra and Neo4j integrations for cross-database investigations and measured query benchmarks.</p><div class="labs-db-grid">${Object.entries(dbs).map(([name, status]) => `<article><strong>${esc(name)}</strong><span class="labs-status ${String(status.status).toLowerCase() === 'connected' || String(status.status).toLowerCase() === 'available' ? 'good' : 'neutral'}">${esc(status.status || 'Not reported')}</span>${status.latency_ms !== undefined ? `<small>${esc(status.latency_ms)} ms response</small>` : ''}</article>`).join('') || '<p>No database health details returned.</p>'}</div><button type="button" class="xs-btn" data-labs-project="chapter18">Open Threat Intelligence</button></div>`;
    }
    if (chapter.id === '14') return `<div class="labs-explainer"><h3>What this module does</h3><p>Tracks real database and application operations instrumented by this deployment.</p>${operationData ? `<p>Overall health: ${esc(operationData.summary?.overall_health || 'Not reported')} · retained operations: ${value(operationData.summary?.total_operations)} · failed: ${value(operationData.summary?.failed_operations)} · warnings: ${value(operationData.summary?.warning_operations)}.</p><p>${esc(operationData.tracking?.historical_note || '')}</p>` : unavailable('The authenticated operations API did not return data.')}</div>`;
    if (chapter.id === '13') return `<div class="labs-explainer"><h3>What this module does</h3><p>Container and cloud deployment concepts: replica sets, failover, monitoring, Kubernetes, and Atlas.</p>${data ? replicaSet(data) : unavailable('The authenticated replica set API did not return live status.')}<p class="labs-data-note">This is the MongoDB replica set used by this application. Kubernetes and Atlas are separate deployments and are not inferred from MongoDB connectivity.</p></div>`;
    if (chapter.id === '15') return `<div class="labs-explainer"><h3>What this module does</h3><p>Manages cases under organisation memberships. Case details are scoped by membership; the organisation summary returns counts and organisation metadata only.</p>${data ? `<div class="labs-metric-row"><article><span>Organisations</span><strong>${value(data.organization_count)}</strong></article><article><span>Members</span><strong>${value(data.member_count)}</strong></article><article><span>Cases</span><strong>${value(data.case_count)}</strong></article><article><span>Rate limit refusals</span><strong>${value(data.rate_limit_refusals)}</strong></article></div><p>${esc(data.privacy_note || '')}</p>` : unavailable('Organisation analytics are not available.')}</div>`;
    if (chapter.id === '16') {
      const databases = data?.databases || {};
      const labels = { connected: 'good', unavailable: 'bad', not_configured: 'neutral' };
      return `<div class="labs-explainer"><h3>Database health</h3><p>Read-only checks use the separately configured databases. Credentials are never returned to the browser.</p><div class="labs-db-grid">${Object.entries(databases).map(([name, result]) => `<article><strong>${esc(name)}</strong><span class="labs-status ${labels[result.status] || 'neutral'}">${esc(result.status || 'Not reported')}</span>${result.reason ? `<small>${esc(result.reason)}</small>` : ''}${result.error_type ? `<small>${esc(result.error_type)}</small>` : ''}${result.latency_ms !== undefined ? `<small>${esc(result.latency_ms)} ms</small>` : ''}</article>`).join('') || '<p>Health checks are unavailable.</p>'}</div><p>Circuit breakers: ${Object.entries(data?.circuit_breakers || {}).map(([name, state]) => `${esc(name)} ${esc(state)}`).join(' · ') || 'Not reported'}.</p></div>`;
    }
    if (chapter.id === '15') return `<div class="labs-explainer"><h3>What this module does</h3><p>Organisation-scoped case workflows and membership permissions.</p>${unavailable('No integrated cases or organisations API is registered in this application. Use the separately deployed tenant service if it is available.')}</div>`;
    return `<div class="labs-explainer"><h3>What this module does</h3><p>Combines Cassandra distributed storage concepts with Neo4j graph investigation for audit and fraud review.</p>${unavailable('No service-management, audit, or fraud endpoints are registered for this deployment. Status from another integration does not imply these services are running.')}</div>`;
  }

  function c13Envelope() { return JSON.parse(root.dataset.checks || '{}')['13']; }
  function c13Errors() { return JSON.parse(root.dataset.checkErrors || '{}'); }
  function c13ReplicaSet() { return c13Envelope()?.replica_set || null; }
  function c13State(data) {
    if (!data) return ['WAITING FOR TELEMETRY', 'unknown'];
    return data.healthy === true ? ['HEALTHY', 'healthy'] : ['DEGRADED', 'degraded'];
  }
  function c13Bytes(input) {
    const number = Number(input);
    if (!Number.isFinite(number)) return 'Not reported';
    if (number < 1024) return `${number.toLocaleString()} B`;
    const units = ['KiB', 'MiB', 'GiB', 'TiB'];
    let value = number, index = -1;
    do { value /= 1024; index += 1; } while (value >= 1024 && index < units.length - 1);
    return `${value.toFixed(1)} ${units[index]}`;
  }
  function c13Uptime(input) {
    const seconds = Number(input);
    if (!Number.isFinite(seconds) || seconds < 0) return 'Not reported';
    const days = Math.floor(seconds / 86400), hours = Math.floor((seconds % 86400) / 3600), minutes = Math.floor((seconds % 3600) / 60);
    return days ? `${days}d ${hours}h` : hours ? `${hours}h ${minutes}m` : `${minutes}m`;
  }
  function c13Node(member) {
    const state = String(member.state || 'UNKNOWN').toUpperCase();
    const tone = state === 'PRIMARY' ? 'primary' : state === 'SECONDARY' ? 'secondary' : 'unknown';
    const health = member.health === 1 ? 'Responding' : member.health === 0 ? 'Unhealthy' : 'Not reported';
    return `<article class="c13-node c13-node-${tone}"><div class="c13-node-top"><span class="c13-node-dot" aria-hidden="true"></span><span class="c13-node-role">${esc(state)}</span><span class="c13-node-health">${esc(health)}</span></div><h3>${esc(member.name || 'Member name not reported')}</h3><div class="c13-node-metrics"><span>Heartbeat<strong>${value(member.ping_ms, ' ms')}</strong></span><span>Uptime<strong>${esc(c13Uptime(member.uptime_seconds))}</strong></span></div><small>${member.sync_source ? `Sync source · ${esc(member.sync_source)}` : state === 'PRIMARY' ? 'Current elected primary' : 'Sync source not reported'}</small></article>`;
  }
  function c13Topology(data) {
    if (!data?.members?.length) {
      const error = c13Errors()['13'];
      return `<div class="c13-empty"><span class="c13-overline">LIVE REPLICA SET</span><h3>${root.dataset.checksLoaded === 'true' ? 'Topology unavailable' : 'Connecting to the configured replica set'}</h3><p>${esc(error || 'No member status has been returned yet. The workspace does not substitute sample nodes for live infrastructure.')}</p></div>`;
    }
    return `<div class="c13-topology-stage"><div class="c13-topology-core"><span class="c13-core-pulse"></span><div><small>REPLICA SET</small><strong>${esc(data.set_name || 'Name not reported')}</strong><span>${esc(data.member_count ?? data.members.length)} reported member${data.member_count === 1 ? '' : 's'}</span></div><b>TERM ${esc(data.term ?? '—')}</b></div><div class="c13-topology-links" aria-hidden="true"><i></i><i></i><i></i></div><div class="c13-node-grid">${data.members.map(c13Node).join('')}</div></div>`;
  }
  function c13Metric(label, metric, note = '') {
    return `<article class="c13-metric"><span>${esc(label)}</span><strong>${esc(metric)}</strong>${note ? `<small>${esc(note)}</small>` : ''}</article>`;
  }
  function c13ElectionTimeline(events) {
    if (!events?.length) return '<div class="c13-timeline-empty">No election changes were recorded in this time window.</div>';
    return `<ol class="c13-timeline">${events.slice(-8).reverse().map(event => `<li><span class="c13-timeline-mark" aria-hidden="true"></span><time>${esc(event.timestamp || 'Time not reported')}</time><strong>Primary election observed</strong><p>${esc(event.previous_primary || 'Previous primary not reported')} <span aria-hidden="true">→</span> ${esc(event.new_primary || 'New primary not reported')}</p></li>`).join('')}</ol>`;
  }
  function c13Overview() {
    const envelope = c13Envelope();
    const data = c13ReplicaSet();
    const [label, tone] = c13State(data);
    const members = data?.members || [];
    const healthy = members.filter(member => member.health === 1).length;
    const secondaryLag = data?.replication_lag_ms || {};
    const maxLag = Object.values(secondaryLag).length ? Math.max(...Object.values(secondaryLag).map(Number)) : null;
    const prom = envelope?.prometheus || {}, grafana = envelope?.grafana || {}, kube = envelope?.kubernetes || {}, atlas = envelope?.atlas || {};
    const deployment = c13Topology(data);
    const statCards = [
      c13Metric('ELECTED PRIMARY', data?.primary || 'Not reported', data?.set_name ? `Replica set ${data.set_name}` : 'Live status pending'),
      c13Metric('HEALTHY MEMBERS', data ? `${healthy} / ${members.length}` : 'Not reported', data ? `${members.filter(member => member.state === 'SECONDARY').length} secondary members` : 'No synthetic topology shown'),
      c13Metric('MAX REPLICATION LAG', maxLag === null ? 'Not sampled' : `${maxLag.toLocaleString()} ms`, 'Measured against the primary optime'),
      c13Metric('OPS / SECOND', data?.operations_per_second ?? 'Sampling', 'Derived from serverStatus opcounters'),
    ].join('');
    return `<div class="c13-workspace"><section class="c13-status-band"><div><span class="c13-overline">LIVE MONGODB TELEMETRY · APPLICATION CONFIGURED CLUSTER</span><p>${esc(envelope?.note || 'Replica-set state is collected from MongoDB server commands. Container and external-cluster state is not inferred.')}</p></div><span class="c13-health-pill ${tone}"><i></i>${label}</span></section><div class="c13-metric-grid">${statCards}</div><div class="c13-overview-grid"><section class="c13-panel c13-topology-panel"><header class="c13-panel-head"><div><span class="c13-overline">01 / TOPOLOGY</span><h3>Replica-set members</h3></div><span class="c13-source-chip">${data ? 'LIVE SAMPLE' : 'WAITING'}</span></header>${deployment}</section><aside class="c13-panel c13-side-panel"><span class="c13-overline">02 / INTEGRATIONS</span><h3>Infrastructure services</h3>${c13IntegrationRow('Prometheus', prom)}${c13IntegrationRow('Grafana', grafana)}${c13IntegrationRow('Kubernetes', {status: kube.status || 'not_configured', url: null})}${c13IntegrationRow('Atlas M0', {status: atlas.status || 'not_configured', url: null})}<p class="c13-disclosure">Prometheus/Grafana are probed only when configured. Kubernetes pod state is queried from its selected context. Atlas shows configuration presence only; Terraform state is not queried.</p></aside></div><section class="c13-panel c13-elections-panel"><header class="c13-panel-head"><div><span class="c13-overline">03 / ELECTION HISTORY</span><h3>Observed primary changes</h3></div><span class="c13-source-chip">LAST MINUTE</span></header>${c13ElectionTimeline(envelope?.elections || [])}</section></div>`;
  }
  function c13IntegrationRow(name, service) {
    const state = String(service.status || 'not_configured').replaceAll('_', ' ');
    return `<div class="c13-integration-row"><span class="c13-integration-dot ${esc(service.status || 'not_configured')}"></span><div><strong>${esc(name)}</strong><small>${esc(state)}</small></div>${service.url ? `<a href="${esc(service.url)}" target="_blank" rel="noopener noreferrer" aria-label="Open ${esc(name)}">Open ↗</a>` : ''}</div>`;
  }
  function c13ReplicaSetPage() {
    const data = c13ReplicaSet();
    const [label, tone] = c13State(data);
    const rows = data?.members || [];
    return `<div class="c13-workspace"><section class="c13-panel"><header class="c13-panel-head"><div><span class="c13-overline">REPLICA SET / MEMBER HEALTH</span><h3>Live cluster topology</h3></div><span class="c13-health-pill ${tone}"><i></i>${label}</span></header>${c13Topology(data)}${rows.length ? `<div class="c13-table-wrap"><table class="c13-table"><thead><tr><th>Member</th><th>State</th><th>Health</th><th>Heartbeat</th><th>Replication lag</th><th>Uptime</th><th>Sync source</th></tr></thead><tbody>${rows.map(member => `<tr><td>${esc(member.name || 'Not reported')}</td><td><span class="c13-state-tag ${String(member.state).toLowerCase()}">${esc(member.state || 'Unknown')}</span></td><td>${member.health === 1 ? 'Healthy' : member.health === 0 ? 'Unhealthy' : 'Not reported'}</td><td>${value(member.ping_ms, ' ms')}</td><td>${member.state === 'SECONDARY' && data.replication_lag_ms?.[member.name] !== undefined ? `${Number(data.replication_lag_ms[member.name]).toLocaleString()} ms` : member.state === 'PRIMARY' ? 'Primary' : 'Not reported'}</td><td>${esc(c13Uptime(member.uptime_seconds))}</td><td>${esc(member.sync_source || 'Not reported')}</td></tr>`).join('')}</tbody></table></div>` : ''}<p class="c13-disclosure">Member names, states, health, uptime, and ping values come from the protected deployment telemetry API.</p></section></div>`;
  }
  function c13FailoverPage() {
    const data = c13ReplicaSet();
    const primary = data?.primary || 'No primary reported';
    return `<div class="c13-workspace c13-failover-layout"><section class="c13-panel c13-failover-card"><span class="c13-overline">CONTROLLED OPERATION / REQUIRES ADMIN CONFIRMATION</span><h3>Test primary failover</h3><p>This sends a stepdown request to the MongoDB replica set configured for this application. A brief interruption to database operations is expected. The server verifies a different PRIMARY before reporting success.</p><div class="c13-current-primary"><span>Current primary</span><strong>${esc(primary)}</strong></div><label class="c13-duration-field">Stepdown duration (seconds)<input type="number" min="5" max="120" value="60" data-labs-seconds><small>Choose a value from 5 to 120 seconds.</small></label><button type="button" class="c13-danger-button" data-labs-stepdown>Confirm and test failover</button><div class="c13-operation-result" data-labs-detail aria-live="polite"><span class="c13-overline">OPERATION RESULT</span><p>No failover request has been made in this session.</p></div></section><aside class="c13-panel c13-procedure-panel"><span class="c13-overline">EXPECTED SEQUENCE</span><h3>Election verification</h3><ol class="c13-procedure"><li><b>01</b><span>Record current primary and replica-set term.</span></li><li><b>02</b><span>Request a non-forced stepdown with explicit confirmation.</span></li><li><b>03</b><span>Wait for the replica set to elect another primary.</span></li><li><b>04</b><span>Record the observed transition as an election marker.</span></li></ol><p class="c13-disclosure">No failover is simulated. If a new primary is not observed, the API returns an error and the current live status should be checked before another attempt.</p></aside></div>`;
  }
  function c13LineChart(points, selector, label, color, suffix = '') {
    const valid = points.map(point => ({time: point.timestamp, value: selector(point)})).filter(point => point.value !== null && point.value !== undefined && point.value !== '' && Number.isFinite(Number(point.value)));
    if (!valid.length) return `<div class="c13-chart-empty">No measured ${esc(label.toLowerCase())} samples are available for this time window.</div>`;
    const width=720,height=230,left=58,right=18,top=18,bottom=38,plotWidth=width-left-right,plotHeight=height-top-bottom;
    const vals=valid.map(point=>Number(point.value)), min=Math.min(0,...vals), max=Math.max(...vals), span=max-min || 1;
    const coords=valid.map((point,index)=>({x:left+(valid.length===1?plotWidth/2:index*plotWidth/(valid.length-1)),y:top+(1-(Number(point.value)-min)/span)*plotHeight,point}));
    const line=coords.map(point=>`${point.x},${point.y}`).join(' ');
    const grid=[0,.5,1].map(ratio=>{const y=top+ratio*plotHeight;const v=max-(max-min)*ratio;return `<line x1="${left}" y1="${y}" x2="${width-right}" y2="${y}" class="c13-svg-gridline"/><text x="${left-8}" y="${y+4}" text-anchor="end" class="c13-chart-label">${v.toFixed(max>10?0:2)}${esc(suffix)}</text>`}).join('');
    const first=esc(valid[0].time || 'First sample'),last=esc(valid.at(-1).time || 'Latest sample');
    return `<figure class="c13-chart"><figcaption>${esc(label)} <span>${valid.length} actual samples</span></figcaption><svg viewBox="0 0 ${width} ${height}" role="img" aria-label="${esc(label)} from ${valid.length} actual telemetry samples">${grid}<polyline points="${line}" fill="none" stroke="${color}" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/>${coords.map(({x,y,point})=>`<circle cx="${x}" cy="${y}" r="3.5" fill="${color}"><title>${esc(point.time)} · ${Number(point.value).toFixed(2)}${esc(suffix)}</title></circle>`).join('')}<text x="${left}" y="${height-10}" class="c13-chart-label">${first}</text><text x="${width-right}" y="${height-10}" text-anchor="end" class="c13-chart-label">${last}</text></svg></figure>`;
  }
  function c13MonitoringPage() {
    return `<div class="c13-workspace"><section class="c13-panel"><header class="c13-panel-head"><div><span class="c13-overline">OBSERVABILITY / REAL SERVER STATUS</span><h3>MongoDB performance telemetry</h3><p>Charts use stored serverStatus samples; unsupported or unsampled values remain blank.</p></div><div class="c13-monitor-controls"><select aria-label="Metrics time window" data-c13-window><option value="5">5 minutes</option><option value="15">15 minutes</option><option value="30" selected>30 minutes</option></select><button type="button" class="xs-btn secondary" data-labs-metrics>Load metrics</button></div></header><div class="c13-monitor-metrics" data-c13-monitor-metrics><div class="c13-chart-empty">Load the requested time window to view measured metrics.</div></div><div class="c13-chart-grid" data-c13-charts></div><section class="c13-elections-panel"><h4>Election markers</h4><div data-c13-elections><div class="c13-timeline-empty">Election history loads with the metrics window.</div></div></section><p class="c13-disclosure">Sampling runs every 10 seconds while the API is running. Historical points are retained for up to 30 days and the endpoint returns at most the requested 30-minute window.</p></section></div>`;
  }
  function renderChapter13Metrics(data) {
    const metricBox = $('[data-c13-monitor-metrics]');
    const chartsBox = $('[data-c13-charts]');
    const electionsBox = $('[data-c13-elections]');
    if (!metricBox || !chartsBox || !electionsBox) return;
    const history = data.history || [];
    const latest = data.latest;
    if (!data.available || !latest) {
      metricBox.innerHTML = `<div class="c13-empty"><h3>No stored telemetry samples</h3><p>The sampler has not produced data for this window. No chart values are invented.</p></div>`;
      chartsBox.innerHTML = '';
      electionsBox.innerHTML = c13ElectionTimeline(data.elections || []);
      return;
    }
    const cache = latest.cache || {}, connections = latest.connections || {};
    metricBox.innerHTML = [
      c13Metric('OPERATIONS / SECOND', latest.operations_per_second ?? 'Not sampled', 'Counter delta over the sampler interval'),
      c13Metric('CONNECTIONS', connections.current ?? 'Not reported', 'Current serverStatus connections'),
      c13Metric('CACHE RESIDENT', c13Bytes(cache.bytes_in_cache), `Capacity ${c13Bytes(cache.max_bytes)}`),
      c13Metric('DIRTY CACHE', c13Bytes(cache.dirty_bytes), 'Current WiredTiger dirty bytes'),
      c13Metric('PAGES READ', cache.pages_read ?? 'Not reported', 'Cumulative serverStatus counter'),
      c13Metric('PAGES EVICTED', cache.pages_evicted ?? 'Not reported', 'Cumulative serverStatus counter'),
    ].join('');
    const lag = point => {
      const values = Object.values(point.replication_lag_ms || {}).map(Number).filter(Number.isFinite);
      return values.length ? Math.max(...values) : null;
    };
    chartsBox.innerHTML = c13LineChart(history, point => {
      const hit = point.cache?.hit_ratio_interval;
      return hit === null || hit === undefined ? null : Number(hit) * 100;
    }, 'WiredTiger cache hit ratio', '#55c2a1', '%')
      + c13LineChart(history, point => point.operations_per_second, 'Operations per second', '#e28a68', ' ops/s')
      + c13LineChart(history, lag, 'Maximum secondary replication lag', '#6f9fe8', ' ms');
    electionsBox.innerHTML = c13ElectionTimeline(data.elections || []);
  }
  async function loadChapter13Metrics() {
    const metricBox = $('[data-c13-monitor-metrics]');
    if (!metricBox) return;
    metricBox.innerHTML = '<div class="c13-chart-empty" role="status">Loading measured MongoDB telemetry…</div>';
    const range = Number($('[data-c13-window]')?.value || 30);
    try {
      const data = await get(`/api/ops/metrics/deployment?minutes=${encodeURIComponent(range)}`);
      renderChapter13Metrics(data);
    } catch (error) {
      metricBox.innerHTML = `<div class="c13-empty c13-error-state"><h3>Metrics unavailable</h3><p>${esc(error.message)}</p></div>`;
    }
  }
  function c13KubernetesPage() {
    const status = c13Envelope()?.kubernetes || {};
    const pods = status.pods || [];
    const live = pods.length ? `<div class="c13-table-wrap"><table class="c13-table"><thead><tr><th>Pod</th><th>Phase</th><th>Ready</th><th>Restarts</th><th>Node</th></tr></thead><tbody>${pods.map(pod=>`<tr><td>${esc(pod.name)}</td><td>${esc(pod.phase)}</td><td>${pod.ready ? 'Ready' : 'Not ready'}</td><td>${Number(pod.restarts || 0).toLocaleString()}</td><td>${esc(pod.node || 'Not scheduled')}</td></tr>`).join('')}</tbody></table></div>` : `<div class="c13-empty"><span class="c13-overline">KUBERNETES API</span><h3>${esc(String(status.status || 'not_configured').replaceAll('_',' '))}</h3><p>${esc(status.reason || 'No live Kubernetes pod data was returned.')}</p></div>`;
    const job=status.deployment_job;
    return `<div class="c13-workspace"><div class="c13-kube-grid"><section class="c13-panel"><header class="c13-panel-head"><div><span class="c13-overline">LIVE STATUS · NAMESPACE MONGO-HA</span><h3>MongoDB StatefulSet</h3></div><button type="button" class="xs-btn secondary" data-labs-refresh>Refresh pod status</button></header>${live}${job?`<p class="c13-job-state">Latest requested deployment: <strong>${esc(job.status)}</strong> · ${esc(job.message || '')}</p>`:''}<div class="c13-start-lab"><div><strong>Deploy Kubernetes resources</strong><p>Applies the existing namespace, Secrets, headless Service, three-replica StatefulSet, and initialization Job. Requires a configured kubectl context and local deployment credentials.</p></div><button type="button" class="c13-action-button" data-c13-kubernetes-start>Start deployment</button></div><div class="c13-operation-result" data-c13-kubernetes-result aria-live="polite"></div></section><aside class="c13-panel c13-blueprint-panel"><span class="c13-overline">DOCUMENTED DESIGN</span><h3>Stateful replica-set architecture</h3><div class="c13-kube-flow"><div>MongoDB StatefulSet<strong>3 stable pods</strong></div><b aria-hidden="true">↔</b><div>Headless Service<strong>Stable member DNS</strong></div><b aria-hidden="true">↔</b><div>MongoDB rs0<strong>1 primary · 2 secondaries</strong></div></div><div class="c13-pvc-stack"><span>mongo-data-mongo-0 · 10 GiB</span><span>mongo-data-mongo-1 · 10 GiB</span><span>mongo-data-mongo-2 · 10 GiB</span></div><p class="c13-disclosure">The architecture shown is transcribed from the checked-in manifests. Pod state above is shown only when kubectl returns live data.</p></aside></div></div>`;
  }
  function c13AtlasPage() {
    const atlas=c13Envelope()?.atlas || {};
    return `<div class="c13-workspace"><section class="c13-panel c13-atlas-panel"><span class="c13-overline">OPTIONAL CLOUD TARGET · TERRAFORM</span><h3>MongoDB Atlas M0</h3><p>The Terraform configuration provisions an Atlas account, an M0 replica-set cluster, a database-scoped <code>readWrite</code> account, and a restricted client CIDR allowlist.</p><div class="c13-atlas-flow"><div><small>01 / ACCOUNT</small><strong>Aegis Infrastructure</strong></div><i aria-hidden="true">→</i><div><small>02 / CLUSTER</small><strong>aegis-cluster / M0</strong></div><i aria-hidden="true">→</i><div><small>03 / ACCESS</small><strong>application / readWrite</strong></div></div><div class="c13-atlas-state"><span class="c13-integration-dot ${atlas.configured?'healthy':'not_configured'}"></span><div><strong>Configuration: ${esc(atlas.status || 'not configured')}</strong><p>${esc(atlas.detail || 'Atlas API state is not queried by this application.')}</p></div></div><p class="c13-danger-note">This workspace does not apply or destroy Terraform plans. Review the plan and credentials on the deployment host before provisioning cloud resources.</p><p class="c13-disclosure">No Atlas cluster health is implied by the local replica-set status. No credentials or Terraform state are read by the browser.</p></section></div>`;
  }
  function c13Page(tab) {
    if (tab === 0) return c13Overview();
    if (tab === 1) return c13ReplicaSetPage();
    if (tab === 2) return c13FailoverPage();
    if (tab === 3) return c13MonitoringPage();
    if (tab === 4) return c13KubernetesPage();
    return c13AtlasPage();
  }

  function c14Envelope() { return JSON.parse(root.dataset.checks || '{}')['14'] || null; }
  function c14Errors() { return JSON.parse(root.dataset.checkErrors || '{}'); }
  function c14Number(input, digits = 1) { const n = Number(input); return Number.isFinite(n) ? n.toFixed(digits) : 'Not sampled'; }
  function c14Bytes(input) {
    const n = Number(input);
    if (!Number.isFinite(n)) return 'Not reported';
    if (n < 1024) return `${n.toLocaleString()} B`;
    const unit = ['KiB', 'MiB', 'GiB', 'TiB'];
    let value = n, i = -1;
    do { value /= 1024; i++; } while (value >= 1024 && i < unit.length - 1);
    return `${value.toFixed(1)} ${unit[i]}`;
  }
  function c14MaxLag(sample) {
    const values = (sample?.replication_lags || []).map(row => Number(row.lag_seconds)).filter(Number.isFinite);
    return values.length ? Math.max(...values) : null;
  }
  function c14DatabaseStatus(sample) {
    if (!sample) return ['Awaiting samples', 'unknown'];
    if (sample.replica_set_applicable === false) return ['Standalone MongoDB', 'neutral'];
    const members = sample.members || [];
    return sample.primary && members.length && members.every(row => row.health === 1)
      ? ['Healthy replica set', 'healthy'] : ['Degraded / no primary', 'degraded'];
  }
  function c14Stat(title, valueText, detail, tone = '') {
    return `<article class="c14-stat ${tone}"><span>${esc(title)}</span><strong>${esc(valueText)}</strong><small>${esc(detail)}</small></article>`;
  }
  function c14Chart(history, title, readValue, color, unit) {
    const points = (history || []).map(row => ({ time: row.timestamp, value: readValue(row) }))
      .filter(point => Number.isFinite(point.value));
    if (!points.length) return `<section class="c14-chart"><header><span>${esc(title)}</span><small>No measured points in this window</small></header><div class="c14-no-data">Waiting for backend samples. No values are estimated.</div></section>`;
    const width = 640, height = 210, left = 56, right = 16, top = 18, bottom = 35;
    const vals = points.map(point => point.value);
    let min = Math.min(...vals), max = Math.max(...vals);
    if (min === max) { const padding = Math.max(Math.abs(max) * .08, 1); min -= padding; max += padding; }
    const usableW = width - left - right, usableH = height - top - bottom;
    const coords = points.map((point, index) => ({
      x: left + (points.length === 1 ? usableW / 2 : index * usableW / (points.length - 1)),
      y: top + ((max - point.value) / (max - min)) * usableH,
      ...point,
    }));
    const grid = [0, .5, 1].map(r => {
      const y = top + r * usableH, val = max - r * (max - min);
      return `<line x1="${left}" y1="${y}" x2="${width-right}" y2="${y}" class="c14-gridline"/><text x="${left-7}" y="${y+4}" text-anchor="end">${val.toFixed(max > 20 ? 0 : 1)}${esc(unit)}</text>`;
    }).join('');
    const line = coords.map(p => `${p.x},${p.y}`).join(' ');
    const marks = coords.map(p => `<circle cx="${p.x}" cy="${p.y}" r="3.5"><title>${esc(p.time)} · ${p.value.toFixed(2)}${esc(unit)}</title></circle>`).join('');
    return `<section class="c14-chart"><header><span>${esc(title)}</span><small>${points.length} real sample${points.length === 1 ? '' : 's'}</small></header><svg viewBox="0 0 ${width} ${height}" role="img" aria-label="${esc(title)} from ${points.length} actual backend samples">${grid}<polyline points="${line}" stroke="${color}"/>${marks}<text x="${left}" y="${height-9}">${esc(points[0].time || 'First sample')}</text><text x="${width-right}" y="${height-9}" text-anchor="end">${esc(points.at(-1).time || 'Latest sample')}</text></svg></section>`;
  }
  function c14Overview() {
    const data = c14Envelope(), sample = data?.latest, error = c14Errors()['14'];
    const [health, tone] = c14DatabaseStatus(sample);
    const cache = sample?.cache_hit_ratio_percent;
    const lag = c14MaxLag(sample);
    const backup = data?.latest_backup, verification = data?.latest_verification;
    const healthMembers = sample?.members || [];
    const alerts = data?.alert_count;
    return `<div class="c14-workspace"><div class="c14-hero"><div><span class="c14-kicker">OPERATIONS CENTER / RESILIENCE</span><h3>Observability &amp; Recovery</h3><p>Live MongoDB signals, acknowledged alerts, and verified backup operations.</p><small>Source: application-configured MongoDB · ${esc(sample?.timestamp || 'No sample yet')}</small></div><div class="c14-health ${tone}"><i></i>${esc(health)}</div></div>${error ? `<div class="c14-error"><strong>Monitoring unavailable</strong><p>${esc(error)}</p></div>` : ''}<div class="c14-stat-grid">${c14Stat('DATABASE HEALTH', health, sample?.replica_set_applicable === false ? 'Standalone connection' : `${healthMembers.filter(m=>m.health===1).length} / ${healthMembers.length} members healthy`, tone)}${c14Stat('CACHE HIT RATIO', cache == null ? 'Not sampled' : `${c14Number(cache)}%`, 'Alert threshold below 80%', cache != null && cache < 80 ? 'warning' : '')}${c14Stat('REPLICATION LAG', lag == null ? 'Not sampled' : `${c14Number(lag)} s`, 'Alert threshold above 10 seconds', lag != null && lag > 10 ? 'warning' : '')}${c14Stat('OPEN ALERTS', alerts == null ? 'Not reported' : alerts, 'Active and acknowledged alerts')}${c14Stat('LATEST BACKUP', backup?.status === 'success' ? 'Verified dump' : backup?.status || 'No successful backup', backup?.completed_at || 'No backup recorded')}${c14Stat('RESTORE RTO', verification?.restore_duration_seconds == null ? 'Not measured' : `${c14Number(verification.restore_duration_seconds,2)} s`, verification?.verification_status || 'No restore verification')}${c14Stat('BACKUP WORKER', data?.worker?.status || 'Not configured', data?.worker?.last_seen_at || 'No heartbeat')}</div><div class="c14-chart-grid">${c14Chart(data?.history,'Cache hit ratio',row=>row.cache_hit_ratio_percent,'#137e78','%')}${c14Chart(data?.history,'Maximum replication lag',row=>c14MaxLag(row),'#bf674c',' s')}${c14Chart(data?.history,'Operations per second',row=>row.operations_per_second,'#527cb0',' ops/s')}</div><div class="c14-note">History is sampled by the backend every 30 seconds. Blank metrics mean MongoDB did not report the value.</div></div>`;
  }
  function c14MetricsPage() {
    return `<div class="c14-workspace"><section class="c14-panel"><header class="c14-section-head"><div><span class="c14-kicker">MEASURED TELEMETRY</span><h3>MongoDB performance signals</h3><p>Charts render only persisted serverStatus and replSetGetStatus samples.</p></div><label class="c14-time-filter">Time window<select data-c14-window><option value="30">30 minutes</option><option value="120">2 hours</option><option value="720">12 hours</option></select></label></header><div class="c14-metrics-result" data-c14-metrics-result><p class="c14-no-data">Loading monitoring history…</p></div></section></div>`;
  }
  function c14RenderMetrics(data) {
    const target = $('[data-c14-metrics-result]');
    if (!target) return;
    if (!data.available || !data.history?.length) { target.innerHTML = `<div class="c14-no-data">No stored monitoring samples are available for this time window.</div>`; return; }
    target.innerHTML = `<div class="c14-stat-grid c14-compact-stats">${c14Stat('SAMPLES',data.sample_count,'30-second scheduler')}${c14Stat('CACHE HIT RATIO',data.latest.cache_hit_ratio_percent == null ? 'Not sampled' : `${c14Number(data.latest.cache_hit_ratio_percent)}%`,'Current sample')}${c14Stat('OPEN ALERTS',data.alert_count,'Active or acknowledged')}${c14Stat('MAX REPLICATION LAG',c14MaxLag(data.latest) == null ? 'Not sampled' : `${c14Number(c14MaxLag(data.latest))} s`,'Current sample')}</div><div class="c14-chart-grid">${c14Chart(data.history,'Cache hit ratio',row=>row.cache_hit_ratio_percent,'#137e78','%')}${c14Chart(data.history,'Maximum replication lag',row=>c14MaxLag(row),'#bf674c',' s')}${c14Chart(data.history,'Operations per second',row=>row.operations_per_second,'#527cb0',' ops/s')}${c14Chart(data.history,'Current connections',row=>row.connections_current,'#8c68aa','')}</div><div class="c14-note">${data.sample_count} samples returned · range ${data.minutes} minutes · source ${esc(data.source)}.</div>`;
  }
  async function c14LoadMetrics(minutes) {
    const target = $('[data-c14-metrics-result]');
    if (target) target.innerHTML = '<div class="c14-no-data">Loading measured monitoring history…</div>';
    try {
      const data = await get(`/api/ops/metrics/monitoring?minutes=${encodeURIComponent(minutes || 60)}`);
      if (target) c14RenderMetrics(data);
    } catch (error) { if (target) target.innerHTML = `<div class="c14-error"><strong>Metrics request failed</strong><p>${esc(error.message)}</p></div>`; }
  }
  function c14AlertsPage() {
    return `<div class="c14-workspace"><section class="c14-panel"><header class="c14-section-head"><div><span class="c14-kicker">ALERT TRIAGE</span><h3>Threshold events</h3><p>Cache below 80%, replication lag above 10 seconds, and signed external alert events.</p></div><button class="c14-button secondary" type="button" data-c14-alert-refresh>Refresh alerts</button></header><div data-c14-alerts><div class="c14-no-data">Loading alerts…</div></div></section></div>`;
  }
  async function c14LoadAlerts() {
    const target = $('[data-c14-alerts]'); if (!target) return;
    target.innerHTML = '<div class="c14-no-data">Loading alert records…</div>';
    try {
      const data = await get('/api/moderation/alerts?limit=100');
      if (!data.alerts?.length) { target.innerHTML = '<div class="c14-no-data">No alerts have been recorded.</div>'; return; }
      target.innerHTML = `<div class="c14-alert-list">${data.alerts.map(alert=>`<article class="c14-alert-card ${esc(alert.status)}"><div><span class="c14-alert-state">${esc(alert.status)}</span><span class="c14-alert-metric">${esc(alert.metric)}</span><h4>${esc(alert.description || alert.source || 'Monitoring threshold event')}</h4><p>Current ${esc(alert.current_value ?? 'Not reported')} · threshold ${esc(alert.threshold ?? 'Not reported')}</p><small>First seen ${esc(alert.created_at)} · last seen ${esc(alert.last_seen_at)}</small>${alert.acknowledgement_reason?`<p>Note: ${esc(alert.acknowledgement_reason)}</p>`:''}</div>${alert.status==='active'?`<div class="c14-ack-controls"><label>Resolution note<textarea maxlength="500" data-c14-ack-reason="${esc(alert.alert_id)}"></textarea></label><button type="button" class="c14-button" data-c14-ack="${esc(alert.alert_id)}">Acknowledge</button></div>`:''}</article>`).join('')}</div>`;
    } catch (error) { target.innerHTML = `<div class="c14-error"><strong>Alerts unavailable</strong><p>${esc(error.message)}</p></div>`; }
  }
  function c14BackupsPage(verify = false) {
    return `<div class="c14-workspace"><section class="c14-panel"><header class="c14-section-head"><div><span class="c14-kicker">${verify?'RECOVERY DRILL':'BACKUP CONTROL'}</span><h3>${verify?'Restore verification':'Backup history'}</h3><p>${verify?'The drill creates a unique temporary restore-verification database and restores only its owned test collection.':'Backups are scoped to the configured application database and retained for seven days.'}</p></div><span class="c14-worker-pill" data-c14-worker-status>Checking worker…</span></header><div class="c14-operation-bar"><button type="button" class="c14-button" data-c14-backup-now>Back Up Now</button><button type="button" class="c14-button secondary" data-c14-verify>Verify Restore</button><span data-c14-job-result aria-live="polite"></span></div><div data-c14-backup-history><div class="c14-no-data">Loading backup history…</div></div>${verify?'<div data-c14-verification-result></div>':''}</section></div>`;
  }
  async function c14LoadBackups() {
    const target = $('[data-c14-backup-history]'); if (!target) return;
    try {
      const data = await get('/api/ops/lab14/backups?limit=100');
      const worker = $('[data-c14-worker-status]');
      if (worker) { worker.textContent = `Worker ${String(data.worker?.status || 'unknown').replaceAll('_',' ')}`; worker.className = `c14-worker-pill ${esc(data.worker?.status || 'unknown')}`; }
      const rows = data.backups || [];
      if (!rows.length) { target.innerHTML = '<div class="c14-no-data">No backup or restore-verification runs are recorded.</div>'; return; }
      const sizes = rows.filter(row=>Number.isFinite(Number(row.backup_size_bytes))).reverse();
      target.innerHTML = `<div class="c14-backup-layout"><div class="c14-table-scroll"><table class="c14-table"><thead><tr><th>Completed</th><th>Type</th><th>Status</th><th>Oplog</th><th>Size</th><th>Backup</th><th>Restore / RTO</th><th>Documents</th></tr></thead><tbody>${rows.map(row=>`<tr><td>${esc(row.completed_at || row.created_at)}</td><td>${esc(row.kind)}</td><td><span class="c14-status ${esc(row.status)}">${esc(row.verification_status || row.status)}</span></td><td>${row.oplog_enabled===true?'Captured':row.kind==='backup'?'Not reported':'—'}</td><td>${row.backup_size_bytes == null?'Not measured':esc(c14Bytes(row.backup_size_bytes))}</td><td>${row.backup_duration_seconds == null?'Not measured':`${esc(c14Number(row.backup_duration_seconds,2))} s`}</td><td>${row.restore_duration_seconds == null?'Not measured':`${esc(c14Number(row.restore_duration_seconds,2))} s`}</td><td>${esc(row.document_count ?? '—')}</td></tr>`).join('')}</tbody></table></div><section class="c14-size-chart"><h4>Recorded backup sizes</h4>${sizes.length?`<div class="c14-bars">${sizes.slice(-12).map(row=>`<div title="${esc(row.completed_at)} · ${esc(c14Bytes(row.backup_size_bytes))}"><i style="--bar:${Math.max(5,Math.min(100,Number(row.backup_size_bytes)/Math.max(...sizes.map(x=>Number(x.backup_size_bytes)))*100))}%"></i><small>${esc(new Date(row.completed_at || row.created_at).toLocaleDateString())}</small></div>`).join('')}</div>`:'<p>No measured backup sizes yet.</p>'}</section></div>`;
    } catch (error) { target.innerHTML = `<div class="c14-error"><strong>Backup history unavailable</strong><p>${esc(error.message)}</p></div>`; }
  }
  async function c14RunJob(kind) {
    const result = $('[data-c14-job-result]');
    const verify = kind === 'verify';
    const confirmed = window.confirm(verify
      ? 'Run a restore test in a newly generated temporary restore-verification database? It will only replace the test collection created by this run.'
      : 'Create a timestamped full replica-set mongodump with oplog capture? The archive contains every database on the configured replica set.');
    if (!confirmed) return;
    const actionButton = $(`[data-c14-${verify?'verify':'backup-now'}]`);
    if (actionButton) actionButton.disabled = true;
    if (result) result.textContent = 'Queueing the confirmed operation…';
    try {
      const job = await get(`/api/ops/lab14/${verify?'verify':'backup'}`, {method:'POST',body:JSON.stringify({confirm:true})});
      if (result) result.textContent = `Job ${job.job_id} queued; waiting for the backup worker.`;
      let state = job;
      for (let attempt=0; attempt<30 && (state.status==='queued' || state.status==='running'); attempt++) {
        await new Promise(resolve=>setTimeout(resolve,2000));
        state = await get(`/api/ops/lab14/jobs/${encodeURIComponent(job.job_id)}`);
        if (result) result.textContent = `Job ${job.job_id}: ${state.status}${state.error_type?` (${state.error_type})`:''}.`;
      }
      if (result && ['success','failed'].includes(state.status)) result.textContent = `Job ${job.job_id}: ${state.status}. ${state.result?.verification_status || ''}`;
      await c14LoadBackups();
    } catch (error) { if (result) result.textContent = `Operation failed: ${error.message}`; }
    finally { if (actionButton) actionButton.disabled = false; }
  }

  function c14Page(tab) {
    if (tab===0) return c14Overview();
    if (tab===1) return c14MetricsPage();
    if (tab===2) return c14AlertsPage();
    if (tab===3) return c14BackupsPage(false);
    return c14BackupsPage(true);
  }

  function c15Envelope() { return JSON.parse(root.dataset.checks || '{}')['15'] || null; }
  function c15Stat(name, valueText, hint) { return `<article class="c15-stat"><span>${esc(name)}</span><strong>${esc(valueText)}</strong><small>${esc(hint)}</small></article>`; }
  function c15Overview() {
    const data = c15Envelope(), organizations = data?.organizations || [];
    const bars = organizations.slice(0, 8).map(org=>`<div class="c15-bar-row"><span>${esc(org.name)}</span><i><b style="--fill:${Math.max(3,Math.min(100,Number(org.cases||0)/Math.max(1,...organizations.map(x=>Number(x.cases||0)))*100))}%"></b></i><strong>${esc(org.cases||0)}</strong></div>`).join('');
    const roles = JSON.parse(root.dataset.c15Memberships || '[]');
    return `<div class="c15-workspace"><div class="c15-hero"><div><span class="c15-kicker">TENANT OPERATIONS / CASE MANAGEMENT</span><h3>Enterprise Multi-Tenant Workspace</h3><p>Organisation-scoped casework, memberships, and access controls.</p></div><span class="c15-security-badge"><i></i>Tenant isolation active</span></div><div class="c15-stat-grid">${c15Stat('ORGANISATIONS',data?.organization_count??'Not reported','Live organization records')}${c15Stat('MEMBERS',data?.member_count??'Not reported','Membership records')}${c15Stat('CASES',data?.case_count??'Not reported','All tenant cases')}${c15Stat('RATE LIMIT REFUSALS',data?.rate_limit_refusals??'Not reported','Rolling per-user window')}</div><div class="c15-overview-grid"><section class="c15-panel"><header><span class="c15-kicker">TENANT COMPARISON</span><h4>Cases by organization</h4></header>${bars?`<div class="c15-bars">${bars}</div>`:'<div class="c15-empty">No organization statistics are available.</div>'}</section><section class="c15-panel"><header><span class="c15-kicker">MY MEMBERSHIPS</span><h4>Organization access</h4></header>${roles.length?`<div class="c15-memberships">${roles.map(row=>`<div><strong>${esc(row.name)}</strong><span>${esc(row.role)}</span></div>`).join('')}</div>`:'<div class="c15-empty">No organization membership was returned for this account.</div>'}</section></div><p class="c15-disclosure">${esc(data?.privacy_note || 'Case descriptions are not included in tenant comparison analytics.')}</p></div>`;
  }
  function c15CasePage() {
    return `<div class="c15-workspace"><section class="c15-panel"><header class="c15-section-head"><div><span class="c15-kicker">CASEWORK QUEUE</span><h3>Cases across your organizations</h3><p>Each result is authorized by the tenant membership of the signed-in user.</p></div><button type="button" class="c15-button secondary" data-c15-cases-refresh>Refresh queue</button></header><div class="c15-toolbar"><label>Organization<select data-c15-org-filter><option value="">All accessible organizations</option></select></label><label>Status<select data-c15-case-status><option value="">All states</option><option value="open">Open</option><option value="in_progress">In progress</option><option value="closed">Closed</option></select></label><button type="button" class="c15-button" data-c15-case-apply>Apply filters</button></div><details class="c15-create"><summary>Open a case</summary><form class="c15-form" data-case-create><label>Organization<select name="organization_id" required data-org-options></select></label><label>Case title<input name="title" maxlength="180" required></label><label class="wide">Description<textarea name="description" maxlength="8000"></textarea></label><label class="wide">Linked threat or event IDs<input name="linked_threat_ids" maxlength="2000" placeholder="Paste record IDs, separated by commas"><small>Links are stored as references; no threat data is copied into the case.</small></label><button class="c15-button" type="submit">Create case</button></form></details><div data-case-list><div class="c15-empty">Loading tenant case records…</div></div><div data-case-detail></div><div data-labs-detail></div></section></div>`;
  }
  function c15OrganizationsPage() {
    return `<div class="c15-workspace"><div class="c15-management-grid"><section class="c15-panel"><span class="c15-kicker">TENANT DIRECTORY</span><h3>Organizations</h3><p>Administrators can create organizations. Members see only organizations in their membership list.</p><form class="c15-form" data-org-create><label>Name<input name="name" maxlength="120" required></label><label>Description<input name="description" maxlength="500"></label><button type="submit" class="c15-button">Create organization</button></form><div data-c15-org-list><div class="c15-empty">Loading organization records…</div></div></section><aside class="c15-panel c15-schema"><span class="c15-kicker">DATA BOUNDARY</span><h3>Tenant partition</h3><div class="c15-tenant-key">tenant_id<strong>organization identifier</strong></div><p>New cases carry both <code>tenant_id</code> and the existing <code>organization_id</code> compatibility field. Existing rows without tenant_id remain scoped through organization_id until the additive migration is reviewed.</p></aside></div></div>`;
  }
  function c15MembersPage() {
    return `<div class="c15-workspace"><section class="c15-panel"><header class="c15-section-head"><div><span class="c15-kicker">ACCESS ADMINISTRATION</span><h3>Members and roles</h3><p>Organization administrators can manage membership roles only within their own tenant.</p></div></header><form class="c15-form c15-member-form" data-member-add><label>Organization<select name="organization_id" required data-org-options></select></label><label>Platform user ID<input name="user_id" maxlength="64" required></label><label>Role<select name="role"><option value="analyst">Analyst</option><option value="viewer">Viewer</option><option value="org_admin">Organization admin</option></select></label><button type="submit" class="c15-button">Save membership</button></form><button type="button" class="c15-button secondary" data-c15-members-refresh>Load tenant members</button><div data-case-members-list><div class="c15-empty">Select an organization to inspect its members.</div></div></section></div>`;
  }
  function c15RatePage() {
    const data=c15Envelope();
    return `<div class="c15-workspace"><div class="c15-management-grid"><section class="c15-panel"><span class="c15-kicker">ABUSE CONTROLS</span><h3>Per-user request budget</h3><div class="c15-rate-number">30<span>requests / minute</span></div><p>MongoDB atomically increments a per-user fixed-window record. Expired counters are removed through TTL.</p><div class="c15-stat-grid c15-one-stat">${c15Stat('OBSERVED REFUSALS',data?.rate_limit_refusals??'Not reported','429 responses recorded by the API')}</div></section><section class="c15-panel"><span class="c15-kicker">API INSPECTOR</span><h3>Inspect an authorized request</h3><p>Run a real case-list request and review its status, Retry-After header, and response body.</p><button type="button" class="c15-button" data-c15-api-inspect>Run GET /api/cases</button><pre class="c15-response" data-c15-api-result>API response appears here.</pre></section></div></div>`;
  }
  function c15Page(tab) { return tab===0?c15Overview():tab===1?c15CasePage():tab===2?c15OrganizationsPage():tab===3?c15MembersPage():c15RatePage(); }
  async function c15LoadOrganizations() {
    const data=await get('/api/cases/organizations'), options=`<option value="">Select organization</option>${(data.organizations||[]).map(org=>`<option value="${esc(org.organization_id)}">${esc(org.name)}</option>`).join('')}`;
    root.querySelectorAll('[data-org-options],[data-c15-org-filter]').forEach(select=>{const previous=select.value;select.innerHTML=select.hasAttribute('data-c15-org-filter')?`<option value="">All accessible organizations</option>${options.replace('<option value="">Select organization</option>','')}`:options;if([...select.options].some(option=>option.value===previous))select.value=previous;});
    root.dataset.organizations=JSON.stringify(data.organizations||[]);
    const list=$('[data-c15-org-list]'); if(list) list.innerHTML=(data.organizations||[]).length?`<div class="c15-org-cards">${data.organizations.map(org=>`<article><strong>${esc(org.name)}</strong><span>${esc(org.role||'Member')}</span><small>${esc(org.description||'No description')}</small><code>${esc(org.organization_id)}</code></article>`).join('')}</div>`:'<div class="c15-empty">No organizations are visible to this account.</div>';
    return data.organizations||[];
  }
  async function c15LoadCases() {
    const target=$('[data-case-list]'); if(!target)return;
    try {const organization=$('[data-c15-org-filter]')?.value||'', status=$('[data-c15-case-status]')?.value||'';const params=new URLSearchParams();if(organization)params.set('organization_id',organization);if(status)params.set('status',status);const data=await get(`/api/cases${params.size?'?'+params:''}`);target.innerHTML=c15CaseTable(data.cases||[]);}
    catch(error){target.innerHTML=`<div class="c15-error"><strong>Case list unavailable</strong><p>${esc(error.message)}</p></div>`;}
  }
  function c15CaseTable(items) {
    if(!items.length)return '<div class="c15-empty">No cases match the selected tenant and status.</div>';
    return `<div class="c15-table-scroll"><table class="c15-table"><thead><tr><th>Case</th><th>Tenant</th><th>Status</th><th>Assignee</th><th>Indicators</th><th>Updated</th><th>Actions</th></tr></thead><tbody>${items.map(row=>`<tr><td><strong>${esc(row.title)}</strong><small>${esc(row.case_id)}</small></td><td>${esc(row.tenant_id||row.organization_id)}</td><td><span class="c15-case-state ${esc(row.status)}">${esc(row.status)}</span></td><td>${esc(row.assigned_to||'Unassigned')}</td><td>${esc(row.linked_threat_count||0)}</td><td>${esc(row.updated_at||'Not reported')}</td><td><button type="button" class="c15-link" data-case-detail="${esc(row.case_id)}">Inspect</button><button type="button" class="c15-link danger" data-c15-delete="${esc(row.case_id)}">Delete</button></td></tr>`).join('')}</tbody></table></div>`;
  }
  async function c15LoadMembers() {
    const target=$('[data-case-members-list]');if(!target)return;
    const org=$('[data-member-add] [name="organization_id"]')?.value;if(!org){target.innerHTML='<div class="c15-empty">Select an organization first.</div>';return;}
    try{const data=await get(`/api/cases/org/members?organization_id=${encodeURIComponent(org)}`);target.innerHTML=(data.members||[]).length?`<div class="c15-table-scroll"><table class="c15-table"><thead><tr><th>User</th><th>Email</th><th>Role</th><th>Added</th></tr></thead><tbody>${data.members.map(member=>`<tr><td>${esc(member.name||member.user_id)}</td><td>${esc(member.email||'Not reported')}</td><td>${esc(member.role)}</td><td>${esc(member.created_at||'Not reported')}</td></tr>`).join('')}</tbody></table></div>`:'<div class="c15-empty">No member records are returned.</div>';}
    catch(error){target.innerHTML=`<div class="c15-error"><strong>Member list unavailable</strong><p>${esc(error.message)}</p></div>`;}
  }

  function c16Databases() { return JSON.parse(root.dataset.checks || '{}')['16']?.databases || {}; }
  function c16StatusCard(name, item = {}) {
    const state = item.status || 'not reported';
    const tone = state === 'connected' ? 'healthy' : state === 'unavailable' ? 'degraded' : 'unknown';
    return `<article class="c16-db-card ${tone}"><header><span class="c16-db-glyph">${({mongodb:'M',cassandra:'C',neo4j:'N'})[name] || 'DB'}</span><span class="c16-state-dot ${tone}"></span></header><p class="c16-db-name">${esc(name)}</p><strong>${esc(state.replaceAll('_',' '))}</strong><small>${esc(item.database || item.version || item.reason || item.error_type || (item.latency_ms != null ? `${item.latency_ms} ms` : 'No live connection details'))}</small>${item.latency_ms != null ? `<span class="c16-latency">${esc(item.latency_ms)} ms response</span>` : ''}</article>`;
  }
  function c16Overview() {
    const databases = c16Databases();
    const connected = Object.values(databases).filter(item => item.status === 'connected').length;
    const circuits = JSON.parse(root.dataset.checks || '{}')['16']?.circuit_breakers || {};
    return `<div class="c16-workspace"><header class="c16-hero"><div><span class="c16-kicker">AEGIS / DATA ENGINEERING</span><h3>Database Operations</h3><p>Three database models, independently probed. Evidence and latency are shown only when returned by live backends.</p></div><div class="c16-hero-state"><strong>${connected}/3</strong><span>backends connected</span></div></header><div class="c16-db-grid">${['mongodb','cassandra','neo4j'].map(name=>c16StatusCard(name,databases[name])).join('')}</div><div class="c16-systems-grid"><section class="c16-panel"><span class="c16-kicker">RESILIENCE</span><h4>Circuit breakers</h4><div class="c16-breaker-list">${Object.entries(circuits).map(([name,state])=>`<div><span>${esc(name)}</span><b class="${state==='closed'?'closed':'open'}">${esc(state)}</b></div>`).join('') || '<p class="c16-empty">No breaker state was returned.</p>'}</div><p class="c16-footnote">Breaker state is reported by the application probe. It is not simulated from connection status.</p></section><section class="c16-panel"><span class="c16-kicker">SERVICE CONTROL</span><h4>Database services</h4><p>Start/stop requires an orchestrator. Requests are audited and rejected while no safe controller is configured.</p><div class="c16-action-row"><button type="button" class="c16-button" data-c16-service="start">Request start</button><button type="button" class="c16-button quiet" data-c16-service="stop">Request stop</button></div><div data-c16-action-result aria-live="polite"></div></section></div><p class="c16-integrity-note">No benchmark, queue count, or cross-database consistency result is reported without a configured service response.</p></div>`;
  }
  function c16DatabasesPage() {
    const databases=c16Databases();
    return `<div class="c16-workspace"><header class="c16-section-heading"><div><span class="c16-kicker">CONNECTION MAP</span><h3>Database health</h3><p>Per-backend reachability and measured connection latency.</p></div><button type="button" class="c16-button quiet" data-labs-refresh>Probe again</button></header><div class="c16-db-grid">${['mongodb','cassandra','neo4j'].map(name=>c16StatusCard(name,databases[name])).join('')}</div><section class="c16-panel c16-architecture"><div><span>MongoDB</span><small>Documents &amp; transactions</small></div><i></i><div><span>Cassandra</span><small>Time-partitioned event history</small></div><i></i><div><span>Neo4j</span><small>Account transfer paths</small></div></section><p class="c16-integrity-note">A backend marked unavailable or not configured is not queried as if it were online. Configure backend database connections to enable it.</p></div>`;
  }
  function c16AuditPage() {
    return `<div class="c16-workspace"><header class="c16-section-heading"><div><span class="c16-kicker">AUDIT / RETENTION WINDOW</span><h3>Operational audit log</h3><p>Filters query stored MongoDB administrator events. Cassandra audit delivery is reported separately.</p></div><label class="c16-window">Window<select data-c16-days><option value="7">7 days</option><option value="14">14 days</option><option value="30">30 days</option></select></label></header><section class="c16-panel"><form class="c16-filter-form" data-c16-audit-filter><label>UTC day<input type="date" name="day"></label><label>Actor or target<input type="search" name="actor" maxlength="128" placeholder="Administrator ID"></label><button type="submit" class="c16-button">Apply filters</button><button type="button" class="c16-button quiet" data-c16-audit-clear>Clear</button></form><div data-c16-audit-rows><p class="c16-empty">Loading audit entries…</p></div></section><div class="c16-summary-grid" data-c16-audit-summary><p class="c16-empty">Loading seven-day summary…</p></div><p class="c16-integrity-note">Cassandra outbox and exactly-once delivery are not integrated here; these counts are explicitly from the platform MongoDB audit log.</p></div>`;
  }
  async function c16LoadAudit() {
    const form=$('[data-c16-audit-filter]'), target=$('[data-c16-audit-rows]'); if(!target)return;
    target.innerHTML='<p class="c16-empty">Loading stored audit entries…</p>';
    const params=new URLSearchParams(new FormData(form));
    try { const data=await get(`/api/ops/audit?limit=200&${params}`); const rows=data.events||[]; target.innerHTML=`<p class="c16-result-note">${rows.length} event${rows.length===1?'':'s'} · source ${esc(data.source)}</p>${rows.length?`<div class="c16-table-scroll"><table class="c16-table"><thead><tr><th>Time</th><th>Actor</th><th>Action</th><th>Target</th><th>Outcome</th></tr></thead><tbody>${rows.map(row=>`<tr><td>${esc(row.created_at)}</td><td>${esc(row.administrator_id||'System')}</td><td>${esc(row.action||'Not recorded')}</td><td>${esc(row.target_record||'Not recorded')}</td><td>${esc(row.outcome||'Not recorded')}</td></tr>`).join('')}</tbody></table></div>`:'<div class="c16-empty">No audit events match these filters.</div>'}`; }
    catch(error){target.innerHTML=`<div class="c16-error">Audit query failed: ${esc(error.message)}</div>`;}
  }
  async function c16LoadAuditSummary() {
    const target=$('[data-c16-audit-summary]'); if(!target)return;
    try {const days=Number($('[data-c16-days]')?.value||7), data=await get(`/api/ops/audit/summary?days=${days}`); const kinds=data.by_action||[];const max=Math.max(1,...kinds.map(row=>Number(row.count)||0));target.innerHTML=`<article class="c16-summary-total"><span>${esc(data.days)} DAY TOTAL</span><strong>${Number(data.total||0).toLocaleString()}</strong><small>MongoDB admin audit events</small></article><section class="c16-panel c16-kind-chart"><h4>Actions by kind</h4>${kinds.length?kinds.map(row=>`<div class="c16-kind-row"><span>${esc(row.kind)}</span><i><b style="--width:${Math.max(2,(Number(row.count)/max)*100)}%"></b></i><strong>${Number(row.count).toLocaleString()}</strong></div>`).join(''):'<p class="c16-empty">No stored events in this time window.</p>'}</section><p class="c16-summary-source">${esc(data.note)} · Cassandra: ${esc(data.cassandra_delivery)}</p>`;}
    catch(error){target.innerHTML=`<div class="c16-error">Summary unavailable: ${esc(error.message)}</div>`;}
  }
  function c16FraudPage(graph=false) {
    return `<div class="c16-workspace"><div class="c16-investigation-layout"><section class="c16-panel c16-query-panel"><span class="c16-kicker">INVESTIGATOR CONSOLE</span><h3>${graph?'Transfer path explorer':'Account evidence review'}</h3><p>Submit an account and optional transaction reference. Results remain separate evidence from each configured database.</p><form class="c16-fraud-form" data-ch16-fraud><label>Account / user ID<input name="account_id" required maxlength="128" autocomplete="off" placeholder="Enter an account identifier"></label><label>Transaction ID<input name="transaction_id" maxlength="128" placeholder="Optional for account-only review"></label><label>Maximum graph hops<select name="max_hops"><option>1</option><option>2</option><option selected>3</option></select></label><button type="submit" class="c16-button">${graph?'Trace account paths':'Run evidence review'}</button></form></section><section class="c16-panel c16-result-panel"><span class="c16-kicker">EVIDENCE OUTPUT</span><div data-ch16-results><p class="c16-empty">Run an investigation to display the responses from configured backends.</p></div></section></div><p class="c16-integrity-note">No automated fraud verdict is asserted. Unavailable sources are identified in the result.</p></div>`;
  }
  function c16Page(tab) { return tab===0?c16Overview():tab===1?c16DatabasesPage():tab===2?c16AuditPage():c16FraudPage(tab===4); }

  function renderTab() {
    const item = chapters.find(chapter => chapter.id === currentChapter);
    const content = $('[data-labs-content]');
    const tabName = tabs[item.id][currentTab];
    $('[data-labs-tabs]').innerHTML = tabs[item.id].map((label, index) => `<button type="button" class="labs-chapter-tab${index === currentTab ? ' is-active' : ''}" aria-current="${index === currentTab ? 'page' : 'false'}" data-labs-tab="${index}">${esc(label)}</button>`).join('');
    if (item.id === '13') content.innerHTML = c13Page(currentTab);
    else if (item.id === '14') {
      content.innerHTML = c14Page(currentTab);
      setMessage('');
      if (currentTab === 1) c14LoadMetrics(Number($('[data-c14-window]')?.value || 30));
      if (currentTab === 2) c14LoadAlerts();
      if (currentTab >= 3) c14LoadBackups();
      return;
    }
    else if (item.id === '15') {
      content.innerHTML = c15Page(currentTab);
      setMessage('');
      if (currentTab === 0) {
        Promise.all([c15LoadOrganizations(), get('/api/cases/me').catch(()=>({organizations:[]}))])
          .then(([,data])=>{root.dataset.c15Memberships=JSON.stringify(data.organizations||[]);if(currentChapter==='15'&&currentTab===0){const target=$('[data-labs-content]');if(target)target.innerHTML=c15Overview();}})
          .catch(()=>{});
      }
      if (currentTab === 1) c15LoadOrganizations().then(c15LoadCases).catch(error=>{const target=$('[data-case-list]');if(target)target.innerHTML=`<div class="c15-error"><p>${esc(error.message)}</p></div>`;});
      if (currentTab === 2) c15LoadOrganizations().catch(error=>{const target=$('[data-c15-org-list]');if(target)target.textContent=error.message;});
      if (currentTab === 3) c15LoadOrganizations().catch(error=>{const target=$('[data-case-members-list]');if(target)target.textContent=error.message;});
      return;
    }
    else if (item.id === '16') {
      content.innerHTML = c16Page(currentTab);
      setMessage('');
      if (currentTab === 2) { c16LoadAudit(); c16LoadAuditSummary(); }
      return;
    }
    else if (currentTab === 0) content.innerHTML = overview(item);
    else if (item.id === '15') {
      if (currentTab === 1) content.innerHTML = `<div class="labs-section-intro"><h3>Cases</h3><form class="labs-form" data-case-create><label>Organisation<select name="organization_id" required data-org-options></select></label><label>Case title<input name="title" maxlength="180" required></label><label>Private case description<textarea name="description" maxlength="8000"></textarea></label><label>Linked threat IDs, comma separated<input name="linked_threat_ids" maxlength="2000"></label><button class="xs-btn" type="submit">Open case</button></form><div data-case-list></div><div data-case-detail></div></div>`;
      else if (currentTab === 2) content.innerHTML = `<div class="labs-section-intro"><h3>Organisations</h3><p>Only organisation names, descriptions and counts appear here. Case descriptions remain private to case details.</p><form class="labs-form" data-org-create><label>Name<input name="name" maxlength="120" required></label><label>Description<input name="description" maxlength="500"></label><button class="xs-btn" type="submit">Create organisation</button></form><button class="xs-btn secondary" type="button" data-case-organizations>Refresh organisations</button><div data-case-organizations-list></div></div>`;
      else if (currentTab === 3) content.innerHTML = `<div class="labs-section-intro"><h3>Organisation members</h3><form class="labs-form" data-member-add><label>Organisation<select name="organization_id" required data-org-options></select></label><label>Platform user ID<input name="user_id" maxlength="64" required></label><label>Organisation role<select name="role"><option value="analyst">Analyst</option><option value="viewer">Viewer</option><option value="org_admin">Organisation admin</option></select></label><button class="xs-btn" type="submit">Add or update membership</button></form><button type="button" class="xs-btn secondary" data-case-members>Load members</button><div data-case-members-list></div></div>`;
      else content.innerHTML = `<div class="labs-section-intro"><h3>Rate limits</h3><p>Case APIs allow up to 120 requests per organisation and user per rolling minute. A 429 response includes a Retry-After header.</p><div data-case-rate-summary></div></div>`;
      loadCaseWorkspace(currentTab);
    } else if (item.id === '16' && currentTab === 1) {
      const data = JSON.parse(root.dataset.checks || '{}')['16'];
      const service = data?.service_control || {};
      content.innerHTML = `<div class="labs-section-intro"><h3>Database service controls</h3><p>MongoDB, Cassandra, and Neo4j status below is probed from their configured connection settings. This deployment has no database orchestrator; control requests are rejected safely and do not start or stop external services.</p><p>Service control: <span class="labs-status neutral">${esc(service.status || 'Not configured')}</span> ${esc(service.reason || '')}</p><div class="labs-button-row"><button class="xs-btn secondary" type="button" data-labs-refresh>Refresh database status</button><button class="xs-btn" type="button" data-ch16-service="start">Request start</button><button class="xs-btn secondary" type="button" data-ch16-service="stop">Request stop</button></div><div data-labs-detail></div></div>`;
    } else if (item.id === '16' && currentTab === 2) {
      content.innerHTML = '<div class="labs-section-intro"><h3>Audit timeline</h3><p>Shows actual platform MongoDB audit records. Cassandra audit delivery and outbox state are labelled separately and are not implied by this timeline.</p><button type="button" class="xs-btn secondary" data-ch16-audit>Load audit events</button><button type="button" class="xs-btn secondary" data-ch16-audit-summary>Refresh delivery summary</button><div data-labs-detail></div>';
    } else if (item.id === '16' && (currentTab === 3 || currentTab === 4)) {
      const graph = currentTab === 4;
      content.innerHTML = `<div class="labs-section-intro"><h3>${graph ? 'Graph investigation' : 'Fraud evidence review'}</h3><p>Queries actual MongoDB transaction, Cassandra timeline, and Neo4j transfer-path data when configured. Results remain separate evidence sources and do not assert a verdict.</p><form class="labs-form" data-ch16-fraud><label>Account / user ID<input name="account_id" maxlength="128" required></label><label>Transaction ID<input name="transaction_id" maxlength="128" required></label><label>Maximum graph hops<select name="max_hops"><option>1</option><option>2</option><option selected>3</option></select></label><button class="xs-btn" type="submit">${graph ? 'Investigate graph and transaction' : 'Review fraud evidence'}</button></form><div data-ch16-results></div></div>`;
    } else content.innerHTML = item.id === '17' ? `<div class="labs-embed-note"><strong>${esc(tabName)}</strong><p>Interactive operations use the authenticated AI research service.</p></div><div id="labs-vector-workspace" data-vector-project="rag"></div>` : item.id === '18' ? `<div class="labs-embed-note"><strong>${esc(tabName)}</strong><p>Interactive operations use the existing authenticated threat intelligence workspace. The workspace retains import, investigation, analyst review, benchmark, and report actions.</p></div><div id="labs-c18-workspace" data-vector-project="chapter18"></div>` : `<div class="labs-section-intro"><h3>${esc(tabName)}</h3><p>${esc(item.description)}</p></div>${unavailable(`${tabName} has no integrated API in this application.`)}`;
    setMessage('');
    if (currentTab > 0 && item.project) mountProject(item.project);
    if (item.id === '13' && currentTab === 3) loadChapter13Metrics();
  }
  function mountProject(project) {
    const target = project === 'rag' ? $('#labs-vector-workspace') : $('#labs-c18-workspace');
    if (!target) return;
    document.dispatchEvent(new CustomEvent('portfolio:project-opened', { detail: { id: project, root: target } }));
  }
  function openChapter(id, landingTab = 0) {
    const item = chapters.find(chapter => chapter.id === id);
    if (!item) return;
    currentChapter = id;
    currentTab = Number.isInteger(landingTab) && landingTab >= 0 && landingTab < tabs[id].length ? landingTab : 0;
    root.classList.toggle('is-chapter-13', id === '13');
    root.classList.toggle('is-chapter-14', id === '14');
    root.classList.toggle('is-chapter-15', id === '15');
    root.classList.toggle('is-chapter-16', id === '16');
    $('[data-labs-workspace]').hidden = false;
    $('[data-labs-heading]').innerHTML = id === '13'
      ? `<div class="c13-page-heading"><div class="c13-heading-icon" aria-hidden="true"><svg viewBox="0 0 32 32"><path d="M16 3 27 9v14l-11 6L5 23V9l11-6Z"/><path d="M5 9l11 6 11-6M16 15v14M10 6l11 6M22 7l-11 6"/></svg></div><div class="c13-heading-copy"><span class="c13-overline">INFRASTRUCTURE CONTROL CENTER</span><h2>Deployment &amp; Infrastructure</h2><p>High availability, replica-set health, and deployment targets.</p></div><div class="c13-heading-actions"><span class="c13-live-label"><i></i>LIVE TELEMETRY</span><button type="button" class="xs-btn secondary" data-labs-refresh>Refresh state</button></div></div>`
      : id === '14' ? `<div class="c14-page-heading"><span class="c14-heading-mark" aria-hidden="true">◷</span><div><span class="c14-kicker">A E G I S / RESILIENCE OFFICE</span><h2>Observability &amp; Backup Center</h2><p>Measure service health. Protect recovery points. Verify the restore path.</p></div><button type="button" class="c14-button secondary" data-labs-refresh>Refresh signals</button></div>`
      : id === '15' ? `<div class="c15-page-heading"><div class="c15-mark" aria-hidden="true"><span></span><span></span><span></span></div><div><span class="c15-kicker">A E G I S / TENANT SERVICES</span><h2>Enterprise Management</h2><p>Casework, organization governance, and auditable tenant boundaries.</p></div><button type="button" class="c15-button secondary" data-labs-refresh>Refresh workspace</button></div>`
      : id === '16' ? `<div class="c16-page-heading"><div class="c16-heading-icon" aria-hidden="true">DB</div><div><span class="c16-kicker">AEGIS / DATABASE ENGINEERING</span><h2>Database Operations</h2><p>Investigate actual document, wide-column and graph data across configured services.</p></div><button type="button" class="c16-button quiet" data-labs-refresh>Refresh topology</button></div>`
      : `<span class="eyebrow">${esc(item.title)}</span><h2>${esc(item.title)}</h2><p>${esc(item.description)}</p>`;
    renderTab();
  }
  window.moveLabsWorkspaceToExercise = id => {
    if (!/^(13|14|15|16|17|18)$/.test(String(id))) return;
    moveWorkspace(`exercise-labs-mount-${id}`, true);
  };
  window.moveLabsWorkspaceToAdmin = () => {
    if (!standaloneMode) return;
    moveWorkspace('exercise-labs-shell', false);
    currentChapter = null;
    $('[data-labs-workspace]').hidden = true;
  };
  window.openLabsChapter = id => {
    if (!/^(13|14|15|16|17|18)$/.test(String(id))) return;
    if (!standaloneMode) window.moveLabsWorkspaceToExercise(id);
    openChapter(String(id), exerciseLandingTabs[id] ?? 0);
    refresh();
  };
  root.addEventListener('click', async event => {
    const button = event.target.closest('button');
    if (!button) return;
    if (button.hasAttribute('data-c15-cases-refresh') || button.hasAttribute('data-c15-case-apply')) c15LoadCases();
    if (button.hasAttribute('data-c15-members-refresh')) c15LoadMembers();
    if (button.hasAttribute('data-c16-audit-clear')) {
      const form=$('[data-c16-audit-filter]'); form?.reset(); c16LoadAudit();
    }
    if (button.hasAttribute('data-c16-service')) {
      const target=$('[data-c16-action-result]');
      if(target)target.textContent='Submitting an audited service-control request…';
      try { await get(`/api/services/lab16/${button.dataset.c16Service}`,{method:'POST'}); if(target)target.textContent='Request accepted.'; }
      catch(error){ if(target)target.innerHTML=`<p class="c16-control-denied">${esc(error.message)} No service state was changed.</p>`; }
    }
    if (button.dataset.c15Delete) {
      if (!window.confirm('Delete this case from its organization? This action is recorded and cannot be undone.')) return;
      button.disabled = true;
      get(`/api/cases/${encodeURIComponent(button.dataset.c15Delete)}`, {method:'DELETE'})
        .then(()=>c15LoadCases())
        .catch(error=>{const target=$('[data-case-detail]');if(target)target.innerHTML=`<div class="c15-error"><strong>Case deletion failed</strong><p>${esc(error.message)}</p></div>`;})
        .finally(()=>{button.disabled=false;});
    }
    if (button.hasAttribute('data-c15-api-inspect')) {
      const target=$('[data-c15-api-result]'); if(target)target.textContent='Sending request…';
      fetch('/api/cases?limit=5',{credentials:'same-origin',headers:{Accept:'application/json'}})
        .then(async response=>({status:response.status,retryAfter:response.headers.get('Retry-After'),body:await response.json().catch(()=>({}))}))
        .then(result=>{if(target)target.textContent=JSON.stringify({status:result.status,retry_after:result.retryAfter,body:result.body},null,2);})
        .catch(error=>{if(target)target.textContent=`Request failed: ${error.message}`;});
    }
    if (button.hasAttribute('data-c14-alert-refresh')) c14LoadAlerts();
    if (button.hasAttribute('data-c14-backup-now')) c14RunJob('backup');
    if (button.hasAttribute('data-c14-verify')) c14RunJob('verify');
    if (button.dataset.c14Ack) {
      const card = button.closest('.c14-alert-card');
      const reason = card?.querySelector('textarea')?.value || '';
      button.disabled = true;
      get('/api/moderation/alerts/ack', {method:'POST',body:JSON.stringify({alert_id:button.dataset.c14Ack,reason})})
        .then(()=>c14LoadAlerts())
        .catch(error=>{ const target=$('[data-c14-alerts]'); if(target) target.insertAdjacentHTML('afterbegin',`<div class="c14-error"><strong>Acknowledgement failed</strong><p>${esc(error.message)}</p></div>`); })
        .finally(()=>{button.disabled=false;});
    }
    if (button.hasAttribute('data-labs-refresh')) refresh();
    if (button.dataset.labsTab !== undefined) { currentTab = Number(button.dataset.labsTab); renderTab(); }
    if (button.dataset.labsProject) mountProject(button.dataset.labsProject);
    if (button.hasAttribute('data-labs-stepdown')) {
      const seconds = Number($('[data-labs-seconds]')?.value);
      if (!Number.isInteger(seconds) || seconds < 5 || seconds > 120) { setMessage('Enter a stepdown duration between 5 and 120 seconds.', true); return; }
      const primary = c13ReplicaSet()?.primary || 'the currently reported primary';
      if (!window.confirm(`Request primary stepdown on ${primary} for up to ${seconds} seconds? Database requests may be briefly interrupted. Continue only if this is the intended selected database cluster.`)) return;
      const action = event.target.closest('[data-labs-stepdown]');
      if (action) action.disabled = true;
      let target = $('[data-labs-detail]');
      if (target) target.innerHTML = '<p class="loading-state">Requesting stepdown and waiting for a different PRIMARY?</p>';
      get('/api/ops/lab13/failover', { method: 'POST', body: JSON.stringify({ confirm: true, seconds }) })
        .then(async data => {
          await refresh();
          target = $('[data-labs-detail]');
          if (target) target.innerHTML = `<span class="c13-overline">FAILOVER VERIFIED</span><h4>${esc(data.previous_primary)} ? ${esc(data.new_primary)}</h4><p>Observed election marker: ${esc(data.election_marker?.timestamp || 'not reported')} ? term ${esc(data.term ?? 'not reported')}</p>`;
        })
        .catch(error => { target = $('[data-labs-detail]'); if (target) target.innerHTML = `<div class="c13-error-state"><strong>Failover not verified</strong><p>${esc(error.message)}</p><p>Check current replica-set status before retrying.</p></div>`; })
        .finally(() => { if (action) action.disabled = false; });
    }
    if (button.hasAttribute('data-c13-kubernetes-start')) {
      if (!window.confirm('Deploy the configured Kubernetes resources? This applies its namespace, Secrets, Service, StatefulSet and initialization Job to the currently selected Kubernetes context.')) return;
      const action = button;
      action.disabled = true;
      const resultBox = $('[data-c13-kubernetes-result]');
      if (resultBox) resultBox.innerHTML = '<p class="loading-state">Checking prerequisites and requesting the deployment?</p>';
      get('/api/services/lab13/kubernetes', { method: 'POST', body: JSON.stringify({ confirm: true }) })
        .then(async data => {
          await refresh();
          const updatedResultBox = $('[data-c13-kubernetes-result]');
          if (updatedResultBox) updatedResultBox.innerHTML = `<p>Deployment job ${esc(data.job_id)}: ${esc(data.status)}. Refresh pod status to see the observed result.</p>`;
        })
        .catch(error => { if (resultBox) resultBox.innerHTML = `<div class="c13-error-state"><strong>Deployment did not start</strong><p>${esc(error.message)}</p></div>`; })
        .finally(() => { action.disabled = false; });
    }
    if (button.hasAttribute('data-labs-metrics') && currentChapter === '13') loadChapter13Metrics();
    else if (button.hasAttribute('data-labs-metrics')) showApiResult('/api/metrics', (data, target) => {
      const history = data.history || [];
      const hasCurrentHitRatio = data.hit_ratio_interval !== null && data.hit_ratio_interval !== undefined && data.hit_ratio_interval !== '' && Number.isFinite(Number(data.hit_ratio_interval));
      const currentHitRatio = hasCurrentHitRatio ? `${(Number(data.hit_ratio_interval) * 100).toFixed(1)}%` : 'Not sampled';
      target.innerHTML = `<div class="labs-metric-row"><article><span>WiredTiger cache hit ratio</span><strong>${esc(currentHitRatio)}</strong></article><article><span>Bytes in cache</span><strong>${value(data.bytes_in_cache)}</strong></article><article><span>Cache capacity</span><strong>${value(data.max_bytes)}</strong></article><article><span>Sample interval</span><strong>${value(data.sampling_interval_seconds, ' seconds')}</strong></article></div>${hitRatioChart(history)}${history.length ? dataTable(['Time','Hit ratio','Bytes in cache','Maximum bytes'], history.slice(-20).reverse().map(point => [point.timestamp, point.hit_ratio !== null && point.hit_ratio !== undefined && point.hit_ratio !== '' && Number.isFinite(Number(point.hit_ratio)) ? `${(Number(point.hit_ratio) * 100).toFixed(1)}%` : 'Not sampled', point.bytes_in_cache, point.max_bytes]), 'No recorded cache samples.') : '<p>No in-process cache history is available yet.</p>'}`;
    });
    if (button.hasAttribute('data-labs-warnings')) showApiResult('/api/operations/events?status=warning&limit=50', (data, target) => target.innerHTML = dataTable(['Time','Component','Status','Duration','Action'], (data.events || []).map(row => [row.timestamp, row.component, row.status, value(row.duration_ms, ' ms'), row.summary || row.description || row.action]), 'No warning events were returned.'));
    if (button.hasAttribute('data-labs-audit')) showApiResult('/api/operations/events?filter=all&limit=50', (data, target) => target.innerHTML = dataTable(['Time','Event','Action','Target','Outcome'], (data.events || []).filter(row => row.event_type === 'admin_audit' || row.action).map(row => [row.timestamp, row.event_type || 'Administrator action', row.action, row.target_record, row.outcome]), 'No administrator audit events were returned.'));
    if (button.hasAttribute('data-ch16-audit')) showApiResult('/api/ops/audit?limit=100', (data, target) => target.innerHTML = `<p>Source: ${esc(data.source)} · Cassandra audit delivery: ${esc(data.chapter16_cassandra)}</p>${dataTable(['Time','Action','Target','Outcome','Actor role'], (data.events || []).map(row => [row.created_at, row.action, row.target_record, row.outcome, row.actor_role]), 'No audit records were returned.')}`);
    if (button.hasAttribute('data-ch16-audit-summary')) showApiResult('/api/ops/audit/summary', (data, target) => target.innerHTML = `<pre>${esc(JSON.stringify(data, null, 2))}</pre>`);
    if (button.dataset.ch16Service) {
      const target = $('[data-labs-detail]');
      try { await get(`/api/services/lab16/${button.dataset.ch16Service}`, {method:'POST'}); if (target) target.innerHTML = '<p>Service control request accepted.</p>'; }
      catch (error) { if (target) target.innerHTML = `<p class="labs-message is-error">${esc(error.message)} The service was not changed.</p>`; }
    }
    if (button.hasAttribute('data-case-organizations')) loadCaseWorkspace(2);
    if (button.hasAttribute('data-case-reload')) {
      const status = $('[data-case-filter]')?.value || '';
      showApiResult(`/api/cases${status ? `?status=${encodeURIComponent(status)}` : ''}`, (data, target) => target.innerHTML = caseTable(data.cases || []));
    }
    if (button.hasAttribute('data-case-members')) {
      const orgId = $('[data-member-add] [name="organization_id"]')?.value;
      if (!orgId) { $('[data-case-members-list]').textContent = 'Select an organisation first.'; return; }
      showApiResult(`/api/cases/org/${encodeURIComponent(orgId)}/members`, (data, target) => target.innerHTML = dataTable(['Name','Email','Role','Added'], (data.members || []).map(member => [member.name || member.user_id, member.email, member.role, member.created_at]), 'No organisation members found.'));
    }
    if (button.dataset.caseDetail) showApiResult(`/api/cases/${encodeURIComponent(button.dataset.caseDetail)}`, (data, target) => {
      target.innerHTML = `<article class="labs-case-detail"><h4>${esc(data.title)}</h4><p>Status: ${esc(data.status)} · Assigned: ${esc(data.assigned_to || 'Unassigned')}</p><p>${esc(data.description || 'No description.')}</p><p>Linked threat IDs: ${esc((data.linked_threat_ids || []).join(', ') || 'None')}</p><form class="labs-form" data-case-update><input type="hidden" name="case_id" value="${esc(data.case_id)}"><label>Status<select name="status"><option value="open" ${data.status === 'open' ? 'selected' : ''}>Open</option><option value="in_progress" ${data.status === 'in_progress' ? 'selected' : ''}>In progress</option><option value="closed" ${data.status === 'closed' ? 'selected' : ''}>Closed</option></select></label><label>Assign to organisation member<input name="assigned_to" maxlength="64" value="${esc(data.assigned_to || '')}"></label><button class="xs-btn" type="submit">Save case</button></form></article>`;
    });
  });
  root.addEventListener('change', event => {
    if (event.target.matches('[data-c13-window]')) loadChapter13Metrics();
    if (event.target.matches('[data-c14-window]')) c14LoadMetrics(Number(event.target.value));
    if (event.target.matches('[data-c16-days]')) c16LoadAuditSummary();
  });
  root.addEventListener('submit', async event => {
    const form = event.target;
    if (form.matches('[data-c16-audit-filter]')) { event.preventDefault(); await c16LoadAudit(); return; }
    if (form.matches('[data-ch16-fraud]')) {
      event.preventDefault();
      const button = form.querySelector('button[type="submit"]');
      button.disabled = true;
      const target = $('[data-ch16-results]');
      target.innerHTML = '<p class="loading-state">Querying configured evidence sources…</p>';
      try {
        const values=Object.fromEntries(new FormData(form));
        const query=new URLSearchParams({max_hops:values.max_hops||'3'}); if(values.transaction_id)query.set('txn_id',values.transaction_id);
        const result = await get(`/api/moderation/fraud/${encodeURIComponent(values.account_id)}?${query}`);
        const sources = Object.entries(result.databases || {}).map(([name, evidence]) => `<article class="labs-evidence"><h4>${esc(name)} · ${esc(evidence.status)}</h4>${evidence.reason ? `<p>${esc(evidence.reason)}</p>` : ''}${evidence.query_ms !== undefined ? `<p>Query time: ${esc(evidence.query_ms)} ms</p>` : ''}<pre>${esc(JSON.stringify(evidence.transaction ?? evidence.timeline ?? evidence.transfer_paths ?? evidence, null, 2))}</pre></article>`).join('');
        target.innerHTML = `<p>Status: ${esc(result.status)} · unavailable sources: ${esc((result.unavailable_backends || []).join(', ') || 'none')}</p><p>${esc(result.evidence_note || '')}</p><div class="labs-evidence-grid">${sources}</div>`;
      } catch (error) { target.innerHTML = `<p class="labs-message is-error">${esc(error.message)}</p>`; }
      finally { button.disabled = false; }
      return;
    }
    if (!form.matches('[data-case-create],[data-org-create],[data-member-add],[data-case-update]')) return;
    event.preventDefault();
    const button = form.querySelector('button[type="submit"]');
    if (button) button.disabled = true;
    try {
      const values = Object.fromEntries(new FormData(form));
      let path = '/api/cases', options = { method: 'POST' };
      if (form.matches('[data-case-create]')) {
        values.linked_threat_ids = values.linked_threat_ids.split(',').map(value => value.trim()).filter(Boolean);
        delete values.linked_threat_ids_input;
      } else if (form.matches('[data-org-create]')) path = '/api/cases/organizations';
      else if (form.matches('[data-member-add]')) path = `/api/cases/org/${encodeURIComponent(values.organization_id)}/members`;
      else { path = `/api/cases/${encodeURIComponent(values.case_id)}`; options.method = 'PATCH'; if (!values.assigned_to) delete values.assigned_to; }
      if (options.method !== 'PATCH') options.body = JSON.stringify(values);
      else options.body = JSON.stringify({ status: values.status, ...(values.assigned_to ? { assigned_to: values.assigned_to } : {}) });
      const result = await get(path, options);
      setMessage(form.matches('[data-org-create]') ? `Organisation created: ${result.name}.` : form.matches('[data-member-add]') ? `Membership saved for ${result.user_id}.` : form.matches('[data-case-update]') ? 'Case updated.' : `Case opened: ${result.case_id}.`);
      if (currentChapter === '15') {
        if (form.matches('[data-org-create]')) { form.reset(); await c15LoadOrganizations(); }
        else if (form.matches('[data-member-add]')) { form.reset(); await c15LoadMembers(); }
        else { form.reset(); await c15LoadCases(); }
      } else if (form.matches('[data-org-create]')) { form.reset(); await refresh(); await loadCaseWorkspace(2); }
      else if (form.matches('[data-member-add]')) await loadCaseWorkspace(3);
      else if (form.matches('[data-case-update]')) await loadCaseWorkspace(1);
      else { form.reset(); await loadCaseWorkspace(1); }
    } catch (error) { setMessage(rateMessage(error), true); }
    finally { if (button) button.disabled = false; }
  });
  root.addEventListener('click', event => {
    if (event.target.closest('a[data-labs-project]')) { event.preventDefault(); mountProject(event.target.closest('a').dataset.labsProject); }
  });
})();
