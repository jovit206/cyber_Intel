(() => {
  const root = document.getElementById('labs-operations');
  if (!root) return;
  const $ = selector => root.querySelector(selector);
  const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]));
  const chapters = [
    { id: '13', title: 'Containers & Cloud', tech: 'Docker · Kubernetes · MongoDB Atlas', description: 'Explore container deployment, replica set operations, failover, monitoring, and cloud infrastructure.', endpoint: '/api/lab8/status' },
    { id: '14', title: 'Monitoring & Backup', tech: 'Metrics · Alerts · Recovery', description: 'Review live database health, monitoring signals, backup workflows, and recovery verification.', endpoint: '/api/operations/overview' },
    { id: '15', title: 'Multi-Tenant Platform', tech: 'Organisations · Cases · Access', description: 'Understand organisation-scoped access, memberships, and case workflows.', endpoint: '/api/cases/overview' },
    { id: '16', title: 'Cassandra & Neo4j', tech: 'Distributed storage · Graphs', description: 'Explore distributed database operations, audit evidence, and graph-based investigations.', endpoint: '/api/chapter16/status' },
    { id: '17', title: 'Vector Search & RAG', tech: 'MongoDB Vector Search · Embeddings', description: 'Index threat reports, ask source-grounded questions, search semantically, and run vector benchmarks.', endpoint: '/api/rag/status', project: 'rag' },
    { id: '18', title: 'Comparative Database Capstone', tech: 'MongoDB · Cassandra · Neo4j', description: 'Compare database capabilities and investigate threat intelligence using the integrated Chapter 18 workspace.', endpoint: '/api/chapter18/status', project: 'chapter18' },
  ];
  const tabs = {
    '13': ['Overview', 'Replica Set', 'Failover', 'Monitoring', 'Kubernetes', 'MongoDB Atlas'],
    '14': ['Overview', 'Live Metrics', 'Alerts', 'Backups', 'Recovery Verification'],
    '15': ['Overview', 'Cases', 'Organisations', 'Members', 'Rate Limits'],
    '16': ['Overview', 'Service Management', 'Audit Log', 'Fraud Review', 'Graph Investigation'],
    '17': ['Overview', 'Ask CTI Verify', 'Document Index', 'Vector Search', 'Benchmarking', 'History'],
    '18': ['Overview', 'Database Comparison', 'Threat Intelligence', 'False-Positive Detection', 'Benchmarks', 'Threat Communities', 'Comparative Report', 'Settings'],
  };
  let operationData = null, currentChapter = null, currentTab = 0;

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
  function checkLabel(result) {
    if (!result) return ['Not configured', 'neutral'];
    if (result.error) return ['Unavailable', 'bad'];
    if (result.data?.healthy === false || result.data?.summary?.overall_health === 'degraded') return ['Degraded', 'warn'];
    if (result.data?.summary?.overall_health === 'healthy' || result.data?.healthy === true) return ['Healthy', 'good'];
    const status = String(result.data?.status || result.data?.state || result.data?.connection_status || 'API available').toLowerCase();
    if (['healthy', 'online', 'ready', 'connected', 'available', 'ok'].includes(status)) return ['Online', 'good'];
    if (['degraded', 'warning', 'partial'].includes(status)) return ['Degraded', 'warn'];
    if (['unavailable', 'offline', 'failed'].includes(status)) return ['Unavailable', 'bad'];
    if (status === 'not_configured') return ['Not configured', 'neutral'];
    if (status === 'api available') return ['API available', 'good'];
    return [status[0].toUpperCase() + status.slice(1), 'neutral'];
  }
  async function refresh() {
    setMessage('');
    const checks = await Promise.all(chapters.filter(item => item.endpoint).map(async item => {
      try { return [item.id, { data: await get(item.endpoint) }]; }
      catch (error) { return [item.id, { error: error.message }]; }
    }));
    const results = Object.fromEntries(checks);
    operationData = results['14']?.data || null;
    const online = Object.values(results).filter(result => checkLabel(result)[1] === 'good').length;
    const activity = operationData?.summary?.total_operations;
    $('[data-labs-summary]').innerHTML = [
      ['Integrated checks', String(checks.length), 'Registered live APIs'],
      ['Online', String(online), 'Healthy API responses'],
      ['Unavailable', String(checks.length - online), 'Includes failed checks'],
      ['Recorded operations', activity === undefined ? 'Not available' : Number(activity).toLocaleString(), 'Since instrumentation started'],
    ].map(([label, value, note]) => `<article><span>${esc(label)}</span><strong>${esc(value)}</strong><small>${esc(note)}</small></article>`).join('');
    $('[data-labs-cards]').innerHTML = chapters.map(item => {
      const [status, tone] = checkLabel(results[item.id]);
      const points = item.id === '13' ? (results['13']?.data?.member_count ?? null) : item.id === '15' ? (results['15']?.data?.organization_count ?? null) : item.id === '16' ? Object.values(results['16']?.data?.databases || {}).filter(db => db.status === 'connected').length : item.id === '17' ? (results['17']?.data?.documents?.embedded_chunks ?? null) : item.id === '18' ? (results['18']?.data?.databases ? Object.keys(results['18'].data.databases).length : null) : null;
      const pointsLabel = item.id === '13' ? 'Replica set members' : item.id === '15' ? 'Organisations' : item.id === '16' ? 'Connected databases' : item.id === '17' ? 'Embedded chunks' : 'Database checks';
      return `<article class="labs-card"><div class="labs-card-top"><span class="labs-number">CHAPTER ${item.id}</span><span class="labs-status ${tone}">${esc(status)}</span></div><h3>${esc(item.title)}</h3><p>${esc(item.description)}</p><div class="labs-tech">${esc(item.tech)}</div><div class="labs-card-meta"><span>${points === null ? 'Live components: not reported' : `${pointsLabel}: ${Number(points).toLocaleString()}`}</span><span>${item.endpoint ? 'Endpoint: checked now' : 'Health: no integrated endpoint'}</span></div><button type="button" class="xs-btn" data-labs-open="${item.id}">Open Workspace <span aria-hidden="true">→</span></button></article>`;
    }).join('');
    root.dataset.checks = JSON.stringify(Object.fromEntries(checks.map(([id, value]) => [id, value.error ? null : value.data])));
  }
  function unavailable(text) {
    return `<div class="labs-empty"><span class="labs-status neutral">Not configured in this application</span><h3>Live service integration unavailable</h3><p>${esc(text)}</p><p>The corresponding chapter project files may exist separately, but this application has no registered API for this operation. No sample values or simulated status are shown here.</p></div>`;
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
      return `<div class="labs-explainer"><h3>What this module does</h3><p>Stores threat records in MongoDB and checks optional Cassandra and Neo4j integrations for cross-database investigations and measured query benchmarks.</p><div class="labs-db-grid">${Object.entries(dbs).map(([name, status]) => `<article><strong>${esc(name)}</strong><span class="labs-status ${String(status.status).toLowerCase() === 'connected' || String(status.status).toLowerCase() === 'available' ? 'good' : 'neutral'}">${esc(status.status || 'Not reported')}</span>${status.latency_ms !== undefined ? `<small>${esc(status.latency_ms)} ms response</small>` : ''}</article>`).join('') || '<p>No database health details returned.</p>'}</div><button type="button" class="xs-btn" data-labs-project="chapter18">Open comparative intelligence workspace</button></div>`;
    }
    if (chapter.id === '14') return `<div class="labs-explainer"><h3>What this module does</h3><p>Tracks real database and application operations instrumented by this deployment.</p>${operationData ? `<p>Overall health: ${esc(operationData.summary?.overall_health || 'Not reported')} · retained operations: ${value(operationData.summary?.total_operations)} · failed: ${value(operationData.summary?.failed_operations)} · warnings: ${value(operationData.summary?.warning_operations)}.</p><p>${esc(operationData.tracking?.historical_note || '')}</p>` : unavailable('The authenticated operations API did not return data.')}</div>`;
    if (chapter.id === '13') return `<div class="labs-explainer"><h3>What this module does</h3><p>Container and cloud deployment concepts: replica sets, failover, monitoring, Kubernetes, and Atlas.</p>${data ? replicaSet(data) : unavailable('The authenticated replica set API did not return live status.')}<p class="labs-data-note">This is the MongoDB replica set used by this application. Kubernetes and Atlas are separate deployments and are not inferred from MongoDB connectivity.</p></div>`;
    if (chapter.id === '15') return `<div class="labs-explainer"><h3>What this module does</h3><p>Manages cases under organisation memberships. Case details are scoped by membership; the organisation summary returns counts and organisation metadata only.</p>${data ? `<div class="labs-metric-row"><article><span>Organisations</span><strong>${value(data.organization_count)}</strong></article><article><span>Members</span><strong>${value(data.member_count)}</strong></article><article><span>Cases</span><strong>${value(data.case_count)}</strong></article><article><span>Rate limit refusals</span><strong>${value(data.rate_limit_refusals)}</strong></article></div><p>${esc(data.privacy_note || '')}</p>` : unavailable('Organisation analytics are not available.')}</div>`;
    if (chapter.id === '16') {
      const databases = data?.databases || {};
      const labels = { connected: 'good', unavailable: 'bad', not_configured: 'neutral' };
      return `<div class="labs-explainer"><h3>Chapter 16 database health</h3><p>Read-only checks use the separately configured Chapter 16 databases. Credentials are never returned to the browser.</p><div class="labs-db-grid">${Object.entries(databases).map(([name, result]) => `<article><strong>${esc(name)}</strong><span class="labs-status ${labels[result.status] || 'neutral'}">${esc(result.status || 'Not reported')}</span>${result.reason ? `<small>${esc(result.reason)}</small>` : ''}${result.error_type ? `<small>${esc(result.error_type)}</small>` : ''}${result.latency_ms !== undefined ? `<small>${esc(result.latency_ms)} ms</small>` : ''}</article>`).join('') || '<p>Health checks are unavailable.</p>'}</div><p>Circuit breakers: ${Object.entries(data?.circuit_breakers || {}).map(([name, state]) => `${esc(name)} ${esc(state)}`).join(' · ') || 'Not reported'}.</p></div>`;
    }
    if (chapter.id === '15') return `<div class="labs-explainer"><h3>What this module does</h3><p>Organisation-scoped case workflows and membership permissions.</p>${unavailable('No integrated cases or organisations API is registered in this application. Use the separate Chapter 15 project if it is deployed independently.')}</div>`;
    return `<div class="labs-explainer"><h3>What this module does</h3><p>Combines Cassandra distributed storage concepts with Neo4j graph investigation for audit and fraud review.</p>${unavailable('No Chapter 16 service-management, audit, or fraud endpoints are registered in this application. Chapter 18 optional database status does not imply Chapter 16 services are running.')}</div>`;
  }
  function renderTab() {
    const item = chapters.find(chapter => chapter.id === currentChapter);
    const content = $('[data-labs-content]');
    const tabName = tabs[item.id][currentTab];
    $('[data-labs-tabs]').innerHTML = tabs[item.id].map((label, index) => `<button type="button" class="labs-chapter-tab${index === currentTab ? ' is-active' : ''}" aria-current="${index === currentTab ? 'page' : 'false'}" data-labs-tab="${index}">${esc(label)}</button>`).join('');
    if (currentTab === 0) content.innerHTML = overview(item);
    else if (item.id === '13') {
      const data = JSON.parse(root.dataset.checks || '{}')['13'];
      if (currentTab === 1) content.innerHTML = data ? replicaSet(data) : unavailable('Live replica-set status is not available.');
      else if (currentTab === 2) content.innerHTML = `<div class="labs-section-intro"><h3>Controlled replica set failover</h3><p>Steps down the current primary for an election interval. Clients may briefly see interrupted reads and writes.</p><label>Stepdown duration <input type="number" min="5" max="120" value="60" data-labs-seconds> seconds</label> <button type="button" class="xs-btn" data-labs-stepdown>Test failover</button><div data-labs-detail aria-live="polite"></div></div>`;
      else if (currentTab === 3) content.innerHTML = '<div class="labs-section-intro"><h3>Live monitoring</h3><p>Reads actual WiredTiger cache measurements and replica member heartbeat values.</p><button type="button" class="xs-btn secondary" data-labs-metrics>Load metrics</button><div data-labs-detail></div></div>';
      else content.innerHTML = `<div class="labs-section-intro"><h3>${esc(tabName)}</h3><p>Deployment artifacts are in <code>chapter-13/</code>.</p></div>${unavailable(`${tabName} status requires a configured deployment API; this application cannot infer it from the replica set.`)}`;
    } else if (item.id === '14') {
      if (currentTab === 1) content.innerHTML = '<div class="labs-section-intro"><h3>Live database and operation metrics</h3><p>Data comes from the existing MongoDB serverStatus and operations APIs.</p><button type="button" class="xs-btn secondary" data-labs-metrics>Load metrics</button><div data-labs-detail></div></div>';
      else if (currentTab === 2) content.innerHTML = '<div class="labs-section-intro"><h3>Recorded warning events</h3><p>These are recorded system-operation warnings. This app has no separate threshold-alert acknowledgement service.</p><button type="button" class="xs-btn secondary" data-labs-warnings>Load warnings</button><div data-labs-detail></div></div>';
      else content.innerHTML = `<div class="labs-section-intro"><h3>${esc(tabName)}</h3></div>${unavailable('Backup, restore verification, RPO/RTO, and moderator alert acknowledgement services are not registered in this application.')}`;
    } else if (item.id === '15') {
      if (currentTab === 1) content.innerHTML = `<div class="labs-section-intro"><h3>Cases</h3><form class="labs-form" data-case-create><label>Organisation<select name="organization_id" required data-org-options></select></label><label>Case title<input name="title" maxlength="180" required></label><label>Private case description<textarea name="description" maxlength="8000"></textarea></label><label>Linked threat IDs, comma separated<input name="linked_threat_ids" maxlength="2000"></label><button class="xs-btn" type="submit">Open case</button></form><div data-case-list></div><div data-case-detail></div></div>`;
      else if (currentTab === 2) content.innerHTML = `<div class="labs-section-intro"><h3>Organisations</h3><p>Only organisation names, descriptions and counts appear here. Case descriptions remain private to case details.</p><form class="labs-form" data-org-create><label>Name<input name="name" maxlength="120" required></label><label>Description<input name="description" maxlength="500"></label><button class="xs-btn" type="submit">Create organisation</button></form><button class="xs-btn secondary" type="button" data-case-organizations>Refresh organisations</button><div data-case-organizations-list></div></div>`;
      else if (currentTab === 3) content.innerHTML = `<div class="labs-section-intro"><h3>Organisation members</h3><form class="labs-form" data-member-add><label>Organisation<select name="organization_id" required data-org-options></select></label><label>Platform user ID<input name="user_id" maxlength="64" required></label><label>Organisation role<select name="role"><option value="analyst">Analyst</option><option value="viewer">Viewer</option><option value="org_admin">Organisation admin</option></select></label><button class="xs-btn" type="submit">Add or update membership</button></form><button type="button" class="xs-btn secondary" data-case-members>Load members</button><div data-case-members-list></div></div>`;
      else content.innerHTML = `<div class="labs-section-intro"><h3>Rate limits</h3><p>Case APIs allow up to 120 requests per organisation and user per rolling minute. A 429 response includes a Retry-After header.</p><div data-case-rate-summary></div></div>`;
      loadCaseWorkspace(currentTab);
    } else if (item.id === '16' && currentTab === 1) {
      const data = JSON.parse(root.dataset.checks || '{}')['16'];
      const service = data?.service_control || {};
      content.innerHTML = `<div class="labs-section-intro"><h3>Database service management</h3><p>Health and circuit state are read-only. Starting or stopping services requires an orchestrator that is not configured for this deployment.</p><p>Service control: <span class="labs-status neutral">${esc(service.status || 'Not configured')}</span> ${esc(service.reason || '')}</p><button class="xs-btn secondary" type="button" data-labs-refresh>Refresh health</button><div data-labs-detail></div></div>`;
    } else if (item.id === '16' && currentTab === 2) {
      content.innerHTML = '<div class="labs-section-intro"><h3>Platform administrator audit events</h3><p>These events come from the existing platform operations log; they are not Cassandra records or Chapter 16 service audits.</p><button type="button" class="xs-btn secondary" data-labs-audit>Load audit events</button><div data-labs-detail></div></div>';
    } else if (item.id === '16' && (currentTab === 3 || currentTab === 4)) {
      const graph = currentTab === 4;
      content.innerHTML = `<div class="labs-section-intro"><h3>${graph ? 'Graph investigation' : 'Fraud evidence review'}</h3><p>Queries actual Chapter 16 MongoDB transaction, Cassandra timeline, and Neo4j transfer-path data when configured. Results remain separate evidence sources and do not assert a verdict.</p><form class="labs-form" data-ch16-fraud><label>Account / user ID<input name="account_id" maxlength="128" required></label><label>Transaction ID<input name="transaction_id" maxlength="128" required></label><label>Maximum graph hops<select name="max_hops"><option>1</option><option>2</option><option selected>3</option></select></label><button class="xs-btn" type="submit">${graph ? 'Investigate graph and transaction' : 'Review fraud evidence'}</button></form><div data-ch16-results></div></div>`;
    } else content.innerHTML = item.id === '17' ? `<div class="labs-embed-note"><strong>${esc(tabName)}</strong><p>Interactive operations use the existing authenticated Chapter 17 API workspace.</p></div><div id="labs-vector-workspace" data-vector-project="rag"></div>` : item.id === '18' ? `<div class="labs-embed-note"><strong>${esc(tabName)}</strong><p>Interactive operations use the existing authenticated Chapter 18 API workspace. The workspace retains import, investigation, analyst review, benchmark, and report actions.</p></div><div id="labs-c18-workspace" data-vector-project="chapter18"></div>` : `<div class="labs-section-intro"><h3>${esc(tabName)}</h3><p>${esc(item.description)}</p></div>${unavailable(`${tabName} has no integrated API in this application.`)}`;
    setMessage('');
    if (currentTab > 0 && item.project) mountProject(item.project);
  }
  function mountProject(project) {
    const target = project === 'rag' ? $('#labs-vector-workspace') : $('#labs-c18-workspace');
    if (!target) return;
    document.dispatchEvent(new CustomEvent('portfolio:project-opened', { detail: { id: project, root: target } }));
  }
  function openChapter(id) {
    const item = chapters.find(chapter => chapter.id === id);
    if (!item) return;
    currentChapter = id;
    currentTab = 0;
    $('[data-labs-hub]').hidden = true;
    $('[data-labs-workspace]').hidden = false;
    $('[data-labs-heading]').innerHTML = `<span class="eyebrow">CHAPTER ${item.id} · ${esc(item.tech)}</span><h2>${esc(item.title)}</h2><p>${esc(item.description)}</p>`;
    renderTab();
  }
  root.addEventListener('click', event => {
    const button = event.target.closest('button');
    if (!button) return;
    if (button.hasAttribute('data-labs-refresh')) refresh();
    if (button.dataset.labsOpen) openChapter(button.dataset.labsOpen);
    if (button.hasAttribute('data-labs-back')) { $('[data-labs-workspace]').hidden = true; $('[data-labs-hub]').hidden = false; }
    if (button.dataset.labsTab !== undefined) { currentTab = Number(button.dataset.labsTab); renderTab(); }
    if (button.dataset.labsProject) mountProject(button.dataset.labsProject);
    if (button.hasAttribute('data-labs-stepdown')) {
      const seconds = Number($('[data-labs-seconds]').value);
      if (!Number.isInteger(seconds) || seconds < 5 || seconds > 120) { setMessage('Enter a stepdown duration between 5 and 120 seconds.', true); return; }
      const primary = JSON.parse(root.dataset.checks || '{}')['13']?.primary?.name || 'the current MongoDB primary';
      if (!window.confirm(`Step down ${primary} for ${seconds} seconds? Clients may briefly experience interrupted operations.`)) return;
      const target = $('[data-labs-detail]');
      target.innerHTML = '<p class="loading-state">Requesting a real replica set stepdown…</p>';
      fetch('/api/lab8/stepdown', { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ confirm: true, seconds }) })
        .then(async response => { const data = await response.json().catch(() => ({})); if (!response.ok) throw new Error(data.detail || `Request failed (${response.status})`); target.innerHTML = `<h4>Actual API response</h4><pre>${esc(JSON.stringify(data, null, 2))}</pre>`; })
        .catch(error => { target.innerHTML = `<p class="labs-message is-error">${esc(error.message)}</p>`; });
    }
    if (button.hasAttribute('data-labs-metrics')) showApiResult('/api/metrics', (data, target) => {
      const history = data.history || [];
      const hasCurrentHitRatio = data.hit_ratio_interval !== null && data.hit_ratio_interval !== undefined && data.hit_ratio_interval !== '' && Number.isFinite(Number(data.hit_ratio_interval));
      const currentHitRatio = hasCurrentHitRatio ? `${(Number(data.hit_ratio_interval) * 100).toFixed(1)}%` : 'Not sampled';
      target.innerHTML = `<div class="labs-metric-row"><article><span>WiredTiger cache hit ratio</span><strong>${esc(currentHitRatio)}</strong></article><article><span>Bytes in cache</span><strong>${value(data.bytes_in_cache)}</strong></article><article><span>Cache capacity</span><strong>${value(data.max_bytes)}</strong></article><article><span>Sample interval</span><strong>${value(data.sampling_interval_seconds, ' seconds')}</strong></article></div>${hitRatioChart(history)}${history.length ? dataTable(['Time','Hit ratio','Bytes in cache','Maximum bytes'], history.slice(-20).reverse().map(point => [point.timestamp, point.hit_ratio !== null && point.hit_ratio !== undefined && point.hit_ratio !== '' && Number.isFinite(Number(point.hit_ratio)) ? `${(Number(point.hit_ratio) * 100).toFixed(1)}%` : 'Not sampled', point.bytes_in_cache, point.max_bytes]), 'No recorded cache samples.') : '<p>No in-process cache history is available yet.</p>'}`;
    });
    if (button.hasAttribute('data-labs-warnings')) showApiResult('/api/operations/events?status=warning&limit=50', (data, target) => target.innerHTML = dataTable(['Time','Component','Status','Duration','Action'], (data.events || []).map(row => [row.timestamp, row.component, row.status, value(row.duration_ms, ' ms'), row.summary || row.description || row.action]), 'No warning events were returned.'));
    if (button.hasAttribute('data-labs-audit')) showApiResult('/api/operations/events?filter=all&limit=50', (data, target) => target.innerHTML = dataTable(['Time','Event','Action','Target','Outcome'], (data.events || []).filter(row => row.event_type === 'admin_audit' || row.action).map(row => [row.timestamp, row.event_type || 'Administrator action', row.action, row.target_record, row.outcome]), 'No administrator audit events were returned.'));
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
  root.addEventListener('submit', async event => {
    const form = event.target;
    if (form.matches('[data-ch16-fraud]')) {
      event.preventDefault();
      const button = form.querySelector('button[type="submit"]');
      button.disabled = true;
      const target = $('[data-ch16-results]');
      target.innerHTML = '<p class="loading-state">Querying configured evidence sources…</p>';
      try {
        const result = await get('/api/chapter16/fraud-review', { method: 'POST', body: JSON.stringify(Object.fromEntries(new FormData(form))) });
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
      if (form.matches('[data-org-create]')) { form.reset(); await refresh(); await loadCaseWorkspace(2); }
      else if (form.matches('[data-member-add]')) await loadCaseWorkspace(3);
      else if (form.matches('[data-case-update]')) await loadCaseWorkspace(1);
      else { form.reset(); await loadCaseWorkspace(1); }
    } catch (error) { setMessage(rateMessage(error), true); }
    finally { if (button) button.disabled = false; }
  });
  root.addEventListener('click', event => {
    if (event.target.closest('a[data-labs-project]')) { event.preventDefault(); mountProject(event.target.closest('a').dataset.labsProject); }
  });
  window.loadLabsDashboard = refresh;
})();
