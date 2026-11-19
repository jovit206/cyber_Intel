/**
 * Aegis-CTI Command Center Client Logic
 * Handles navigation, threat intelligence views, and system monitoring.
 */

document.addEventListener('DOMContentLoaded', () => {
  const configuredApiUrl = window.AEGIS_API_BASE_URL || (
    window.location.protocol === 'file:' ? 'http://127.0.0.1:8000' : ''
  );
  const apiUrl = path => `${configuredApiUrl.replace(/\/+$/, '')}${path}`;

  // Navigation Links
  const navLinks = document.querySelectorAll('.sidebar-nav .nav-link');
  const views = document.querySelectorAll('.content-view');

  navLinks.forEach(link => {
    link.addEventListener('click', () => {
      const viewId = link.dataset.view;
      navLinks.forEach(l => l.classList.remove('active'));
      views.forEach(v => v.classList.remove('active'));

      link.classList.add('active');
      const targetView = document.getElementById(`view-${viewId}`);
      if (targetView) targetView.classList.add('active');

      // Lazy load view data
      if (viewId === 'feed') loadThreatStream();
      if (viewId === 'threats') loadAdversaryMatrix();
      if (viewId === 'reports') loadReportCorpus();
      if (viewId === 'cache') {
        loadWorkingSetAnalysis();
        loadWiredTigerTelemetry();
        if (cacheChart) cacheChart.resize();
      }
    });
  });

  // Drawer handlers
  const drawerBackdrop = document.getElementById('drawer-backdrop');
  const drawerClose = document.getElementById('drawer-close');
  drawerClose.addEventListener('click', () => drawerBackdrop.classList.add('hidden'));
  drawerBackdrop.addEventListener('click', (e) => {
    if (e.target === drawerBackdrop) drawerBackdrop.classList.add('hidden');
  });

  // ==================== VIEW 1: THREAT STREAM ====================
  const eventsPerPage = 30;
  let currentSourceFilter = '';
  let currentEventPage = 1;
  let eventRequestId = 0;
  const filterPills = document.querySelectorAll('.filter-pills .pill');
  const pagination = document.getElementById('feed-pagination');
  const previousPageButton = document.getElementById('feed-previous');
  const nextPageButton = document.getElementById('feed-next');

  previousPageButton.addEventListener('click', () => {
    if (currentEventPage > 1) {
      currentEventPage -= 1;
      loadThreatStream();
    }
  });

  nextPageButton.addEventListener('click', () => {
    currentEventPage += 1;
    loadThreatStream();
  });

  filterPills.forEach(p => {
    p.addEventListener('click', () => {
      filterPills.forEach(pill => pill.classList.remove('active'));
      p.classList.add('active');
      currentSourceFilter = p.dataset.source;
      currentEventPage = 1;
      loadThreatStream();
    });
  });

  function loadThreatStream() {
    const stream = document.getElementById('events-stream');
    const counter = document.getElementById('feed-counter');
    const pageStatus = document.getElementById('feed-page-status');
    const requestId = ++eventRequestId;
    const url = `/api/events?page=${currentEventPage}&limit=${eventsPerPage}${currentSourceFilter ? `&source=${encodeURIComponent(currentSourceFilter)}` : ''}`;

    stream.innerHTML = '<div class="loading-state">Streaming intelligence records...</div>';
    pagination.hidden = true;

    fetch(apiUrl(url))
      .then(r => {
        if (!r.ok) throw new Error(`Threat stream request failed (${r.status})`);
        return r.json();
      })
      .then(data => {
        if (requestId !== eventRequestId) return;

        const totalEvents = Number(data.total);
        const totalPages = Math.ceil(totalEvents / Number(data.limit || eventsPerPage));
        counter.textContent = `Showing ${data.events.length.toLocaleString()} of ${totalEvents.toLocaleString()} live records`;
        pagination.hidden = totalPages <= 1;
        previousPageButton.disabled = currentEventPage <= 1;
        nextPageButton.disabled = currentEventPage >= totalPages;
        pageStatus.textContent = totalPages ? `Page ${currentEventPage} of ${totalPages}` : 'No pages';

        if (data.events.length === 0) {
          stream.innerHTML = '<div class="empty-prompt">No new threat intelligence available.</div>';
          return;
        }

        stream.innerHTML = data.events.map(event => {
          const sourceLabels = {
            misp: 'MISP',
            circl: 'CIRCL',
            botvrij: 'Botvrij',
            threatfox: 'ThreatFox',
            virustotal: 'VirusTotal',
          };
          const source = sourceLabels[event.source_feed_name] || sourceLabels[event.source] || event.source;
          const title = event.info || event.indicator || 'Source event';
          const tags = (event.tags || []).map(tag =>
            `<span class="ioc-counter-tag">${escapeHtml(tag)}</span>`
          ).join('');
          const correlatedSources = (event.correlated_sources || [])
            .filter(name => name !== event.source)
            .map(name => sourceLabels[name] || name)
            .map(name => `<span class="tag-badge cyan">Also in ${escapeHtml(name)}</span>`)
            .join('');
          const detectionStats = [
            ['Malicious', event.malicious_count],
            ['Suspicious', event.suspicious_count],
            ['Harmless', event.harmless_count],
            ['Undetected', event.undetected_count]
          ].filter(([, value]) => Number.isFinite(value));
          const detectionHtml = detectionStats.length
            ? `<div class="ioc-stats-row">${detectionStats.map(([label, value]) =>
              `<span class="ioc-counter-tag">${label}: <strong>${value}</strong></span>`
            ).join('')}</div>`
            : '';
          const metadata = [
            event.indicator_type && `Type: ${event.indicator_type}`,
            event.category && `Category: ${event.category}`,
            event.timestamp && `Timestamp: ${event.timestamp}`,
            event.date && `Date: ${event.date}`,
            event.first_seen && `First seen: ${event.first_seen}`,
            event.last_seen && `Last seen: ${event.last_seen}`,
            event.threat_level && `Threat level: ${event.threat_level}`,
            event.file_type && `File type: ${event.file_type}`,
            event.file_name && `File name: ${event.file_name}`,
            event.delivery_method && `Delivery: ${event.delivery_method}`,
            event.reporter && `Reporter: ${event.reporter}`,
            event.attribute_id && `Attribute ID: ${event.attribute_id}`,
            Number.isFinite(event.attribute_count) && `Attributes: ${event.attribute_count}`,
            typeof event.to_ids === 'boolean' && `IDS flag: ${event.to_ids ? 'Yes' : 'No'}`,
            event.categories && `Categories: ${typeof event.categories === 'string' ? event.categories : JSON.stringify(event.categories)}`,
            typeof event.published === 'boolean' && `Published: ${event.published ? 'Yes' : 'No'}`,
            Number.isFinite(event.reputation) && `Reputation: ${event.reputation}`,
            event.event_id && `Event ID: ${event.event_id}`,
            event.source_id && `Source ID: ${event.source_id}`
          ].filter(Boolean);
          const detailsHtml = metadata.length
            ? `<div class="event-card-meta">${metadata.map(value =>
              `<span>${escapeHtml(value)}</span>`
            ).join('')}</div>`
            : '';
          const comment = event.comment
            ? `<p class="event-card-description">${escapeHtml(event.comment)}</p>`
            : '';
          const description = event.description
            ? `<p class="event-card-description">${escapeHtml(event.description)}</p>`
            : '';
          const publisher = event.publisher
            ? `<span class="event-card-publisher">Publisher: ${escapeHtml(event.publisher)}</span>`
            : '';
          const info = event.info && event.info !== title
            ? `<div class="event-card-title">${escapeHtml(event.info)}</div>`
            : '';
          const indicator = event.indicator
            ? `<div class="event-card-indicator">${escapeHtml(event.indicator)}</div>`
            : '';
          const safeReference = safeHttpUrl(event.source_reference);
          const sourceReference = safeReference
            ? `<a class="event-card-reference" href="${escapeHtml(safeReference)}" target="_blank" rel="noopener noreferrer">Source reference</a>`
            : '';
          const tagsHtml = tags ? `<div class="event-card-tags">${tags}</div>` : '';
          return `
            <div class="event-card">
              <div class="event-card-head">
                <span class="tag-badge ref">Source: ${escapeHtml(source)}</span>
                ${event.threat_type ? `<span class="tag-badge cyan">${escapeHtml(event.threat_type)}</span>` : ''}
              </div>
              <div class="event-card-title">${escapeHtml(title)}</div>
              ${indicator}
              ${info}
              ${detectionHtml}
              ${detailsHtml}
              ${publisher}
              ${description}
              ${comment}
              ${tagsHtml}
              ${correlatedSources ? `<div class="event-card-correlations">${correlatedSources}</div>` : ''}
              ${sourceReference}
            </div>
          `;
        }).join('');
      })
      .catch(err => {
        if (requestId !== eventRequestId) return;
        console.error(err);
        counter.textContent = 'Unable to load event count';
        stream.innerHTML = '<div class="empty-prompt">Threat stream is unavailable. Please try again.</div>';
        pagination.hidden = true;
      });
  }

  function escapeHtml(value) {
    if (value === null || value === undefined) return '';
    return String(value).replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#039;');
  }

  function safeHttpUrl(value) {
    if (!value) return null;
    try {
      const url = new URL(value);
      return ['http:', 'https:'].includes(url.protocol) ? url.href : null;
    } catch {
      return null;
    }
  }

  // ==================== VIEW 2: TOP THREATS ====================
  function loadAdversaryMatrix() {
    const matrix = document.getElementById('threats-matrix');
    matrix.innerHTML = '<div class="loading-state">Loading live threat indicators...</div>';

    fetch(apiUrl('/api/threats/top?limit=12'))
      .then(response => {
        if (!response.ok) throw new Error(`Threat request failed (${response.status})`);
        return response.json();
      })
      .then(events => {
        if (!events.length) {
          matrix.innerHTML = '<div class="empty-prompt">No live threat indicators are available yet.</div>';
          return;
        }
        matrix.innerHTML = events.map(event => {
          const title = event.info || event.indicator || 'Threat event';
          const source = event.source_feed_name || event.source || 'Unknown source';
          const indicator = event.indicator
            ? `<div class="event-card-indicator">${escapeHtml(event.indicator)}</div>`
            : '';
          const description = event.description || event.comment || '';
          return `
            <div class="threat-card" data-event-id="${escapeHtml(event._id)}" role="button" tabindex="0">
              <div class="event-card-head">
                <span class="threat-card-title">${escapeHtml(title)}</span>
                <span class="tag-badge cyan">${escapeHtml(source)}</span>
              </div>
              ${event.indicator_type ? `<div class="event-card-meta"><span>Type: ${escapeHtml(event.indicator_type)}</span></div>` : ''}
              ${indicator}
              ${description ? `<p class="event-card-description">${escapeHtml(description)}</p>` : ''}
              ${event.timestamp ? `<div class="event-card-meta"><span>Received: ${escapeHtml(event.timestamp)}</span></div>` : ''}
            </div>
          `;
        }).join('');
        matrix.querySelectorAll('[data-event-id]').forEach(card => {
          const open = () => window.openThreatDrawer(card.dataset.eventId);
          card.addEventListener('click', open);
          card.addEventListener('keydown', event => {
            if (event.key === 'Enter' || event.key === ' ') {
              event.preventDefault();
              open();
            }
          });
        });
      })
      .catch(error => {
        matrix.innerHTML = `<div class="empty-prompt">${escapeHtml(error.message)}</div>`;
      });
  }

  window.openThreatDrawer = function(threatId) {
    const drawer = document.getElementById('drawer-backdrop');
    const drawerTitle = document.getElementById('drawer-title');
    const drawerType = document.getElementById('drawer-type');
    const drawerContent = document.getElementById('drawer-content');

    drawer.classList.remove('hidden');
    drawerContent.innerHTML = '<div class="loading-state">Loading threat dossier...</div>';

    fetch(apiUrl(`/api/threats/${encodeURIComponent(threatId)}`))
      .then(response => {
        if (!response.ok) throw new Error(`Threat details failed (${response.status})`);
        return response.json();
      })
      .then(data => {
        const event = data.event;
        if (!event) throw new Error('The response did not contain a live source event.');
        drawerTitle.textContent = event.info || event.indicator || 'Threat event';
        drawerType.textContent = (event.indicator_type || event.source || 'event').toUpperCase();
        const attributes = (data.attributes || []).map(attribute => `
          <div class="event-card-indicator">${escapeHtml(attribute.value || '')}</div>
        `).join('');
        drawerContent.innerHTML = `
          ${event.indicator ? `<h3>Indicator</h3><div class="event-card-indicator">${escapeHtml(event.indicator)}</div>` : ''}
          ${event.description || event.comment ? `<h3>Source details</h3><p class="event-card-description">${escapeHtml(event.description || event.comment)}</p>` : ''}
          <h3>Source</h3><p>${escapeHtml(event.source || 'Unknown')}</p>
          ${event.source_reference ? `<p><a href="${escapeHtml(safeHttpUrl(event.source_reference) || '#')}" target="_blank" rel="noopener noreferrer">Open source record</a></p>` : ''}
          ${attributes ? `<h3>Associated attributes (${(data.attributes || []).length})</h3>${attributes}` : ''}
        `;
      })
      .catch(error => {
        drawerContent.innerHTML = `<div class="empty-prompt">${escapeHtml(error.message)}</div>`;
      });
  };

  // ==================== VIEW 3: REPORT CORPUS ====================
  function loadReportCorpus() {
    fetch(apiUrl('/api/reports'))
      .then(response => {
        if (!response.ok) throw new Error(`Report request failed (${response.status})`);
        return response.json();
      })
      .then(reports => {
        const tbody = document.querySelector('#corpus-table tbody');
        tbody.innerHTML = reports.length ? reports.map(r => {
          const pdfUrl = safeHttpUrl(r.pdf_url);
          const chunks = Number.isFinite(r.chunk_count) ? `${r.chunk_count} passages` : 'Not converted';
          const entities = Number.isFinite(r.entity_count) ? r.entity_count : 'Not extracted';
          return `
          <tr>
            <td><strong style="color: var(--text-white);">${escapeHtml(r.title || r.filename || r._id)}</strong></td>
            <td>${escapeHtml(r.vendor || 'Not provided')}</td>
            <td>${escapeHtml(r.year ?? 'Not provided')}</td>
            <td>${escapeHtml(chunks)}</td>
            <td>${escapeHtml(entities)}</td>
            <td>${pdfUrl ? `<a href="${escapeHtml(pdfUrl)}" target="_blank" rel="noopener noreferrer" style="color: var(--cyan-accent); text-decoration: none;">GitHub PDF &nearr;</a>` : 'Unavailable'}</td>
          </tr>
        `;
        }).join('') : '<tr><td colspan="6">No annual reports have been synced yet.</td></tr>';
      })
      .catch(error => {
        const tbody = document.querySelector('#corpus-table tbody');
        tbody.innerHTML = `<tr><td colspan="6">${escapeHtml(error.message)}</td></tr>`;
      });

    fetch(apiUrl('/api/sources'))
      .then(response => {
        if (!response.ok) throw new Error(`Source request failed (${response.status})`);
        return response.json();
      })
      .then(data => {
        const tbody = document.querySelector('#sources-table tbody');
        tbody.innerHTML = (data.feeds || []).map(f => `
          <tr>
            <td><strong style="color: var(--text-white);">${escapeHtml(f._id)}</strong></td>
            <td>${f.events.toLocaleString()}</td>
            <td>${Number(f.indicators || 0).toLocaleString()}</td>
            <td>${escapeHtml(f.last || 'Not provided')}</td>
          </tr>
        `).join('');
        if (!data.feeds || data.feeds.length === 0) {
          tbody.innerHTML = '<tr><td colspan="4">No feed events have been synced yet.</td></tr>';
        }
      })
      .catch(error => {
        document.querySelector('#sources-table tbody').innerHTML =
          `<tr><td colspan="4">${escapeHtml(error.message)}</td></tr>`;
      });
  }

  function loadSourceSyncStatus() {
    const status = document.getElementById('last-sync-status');
    fetch(apiUrl('/api/data-source-status'))
      .then(response => {
        if (!response.ok) throw new Error(`Sync status request failed (${response.status})`);
        return response.json();
      })
      .then(data => {
        const feedRows = data.misp_feeds || [];
        const latest = feedRows
          .filter(feed => feed.last_successful_sync)
          .sort((left, right) => new Date(right.last_successful_sync) - new Date(left.last_successful_sync))[0];
        const failedFeeds = feedRows.filter(feed => feed.last_error).map(feed => feed._id);
        let message = latest
          ? `Last successful feed sync: ${new Date(latest.last_successful_sync).toLocaleString()}`
          : 'No successful public-feed sync yet';
        if (failedFeeds.length) message += ` · Sync errors: ${failedFeeds.join(', ')}`;

        const virusTotal = data.virustotal || {};
        if (!data.virustotal_configured) {
          message += ' · VirusTotal key not configured';
        } else if (virusTotal.last_error && /429|quota|limit/i.test(virusTotal.last_error)) {
          message += ' · VirusTotal free-tier limit reached; cached results are still available';
        }
        status.textContent = message;
      })
      .catch(error => {
        status.textContent = `Sync status unavailable: ${error.message}`;
      });
  }

  // ==================== LAB 7.2: WORKING SET & WIREDTIGER CACHE ====================
  function formatBytes(bytes) {
    if (!bytes || bytes === 0) return '0 B';
    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
  }

  let cacheChart = null;

  function loadWorkingSetAnalysis() {
    fetch(apiUrl('/api/lab72/analysis'))
      .then(response => {
        if (!response.headers.get('content-type')?.includes('application/json')) {
          throw new Error('Lab 7.2 API is not loaded. Restart the app server and reload this page.');
        }
        if (!response.ok) throw new Error(`Working set analysis failed (${response.status})`);
        return response.json();
      })
      .then(data => {
        document.getElementById('ws-document-count').textContent = Number(data.count).toLocaleString();
        document.getElementById('ws-document-size').textContent = data.count
          ? `Average BSON document: ${formatBytes(data.avgSize)}`
          : 'No real-source records found; sync feeds and build the Lab 7.2 workload';
        document.getElementById('ws-footprint').textContent = formatBytes(data.totalFootprintBytes);
      })
      .catch(error => {
        console.error(error);
        document.getElementById('ws-document-count').textContent = 'Unavailable';
        document.getElementById('ws-document-size').textContent = error.message;
        document.getElementById('ws-footprint').textContent = 'Unavailable';
      });
  }

  function loadWiredTigerTelemetry() {
    fetch(apiUrl('/api/metrics'))
      .then(response => {
        if (!response.ok) throw new Error(`WiredTiger telemetry failed (${response.status})`);
        return response.json();
      })
      .then(data => {
        if (!data.available) {
          console.error(data.reason || 'WiredTiger telemetry unavailable');
          return;
        }

        const hitRatio = data.hit_ratio_interval != null ? (data.hit_ratio_interval * 100).toFixed(2) + '%' : '—';
        document.getElementById('wt-hit-ratio').textContent = hitRatio;

        const maxBytes = Number(data.max_bytes) || 0;
        const cachePct = maxBytes ? Math.min(100, (data.bytes_in_cache / maxBytes) * 100) : 0;
        document.getElementById('wt-bytes-cache').textContent = formatBytes(data.bytes_in_cache);
        document.getElementById('wt-cache-pct').textContent = `${cachePct.toFixed(2)}% of ${formatBytes(maxBytes)}`;
        document.getElementById('wt-cache-capacity').textContent = `Cache limit: ${formatBytes(maxBytes)}`;
        document.getElementById('wt-dirty').textContent = formatBytes(data.dirty_bytes);
        document.getElementById('wt-evictions').textContent = Number(data.pages_evicted || 0).toLocaleString();
        document.getElementById('wt-pages-read').textContent = Number(data.pages_read || 0).toLocaleString();
        const progress = document.getElementById('wt-cache-progress');
        progress.style.width = `${cachePct}%`;
        progress.parentElement.setAttribute('aria-valuenow', cachePct.toFixed(2));
        renderCacheChart(data);
      })
      .catch(error => {
        console.error(error);
      });
  }

  function renderCacheChart(data) {
    const canvas = document.getElementById('wt-cache-chart');
    if (!canvas) return;
    if (!window.Chart) {
      console.error('Chart.js is unavailable; WiredTiger cache chart cannot be rendered.');
      return;
    }

    const history = (data.history || []).slice(-20);
    const labels = history.map(point => new Date(point.timestamp).toLocaleTimeString());
    const values = history.map(point => point.max_bytes
      ? Number(((point.bytes_in_cache / point.max_bytes) * 100).toFixed(2))
      : 0);
    if (history.length === 0 || history[history.length - 1].timestamp !== data.timestamp) {
      labels.push(new Date().toLocaleTimeString());
      values.push(Number(((data.bytes_in_cache / data.max_bytes) * 100).toFixed(2)));
    }

    if (!cacheChart) {
      cacheChart = new Chart(canvas, {
        type: 'line',
        data: {
          labels,
          datasets: [{
            label: 'Cache used (%)',
            data: values,
            borderColor: '#79536f',
            backgroundColor: 'rgba(121, 83, 111, 0.12)',
            fill: true,
            tension: 0.3,
            pointRadius: 2
          }]
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          animation: false,
          scales: {
            y: { beginAtZero: true, max: 100, title: { display: true, text: 'Cache capacity used (%)' } },
            x: { ticks: { maxTicksLimit: 8 } }
          },
          plugins: { legend: { display: false } }
        }
      });
      return;
    }
    cacheChart.data.labels = labels;
    cacheChart.data.datasets[0].data = values;
    cacheChart.update('none');
  }

  const randomReadsButton = document.getElementById('btn-run-random-reads');
  randomReadsButton.addEventListener('click', () => {
    randomReadsButton.disabled = true;
    randomReadsButton.textContent = 'Running 500 random reads...';
    document.getElementById('ws-experiment-status').textContent = 'Running indexed random lookups against the real-derived benchmark collection...';
    fetch(apiUrl('/api/lab72/simulate-reads'), { method: 'POST' })
      .then(response => response.json().then(data => {
        if (!response.ok) throw new Error(data.error || `Random-read experiment failed (${response.status})`);
        return data;
      }))
      .then(result => {
        document.getElementById('ws-read-latency').textContent = `${result.avgLatencyMs} ms`;
        document.getElementById('ws-read-count').textContent = `${result.queriesExecuted.toLocaleString()} random _id lookups in ${result.durationMs} ms total`;
        document.getElementById('ws-read-pages').textContent = `${result.pageReads.toLocaleString()} / ${result.evictions.toLocaleString()}`;
        document.getElementById('ws-read-pages-detail').textContent = 'Pages read into cache / application-thread evictions during workload';
        document.getElementById('ws-experiment-status').textContent = 'Experiment completed; cache metrics refreshed from MongoDB.';
        loadWiredTigerTelemetry();
      })
      .catch(error => {
        console.error(error);
        document.getElementById('ws-experiment-status').textContent = `Experiment failed: ${error.message}`;
      })
      .finally(() => {
        randomReadsButton.disabled = false;
        randomReadsButton.textContent = 'Run 500 Random Reads';
      });
  });

  // WiredTiger telemetry is sampled by the server every 10 seconds.
  setInterval(() => {
    loadWiredTigerTelemetry();
  }, 10000);

  setInterval(loadSourceSyncStatus, 60000);

  setInterval(() => {
    if (document.getElementById('view-feed').classList.contains('active')) {
      loadThreatStream();
    }
  }, 60000);

  // ==================== SEARCH & ENRICH: VIRUSTOTAL SCAN ====================
  const vtScanInput = document.getElementById('vt-scan-input');
  const vtScanButton = document.getElementById('vt-scan-button');
  const vtScanResult = document.getElementById('vt-scan-result');
  const vtRecentBody = document.getElementById('vt-recent-body');
  const vtCacheCount = document.getElementById('vt-cache-count');
  const vtRefreshButton = document.getElementById('vt-refresh');
  const vtModalBackdrop = document.getElementById('vt-modal-backdrop');
  const vtModalClose = document.getElementById('vt-modal-close');
  const vtModalContent = document.getElementById('vt-modal-content');
  const vtModalTitle = document.getElementById('vt-modal-title');

  function formatRelativeTime(value) {
    if (!value) return 'Just now';
    try {
      const stamp = new Date(value);
      if (Number.isNaN(stamp.getTime())) return value;
      const diffMs = Date.now() - stamp.getTime();
      const minutes = Math.max(1, Math.round(diffMs / 60000));
      if (minutes < 60) return `${minutes}m ago`;
      const hours = Math.round(minutes / 60);
      if (hours < 24) return `${hours}h ago`;
      const days = Math.round(hours / 24);
      return `${days}d ago`;
    } catch {
      return value;
    }
  }

  function verdictFromResult(result) {
    const malicious = Number(result?.malicious_count || 0);
    const suspicious = Number(result?.suspicious_count || 0);
    if (malicious > 0) return 'Malicious';
    if (suspicious > 0) return 'Suspicious';
    return 'Clean';
  }

  function verdictClassFor(value) {
    const verdict = String(value || 'Clean').toLowerCase();
    if (verdict.includes('mal')) return 'danger';
    if (verdict.includes('susp')) return 'warn';
    return 'success';
  }

  function setVtButtonLoading(loading) {
    vtScanButton.disabled = loading;
    vtScanButton.classList.toggle('loading', loading);
    vtScanButton.innerHTML = loading
      ? '<span class="vt-spinner" aria-hidden="true"></span>Scanning...'
      : 'Scan';
  }

  function openVtModal(report) {
    if (!report) return;
    const engineResults = report.engine_results || report.raw_source?.attributes?.last_analysis_results || {};
    const entries = Object.entries(engineResults)
      .map(([name, details]) => {
        if (!details || typeof details !== 'object') return null;
        const category = details.category || details.result || 'unknown';
        const result = details.result || 'No result';
        const method = details.method || details.engine_update || 'n/a';
        return `
          <div class="vt-engine-item">
            <div class="vt-engine-name">${escapeHtml(name)}</div>
            <div class="vt-engine-type">${escapeHtml(category)}</div>
            <div class="vt-engine-result">${escapeHtml(result)}</div>
            <div class="vt-engine-method">${escapeHtml(method)}</div>
          </div>`;
      })
      .filter(Boolean)
      .join('');

    vtModalTitle.textContent = `${report.indicator || 'Target'} full report`;
    vtModalContent.innerHTML = entries
      ? `<div class="vt-engine-list">${entries}</div>`
      : '<div class="empty-prompt">No real VirusTotal engine results are available for this target.</div>';
    vtModalBackdrop.classList.remove('hidden');
    vtModalBackdrop.setAttribute('aria-hidden', 'false');
  }

  function closeVtModal() {
    vtModalBackdrop.classList.add('hidden');
    vtModalBackdrop.setAttribute('aria-hidden', 'true');
  }

  function renderRecentScans(items) {
    if (!Array.isArray(items) || !items.length) {
      vtRecentBody.innerHTML = '<tr><td colspan="6" class="vt-empty-row">No real VirusTotal scans yet.</td></tr>';
      vtCacheCount.textContent = '0 cached';
      return;
    }

    vtCacheCount.textContent = `${items.length} cached`;
    vtRecentBody.innerHTML = items.map(item => {
      const verdict = verdictFromResult(item);
      const className = verdictClassFor(verdict);
      const indicator = item.indicator || 'Unknown';
      const type = String(item.indicator_type || 'HASH').toUpperCase();
      return `
        <tr class="vt-row" data-vt-indicator="${escapeHtml(indicator)}" data-vt-type="${escapeHtml(item.indicator_type || '')}">
          <td><span class="vt-row-indicator">${escapeHtml(indicator)}</span></td>
          <td><span class="vt-type-pill">${escapeHtml(type)}</span></td>
          <td><span class="vt-verdict-tag ${className}">${escapeHtml(verdict)}</span></td>
          <td>${escapeHtml(item.country || 'Unknown')}</td>
          <td>${escapeHtml(item.owner || 'Unknown')}</td>
          <td>${escapeHtml(formatRelativeTime(item.scanned_at || item.last_analysis_date))}</td>
        </tr>`;
    }).join('');

    vtRecentBody.querySelectorAll('.vt-row').forEach(row => {
      row.addEventListener('click', async () => {
        const indicator = row.dataset.vtIndicator;
        const type = row.dataset.vtType;
        try {
          const response = await fetch(apiUrl(`/api/virustotal/report?indicator=${encodeURIComponent(indicator)}${type ? `&indicator_type=${encodeURIComponent(type)}` : ''}`));
          const data = await response.json();
          if (!response.ok || data.error || !data.report) {
            throw new Error(data.error || 'The full report is unavailable.');
          }
          openVtModal(data.report);
        } catch (error) {
          openVtModal({
            indicator,
            engine_results: {},
            error: error.message,
          });
        }
      });
    });
  }

  async function refreshRecentScans() {
    try {
      const response = await fetch(apiUrl('/api/virustotal/recent'));
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || 'Recent VirusTotal scans could not be loaded.');
      renderRecentScans(data.items || []);
    } catch (error) {
      vtRecentBody.innerHTML = `<tr><td colspan="6" class="vt-empty-row">${escapeHtml(error.message)}</td></tr>`;
      vtCacheCount.textContent = '0 cached';
    }
  }

  async function runVtScan() {
    const target = vtScanInput.value.trim();
    if (!target) {
      vtScanResult.innerHTML = '<div class="empty-prompt">Enter an IP address, domain, URL or file hash.</div>';
      return;
    }

    setVtButtonLoading(true);
    vtScanResult.innerHTML = '<div class="loading-state">Scanning VirusTotal...</div>';

    try {
      const response = await fetch(apiUrl('/api/virustotal/scan'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ target }),
      });
      const data = await response.json();
      if (!response.ok || data.error) {
        throw new Error(data.error || data.detail || 'VirusTotal scan failed');
      }
      if (data.message && !data.indicator) {
        vtScanResult.innerHTML = `<div class="empty-prompt">${escapeHtml(data.message)}</div>`;
        return;
      }
      const malicious = Number(data.malicious_count || 0);
      const suspicious = Number(data.suspicious_count || 0);
      const verdict = verdictFromResult(data);
      const badgeClass = verdictClassFor(verdict);
      const severity = malicious > 0 ? 'High' : 'Low';
      const reportId = (data.report_id || data.source_id || '').toString();
      const safeTarget = data.indicator || target;
      const url = data.virustotal_url || `https://www.virustotal.com/gui/search/${encodeURIComponent(safeTarget)}`;

      vtScanResult.innerHTML = `
        <div class="vt-result-card">
          <div class="vt-result-row">
            <div class="vt-result-target">Target Scanned: ${escapeHtml(safeTarget)}</div>
            <span class="vt-verdict-pill ${badgeClass}">Detections: ${malicious} malicious engines</span>
          </div>
          <div class="vt-meta-row">
            <span>Saved in MongoDB Atlas (Report ID: ${escapeHtml(reportId ? `VT-${reportId.slice(-6)}` : 'VT-unknown')}), Severity: ${severity}.</span>
          </div>
          <div class="vt-actions">
            <button class="vt-report-btn" type="button" data-vt-report="${escapeHtml(JSON.stringify(data))}">🔍 View Full Report</button>
            <a class="vt-official-btn" href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">Official VT GUI ↗</a>
          </div>
        </div>`;

      const reportButton = vtScanResult.querySelector('[data-vt-report]');
      if (reportButton) {
        reportButton.addEventListener('click', () => openVtModal(data));
      }

      await refreshRecentScans();
    } catch (error) {
      vtScanResult.innerHTML = `<div class="empty-prompt">${escapeHtml(error.message)}</div>`;
    } finally {
      setVtButtonLoading(false);
    }
  }

  vtScanButton.addEventListener('click', runVtScan);
  vtScanInput.addEventListener('keydown', event => {
    if (event.key === 'Enter') {
      event.preventDefault();
      runVtScan();
    }
  });

  vtRefreshButton.addEventListener('click', refreshRecentScans);
  vtModalClose.addEventListener('click', closeVtModal);
  vtModalBackdrop.addEventListener('click', event => {
    if (event.target === vtModalBackdrop) closeVtModal();
  });

  refreshRecentScans();

  // Initial Load
  loadThreatStream();
  loadWorkingSetAnalysis();
  loadWiredTigerTelemetry();
  loadSourceSyncStatus();
});
