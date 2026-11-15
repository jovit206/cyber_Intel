document.addEventListener('DOMContentLoaded', () => {
  const $ = id => document.getElementById(id);
  const escape = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
  const date = value => value ? new Date(value).toLocaleString() : '—';
  const money = value => new Intl.NumberFormat('en-IN', {style:'currency',currency:'INR',maximumFractionDigits:0}).format(Number(value || 0));
  async function get(path) {
    const response = await fetch(path, {credentials:'same-origin'});
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(body.detail || `Request failed (${response.status})`);
    return body;
  }
  function table(headers, rows) { return `<table class="admin-table"><thead><tr>${headers.map(item=>`<th>${escape(item)}</th>`).join('')}</tr></thead><tbody>${rows.length ? rows.join('') : `<tr><td colspan="${headers.length}">No records available.</td></tr>`}</tbody></table>`; }
  async function loadUsers() {
    $('manager-users').textContent = 'Loading users…';
    try {
      const data = await get(`/api/manager/users?q=${encodeURIComponent($('manager-search').value.trim())}`);
      $('manager-users').innerHTML = table(['User','Name / email','Plan','Status','Wallet','Last login','Details'], data.items.map(user => `<tr><td>${escape(user.user_id)}</td><td>${escape(user.name || user.email)}</td><td>${escape(user.plan || 'free')}</td><td>${escape(user.status || user.account_status || 'active')}</td><td>${money(user.wallet_balance)}</td><td>${date(user.last_login_at)}</td><td><button type="button" class="xs-btn secondary" data-manager-user="${escape(user.user_id)}">View</button></td></tr>`));
    } catch (error) { $('manager-users').textContent = error.message; }
  }
  async function loadList(path, target, headers, project) {
    $(target).textContent = 'Loading…';
    try { const data = await get(path); $(target).innerHTML = table(headers, data.items.map(project)); }
    catch (error) { $(target).textContent = error.message; }
  }
  async function load() {
    try {
      const data = await get('/api/manager/dashboard');
      $('manager-summary').innerHTML = [['Registered users',data.registered_users],['Active users · 30 days',data.active_users],['Wallet balances',money(data.wallet_balance_total)],['Wallet transactions',data.wallet_transactions],['Wallet operations',data.wallet_operations],['Subscriptions',Object.entries(data.subscriptions).map(([k,v])=>`${k}: ${v}`).join(' · ')]].map(([label,value])=>`<article class="admin-summary-card"><span>${escape(label)}</span><strong>${escape(value)}</strong></article>`).join('');
      await loadUsers();
      await loadList('/api/manager/subscriptions?limit=500','manager-subscriptions',['User ID','Email','Plan','Status','Renewal','Wallet'],row=>`<tr><td>${escape(row.user_id)}</td><td>${escape(row.email)}</td><td>${escape(row.plan || 'free')}</td><td>${escape(row.account_status || row.status || (row.plan === 'free' ? 'Free' : new Date(row.renewal_at || 0) > new Date() ? 'Active' : 'Expired'))}</td><td>${date(row.renewal_at)}</td><td>${money(row.wallet_balance)}</td></tr>`);
      await loadList('/api/manager/transactions?limit=100','manager-transactions',['ID','User','Date','Type','Amount','Status'],tx=>`<tr><td><button class="admin-link-button" type="button" data-manager-transaction="${escape(tx.transaction_id)}">${escape(tx.transaction_id)}</button></td><td>${escape(tx.user_id)}</td><td>${date(tx.created_at)}</td><td>${escape(tx.type)}</td><td>${money(tx.amount)}</td><td>${escape(tx.status)}</td></tr>`);
      await loadList('/api/manager/audit?limit=100','manager-audit',['Date','Actor','Role','Action','Target','Outcome'],row=>`<tr><td>${date(row.created_at)}</td><td>${escape(row.administrator_id)}</td><td>${escape(row.actor_role || 'admin')}</td><td>${escape(row.action)}</td><td>${escape(row.target_record)}</td><td>${escape(row.outcome)}</td></tr>`);
    } catch (error) { $('manager-message').textContent = error.message; }
  }
  document.querySelectorAll('[data-manager-tab]').forEach(button => button.addEventListener('click', () => {
    document.querySelectorAll('[data-manager-tab]').forEach(tab => tab.classList.toggle('is-active', tab === button));
    document.querySelectorAll('[data-manager-panel]').forEach(panel => { panel.hidden = panel.dataset.managerPanel !== button.dataset.managerTab; });
  }));
  $('manager-search-button').addEventListener('click', loadUsers);
  $('manager-search').addEventListener('keydown', event => { if (event.key === 'Enter') loadUsers(); });
  $('manager-users').addEventListener('click', async event => {
    const button = event.target.closest('[data-manager-user]'); if (!button) return;
    try {
      const data = await get(`/api/manager/users/${encodeURIComponent(button.dataset.managerUser)}`);
      const user = data.user;
      $('manager-message').textContent = `${user.user_id} · ${user.email || ''} · ${user.plan || 'free'} plan · wallet ${money(data.wallet?.balance)} · ${data.transactions.length} transaction records`;
    } catch (error) { $('manager-message').textContent = error.message; }
  });
  $('manager-transactions').addEventListener('click', async event => {
    const button = event.target.closest('[data-manager-transaction]'); if (!button) return;
    try { const row = await get(`/api/manager/transactions/${encodeURIComponent(button.dataset.managerTransaction)}`); $('manager-message').textContent = `${row.transaction_id} · ${row.type} · ${money(row.amount)} · ${row.status} · ${row.description || ''}`; }
    catch (error) { $('manager-message').textContent = error.message; }
  });
  window.loadManagerDashboard = load;
});
