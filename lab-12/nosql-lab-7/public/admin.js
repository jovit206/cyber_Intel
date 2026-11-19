document.addEventListener('DOMContentLoaded', () => {
  const $ = id => document.getElementById(id);
  const money = value => new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR', maximumFractionDigits: 2 }).format(Number(value || 0));
  const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]));
  const date = value => value ? new Date(value).toLocaleString() : 'Not recorded';
  let userPage = 1, txPage = 1, subscriptionPage = 1, activeTab = 'overview', dashboardLoaded = false;
  let pendingCreditKey = '', pendingCreditFingerprint = '';

  async function api(path, options = {}) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 15000);
    try {
      const response = await fetch(path, { credentials: 'same-origin', ...options, signal: controller.signal,
        headers: { 'Content-Type': 'application/json', ...(options.headers || {}) } });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.detail || `Request failed (${response.status}).`);
      return data;
    } catch (error) {
      if (error.name === 'AbortError') throw new Error('The administrator request timed out. Please retry.');
      throw error;
    } finally { clearTimeout(timer); }
  }

  function message(text, kind = 'error') {
    const box = $('admin-message');
    box.textContent = text;
    box.className = `admin-message ${kind}`;
    box.hidden = !text;
  }
  function queryString(values) {
    const params = new URLSearchParams();
    Object.entries(values).forEach(([key, value]) => { if (value !== '' && value !== null && value !== undefined) params.set(key, value); });
    return params.toString();
  }
  function setBusy(target, text) { $(target).innerHTML = `<tr><td colspan="8" class="loading-state">${esc(text)}</td></tr>`; }
  function summaryCard(label, value, note = '') {
    return `<article class="admin-summary-card"><span>${esc(label)}</span><strong>${esc(value)}</strong>${note ? `<small>${esc(note)}</small>` : ''}</article>`;
  }

  async function loadDashboard() {
    $('admin-summary-cards').innerHTML = '<div class="loading-state">Loading administrator summary…</div>';
    try {
      const data = await api('/api/admin/dashboard');
      const total = data.lifetime || {};
      $('admin-period-note').textContent = `Lifetime totals through ${date(data.generated_at)}. Active users means at least one successful login in the 30 days before ${date(data.activity_period?.since)}; older activity is not inferred.`;
      $('admin-summary-cards').innerHTML = [
        summaryCard('Registered users', Number(data.registered_users || 0).toLocaleString(), 'Current account records'),
        summaryCard('Active users', Number(data.active_users || 0).toLocaleString(), 'Successful login · past 30 days'),
        summaryCard('Banned users', Number(data.banned_users || 0).toLocaleString(), 'Accounts marked banned'),
        summaryCard('Total transactions', Number(total.total_transactions || 0).toLocaleString(), 'Financial activity and unique failed receipts'),
        summaryCard('Successful transactions', Number(total.successful_transactions || 0).toLocaleString(), 'Committed or refunded transaction records'),
        summaryCard('Failed transactions', Number(total.failed_transactions || 0).toLocaleString(), 'Failed records and unique rolled-back attempts'),
        summaryCard('Deposits', money(total.total_deposits), 'Committed wallet top-ups'),
        summaryCard('Subscription purchases', money(total.total_subscription_purchases), 'Committed subscription charges'),
        summaryCard('Refunds', money(total.total_refunds), 'Recorded refund amounts'),
        summaryCard('User wallet balances', money(data.total_wallet_balances), 'Registered users only · excludes platform wallet'),
        summaryCard('Wallet transaction records', Number(data.wallet_transaction_count || 0).toLocaleString()),
        summaryCard('Wallet operations', Number(data.wallet_operation_count || 0).toLocaleString()),
        summaryCard('Subscription payments', Number(data.subscription_payment_count || 0).toLocaleString()),
        summaryCard('Subscriptions', `Free ${data.subscriptions?.free || 0} · Plus ${data.subscriptions?.plus || 0} · Pro ${data.subscriptions?.pro || 0}`, `${data.subscriptions?.active_paid || 0} active paid · ${data.subscriptions?.expired_paid || 0} expired`),
      ].join('');
      $('admin-recent-users').innerHTML = (data.recent_users || []).map(row => `<li>${esc(row.user_id)} · ${esc(row.email || row.name || '')} · ${esc(row.plan || 'free')}</li>`).join('') || '<li>No account records.</li>';
      $('admin-recent-transactions').innerHTML = (data.recent_transactions || []).map(row => `<li>${esc(row.transaction_id)} · ${esc(row.type)} · ${money(row.amount)} · ${esc(row.status)}</li>`).join('') || '<li>No transaction records.</li>';
      $('admin-recent-actions').innerHTML = (data.recent_admin_actions || []).map(row => `<li>${esc(row.action)} · ${esc(row.target_record)} · ${esc(row.outcome)} · ${esc(date(row.created_at))}</li>`).join('') || '<li>No administrator actions recorded.</li>';
      dashboardLoaded = true;
    } catch (error) {
      $('admin-summary-cards').innerHTML = `<div class="admin-empty">${esc(error.message)}</div>`;
      dashboardLoaded = false;
    }
  }

  function setPager(prefix, page, pages, total) {
    $(`admin-${prefix}-page`).textContent = `Page ${page} of ${Math.max(1, pages)} · ${Number(total || 0).toLocaleString()} records`;
    $(`admin-${prefix}-prev`).disabled = page <= 1;
    $(`admin-${prefix}-next`).disabled = page >= pages;
  }

  async function loadUsers() {
    setBusy('admin-users-body', 'Loading users…');
    try {
      const qs = queryString({ page: userPage, page_size: 25, q: $('admin-user-search').value.trim(),
        plan: $('admin-user-plan').value, status: $('admin-user-status').value,
        sort_by: $('admin-user-sort').value, sort_dir: $('admin-user-order').value });
      const data = await api(`/api/admin/users?${qs}`);
      $('admin-users-body').innerHTML = data.items.length ? data.items.map(user => `
        <tr><td><code>${esc(user.user_id)}</code></td><td><strong>${esc(user.name || 'Name not recorded')}</strong><small>${esc(user.email || 'Email not recorded')}</small></td>
        <td><span class="admin-badge">${esc(user.plan || 'Not recorded')}</span></td><td>${esc(user.account_status || 'Not recorded')}</td>
        <td>${user.wallet_balance === undefined ? 'Not recorded' : money(user.wallet_balance)}</td><td>${esc(date(user.last_login_at))}</td>
        <td>${Number(user.transaction_count || 0).toLocaleString()}</td><td><button type="button" class="xs-btn secondary" data-admin-user="${esc(user.user_id)}">Details</button></td></tr>`).join('')
        : '<tr><td colspan="8" class="admin-empty">No matching users.</td></tr>';
      setPager('users', data.page, data.pages, data.total);
      message('');
    } catch (error) {
      $('admin-users-body').innerHTML = `<tr><td colspan="8" class="admin-empty">${esc(error.message)}</td></tr>`;
      message(error.message);
    }
  }

  async function showUser(userId) {
    const detail = $('admin-user-detail');
    detail.hidden = false;
    detail.innerHTML = '<div class="loading-state">Loading account and financial history…</div>';
    try {
      const data = await api(`/api/admin/users/${encodeURIComponent(userId)}`);
      const user = data.user || {};
      const history = data.transactions || [];
      const transactions = history.length ? `<div class="admin-table-wrap"><table class="admin-table"><thead><tr><th>ID</th><th>Date</th><th>Type</th><th>Amount</th><th>Status</th></tr></thead><tbody>${history.map(row => `<tr><td>${esc(row.transaction_id)}</td><td>${esc(date(row.created_at))}</td><td>${esc(row.type)}</td><td>${money(row.amount)}</td><td>${esc(row.status)}</td></tr>`).join('')}</tbody></table></div>` : '<p>No transaction records.</p>';
      const changes = data.subscription_history?.length ? `<ul>${data.subscription_history.map(row => `<li>${esc(date(row.changed_at))} · ${esc(row.old_plan || 'Not recorded')} → ${esc(row.new_plan || row.plan || 'Not recorded')}</li>`).join('')}</ul>` : '<p>No subscription history recorded.</p>';
      const currentStatus = user.status || user.account_status || 'active';
      const statusControl = user.role === 'admin' ? '<p>Administrator accounts are protected from ban actions.</p>' : `<form id="admin-status-form" data-user-id="${esc(user.user_id)}" class="admin-status-form"><label>Account access<select name="status"><option value="active" ${currentStatus === 'active' ? 'selected' : ''}>Active</option><option value="banned" ${currentStatus === 'banned' ? 'selected' : ''}>Banned</option></select></label><button class="xs-btn" type="submit">Update access</button></form>`;
      detail.innerHTML = `<div class="admin-detail-heading"><div><span class="eyebrow">USER DETAILS</span><h2>${esc(user.name || user.email || user.user_id)}</h2></div><button type="button" class="xs-btn secondary" id="admin-user-detail-close">Close</button></div>
        <div class="admin-detail-facts"><span>User ID <strong>${esc(user.user_id)}</strong></span><span>Email <strong>${esc(user.email || 'Not recorded')}</strong></span><span>Plan <strong>${esc(user.plan || 'Not recorded')}</strong></span><span>Registered <strong>${esc(date(user.created_at))}</strong></span><span>Last login <strong>${esc(date(user.last_login_at))}</strong></span><span>Wallet <strong>${data.wallet ? money(data.wallet.balance) : 'Not recorded'}</strong></span></div>
        ${statusControl}<h3>Financial history</h3>${transactions}<h3>Subscription history</h3>${changes}`;
      $('admin-user-detail-close').addEventListener('click', () => { detail.hidden = true; });
      const form = $('admin-status-form');
      if (form) form.addEventListener('submit', async event => {
        event.preventDefault();
        if (!window.confirm('Change this account access? The action will be recorded in the administrator audit log.')) return;
        const status = new FormData(form).get('status');
        try { await api(`/api/admin/users/${encodeURIComponent(form.dataset.userId)}/status`, { method: 'PATCH', body: JSON.stringify({ status }) }); message('Account status updated.', 'success'); await loadDashboard(); await loadUsers(); await showUser(form.dataset.userId); }
        catch (error) { message(error.message); }
      });
    } catch (error) { detail.innerHTML = `<div class="admin-empty">${esc(error.message)}</div>`; }
  }

  async function loadTransactions() {
    setBusy('admin-transactions-body', 'Loading transactions…');
    try {
      const qs = queryString({ page: txPage, page_size: 25, q: $('admin-tx-search').value.trim(), user_id: $('admin-tx-user').value.trim(),
        transaction_type: $('admin-tx-type').value, status: $('admin-tx-status').value,
        date_from: $('admin-tx-from').value ? `${$('admin-tx-from').value}T00:00:00Z` : '',
        date_to: $('admin-tx-to').value ? `${$('admin-tx-to').value}T23:59:59Z` : '',
        sort_by: $('admin-tx-sort').value, sort_dir: $('admin-tx-order').value });
      const data = await api(`/api/admin/transactions?${qs}`);
      const summary = data.summary || {};
      $('admin-transaction-summary').innerHTML = [
        summaryCard('Deposits', money(summary.deposits)), summaryCard('Subscription charges', money(summary.subscription_charges)),
        summaryCard('Refunds', money(summary.refunds)), summaryCard('Failed transactions', Number(summary.failed || 0).toLocaleString()),
      ].join('');
      $('admin-transactions-body').innerHTML = data.items.length ? data.items.map(row => `<tr>
        <td><button type="button" class="admin-link-button" data-admin-transaction="${esc(row.transaction_id)}">${esc(row.transaction_id)}</button></td>
        <td><code>${esc(row.user_id)}</code><small>${esc(row.user?.name || row.user?.email || '')}</small></td><td>${esc(date(row.created_at))}</td>
        <td>${esc(row.type)}</td><td>${money(row.amount)} ${esc(row.currency || 'INR')}</td><td><span class="admin-badge ${['failed','rolled_back'].includes(row.status) ? 'failed' : 'ok'}">${esc(row.status)}</span></td>
        <td>${esc(row.description || 'Not recorded')}</td><td>${row.balance_after === undefined ? 'Not recorded' : money(row.balance_after)}</td></tr>
        <tr class="admin-transaction-detail-row" hidden><td colspan="8"><strong>Transaction detail</strong><p>${esc(row.failure_reason || row.description || 'No additional details recorded.')}</p><span>ID: ${esc(row.transaction_id)}</span></td></tr>`).join('')
        : '<tr><td colspan="8" class="admin-empty">No matching financial activity.</td></tr>';
      setPager('tx', data.page, data.pages, data.total);
      message('');
    } catch (error) {
      $('admin-transactions-body').innerHTML = `<tr><td colspan="8" class="admin-empty">${esc(error.message)}</td></tr>`;
      message(error.message);
    }
  }

  async function loadSubscriptions() {
    $('admin-subscriptions-body').innerHTML = '<tr><td colspan="6" class="loading-state">Loading subscriptions…</td></tr>';
    try {
      const qs = queryString({page:subscriptionPage,page_size:25,plan:$('admin-subscription-plan').value});
      const data = await api(`/api/admin/subscriptions?${qs}`);
      $('admin-subscriptions-body').innerHTML = data.items.length ? data.items.map(row => {
        const renewal = row.renewal_at ? new Date(row.renewal_at) : null;
        const state = row.plan === 'free' ? 'Free' : renewal && renewal > new Date() ? 'Active' : 'Expired';
        return `<tr><td>${esc(row.user_id)}</td><td>${esc(row.email || 'Not recorded')}</td><td>${esc(row.plan || 'free')}</td><td>${esc(state)}</td><td>${esc(date(row.renewal_at))}</td><td>${row.wallet_balance === undefined ? 'Not recorded' : money(row.wallet_balance)}</td></tr>`;
      }).join('') : '<tr><td colspan="6" class="admin-empty">No subscription records.</td></tr>';
      $('admin-subscriptions-page').textContent = `Page ${subscriptionPage} · ${Number(data.total || 0).toLocaleString()} accounts`;
      $('admin-subscriptions-prev').disabled = subscriptionPage <= 1;
      $('admin-subscriptions-next').disabled = subscriptionPage * 25 >= data.total;
    } catch (error) { $('admin-subscriptions-body').innerHTML = `<tr><td colspan="6" class="admin-empty">${esc(error.message)}</td></tr>`; }
  }

  document.querySelectorAll('[data-admin-tab]').forEach(button => button.addEventListener('click', () => {
    activeTab = button.dataset.adminTab;
    document.querySelectorAll('[data-admin-tab]').forEach(tab => tab.classList.toggle('is-active', tab === button));
    document.querySelectorAll('[data-admin-panel]').forEach(panel => { panel.hidden = panel.dataset.adminPanel !== activeTab; });
    message('');
    if (activeTab === 'overview') loadDashboard();
    if (activeTab === 'users') loadUsers();
    if (activeTab === 'transactions') loadTransactions();
    if (activeTab === 'subscriptions') loadSubscriptions();
  }));
  $('admin-user-apply').addEventListener('click', () => { userPage = 1; loadUsers(); });
  $('admin-user-search').addEventListener('keydown', event => { if (event.key === 'Enter') { event.preventDefault(); userPage = 1; loadUsers(); } });
  $('admin-users-prev').addEventListener('click', () => { if (userPage > 1) { userPage--; loadUsers(); } });
  $('admin-users-next').addEventListener('click', () => { userPage++; loadUsers(); });
  $('admin-users-body').addEventListener('click', event => { const button = event.target.closest('[data-admin-user]'); if (button) showUser(button.dataset.adminUser); });
  $('admin-tx-apply').addEventListener('click', () => { txPage = 1; loadTransactions(); });
  $('admin-subscription-apply').addEventListener('click', () => { subscriptionPage = 1; loadSubscriptions(); });
  $('admin-subscriptions-prev').addEventListener('click', () => { if (subscriptionPage > 1) { subscriptionPage--; loadSubscriptions(); } });
  $('admin-subscriptions-next').addEventListener('click', () => { subscriptionPage++; loadSubscriptions(); });
  $('admin-wallet-credit').addEventListener('submit', async event => {
    event.preventDefault();
    const form = event.currentTarget;
    const values = Object.fromEntries(new FormData(form));
    values.amount = Number(values.amount);
    const fingerprint = JSON.stringify([values.user_id, values.amount, values.reason]);
    if (fingerprint !== pendingCreditFingerprint) {
      pendingCreditFingerprint = fingerprint;
      pendingCreditKey = crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
    }
    if (!window.confirm(`Credit ₹${values.amount.toLocaleString('en-IN')} to ${values.user_id}? This operation is audited.`)) return;
    const button = form.querySelector('button[type="submit"]'); button.disabled = true;
    try {
      const result = await api('/api/admin/wallets/credit', {method:'POST', body:JSON.stringify({...values, idempotency_key:pendingCreditKey})});
      message(`Wallet credited. Transaction ${result.transaction_id}; new balance ${money(result.wallet_after)}.`, 'success');
      pendingCreditKey = ''; pendingCreditFingerprint = '';
      form.reset(); await loadDashboard(); await loadUsers();
    } catch (error) { message(error.message, 'error'); }
    finally { button.disabled = false; }
  });
  $('admin-tx-search').addEventListener('keydown', event => { if (event.key === 'Enter') { event.preventDefault(); txPage = 1; loadTransactions(); } });
  $('admin-tx-prev').addEventListener('click', () => { if (txPage > 1) { txPage--; loadTransactions(); } });
  $('admin-tx-next').addEventListener('click', () => { txPage++; loadTransactions(); });
  $('admin-transactions-body').addEventListener('click', event => {
    const button = event.target.closest('[data-admin-transaction]');
    if (!button) return;
    const row = button.closest('tr');
    const detail = row?.nextElementSibling;
    if (!detail) return;
    detail.hidden = !detail.hidden;
    if (detail.hidden) return;
    detail.querySelector('td').innerHTML = '<div class="loading-state">Loading transaction details…</div>';
    api(`/api/admin/transactions/${encodeURIComponent(button.dataset.adminTransaction)}`)
      .then(data => { detail.querySelector('td').innerHTML = `<strong>Transaction detail</strong><p>${esc(data.description || 'Description not recorded.')}</p><span>User: ${esc(data.user?.user_id || data.user_id)} · ${esc(data.user?.name || data.user?.email || 'Details not recorded')}</span><p>Failure reason: ${esc(data.failure_reason || 'None recorded')}</p><p>Balance after: ${data.balance_after === undefined ? 'Not recorded' : money(data.balance_after)}</p>`; })
      .catch(error => { detail.querySelector('td').textContent = error.message; });
  });

  window.loadAdminDashboard = () => {
    if (activeTab === 'overview' && !dashboardLoaded) loadDashboard();
  };
});
