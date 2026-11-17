(() => {
  const status = document.getElementById('tour-status');
  const grid = document.getElementById('tour-grid');
  const escapeHtml = value => String(value ?? '').replace(/[&<>"\']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const integrations = {
    7: ['Working Set Monitor', 'Database cache and activity signals.'],
    8: ['Reliability & Inventory', 'Operational reliability and inventory workflows.'],
    9: ['Sharding & Horizontal Scalability', 'Distributed data placement and throughput analysis.'],
    10: ['Banking Operations', 'Account, wallet, and transaction workflows.'],
    11: ['Transaction Reliability', 'Transaction behavior and resilience monitoring.'],
    12: ['Platform Integration', 'Service readiness for platform capabilities.'],
    13: ['Deployment & Infrastructure', 'Replica set, orchestration, and deployment status.'],
    14: ['Monitoring & Backup', 'Live signals, backup history, and recovery verification.'],
    15: ['Enterprise Management', 'Tenant-scoped access, memberships, and case workflows.'],
    16: ['Database Operations', 'Distributed database health, audit evidence, and investigations.'],
    17: ['AI Research & Intelligence', 'Threat report retrieval, semantic search, and grounded answers.'],
    18: ['Threat Intelligence', 'Imported indicators, analyst review, and measured database operations.'],
  };
  fetch('/api/tour', { credentials: 'same-origin', headers: { Accept: 'application/json' } })
    .then(async response => {
      if (!response.ok) throw new Error(response.status === 401 ? 'Sign in as an administrator to view platform integration status.' : `Platform status request failed (${response.status}).`);
      return response.json();
    })
    .then(async data => {
      status.textContent = 'Status is based on registered capabilities and live endpoint availability.';
      const rows = await Promise.all((data.chapters || []).map(async item => {
        const [name, description] = integrations[item.chapter] || ['Platform Integration', 'Service readiness information.'];
        if (!item.status_endpoint) return { ...item, name, description, live: 'No live endpoint' };
        try {
          const response = await fetch(item.status_endpoint, { credentials: 'same-origin', headers: { Accept: 'application/json' } });
          return { ...item, name, description, live: `HTTP ${response.status} ? ${response.ok ? 'responding' : 'unavailable'}` };
        } catch (_) { return { ...item, name, description, live: 'Connection failed' }; }
      }));
      grid.innerHTML = rows.map(item => `<article class="tour-card"><h2>${escapeHtml(item.name)}</h2><p>${escapeHtml(item.description)}</p><div class="tour-meta"><span class="tour-pill ${item.status === 'integrated' ? 'ready' : ''}">${escapeHtml(item.status === 'integrated' ? 'Connected' : 'Not configured')}</span><span class="tour-pill ${item.live?.includes('responding') ? 'ready' : ''}">${escapeHtml(item.live)}</span></div>${item.navigation ? `<a href="${escapeHtml(item.navigation)}">Open workspace ?</a>` : ''}</article>`).join('');
    })
    .catch(error => { status.className = 'tour-error'; status.textContent = error.message; });
})();
