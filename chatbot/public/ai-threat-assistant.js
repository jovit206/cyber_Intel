(() => {
  const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g, character => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  })[character]);
  let root = null;
  let selectedConversation = null;
  let draftConversationId = null;
  let sending = false;
  let viewRevision = 0;
  const sourceCache = new Map();

  function newConversationId() {
    return window.crypto?.randomUUID?.() || `chat-${Date.now()}-${Math.random().toString(36).slice(2)}`;
  }

  async function api(url, options = {}) {
    let response;
    try {
      response = await fetch(url, {
        credentials: 'same-origin',
        headers: { ...(options.body ? { 'Content-Type': 'application/json' } : {}), ...(options.headers || {}) },
        ...options,
      });
    } catch (cause) {
      const error = new Error('Cannot reach the Threat Intelligence API. Check that the application server is running, then try again.');
      error.cause = cause;
      throw error;
    }
    const raw = await response.text();
    let payload = {};
    try { payload = raw ? JSON.parse(raw) : {}; } catch { payload = { detail: raw }; }
    if (!response.ok) {
      const detail = payload.detail;
      const message = typeof detail === 'string' ? detail
        : detail?.message || payload.message || payload.error?.message
          || (Array.isArray(payload.detail) ? payload.detail.map(item => item.msg).filter(Boolean).join('; ') : '');
      const code = detail?.code || payload.code;
      const stage = detail?.stage;
      const fallback = response.status === 401 ? 'Your session has expired. Sign in and try again.'
        : `The assistant request failed (HTTP ${response.status}${code ? `, ${code}` : ''}${stage ? ` during ${stage.replaceAll('_', ' ')}` : ''}).`;
      const error = new Error(message || fallback);
      error.status = response.status;
      error.payload = payload;
      throw error;
    }
    return payload;
  }

  function formatTime(value) {
    if (!value) return '';
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? '' : date.toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' });
  }

  function updateWelcome(show) {
    root.querySelector('[data-ai-welcome]').hidden = !show;
    const messages = root.querySelector('[data-ai-message-list]');
    messages.hidden = show;
  }

  // Retrieved sources are still returned, validated and stored with each answer; they are not drawn in the chat.
  // Inline evidence markers such as [S1] refer to those hidden sources, so they are removed from the displayed text.
  const displayText = (text, assistant) => (assistant ? String(text ?? '').replace(/\s?\[S\d+\](?:\s?,\s?\[S\d+\])*/g, '') : String(text ?? ''));

  function renderMessage(message) {
    const assistant = message.role === 'assistant';
    const label = assistant ? 'AI Threat Assistant' : 'You';
    return `<article class="ai-message ${assistant ? 'is-assistant' : 'is-user'}">
      <div class="ai-message-bubble">
        <div class="ai-message-meta"><strong>${label}</strong><time>${escapeHtml(formatTime(message.timestamp))}</time></div>
        <div class="ai-message-content">${escapeHtml(displayText(message.content, assistant))}</div>
      </div>
    </article>`;
  }

  // ---------------- local laptop agent: status, progress, cancel and confirmation
  let pendingConfirmation = null;
  async function refreshAgentStatus() {
    const pill = root.querySelector('[data-ai-agent-status]');
    if (!pill) return;
    try {
      const status = await api('/api/agent/status');
      if (!status.allowed) { pill.hidden = true; return; }
      pill.hidden = false;
      pill.classList.toggle('is-connected', Boolean(status.connected));
      pill.textContent = status.connected ? 'Security agent: connected' : 'Security agent: offline';
      pill.title = status.connected ? `Local security agent ${status.version || ''}: cybersecurity inspection only` : (status.message || 'Local agent offline');
    } catch { pill.hidden = true; }
  }

  function showAgentProgress(event) {
    const pending = root.querySelector('[data-ai-pending]');
    if (!pending) return;
    let box = pending.querySelector('[data-ai-agent-progress]');
    if (!box) {
      pending.querySelector('.ai-message-bubble').insertAdjacentHTML('beforeend', `<div class="ai-agent-progress" data-ai-agent-progress role="status">
        <div class="ai-agent-progress-bar"><span></span></div><p></p><button type="button" class="ai-agent-cancel" data-ai-agent-cancel>Cancel scan</button></div>`);
      box = pending.querySelector('[data-ai-agent-progress]');
      box.querySelector('[data-ai-agent-cancel]').addEventListener('click', async eventClick => {
        const button = eventClick.currentTarget;
        button.disabled = true; button.textContent = 'Cancelling…';
        try { await api(`/api/agent/jobs/${encodeURIComponent(box.dataset.jobId)}/cancel`, { method: 'POST' }); }
        catch (error) { button.textContent = error.message; }
      });
    }
    box.dataset.jobId = event.job_id || '';
    const fraction = typeof event.fraction === 'number' ? Math.max(0, Math.min(1, event.fraction)) : null;
    box.querySelector('.ai-agent-progress-bar').classList.toggle('is-indeterminate', fraction === null);
    box.querySelector('.ai-agent-progress-bar span').style.width = fraction === null ? '' : `${Math.round(fraction * 100)}%`;
    box.querySelector('p').textContent = `${String(event.tool || 'scan').replaceAll('_', ' ')}: ${event.message || 'Working…'} · ${Math.round(event.elapsed_seconds || 0)} s`;
    scrollToLatest();
  }

  async function askForConfirmation(confirmation) {
    const dialog = root.querySelector('[data-ai-confirm-dialog]');
    if (!dialog || !confirmation) return;
    dialog.querySelector('[data-ai-confirm-description]').textContent = confirmation.description || confirmation.tool;
    dialog.querySelector('[data-ai-confirm-reason]').textContent = confirmation.reason || '';
    dialog.returnValue = '';
    dialog.showModal();
    await new Promise(resolve => dialog.addEventListener('close', resolve, { once: true }));
    const approve = dialog.returnValue === 'approve';
    try {
      const result = await api(`/api/agent/confirmations/${encodeURIComponent(confirmation.confirmation_id)}`, {
        method: 'POST', body: JSON.stringify({ approve }),
      });
      if (approve && result.id) {
        await sendQuestion(`Approved: ${confirmation.description}`, { agent_job_id: result.id });
      } else {
        const list = root.querySelector('[data-ai-message-list]');
        list.insertAdjacentHTML('beforeend', renderMessage({ role: 'assistant', content: 'Declined. Nothing was changed.', timestamp: new Date().toISOString() }));
        scrollToLatest();
      }
    } catch (error) {
      const list = root.querySelector('[data-ai-message-list]');
      list.insertAdjacentHTML('beforeend', renderMessage({ role: 'assistant', content: error.message, timestamp: new Date().toISOString() }).replace('class="ai-message ', 'data-ai-error class="ai-message '));
      scrollToLatest();
    }
  }

  function scrollToLatest() {
    const body = root.querySelector('[data-ai-chat-body]');
    requestAnimationFrame(() => { body.scrollTop = body.scrollHeight; });
  }

  function showConversation(messages) {
    const list = root.querySelector('[data-ai-message-list]');
    updateWelcome(!messages.length);
    list.innerHTML = messages.map(renderMessage).join('');
    scrollToLatest();
  }

  async function refreshHistory() {
    const list = root.querySelector('[data-ai-history-list]');
    try {
      const result = await api('/api/ask/history?limit=30&client=ai-threat-assistant');
      const conversations = result.conversations || [];
      list.innerHTML = conversations.length ? conversations.map(item => `
        <button class="ai-history-item ${item.conversation_id === selectedConversation ? 'is-active' : ''}" type="button" data-ai-conversation="${escapeHtml(item.conversation_id)}" ${item.conversation_id === selectedConversation ? 'aria-current="true"' : ''}>
          <span class="ai-history-title">${escapeHtml(item.title || 'New conversation')}</span>
          <time>${escapeHtml(formatTime(item.updated_at))}</time>
        </button>`).join('') : '<p class="ai-history-empty">Your conversations will appear here.</p>';
    } catch (error) {
      list.innerHTML = `<p class="ai-history-empty">${escapeHtml(error.message)}</p>`;
    }
  }

  async function openConversation(conversationId) {
    const revision = ++viewRevision;
    selectedConversation = conversationId || null;
    draftConversationId = selectedConversation ? null : newConversationId();
    if (!selectedConversation) {
      root.querySelector('[data-ai-chat-context]').textContent = 'New conversation';
      showConversation([]);
      return;
    }
    root.querySelector('[data-ai-chat-context]').textContent = 'Loading conversation';
    showConversation([]);
    try {
      const result = await api(`/api/ask/history/${encodeURIComponent(selectedConversation)}`);
      if (revision !== viewRevision) return;
      for (const message of result.messages || []) {
        for (const source of message.sources || []) if (source.chunk_id) sourceCache.set(String(source.chunk_id), source);
      }
      root.querySelector('[data-ai-chat-context]').textContent = result.conversation?.title || 'Conversation';
      showConversation(result.messages || []);
      await refreshHistory();
    } catch (error) {
      if (revision !== viewRevision) return;
      showConversation([{ role: 'assistant', content: error.message, timestamp: new Date().toISOString() }]);
    }
  }

  function addPending(question) {
    const list = root.querySelector('[data-ai-message-list]');
    updateWelcome(false);
    list.insertAdjacentHTML('beforeend', renderMessage({ role: 'user', content: question, timestamp: new Date().toISOString() }).replace('<article class="ai-message', '<article data-ai-transient-user class="ai-message') + `
      <article class="ai-message is-assistant ai-pending" data-ai-pending><div class="ai-message-bubble"><div class="ai-message-meta"><strong>AI Threat Assistant</strong></div><div class="ai-thinking" role="status"><span></span><span></span><span></span><em>Thinking</em></div></div></article>`);
    list.querySelector('[data-ai-error]')?.remove();
    scrollToLatest();
  }

  // Stream the answer from /api/ask/stream (newline-delimited JSON: meta, token..., final | error) and show
  // it as it is generated. Returns the final result, or null when the browser cannot stream so the caller
  // falls back to the complete-answer endpoint. The answer is persisted server-side only after "final".
  async function streamAnswer(body) {
    if (!window.ReadableStream || !window.TextDecoder) return null;
    let response;
    try {
      response = await fetch('/api/ask/stream', {
        method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body,
      });
    } catch (cause) {
      const error = new Error('Cannot reach the Threat Intelligence API. Check that the application server is running, then try again.');
      error.cause = cause;
      throw error;
    }
    if (!response.ok || !response.body) {
      const raw = await response.text();
      let payload = {};
      try { payload = raw ? JSON.parse(raw) : {}; } catch { payload = { detail: raw }; }
      const detail = payload.detail;
      const message = typeof detail === 'string' ? detail : detail?.message
        || (Array.isArray(detail) ? detail.map(item => item.msg).filter(Boolean).join('; ') : '');
      throw new Error(message || (response.status === 401 ? 'Your session has expired. Sign in and try again.'
        : `The assistant request failed (HTTP ${response.status}).`));
    }
    const pending = root.querySelector('[data-ai-pending]');
    let bubble = null;
    let streamed = '';
    let final = null;
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    const handle = event => {
      if (event.type === 'token') {
        if (!bubble && pending) {
          pending.classList.remove('ai-pending');
          pending.querySelector('.ai-thinking')?.remove();
          pending.querySelector('.ai-message-bubble').insertAdjacentHTML('beforeend', '<div class="ai-message-content" aria-live="polite"></div>');
          bubble = pending.querySelector('.ai-message-content');
        }
        streamed += event.text || '';
        if (bubble) bubble.textContent = displayText(streamed, true);
        scrollToLatest();
      } else if (event.type === 'agent_progress') {
        showAgentProgress(event);
      } else if (event.type === 'agent_status') {
        refreshAgentStatus();
      } else if (event.type === 'confirmation_required') {
        pendingConfirmation = event;
      } else if (event.type === 'final') {
        final = event;
      } else if (event.type === 'error') {
        throw new Error(event.message || 'The assistant could not answer this question.');
      }
    };
    for (;;) {
      const { value, done } = await reader.read();
      buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
      let newline;
      while ((newline = buffer.indexOf('\n')) >= 0) {
        const line = buffer.slice(0, newline).trim();
        buffer = buffer.slice(newline + 1);
        if (line) handle(JSON.parse(line));
      }
      if (done) break;
    }
    if (buffer.trim()) handle(JSON.parse(buffer.trim()));
    if (!final) throw new Error('The assistant stopped before finishing. Your question was not added to conversation history.');
    return final;
  }

  async function sendQuestion(question, extra = {}) {
    if (sending || !question.trim()) return;
    const text = question.trim().slice(0, 500);
    if (text.length < 3) return;
    sending = true;
    pendingConfirmation = null;
    let confirmation = null;
    const form = root.querySelector('[data-ai-chat-form]');
    const textarea = form.elements.question;
    const send = root.querySelector('[data-ai-send]');
    textarea.disabled = true;
    send.disabled = true;
    addPending(text);
    const requestBody = JSON.stringify({
      question: text,
      conversation_id: selectedConversation || draftConversationId,
      platform: root.querySelector('[data-ai-platform]').value || null,
      threat_status: root.querySelector('[data-ai-threat-status]').value || null,
      client: 'ai-threat-assistant',
      ...(extra.agent_job_id ? { agent_job_id: extra.agent_job_id } : {}),
    });
    try {
      const result = (await streamAnswer(requestBody)) || await api('/api/ask', { method: 'POST', body: requestBody });
      if (typeof result.answer !== 'string' || !result.answer.trim() || !result.conversation_id) {
        throw new Error('The assistant returned an incomplete response. Your question was not added to conversation history.');
      }
      selectedConversation = result.conversation_id;
      draftConversationId = null;
      for (const source of result.sources || []) if (source.chunk_id) sourceCache.set(String(source.chunk_id), source);
      if (!extra.agent_job_id) {
        textarea.value = '';
        textarea.style.height = '';
      }
      confirmation = pendingConfirmation;
      await openConversation(selectedConversation);
    } catch (error) {
      root.querySelector('[data-ai-pending]')?.remove();
      root.querySelector('[data-ai-transient-user]')?.remove();
      const list = root.querySelector('[data-ai-message-list]');
      list.insertAdjacentHTML('beforeend', renderMessage({ role: 'assistant', content: error.message, timestamp: new Date().toISOString() }).replace('class="ai-message ', 'data-ai-error class="ai-message '));
      updateWelcome(!selectedConversation && !draftConversationId);
      scrollToLatest();
    } finally {
      sending = false;
      textarea.disabled = false;
      send.disabled = false;
      textarea.focus();
    }
    // Open the permission dialog only after this request has fully finished, so the approved follow-up can be sent.
    if (confirmation) askForConfirmation(confirmation);
  }

  // The [data-ai-chat-root] element is itself the .ai-chat-shell; querySelector() only searches
  // descendants and returned null, which broke New Chat, reopening conversations and the history toggle.
  const chatShell = () => (root.matches('.ai-chat-shell') ? root : root.querySelector('.ai-chat-shell'));

  function toggleHistory() {
    const shell = chatShell();
    const toggle = root.querySelector('[data-ai-history-toggle]');
    if (window.matchMedia('(max-width: 760px)').matches) {
      shell.classList.toggle('is-history-open');
      toggle.setAttribute('aria-expanded', String(shell.classList.contains('is-history-open')));
    } else {
      shell.classList.toggle('is-sidebar-collapsed');
      toggle.setAttribute('aria-expanded', String(!shell.classList.contains('is-sidebar-collapsed')));
    }
  }

  function initializeAssistant() {
    if (!root || root.dataset.aiInitialized === 'true') {
      if (root) refreshHistory();
      return;
    }
    root.dataset.aiInitialized = 'true';
    root.addEventListener('click', event => {
      const button = event.target.closest('button');
      if (!button || !root.contains(button)) return;
      if (sending && button.matches('[data-ai-new-chat], [data-ai-conversation], [data-ai-prompt]')) return;
      if (button.matches('[data-ai-new-chat]')) {
        selectedConversation = null;
        draftConversationId = newConversationId();
        viewRevision += 1;
        chatShell().classList.remove('is-history-open');
        root.querySelector('[data-ai-chat-context]').textContent = 'New conversation';
        showConversation([]);
        refreshHistory();
      } else if (button.matches('[data-ai-history-toggle]')) {
        toggleHistory();
      } else if (button.matches('[data-ai-conversation]')) {
        chatShell().classList.remove('is-history-open');
        openConversation(button.dataset.aiConversation);
      } else if (button.matches('[data-ai-prompt]')) {
        sendQuestion(button.dataset.aiPrompt || button.textContent);
      }
    });
    const form = root.querySelector('[data-ai-chat-form]');
    const textarea = form.elements.question;
    textarea.addEventListener('input', () => {
      textarea.style.height = 'auto';
      textarea.style.height = `${Math.min(textarea.scrollHeight, 160)}px`;
    });
    textarea.addEventListener('keydown', event => {
      if (event.key === 'Enter' && !event.shiftKey) {
        event.preventDefault();
        form.requestSubmit();
      }
    });
    form.addEventListener('submit', event => {
      event.preventDefault();
      sendQuestion(textarea.value);
    });
    root.querySelector('[data-ai-chat-body]').addEventListener('click', event => {
      if (!event.target.closest('[data-ai-prompt]')) return;
    });
    draftConversationId = newConversationId();
    refreshHistory();
    refreshAgentStatus();
    showConversation([]);
  }

  window.initializeAiThreatAssistant = () => {
    root = document.querySelector('[data-ai-chat-root]');
    if (root) initializeAssistant();
  };
})();
