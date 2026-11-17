(() => {
  const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g, character => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  })[character]);
  const formatMs = value => value !== null && value !== undefined && Number.isFinite(Number(value))
    ? `${Number(value).toFixed(2)} ms`
    : 'Not measured';
  let activeProject = null;
  let pollTimer = null;
  let selectedConversation = null;

  async function api(url, options = {}) {
    const response = await fetch(url, {
      credentials: 'same-origin',
      headers: options.body instanceof File ? options.headers : {
        ...(options.body ? { 'Content-Type': 'application/json' } : {}),
        ...(options.headers || {}),
      },
      ...options,
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      const error = new Error(
        typeof payload.detail === 'string'
          ? payload.detail
          : payload.detail?.message || payload.detail?.detail || `Request failed with HTTP ${response.status}.`,
      );
      error.detail = payload.detail;
      error.status = response.status;
      throw error;
    }
    return payload;
  }

  function setMessage(target, message, kind = 'info') {
    target.innerHTML = message
      ? `<div class="vector-message is-${escapeHtml(kind)}" role="status">${escapeHtml(message)}</div>`
      : '';
  }

  function stageList(stages = []) {
    if (!stages.length) return '';
    return `<ol class="vector-stage-list">${stages.map(stage => `
      <li class="is-${escapeHtml(stage.status)}">
        <span>${escapeHtml(stage.name)}</span>
        <small>${escapeHtml(stage.status)}${stage.duration_ms != null ? ` · ${formatMs(stage.duration_ms)}` : ''}${stage.message ? ` · ${escapeHtml(stage.message)}` : ''}</small>
      </li>`).join('')}</ol>`;
  }

  function statusCard(label, value, note, state = 'neutral') {
    return `<article class="vector-status-card is-${escapeHtml(state)}"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong><small>${escapeHtml(note)}</small></article>`;
  }

  function ragTemplate() {
    return `
      <div class="vector-workspace rag-research">
        <div class="vector-workspace-heading">
          <div><span class="portfolio-kicker">AI RESEARCH / LIVE CAPABILITIES</span><h2>Intelligence research desk</h2><p>Ask questions across indexed threat reports, then inspect the source evidence behind each response.</p></div>
          <button type="button" class="xs-btn secondary" data-rag-refresh>Refresh status</button>
        </div>
        <div class="vector-status-grid" data-rag-status><p class="ops-empty">Checking provider and database configuration…</p></div>
        <div class="vector-notice" data-rag-notice></div>
        <div class="vector-workspace-grid rag-research-layout">
          <section class="vector-panel">
            <div class="vector-panel-heading"><div><span class="portfolio-kicker">OPTIONAL ADMIN TOOL</span><h3>Add a supplemental source</h3></div></div>
            <form data-rag-upload>
              <label>Source name<input name="source" maxlength="255" required placeholder="e.g. Engineering guide"></label>
              <label>Document text<textarea name="text" maxlength="200000" rows="8" required placeholder="Paste text to chunk and embed (up to 200,000 characters)."></textarea></label>
              <button class="portfolio-primary-button" type="submit">Chunk and embed document</button>
            </form>
            <div data-rag-upload-result aria-live="polite"></div>
            <div class="vector-document-list" data-rag-documents><p class="ops-empty">Loading stored documents…</p></div>
            <div class="vector-index-action">
              <div><strong>MongoDB Vector Search index</strong><small data-rag-index-note>Index readiness has not been checked.</small></div>
              <button type="button" class="xs-btn" data-rag-create-index disabled>Create index</button>
            </div>
            <div data-rag-index-result aria-live="polite"></div>
          </section>
          <section class="vector-panel">
            <div class="vector-panel-heading"><div><span class="portfolio-kicker">PRIVATE CONVERSATION</span><h3>Ask the intelligence corpus</h3></div><button type="button" class="xs-btn secondary" data-rag-forget>Forget my history</button></div>
            <div class="vector-conversation-tools">
              <label>Conversation<select data-rag-conversations><option value="">New conversation</option></select></label>
              <button type="button" class="xs-btn secondary" data-rag-new-conversation>New</button>
            </div>
            <div class="vector-chat-history" data-rag-history aria-live="polite"><p class="ops-empty">Choose or start a conversation.</p></div>
            <form data-rag-question>
              <label>Research question<textarea name="question" minlength="3" maxlength="500" rows="3" required placeholder="Ask about a threat, vulnerability, campaign or report."></textarea></label>
              <button class="portfolio-primary-button" type="submit">Search evidence and answer</button>
            </form>
            <div data-rag-answer-result aria-live="polite"></div>
          </section>
        </div>
      </div>`;
  }

  async function renderRagStatus(root) {
    const statusRoot = root.querySelector('[data-rag-status]');
    const note = root.querySelector('[data-rag-notice]');
    const indexButton = root.querySelector('[data-rag-create-index]');
    try {
      const status = await api('/api/ops/ask-index');
      const config = status.configuration || {};
      const vectors = status.vector_search || {};
      const documents = status.documents || {};
      const reportIndex = status.report_index || {};
      const reportVector = reportIndex.vector_search || {};
      statusRoot.innerHTML = [
        statusCard('Embedding provider', config.embedding_configured ? config.embedding_model : 'Not configured',
          config.embedding_configured ? `${config.embedding_provider || 'Configured provider'} · ${config.embedding_dimensions || 'dimensions validated per response'}` : 'Configure the server-side embedding provider.', config.embedding_configured ? 'ready' : 'warning'),
        statusCard('LLM provider', config.llm_configured ? `${config.llm_provider} · ${config.llm_model}` : 'Not configured',
          config.llm_configured ? 'Configured on the backend; provider credentials are never sent to the browser.' : 'Configure Ollama or the legacy OpenAI-compatible provider on the backend.', config.llm_configured ? 'ready' : 'warning'),
        statusCard('Report corpus', `${reportIndex.indexed_chunks || 0} indexed chunks`,
          `${reportIndex.collection || 'report index'} · ${reportIndex.dimensions || 'dimensions pending'} dimensions`, reportIndex.multiple_dimensions ? 'warning' : 'neutral'),
        statusCard('MongoDB Vector Search', reportVector.ready ? 'Query-ready' : reportVector.supported === false ? 'Unsupported' : reportVector.status || 'Not indexed',
          reportVector.detail || 'Structured retrieval remains available when semantic search is unavailable.', reportVector.ready ? 'ready' : reportVector.supported === false ? 'warning' : 'neutral'),
      ].join('');
      setMessage(note, (status.configuration_error?.message ? `${status.configuration_error.message} ` : '')
        + (status.limitations || []).join(' '), vectors.supported === false ? 'warning' : 'info');
      root.querySelector('[data-rag-index-note]').textContent = reportVector.detail || 'Index readiness has not been confirmed.';
      indexButton.disabled = !config.embedding_configured || reportVector.ready || reportIndex.multiple_dimensions;
      indexButton.dataset.ready = String(reportVector.ready);
      await renderRagDocuments(root);
    } catch (error) {
      statusRoot.innerHTML = `<p class="ops-empty">${escapeHtml(error.message)}</p>`;
      setMessage(note, 'RAG status could not be loaded. Confirm your administrator session and MongoDB connectivity.', 'error');
    }
  }

  async function renderRagDocuments(root) {
    const target = root.querySelector('[data-rag-documents]');
    try {
      const result = await api('/api/rag/documents?limit=20');
      target.innerHTML = result.documents.length ? `
        <h4>Recent stored documents</h4>
        <ul>${result.documents.map(document => `
          <li><span><strong>${escapeHtml(document.source)}</strong><small>${escapeHtml(document.chunk_count)} chunks · ${escapeHtml(document.status)}</small></span>
          <time>${escapeHtml(document.created_at ? new Date(document.created_at).toLocaleString() : '—')}</time></li>`).join('')}</ul>`
        : '<p class="ops-empty">No documents have been added.</p>';
    } catch (error) {
      target.innerHTML = `<p class="ops-empty">${escapeHtml(error.message)}</p>`;
    }
  }

  async function renderConversations(root) {
    const select = root.querySelector('[data-rag-conversations]');
    const priorSelection = selectedConversation;
    try {
      const result = await api('/api/ask/history?limit=20');
      select.innerHTML = `<option value="">New conversation</option>${result.conversations.map(item => `
        <option value="${escapeHtml(item.conversation_id)}">${escapeHtml(item.title || 'Conversation')} · ${escapeHtml(new Date(item.updated_at).toLocaleString())}</option>`).join('')}`;
      if (priorSelection && result.conversations.some(item => item.conversation_id === priorSelection)) {
        select.value = priorSelection;
      } else {
        selectedConversation = null;
      }
    } catch (error) {
      setMessage(root.querySelector('[data-rag-answer-result]'), error.message, 'error');
    }
  }

  async function loadConversation(root, conversationId) {
    const history = root.querySelector('[data-rag-history]');
    selectedConversation = conversationId || null;
    if (!conversationId) {
      history.innerHTML = '<p class="ops-empty">A new conversation will be created when you send a question.</p>';
      return;
    }
    try {
      const result = await api(`/api/ask/history/${encodeURIComponent(conversationId)}`);
      history.innerHTML = result.messages.length ? result.messages.map(message => `
        <article class="vector-message-row is-${escapeHtml(message.role)}">
          <strong>${message.role === 'assistant' ? 'Assistant' : 'You'}</strong>
          <p>${escapeHtml(message.content)}</p>
        </article>`).join('') : '<p class="ops-empty">No messages in this conversation yet.</p>';
      history.scrollTop = history.scrollHeight;
    } catch (error) {
      history.innerHTML = `<p class="ops-empty">${escapeHtml(error.message)}</p>`;
    }
  }

  async function initializeRag(root) {
    root.innerHTML = ragTemplate();
    selectedConversation = null;
    await Promise.all([renderRagStatus(root), renderConversations(root)]);

    root.querySelector('[data-rag-refresh]').addEventListener('click', () => renderRagStatus(root));
    root.querySelector('[data-rag-conversations]').addEventListener('change', event => loadConversation(root, event.target.value));
    root.querySelector('[data-rag-new-conversation]').addEventListener('click', async () => {
      selectedConversation = null;
      root.querySelector('[data-rag-conversations]').value = '';
      await loadConversation(root, null);
    });
    root.querySelector('[data-rag-forget]').addEventListener('click', async event => {
      const button = event.currentTarget;
      button.disabled = true;
      try {
        await api('/api/ask/history', { method: 'DELETE' });
        selectedConversation = null;
        await renderConversations(root);
        await loadConversation(root, null);
        setMessage(root.querySelector('[data-rag-answer-result]'), 'Your conversation history was deleted.', 'success');
      } catch (error) {
        setMessage(root.querySelector('[data-rag-answer-result]'), error.message, 'error');
      } finally { button.disabled = false; }
    });
    root.querySelector('[data-rag-upload]').addEventListener('submit', async event => {
      event.preventDefault();
      const form = event.currentTarget;
      const result = root.querySelector('[data-rag-upload-result]');
      const button = form.querySelector('button[type="submit"]');
      button.disabled = true;
      result.innerHTML = '<p class="ops-empty">Chunking and embedding document…</p>';
      try {
        const payload = Object.fromEntries(new FormData(form).entries());
        const response = await api('/api/rag/documents', { method: 'POST', body: JSON.stringify(payload) });
        result.innerHTML = `${stageList(response.stages)}<div class="vector-message is-success" role="status">Document ${escapeHtml(response.status)}: ${escapeHtml(response.embedded_chunks)} of ${escapeHtml(response.chunk_count)} chunks embedded at ${escapeHtml(response.dimensions || 'validated')} dimensions.</div>`;
        form.reset();
        await renderRagStatus(root);
      } catch (error) {
        const stages = error.detail?.stages || [];
        result.innerHTML = `${stageList(stages)}<div class="vector-message is-error" role="alert">${escapeHtml(error.message)}</div>`;
      } finally {
        button.disabled = false;
      }
    });
    root.querySelector('[data-rag-create-index]').addEventListener('click', async event => {
      const button = event.currentTarget;
      button.disabled = true;
      try {
        const result = await api('/api/ops/ask-index', { method: 'POST', body: '{}' });
        setMessage(root.querySelector('[data-rag-index-result]'), `Indexed ${result.indexed_chunks} report chunks. Vector Search status: ${result.index_status}.`, result.ready ? 'success' : 'info');
        await renderRagStatus(root);
      } catch (error) {
        setMessage(root.querySelector('[data-rag-index-result]'), error.message, 'error');
      }
    });
    root.querySelector('[data-rag-question]').addEventListener('submit', async event => {
      event.preventDefault();
      const form = event.currentTarget;
      const target = root.querySelector('[data-rag-answer-result]');
      const button = form.querySelector('button[type="submit"]');
      button.disabled = true;
      target.innerHTML = '<p class="ops-empty">Embedding query, retrieving sources and generating an answer…</p>';
      try {
        const result = await api('/api/ask', {
          method: 'POST',
          body: JSON.stringify({
            question: new FormData(form).get('question'),
            conversation_id: selectedConversation,
          }),
        });
        selectedConversation = result.conversation_id;
        await renderConversations(root);
        root.querySelector('[data-rag-conversations]').value = selectedConversation;
        await loadConversation(root, selectedConversation);
        target.innerHTML = `
          <div class="vector-message is-success" role="status">${escapeHtml(result.mode || result.response_mode || 'Answer')} · ${escapeHtml(result.retrieval_mode || 'retrieval mode unreported')} · ${escapeHtml(result.metrics.retrieved_chunks)} retrieved chunks · embedding ${formatMs(result.metrics.query_embedding_ms)} · generation ${formatMs(result.metrics.llm_generation_ms)}</div>
          ${stageList(result.stages)}
          <div class="vector-source-list"><h4>Retrieved source chunks</h4>${result.sources.length ? result.sources.map((source, index) => `
            <details><summary>[S${index + 1}] ${escapeHtml(source.source)}${source.page ? ` · page ${escapeHtml(source.page)}` : ''}${Number.isFinite(Number(source.score)) ? ` · score ${escapeHtml(Number(source.score).toFixed(4))}` : ''}</summary><p>${escapeHtml(source.text)}</p><small>${escapeHtml(source.chunk_id)}${source.source_reference ? ` · ${escapeHtml(source.source_reference)}` : ''}</small></details>`).join('') : '<p class="ops-empty">No source chunks were retrieved.</p>'}</div>`;
        form.reset();
      } catch (error) {
        target.innerHTML = `${stageList(error.detail?.stages || [])}<div class="vector-message is-error" role="alert">${escapeHtml(error.message)}</div>`;
        if (error.detail?.conversation_id) {
          selectedConversation = error.detail.conversation_id;
          await renderConversations(root);
          root.querySelector('[data-rag-conversations]').value = selectedConversation;
          await loadConversation(root, selectedConversation);
        }
      } finally {
        button.disabled = false;
      }
    });
  }

  function vectorChart(values, label, unit = 'ms') {
    const numbers = values.map(Number).filter(Number.isFinite);
    if (!numbers.length) return '<p class="ops-empty">No completed measurements to chart.</p>';
    const width = 620;
    const height = 180;
    const pad = 24;
    const max = Math.max(...numbers, 0.001);
    const points = numbers.map((value, index) => {
      const x = pad + index * (width - pad * 2) / Math.max(numbers.length - 1, 1);
      const y = height - pad - value / max * (height - pad * 2);
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    }).join(' ');
    return `<svg class="vector-chart" viewBox="0 0 ${width} ${height}" role="img" aria-label="${escapeHtml(label)}">${[0, .5, 1].map(fraction => {
      const y = pad + fraction * (height - pad * 2);
      return `<line x1="${pad}" y1="${y}" x2="${width - pad}" y2="${y}" class="vector-chart-grid"></line>`;
    }).join('')}<polyline points="${points}" fill="none" stroke="#a65349" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"></polyline></svg><small>${numbers.length} measured queries · ${escapeHtml(unit)} · sampled in execution order</small>`;
  }

  function benchmarkTemplate() {
    return `
      <div class="vector-workspace">
        <div class="vector-workspace-heading">
          <div><span class="portfolio-kicker">PERFORMANCE EVALUATION / MEASURED RESULTS</span><h2>HNSW &amp; IVF vector search benchmark</h2><p>Prepare a real text dataset, build a supported Atlas Vector Search index, then measure ANN against exact-search baselines.</p></div>
          <button type="button" class="xs-btn secondary" data-bench-refresh>Refresh benchmark</button>
        </div>
        <div class="vector-status-grid" data-bench-status><p class="ops-empty">Checking model, dataset and Vector Search capabilities…</p></div>
        <div class="vector-notice" data-bench-notice></div>
        <div class="vector-benchmark-grid">
          <section class="vector-panel">
            <div class="vector-panel-heading"><div><span class="portfolio-kicker">DATASET</span><h3>Upload JSONL records</h3></div></div>
            <p>Exactly 5,000 UTF-8 JSON objects, one per line. Each record requires a <code>source</code> and <code>text</code> field. New dataset versions are added without deleting previous records.</p>
            <form data-bench-upload>
              <label>JSONL file<input type="file" name="dataset" accept=".jsonl,.ndjson,application/json,text/plain" required></label>
              <button type="submit" class="portfolio-primary-button" data-bench-upload-button>Embed and store 5,000 records</button>
            </form>
            <div data-bench-upload-result aria-live="polite"></div>
            <div class="vector-progress" data-bench-progress hidden></div>
            <div class="vector-index-action">
              <div><strong>MongoDB Vector Search index</strong><small data-bench-index-note>Prepare a dataset before requesting an index.</small></div>
              <button type="button" class="xs-btn" data-bench-create-index disabled>Create index</button>
            </div>
            <button type="button" class="portfolio-primary-button vector-run-button" data-bench-run disabled>Run 100-query benchmark</button>
          </section>
          <section class="vector-panel">
            <div class="vector-panel-heading"><div><span class="portfolio-kicker">MEASURED RESULTS</span><h3>ANN, ENN &amp; exact baseline</h3></div></div>
            <div data-bench-results><p class="ops-empty">No completed benchmark run is available. Results will appear here only after a real run finishes.</p></div>
          </section>
        </div>
        <section class="vector-panel vector-config-panel">
          <div class="vector-panel-heading"><div><span class="portfolio-kicker">CAPABILITY BOUNDARIES</span><h3>Supported and unsupported configurations</h3></div></div>
          <div class="vector-config-grid" data-bench-configs></div>
        </section>
      </div>`;
  }

  function renderBenchmarkResults(target, run) {
    const result = run?.result;
    if (!run || run.status !== 'completed' || !result?.measurements) {
      target.innerHTML = '<p class="ops-empty">No completed benchmark run is available. Results will appear here only after a real run finishes.</p>';
      return;
    }
    const measurements = result.measurements;
    const ann = measurements.atlas_ann || {};
    const enn = measurements.atlas_enn || {};
    const exact = measurements.local_exact || {};
    target.innerHTML = `
      <div class="vector-result-context">Run ${escapeHtml(run.run_id)} · completed ${escapeHtml(run.completed_at ? new Date(run.completed_at).toLocaleString() : 'time not recorded')} · ${escapeHtml(result.dataset_size)} vectors · ${escapeHtml(result.dimensions)} dimensions · ${escapeHtml(result.query_count)} queries · top-${escapeHtml(result.top_k)}</div>
      <div class="vector-metric-grid">
        ${[
          ['Atlas ANN avg', ann.average_ms, formatMs(ann.average_ms), `p95 ${formatMs(ann.p95_ms)}`],
          ['ANN recall@k', ann.recall_at_k_percent, `${Number(ann.recall_at_k_percent).toFixed(2)}%`, 'Against local exact cosine top-k'],
          ['Atlas ENN avg', enn.average_ms, formatMs(enn.average_ms), `recall@k ${enn.recall_at_k_percent ?? 'not measured'}% · p95 ${formatMs(enn.p95_ms)}`],
          ['Local exact avg', exact.average_ms, formatMs(exact.average_ms), `p95 ${formatMs(exact.p95_ms)}`],
        ].map(([label, value, display, note]) => `<article class="vector-metric-card"><span>${escapeHtml(label)}</span><strong>${escapeHtml(display)}</strong><small>${escapeHtml(note)}</small></article>`).join('')}
      </div>
      <div class="vector-result-charts">
        <div><h4>ANN query latency</h4>${vectorChart(ann.latency_samples_ms || [], 'Atlas ANN query latency', 'ms')}</div>
        <div><h4>ANN recall by query</h4>${vectorChart(ann.recall_samples_percent || [], 'ANN recall per query', '%')}</div>
      </div>
      <div class="vector-message is-info">${escapeHtml(result.recommendation || 'No recommendation was recorded.')}</div>
      <p class="vector-muted">Index build time: ${formatMs(result.index_build_time_ms)}${result.index_build_time_ms == null ? ' (not measured by this application)' : ' (first-observed readiness interval)'}. No latency from other deployments or datasets is implied.</p>`;
  }

  async function renderBenchmark(root) {
    const statusTarget = root.querySelector('[data-bench-status]');
    const notice = root.querySelector('[data-bench-notice]');
    const configs = root.querySelector('[data-bench-configs]');
    const uploadButton = root.querySelector('[data-bench-upload-button]');
    const indexButton = root.querySelector('[data-bench-create-index]');
    const runButton = root.querySelector('[data-bench-run]');
    try {
      const status = await api('/api/vector-benchmark/status');
      const caps = status.capabilities || {};
      const dataset = status.dataset || {};
      const index = status.index || {};
      statusTarget.innerHTML = [
        statusCard('Embedding model', caps.embedding_model, caps.ready_to_embed ? 'Installed and ready' : 'Optional model dependencies are missing.', caps.ready_to_embed ? 'ready' : 'warning'),
        statusCard('Dataset', `${dataset.record_count || 0} / ${dataset.required_records}`, dataset.ready ? 'Complete and validated' : 'A valid 5,000-record dataset is required.', dataset.ready ? 'ready' : 'neutral'),
        statusCard('Vector Search index', index.ready ? 'Query-ready' : index.supported === false ? 'Unsupported' : index.status || 'Not indexed', index.detail || 'Index state is not available.', index.ready ? 'ready' : index.supported === false ? 'warning' : 'neutral'),
        statusCard('Benchmark queries', '100', `Top-${status.limits?.top_k || 10} · ${status.limits?.insert_batch_size || 500}-record batches`, 'neutral'),
      ].join('');
      const limitations = [
        caps.install_note,
        index.supported === false ? index.detail : null,
        index.build_time_note,
        'Atlas ANN is measured using the managed index settings. IVF and custom HNSW M / efConstruction are unsupported and are not simulated.',
      ].filter(Boolean);
      setMessage(notice, limitations.join(' '), index.supported === false || !caps.ready_to_embed ? 'warning' : 'info');
      uploadButton.disabled = !caps.ready_to_embed || Boolean(status.active_job);
      indexButton.disabled = !dataset.ready || Boolean(status.active_job) || index.ready || index.exists || index.supported === false;
      runButton.disabled = !dataset.ready || !index.ready || Boolean(status.active_job) || !caps.numpy_installed;
      root.querySelector('[data-bench-index-note]').textContent = index.detail || 'Prepare a dataset before requesting an index.';
      configs.innerHTML = (status.configurations || []).map(configuration => `
        <article class="vector-config-card is-${escapeHtml(configuration.status)}">
          <span>${escapeHtml(configuration.status)}</span><strong>${escapeHtml(configuration.label)}</strong><p>${escapeHtml(configuration.note)}</p>
        </article>`).join('');
      const progress = root.querySelector('[data-bench-progress]');
      if (status.active_job) {
        const job = status.active_job;
        progress.hidden = false;
        progress.innerHTML = `<div><strong>${escapeHtml(job.job_type === 'dataset' ? 'Dataset embedding and insertion' : 'Vector benchmark')} · ${escapeHtml(job.stage)}</strong><span>${escapeHtml(job.progress_percent || 0)}%</span></div><progress max="100" value="${escapeHtml(job.progress_percent || 0)}"></progress><small>${escapeHtml(job.completed_units || 0)} / ${escapeHtml(job.total_units || 0)} · ${escapeHtml(job.status)}</small>`;
      } else {
        progress.hidden = true;
        if (status.latest_run?.status === 'failed') {
          progress.hidden = false;
          progress.innerHTML = `<div><strong>Last run failed</strong><span>${escapeHtml(status.latest_run.stage || 'unknown stage')}</span></div><p class="vector-muted">${escapeHtml(status.latest_run.error_message || 'No safe failure message was recorded.')}</p>`;
        }
      }
      const results = root.querySelector('[data-bench-results]');
      renderBenchmarkResults(results, status.latest_completed_run);
      if (status.latest_run?.status === 'failed' && status.latest_completed_run
        && new Date(status.latest_run.created_at) > new Date(status.latest_completed_run.completed_at)) {
        results.insertAdjacentHTML('afterbegin', '<div class="vector-message is-warning">Showing the last completed run; a newer benchmark attempt failed.</div>');
      }
      if (status.active_job) scheduleBenchmarkPoll(root);
      else if (pollTimer) {
        clearTimeout(pollTimer);
        pollTimer = null;
      }
    } catch (error) {
      statusTarget.innerHTML = `<p class="ops-empty">${escapeHtml(error.message)}</p>`;
      setMessage(notice, 'Benchmark status could not be loaded. Confirm your administrator session and database connectivity.', 'error');
    }
  }

  function scheduleBenchmarkPoll(root) {
    if (pollTimer) clearTimeout(pollTimer);
    pollTimer = setTimeout(() => {
      if (activeProject === 'benchmark' && root.isConnected) renderBenchmark(root);
    }, 2000);
  }

  async function initializeBenchmark(root) {
    root.innerHTML = benchmarkTemplate();
    await renderBenchmark(root);
    root.querySelector('[data-bench-refresh]').addEventListener('click', () => renderBenchmark(root));
    root.querySelector('[data-bench-upload]').addEventListener('submit', async event => {
      event.preventDefault();
      const form = event.currentTarget;
      const file = form.querySelector('input[type="file"]').files[0];
      const target = root.querySelector('[data-bench-upload-result]');
      const button = form.querySelector('button[type="submit"]');
      if (!file) return;
      button.disabled = true;
      target.innerHTML = '<p class="ops-empty">Validating JSONL and starting the dataset job…</p>';
      try {
        const response = await api('/api/vector-benchmark/dataset', {
          method: 'POST',
          body: file,
          headers: { 'Content-Type': 'application/x-ndjson' },
        });
        target.innerHTML = `<div class="vector-message is-success" role="status">Dataset job ${escapeHtml(response.status)} · ${escapeHtml(response.job_id)}</div>`;
        await renderBenchmark(root);
      } catch (error) {
        target.innerHTML = `<div class="vector-message is-error" role="alert">${escapeHtml(error.message)}</div>`;
      } finally {
        button.disabled = false;
      }
    });
    root.querySelector('[data-bench-create-index]').addEventListener('click', async event => {
      const button = event.currentTarget;
      button.disabled = true;
      try {
        const result = await api('/api/vector-benchmark/index', { method: 'POST', body: '{}' });
        setMessage(root.querySelector('[data-bench-upload-result]'), result.message, 'success');
        await renderBenchmark(root);
      } catch (error) {
        setMessage(root.querySelector('[data-bench-upload-result]'), error.message, 'error');
      }
    });
    root.querySelector('[data-bench-run]').addEventListener('click', async event => {
      const button = event.currentTarget;
      button.disabled = true;
      try {
        const result = await api('/api/vector-benchmark/runs', { method: 'POST', body: '{}' });
        setMessage(root.querySelector('[data-bench-upload-result]'), `Benchmark ${result.status}: ${result.run_id}`, 'success');
        await renderBenchmark(root);
      } catch (error) {
        setMessage(root.querySelector('[data-bench-upload-result]'), error.message, 'error');
      }
    });
  }

  function openProject(projectId, requestedRoot) {
    const root = requestedRoot || document.getElementById('vector-project-workspace');
    if (!root || root.dataset.vectorProject !== projectId) return;
    activeProject = projectId;
    if (pollTimer) {
      clearTimeout(pollTimer);
      pollTimer = null;
    }
    if (projectId === 'rag') initializeRag(root);
    if (projectId === 'benchmark') initializeBenchmark(root);
  }

  document.addEventListener('portfolio:project-opened', event => openProject(event.detail?.id, event.detail?.root));
  document.addEventListener('portfolio:project-closed', () => {
    activeProject = null;
    if (pollTimer) {
      clearTimeout(pollTimer);
      pollTimer = null;
    }
  });
})();
