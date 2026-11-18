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

  function renderSources(message) {
    const ids = Array.isArray(message.source_chunk_ids) ? message.source_chunk_ids : [];
    if (!ids.length) return '';
    return `<section class="ai-citations" aria-label="Sources"><h3>Sources</h3><div>${ids.map((id, index) => {
      const item = message.sources?.find(source => String(source.chunk_id) === String(id)) || sourceCache.get(String(id));
      const label = `[S${index + 1}]`;
      if (!item) return `<details><summary>${label} ${escapeHtml(id)}</summary><p>Source reference from conversation history.</p></details>`;
      const reference = item.source_reference || item.source_url || item.chunk_id || id;
      return `<details><summary>${label} ${escapeHtml(item.source || 'Threat intelligence source')}</summary><p>${escapeHtml(item.text || 'No excerpt was returned.')}</p><small>${escapeHtml(reference)}</small></details>`;
    }).join('')}</div></section>`;
  }

  function renderMessage(message) {
    const assistant = message.role === 'assistant';
    const label = assistant ? 'AI Threat Assistant' : 'You';
    return `<article class="ai-message ${assistant ? 'is-assistant' : 'is-user'}">
      <div class="ai-message-bubble">
        <div class="ai-message-meta"><strong>${label}</strong><time>${escapeHtml(formatTime(message.timestamp))}</time></div>
        <div class="ai-message-content">${escapeHtml(message.content)}</div>
      </div>
      ${assistant ? renderSources(message) : ''}
    </article>`;
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

  async function sendQuestion(question) {
    if (sending || !question.trim()) return;
    const text = question.trim();
    if (text.length < 3) return;
    sending = true;
    const form = root.querySelector('[data-ai-chat-form]');
    const textarea = form.elements.question;
    const send = root.querySelector('[data-ai-send]');
    textarea.disabled = true;
    send.disabled = true;
    addPending(text);
    try {
      const result = await api('/api/ask', {
        method: 'POST',
        body: JSON.stringify({
          question: text,
          conversation_id: selectedConversation || draftConversationId,
          platform: root.querySelector('[data-ai-platform]').value || null,
          threat_status: root.querySelector('[data-ai-threat-status]').value || null,
          client: 'ai-threat-assistant',
        }),
      });
      if (typeof result.answer !== 'string' || !result.answer.trim() || !result.conversation_id) {
        throw new Error('The assistant returned an incomplete response. Your question was not added to conversation history.');
      }
      selectedConversation = result.conversation_id;
      draftConversationId = null;
      for (const source of result.sources || []) if (source.chunk_id) sourceCache.set(String(source.chunk_id), source);
      textarea.value = '';
      textarea.style.height = '';
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
  }

  function toggleHistory() {
    const shell = root.querySelector('.ai-chat-shell');
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
        root.querySelector('.ai-chat-shell').classList.remove('is-history-open');
        root.querySelector('[data-ai-chat-context]').textContent = 'New conversation';
        showConversation([]);
      } else if (button.matches('[data-ai-history-toggle]')) {
        toggleHistory();
      } else if (button.matches('[data-ai-conversation]')) {
        root.querySelector('.ai-chat-shell').classList.remove('is-history-open');
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
    showConversation([]);
  }

  window.initializeAiThreatAssistant = () => {
    root = document.querySelector('[data-ai-chat-root]');
    if (root) initializeAssistant();
  };
})();
