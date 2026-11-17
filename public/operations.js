document.addEventListener('DOMContentLoaded', () => {
  const byId = id => document.getElementById(id);
  const dashboard = byId('operations-dashboard');
  if (!dashboard) return;

  const tabButtons = [...dashboard.querySelectorAll('[data-ops-tab]')];
  const panels = [...dashboard.querySelectorAll('[data-ops-panel]')];
  let overview = null;
  let events = [];
  let eventPage = 1;
  let eventTotal = 0;
  let failureFilter = 'all';
  let loaded = false;
  let searchTimer = null;

  const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g, char => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[char]));
  const localDate = value => {
    if (!value) return 'Not recorded';
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? 'Unknown time' : new Intl.DateTimeFormat(undefined, {
      dateStyle: 'medium', timeStyle: 'medium',
    }).format(date);
  };
  const duration = value => Number.isFinite(value) ? `${value.toFixed(value < 10 ? 2 : 1)} ms` : 'Not measured';
  const statusClass = status => ({
    healthy: 'is-healthy', success: 'status-success', failed: 'status-failed',
    warning: 'status-warning', degraded: 'is-degraded', unavailable: 'is-unavailable',
    unknown: 'is-unknown', not_monitored: 'is-not-monitored',
  }[status] || 'is-unknown');
  const statusLabel = status => ({
    healthy: 'Healthy', success: 'Successful', failed: 'Failed', warning: 'Warning',
    degraded: 'Degraded', unavailable: 'Unavailable', unknown: 'Unknown',
    running: 'Running', not_monitored: 'Not monitored', not_recorded: 'Not recorded',
  }[status] || 'Unknown');

  function setMessage(id, message, warning = false) {
    const node = byId(id);
    if (!node) return;
    node.textContent = message;
    node.classList.toggle('is-warning', warning);
  }

  function activateTab(name) {
    tabButtons.forEach(button => {
      const active = button.dataset.opsTab === name;
      button.classList.toggle('is-active', active);
      button.setAttribute('aria-selected', String(active));
      button.tabIndex = active ? 0 : -1;
    });
    panels.forEach(panel => {
      panel.hidden = panel.dataset.opsPanel !== name;
    });
    if (name === 'activity') loadEvents(1);
    if (name === 'timeline') renderTimeline();
    if (name === 'failures') loadFailures();
    if (name === 'analytics') renderAnalytics();
  }

  tabButtons.forEach((button, index) => {
    button.addEventListener('click', () => activateTab(button.dataset.opsTab));
    button.addEventListener('keydown', event => {
      if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
      event.preventDefault();
      const next = event.key === 'Home' ? 0
        : event.key === 'End' ? tabButtons.length - 1
          : (index + (event.key === 'ArrowRight' ? 1 : -1) + tabButtons.length) % tabButtons.length;
      tabButtons[next].focus();
      activateTab(tabButtons[next].dataset.opsTab);
    });
  });

  dashboard.querySelectorAll('[data-open-ops-tab]').forEach(button => {
    button.addEventListener('click', () => activateTab(button.dataset.openOpsTab));
  });

  async function getJson(url) {
    const response = await fetch(url, { credentials: 'same-origin', headers: { Accept: 'application/json' } });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      const detail = typeof data.detail === 'string' ? data.detail : 'System operations data could not be loaded.';
      throw new Error(detail);
    }
    return data;
  }

  async function loadDashboard() {
    byId('ops-refresh').disabled = true;
    try {
      const data = await getJson('/api/operations/overview');
      overview = data;
      loaded = true;
      renderOverview();
      renderHealth();
      renderFlow();
      renderInfrastructure();
      renderAnalytics();
      await loadEvents(1);
    } catch (error) {
      setMessage('ops-tracking-note', error.message, true);
      byId('ops-summary-cards').innerHTML = '<p class="ops-empty">Live system metrics are not available. Sign in with an administrator account and retry.</p>';
      byId('ops-recent-activity').innerHTML = '<p class="ops-empty">Recorded activity is unavailable.</p>';
      byId('ops-attention-list').innerHTML = '<p class="ops-empty">Component checks are unavailable.</p>';
      byId('ops-overall-status').textContent = 'Health unavailable';
      byId('ops-overall-status').className = 'ops-status-badge is-unknown';
    } finally {
      byId('ops-refresh').disabled = false;
    }
  }

  function sparkline(values, color) {
    if (!values.length) return '';
    const width = 78;
    const height = 26;
    const max = Math.max(1, ...values);
    const points = values.map((value, index) => {
      const x = values.length === 1 ? width / 2 : 2 + index * (width - 4) / (values.length - 1);
      const y = height - 3 - (Math.max(0, value) / max) * (height - 7);
      return `${x},${y}`;
    }).join(' ');
    return `<svg class="ops-sparkline" viewBox="0 0 ${width} ${height}" role="img" aria-label="Recent recorded trend"><polyline points="${points}" fill="none" stroke="${color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"></polyline></svg>`;
  }

  function renderOverview() {
    if (!overview) return;
    const summary = overview.summary;
    const status = summary.overall_health || 'unknown';
    byId('ops-overall-status').textContent = `System ${statusLabel(status).toLowerCase()}`;
    byId('ops-overall-status').className = `ops-status-badge ${statusClass(status)}`;

    const daily = overview.operations_over_time || [];
    const series = key => daily.map(item => item[key] || 0);
    const cards = [
      { title: 'Total operations', value: summary.total_operations, note: 'Recorded API and platform events in the 30-day retention window.', status: 'unknown', values: daily.map(item => (item.success || 0) + (item.failed || 0) + (item.warning || 0)), color: '#427cb4' },
      { title: 'Successful', value: summary.successful_operations, note: 'Recorded API and workflow stages completed successfully.', status: 'success', values: series('success'), color: '#377c4d' },
      { title: 'Failed', value: summary.failed_operations, note: 'Recorded API errors and failed workflow stages.', status: summary.failed_operations ? 'failed' : 'success', values: series('failed'), color: '#b64e47' },
      { title: 'Currently running', value: summary.running_operations, note: 'In-flight tracked API requests in this process.', status: 'running', color: '#427cb4' },
      { title: 'Recovered operations', value: summary.recovery_data_available ? summary.recovered_operations : 'Not recorded', note: 'No verified recovery history is available.', status: 'unknown' },
      { title: 'Recovery attempts', value: summary.recovery_data_available ? summary.total_recovery_attempts : 'Not recorded', note: 'Recovery instrumentation is not present yet.', status: 'unknown' },
      { title: 'Successful recoveries', value: summary.recovery_data_available ? summary.successful_recovery_attempts : 'Not recorded', note: 'A recovery is successful only when confirmed by an event.', status: 'unknown' },
      { title: 'Failed recoveries', value: summary.recovery_data_available ? summary.failed_recovery_attempts : 'Not recorded', note: 'No recovery-attempt outcomes are being stored.', status: 'unknown' },
      { title: 'Overall system health', value: statusLabel(status), note: `Backend uptime ${formatUptime(summary.uptime_seconds)}.`, status, text: true },
      { title: 'Admin audit actions', value: summary.admin_audit_entries ?? 0, note: 'Existing audited actions in the retention window; identities and details are excluded.', status: 'unknown' },
    ];
    byId('ops-summary-cards').innerHTML = cards.map(card => `
      <article class="ops-stat-card">
        <div class="ops-stat-top"><span class="ops-stat-label">${escapeHtml(card.title)}</span><span class="ops-status-pill ${statusClass(card.status)}">${escapeHtml(statusLabel(card.status))}</span></div>
        <strong class="ops-stat-value${card.text ? ' is-text' : ''}">${escapeHtml(card.value)}</strong>
        <small class="ops-stat-note">${escapeHtml(card.note)}</small>
        ${card.values?.length ? sparkline(card.values, card.color) : ''}
      </article>`).join('');

    const tracking = overview.tracking || {};
    const processStart = localDate(tracking.process_tracking_started_at);
    const oldestEvent = localDate(tracking.earliest_retained_event_at);
    const note = !tracking.tracking_enabled
      ? 'Durable operation tracking is currently unavailable. The dashboard will not invent operation history.'
      : tracking.historical_data_available
      ? `Showing retained API and platform events since ${oldestEvent}. New records are retained for ${tracking.retention_days} days. ${tracking.historical_note}`
      : `No API or platform event history is recorded yet. Tracking has been enabled since ${processStart}; earlier history is unavailable. Existing audited admin actions are listed separately in System Activity. New records are retained for ${tracking.retention_days} days.`;
    setMessage('ops-tracking-note', note, !tracking.tracking_enabled);

    const recent = [
      ['Last successful operation', summary.last_successful_operation],
      ['Last failure', summary.last_failure],
      ['Last recovery action', summary.last_recovery_action],
      ['Latest admin audit action', summary.last_admin_audit_action],
    ];
    byId('ops-recent-activity').innerHTML = recent.map(([label, event]) => event
      ? `<div class="ops-recent-item"><div><strong>${escapeHtml(label)} · ${escapeHtml(event.operation)}</strong><small>${escapeHtml(event.component)} · ${escapeHtml(localDate(event.timestamp))}</small></div><span class="ops-status-pill ${statusClass(event.status)}">${escapeHtml(statusLabel(event.status))}</span></div>`
      : `<div class="ops-recent-item"><div><strong>${escapeHtml(label)}</strong><small>${label === 'Last recovery action' ? 'Recovery history is not recorded.' : 'No matching event has been recorded.'}</small></div><span class="ops-status-pill is-unknown">${label === 'Last recovery action' ? 'Not recorded' : '—'}</span></div>`
    ).join('');

    const attention = (overview.components || []).filter(component => ['degraded', 'unavailable'].includes(component.status));
    byId('ops-attention-list').innerHTML = attention.length
      ? attention.map(component => `<div class="ops-attention-item"><div><strong>${escapeHtml(component.name)}</strong><small>${escapeHtml(component.detail)}</small></div><span class="ops-status-pill ${statusClass(component.status)}">${escapeHtml(statusLabel(component.status))}</span></div>`).join('')
      : '<p class="ops-empty">No monitored component is currently degraded. Components without a direct health check are labeled separately.</p>';
  }

  function formatUptime(seconds) {
    if (!Number.isFinite(seconds)) return 'unknown';
    const days = Math.floor(seconds / 86400);
    const hours = Math.floor((seconds % 86400) / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    return `${days}d ${hours}h ${minutes}m`;
  }

  function eventQuery(page, filter = 'all') {
    const params = new URLSearchParams({ page: String(page), limit: '50', filter });
    const search = byId('ops-search')?.value.trim();
    const status = byId('ops-status-filter')?.value;
    const sort = byId('ops-sort')?.value || 'newest';
    const from = byId('ops-from')?.value;
    const to = byId('ops-to')?.value;
    if (search) params.set('q', search);
    if (status) params.set('status', status);
    if (sort) params.set('sort', sort);
    if (from) {
      const date = new Date(from);
      if (!Number.isNaN(date.getTime())) params.set('from', date.toISOString());
    }
    if (to) {
      const date = new Date(to);
      if (!Number.isNaN(date.getTime())) params.set('to', date.toISOString());
    }
    return `/api/operations/events?${params}`;
  }

  async function loadEvents(page = eventPage) {
    eventPage = page;
    try {
      const result = await getJson(eventQuery(page));
      events = result.events || [];
      eventTotal = result.total || 0;
      renderEvents();
      renderTimeline();
      byId('ops-event-total').textContent = `${eventTotal} recorded event${eventTotal === 1 ? '' : 's'}`;
      const pages = Math.max(1, Math.ceil(eventTotal / result.limit));
      byId('ops-page-label').textContent = `Page ${page} of ${pages}`;
      byId('ops-page-prev').disabled = page <= 1;
      byId('ops-page-next').disabled = page >= pages;
    } catch (error) {
      byId('ops-activity-list').innerHTML = `<p class="ops-empty">${escapeHtml(error.message)}</p>`;
      byId('ops-timeline-list').innerHTML = `<p class="ops-empty">${escapeHtml(error.message)}</p>`;
      byId('ops-failure-list').innerHTML = `<p class="ops-empty">${escapeHtml(error.message)}</p>`;
    }
  }

  function eventRow(event) {
    const err = event.error_type ? ` · ${event.error_type}` : '';
    const project = event.project_name ? `${event.project_name} · ` : '';
    return `<article class="ops-event-row">
      <button type="button" class="ops-event-open" data-event-id="${escapeHtml(event.event_id)}">
        <span class="ops-event-title">${escapeHtml(event.operation || 'Unlabeled operation')}</span>
        <span class="ops-event-subtitle">${escapeHtml(project)}${escapeHtml(event.component)} · ${escapeHtml(localDate(event.timestamp))}${escapeHtml(err)}</span>
      </button>
      <div class="ops-event-meta"><span class="ops-status-pill ${statusClass(event.status)}">${escapeHtml(statusLabel(event.status))}</span><span>${escapeHtml(duration(event.duration_ms))}</span></div>
    </article>`;
  }

  function renderEvents() {
    byId('ops-activity-list').innerHTML = events.length
      ? events.map(eventRow).join('')
      : '<p class="ops-empty">No recorded events match these filters. Data collection starts with this deployment; historical operations are not fabricated.</p>';
  }

  function renderTimeline() {
    const target = byId('ops-timeline-list');
    if (!target) return;
    if (!events.length) {
      target.innerHTML = '<p class="ops-empty">No recorded execution steps are available for this filter.</p>';
      return;
    }
    const ordered = [...events].sort((a, b) => new Date(a.timestamp) - new Date(b.timestamp));
    target.innerHTML = ordered.map(event => `
      <article class="ops-timeline-item ${statusClass(event.status)}">
        <strong>${escapeHtml(event.operation)}</strong>
        <small>${event.project_name ? `${escapeHtml(event.project_name)} · ` : ''}${escapeHtml(localDate(event.timestamp))} · ${escapeHtml(duration(event.duration_ms))} · correlation ${escapeHtml(event.correlation_id || 'not recorded')}</small>
        <button type="button" class="ops-inline-link" data-event-id="${escapeHtml(event.event_id)}">Inspect recorded event</button>
      </article>`).join('');
  }

  async function loadFailures() {
    try {
      const result = await getJson(eventQuery(1, failureFilter));
      byId('ops-failure-list').innerHTML = result.events.length
        ? result.events.map(event => `
          <article class="ops-failure-item ops-event-row">
            <button type="button" class="ops-event-open" data-event-id="${escapeHtml(event.event_id)}">
              <span class="ops-event-title">${escapeHtml(event.operation)}</span>
              <span class="ops-event-subtitle">${event.project_name ? `${escapeHtml(event.project_name)} · ` : ''}${escapeHtml(event.component)} · ${escapeHtml(localDate(event.timestamp))}${event.source === 'http_request' ? ` · HTTP ${escapeHtml(event.http_status_code ?? 'unknown')}` : ''}${event.error_type ? ` · ${escapeHtml(event.error_type)}` : ''}</span>
            </button>
            <span class="ops-status-pill ${statusClass(event.status)}">${escapeHtml(statusLabel(event.status))}</span>
          </article>`).join('')
        : `<p class="ops-empty">${failureFilter === 'recovered' || failureFilter === 'unresolved' || failureFilter === 'fallback'
          ? 'No matching recovery or fallback events have been recorded; absence of telemetry is not treated as a recovery.'
          : 'No recorded events match this filter.'}</p>`;
    } catch (error) {
      byId('ops-failure-list').innerHTML = `<p class="ops-empty">${escapeHtml(error.message)}</p>`;
    }
  }

  function openEventDetail(eventId) {
    const backdrop = byId('ops-event-backdrop');
    backdrop.hidden = false;
    byId('ops-event-detail-content').innerHTML = '<p class="ops-empty">Loading event details…</p>';
    getJson(`/api/operations/events/${encodeURIComponent(eventId)}`).then(result => {
      const event = result.event;
      const fields = [
        ['Event ID', event.event_id], ['Correlation ID', event.correlation_id],
        ['Timestamp', localDate(event.timestamp)], ['Component', event.component],
        ['Service', event.project_name || 'Not applicable'],
        ['Operation', event.operation], ['Status', statusLabel(event.status)],
        ['HTTP status', event.http_status_code], ['Execution duration', duration(event.duration_ms)],
        ['Error type', event.error_type || 'Not recorded'],
        ['Fallback action', event.fallback_action || 'Not recorded'],
        ['Recovery action', event.recovery_action || 'Not recorded'],
        ['Alternative service used', event.alternative_service_used || 'Not recorded'],
        ['Recovery attempts', event.recovery_attempts ?? 'Not recorded'],
        ['Recovery status', statusLabel(event.recovery_status)],
        ['Final outcome', event.final_outcome],
        ['Location', event.location],
      ];
      byId('ops-event-title').textContent = event.operation || 'Event details';
      byId('ops-event-detail-content').innerHTML = `
        <section class="ops-detail-block"><strong>Simple explanation</strong><p>${escapeHtml(result.simple_explanation)}</p></section>
        <section class="ops-detail-block"><strong>Recorded event fields</strong><dl>${fields.map(([name, value]) => `<dt>${escapeHtml(name)}</dt><dd>${escapeHtml(value ?? 'Not recorded')}</dd>`).join('')}</dl></section>
        <section class="ops-detail-block"><strong>Technical details</strong><p>${result.technical_details_available ? `Recorded error type: ${escapeHtml(event.error_type)}.` : 'No technical error detail was recorded for this event.'} ${result.technical_logs_available ? '' : 'Raw server logs are not exposed through this dashboard.'}</p></section>
        <section class="ops-detail-block"><strong>Related events</strong><p>${(result.related_events || []).length} event${(result.related_events || []).length === 1 ? '' : 's'} share this correlation ID. Workflow stage events share one ID; recovery actions are shown only when explicitly recorded.</p></section>
        <section class="ops-detail-block"><strong>Privacy</strong><p>${escapeHtml(result.privacy_note)}</p></section>`;
    }).catch(error => {
      byId('ops-event-detail-content').innerHTML = `<p class="ops-empty">${escapeHtml(error.message)}</p>`;
    });
    byId('ops-event-close').focus();
  }

  function renderHealth() {
    if (!overview) return;
    const components = overview.components || [];
    byId('ops-health-grid').innerHTML = components.map(component => `
      <article class="ops-health-card">
        <div class="ops-health-card-top"><h3>${escapeHtml(component.name)}</h3><span class="ops-status-pill ${statusClass(component.status)}">${escapeHtml(statusLabel(component.status))}</span></div>
        <p>${escapeHtml(component.detail)}</p>
        <p><strong>Location:</strong> ${escapeHtml(component.location || 'Unknown')}</p>
        <p><strong>Last health check:</strong> ${escapeHtml(localDate(component.last_health_check))}</p>
        <p><strong>Response time:</strong> ${escapeHtml(duration(component.response_time_ms))}</p>
        <p><strong>Dependencies:</strong> ${escapeHtml((component.dependencies || []).join(', ') || 'None recorded')}</p>
        <p><strong>Recent errors:</strong> ${escapeHtml((component.recent_errors || []).join(', ') || 'None recorded')}</p>
        ${component.records_received !== undefined ? `<p><strong>Latest fetch:</strong> ${component.records_received} received · ${component.records_inserted} inserted · ${component.duplicates_skipped} duplicates skipped</p>` : ''}
      </article>`).join('') || '<p class="ops-empty">No component health checks are configured.</p>';
  }

  function renderFlow() {
    if (!overview) return;
    const statusById = Object.fromEntries((overview.components || []).map(component => [component.id, component]));
    const nodes = overview.execution_flow || [];
    const sourceStatus = (overview.components || []).find(item => item.id.startsWith('collector-'));
    const nodeStatus = id => {
      if (id === 'browser' || id === 'session') return { status: 'healthy', detail: 'Verified by the current administrator session.' };
      if (id === 'sources') return sourceStatus || { status: 'not_monitored', detail: 'No source collector is configured.' };
      return statusById[id] || { status: 'unknown', detail: 'No direct health check is available.' };
    };
    byId('ops-flow-diagram').innerHTML = nodes.map((node, index) => {
      const data = nodeStatus(node.id);
      const arrow = index ? '<span class="ops-flow-arrow" aria-hidden="true">→</span>' : '';
      return `${arrow}<button type="button" class="ops-flow-node ${statusClass(data.status)}" data-flow-node="${escapeHtml(node.id)}" aria-label="Inspect ${escapeHtml(node.label)}">
        <strong>${escapeHtml(node.label)}</strong><small>${escapeHtml(statusLabel(data.status))}</small>
      </button>`;
    }).join('');
    byId('ops-flow-diagram').querySelectorAll('[data-flow-node]').forEach(button => {
      button.addEventListener('click', () => {
        const node = nodes.find(item => item.id === button.dataset.flowNode);
        const data = nodeStatus(node.id);
        byId('ops-flow-details').innerHTML = `<strong>${escapeHtml(node.label)} · ${escapeHtml(statusLabel(data.status))}</strong><br>${escapeHtml(node.detail)} ${escapeHtml(data.detail || '')}`;
      });
    });
  }

  function renderInfrastructure() {
    if (!overview) return;
    const infra = overview.infrastructure || {};
    const jobs = infra.jobs || [];
    const hosts = infra.members || [];
    const cards = [
      { title: 'FastAPI application container', status: infra.app_container_detected ? 'healthy' : 'unknown', detail: infra.app_container_detected ? 'This app is running in a container and responded to the current authenticated request.' : 'The app container could not be detected from this process.' },
      { title: 'MongoDB deployment', status: infra.available ? 'healthy' : 'unavailable', detail: infra.available ? `Database ${infra.database || 'unknown'} · replica set ${infra.replica_set || 'not reported'} · primary ${infra.primary || 'not reported'}` : 'A live topology check is unavailable.' },
      { title: 'Docker peer containers', status: infra.docker_daemon_status || 'not_monitored', detail: 'Peer container state and logs require host-level Docker monitoring; no Docker socket is mounted into this app.' },
      { title: 'Backup and restore services', status: 'not_monitored', detail: 'No backup service endpoint is registered with the running application.' },
    ];
    byId('ops-infrastructure-view').innerHTML = cards.map(card => `
      <article class="ops-infra-card"><div class="ops-infra-card-top"><h3>${escapeHtml(card.title)}</h3><span class="ops-status-pill ${statusClass(card.status)}">${escapeHtml(statusLabel(card.status))}</span></div><p>${escapeHtml(card.detail)}</p></article>`).join('') +
      `<article class="ops-infra-card"><h3>MongoDB members</h3>${hosts.length
        ? `<div class="ops-job-list">${hosts.map(host => `<div class="ops-job-row"><span>${escapeHtml(host)}</span><span>${host === infra.primary ? 'Primary' : 'Member'}</span></div>`).join('')}</div>`
        : '<p>Replica-set member list was not returned by the live topology check.</p>'}</article>` +
      `<article class="ops-infra-card"><h3>Scheduled background jobs</h3>${jobs.length
        ? `<div class="ops-job-list">${jobs.map(job => `<div class="ops-job-row"><span>${escapeHtml(job.id)}</span><span>${escapeHtml(localDate(job.next_run_at))}</span></div>`).join('')}</div>`
        : '<p>No scheduled jobs are reported by the running scheduler.</p>'}</article>`;
  }

  function emptyChart(message) {
    return `<p class="ops-empty">${escapeHtml(message)}</p>`;
  }

  function renderLineChart(containerId, rows, series, ariaLabel) {
    const target = byId(containerId);
    if (!rows.length) {
      target.innerHTML = emptyChart('Historical data not available yet.');
      return;
    }
    const width = 560;
    const height = 160;
    const left = 32;
    const right = 12;
    const top = 12;
    const bottom = 25;
    const max = Math.max(1, ...rows.flatMap(row => series.map(item => row[item.key] || 0)));
    const x = index => rows.length === 1 ? width / 2 : left + index * (width - left - right) / (rows.length - 1);
    const y = value => height - bottom - (value / max) * (height - top - bottom);
    const lines = series.map(item => {
      const points = rows.map((row, index) => `${x(index)},${y(row[item.key] || 0)}`).join(' ');
      const dots = rows.map((row, index) => `<circle cx="${x(index)}" cy="${y(row[item.key] || 0)}" r="2.4" fill="${item.color}"><title>${escapeHtml(row.day || row.timestamp)}: ${row[item.key] || 0} ${escapeHtml(item.label)}</title></circle>`).join('');
      return `<polyline points="${points}" fill="none" stroke="${item.color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"></polyline>${dots}`;
    }).join('');
    const grid = [0, .5, 1].map(fraction => {
      const gy = top + fraction * (height - top - bottom);
      return `<line x1="${left}" y1="${gy}" x2="${width - right}" y2="${gy}" class="ops-chart-gridline"></line><text x="2" y="${gy + 3}" class="ops-chart-label">${Math.round(max * (1 - fraction))}</text>`;
    }).join('');
    const labels = `<text x="${left}" y="${height - 5}" class="ops-chart-label">${escapeHtml(rows[0].day || rows[0].timestamp?.slice(11, 16) || '')}</text><text x="${width - right}" y="${height - 5}" text-anchor="end" class="ops-chart-label">${escapeHtml(rows[rows.length - 1].day || rows[rows.length - 1].timestamp?.slice(11, 16) || '')}</text>`;
    target.innerHTML = `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="${escapeHtml(ariaLabel)}">${grid}${lines}${labels}</svg><div class="ops-chart-legend">${series.map(item => `<span class="ops-legend-item"><i class="ops-legend-dot" style="background:${item.color}"></i>${escapeHtml(item.label)}</span>`).join('')}</div>`;
  }

  function renderBars(containerId, rows, labelKey, valueKey, color, emptyMessage) {
    const target = byId(containerId);
    if (!rows.length) {
      target.innerHTML = emptyChart(emptyMessage || 'Historical data not available yet.');
      return;
    }
    const max = Math.max(1, ...rows.map(row => row[valueKey] || 0));
    target.innerHTML = `<div class="ops-bar-list">${rows.map(row => {
      const value = row[valueKey] || 0;
      return `<div class="ops-bar-row"><span title="${escapeHtml(row[labelKey])}">${escapeHtml(row[labelKey])}</span><div class="ops-bar-track"><div class="ops-bar-fill" style="width:${Math.max(1, value / max * 100)}%;background:${color}"></div></div><strong>${escapeHtml(value)}</strong></div>`;
    }).join('')}</div>`;
  }

  function renderDonut(containerId, values) {
    const target = byId(containerId);
    const total = values.reduce((sum, item) => sum + item.value, 0);
    if (!total) {
      target.innerHTML = emptyChart('Historical data not available yet.');
      return;
    }
    let position = 0;
    const stops = values.map(item => {
      const start = position;
      position += item.value / total * 100;
      return `${item.color} ${start}% ${position}%`;
    }).join(', ');
    target.innerHTML = `<div class="ops-donut-wrap"><div class="ops-donut" role="img" aria-label="${values.map(item => `${item.label}: ${item.value}`).join(', ')}" style="background:conic-gradient(${stops})"><div class="ops-donut-hole">${total}<small>events</small></div></div></div><div class="ops-chart-legend">${values.map(item => `<span class="ops-legend-item"><i class="ops-legend-dot" style="background:${item.color}"></i>${escapeHtml(item.label)}: ${item.value}</span>`).join('')}</div>`;
  }

  function renderAnalytics() {
    if (!overview) return;
    const days = overview.operations_over_time || [];
    renderLineChart('ops-chart-operations', days, [
      { key: 'success', label: 'Successful', color: '#377c4d' },
      { key: 'failed', label: 'Failed', color: '#b64e47' },
    ], 'Successful and failed API and workflow operations per day');
    const distribution = overview.status_distribution || {};
    renderDonut('ops-chart-status', [
      { label: 'Successful', value: distribution.success || 0, color: '#377c4d' },
      { label: 'Failed', value: distribution.failed || 0, color: '#b64e47' },
      { label: 'Warnings', value: distribution.warning || 0, color: '#be762d' },
      { label: 'Running', value: distribution.running || 0, color: '#427cb4' },
    ]);
    renderBars('ops-chart-failures', overview.failure_categories || [], 'name', 'count', '#b64e47', 'No server failures have been recorded.');
    renderLineChart('ops-chart-duration', overview.duration_series || [], [
      { key: 'duration_ms', label: 'Duration (ms)', color: '#427cb4' },
    ], 'Recorded API and workflow operation duration over time');
    renderBars('ops-chart-components', overview.component_performance || [], 'component', 'average_duration_ms', '#427cb4', 'No component duration history is available yet.');
    renderBars('ops-chart-projects', overview.project_performance || [], 'project_name', 'average_duration_ms', '#a65349', 'No workflow execution events have been recorded yet.');
    const cache = overview.cache_metrics?.history || [];
    renderLineChart('ops-chart-cache', cache.map(item => ({
      timestamp: item.timestamp,
      hit_ratio: Number.isFinite(item.hit_ratio) ? Math.round(item.hit_ratio * 100) : null,
    })).filter(item => item.hit_ratio !== null), [
      { key: 'hit_ratio', label: 'Cache hit ratio (%)', color: '#377c4d' },
    ], 'WiredTiger cache hit ratio from recorded samples');
  }

  function closeEventDetail() {
    byId('ops-event-backdrop').hidden = true;
  }

  byId('ops-refresh').addEventListener('click', loadDashboard);
  byId('ops-apply-filters').addEventListener('click', () => loadEvents(1));
  byId('ops-page-prev').addEventListener('click', () => loadEvents(Math.max(1, eventPage - 1)));
  byId('ops-page-next').addEventListener('click', () => loadEvents(eventPage + 1));
  byId('ops-search').addEventListener('input', () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => loadEvents(1), 250);
  });
  byId('ops-status-filter').addEventListener('change', () => loadEvents(1));
  byId('ops-sort').addEventListener('change', () => loadEvents(1));
  byId('ops-from').addEventListener('change', () => loadEvents(1));
  byId('ops-to').addEventListener('change', () => loadEvents(1));
  dashboard.querySelectorAll('[data-failure-filter]').forEach(button => {
    button.addEventListener('click', () => {
      failureFilter = button.dataset.failureFilter;
      dashboard.querySelectorAll('[data-failure-filter]').forEach(item => item.classList.toggle('is-active', item === button));
      loadFailures();
    });
  });
  dashboard.addEventListener('click', event => {
    const detailButton = event.target.closest('[data-event-id]');
    if (detailButton) openEventDetail(detailButton.dataset.eventId);
  });
  byId('ops-event-close').addEventListener('click', closeEventDetail);
  byId('ops-event-backdrop').addEventListener('click', event => {
    if (event.target === byId('ops-event-backdrop')) closeEventDetail();
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && !byId('ops-event-backdrop').hidden) closeEventDetail();
  });

  const portfolioLink = document.querySelector('.sidebar-nav .nav-link[data-view="portfolio"]');
  portfolioLink?.addEventListener('click', () => {
    window.setTimeout(() => {
      if (!loaded) loadDashboard();
      else renderOverview();
      activateTab('overview');
    }, 0);
  });
});
