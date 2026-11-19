document.addEventListener('DOMContentLoaded', () => {
  const base = (window.AEGIS_API_BASE_URL || '').replace(/\/+$/, '');
  const call = async (path, options = {}) => {
    const response = await fetch(`${base}/api/subscriptions${path}`, {
      credentials: 'same-origin', headers: { 'Content-Type': 'application/json', ...(options.headers || {}) }, ...options,
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || `Request failed (${response.status})`);
    return data;
  };
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const inr = value => new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR', maximumFractionDigits: 0 }).format(Number(value || 0));
  const date = value => value ? new Date(value).toLocaleString() : '—';
  const message = (text, kind = 'info') => {
    const node = document.getElementById('subscription-message');
    node.className = `subscription-message ${kind}`;
    node.textContent = text;
  };
  const key = () => crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(36).slice(2)}`;

  async function refresh() {
    const auth = document.getElementById('subscription-auth');
    const account = document.getElementById('subscription-account');
    try {
      const data = await call('/me');
      auth.hidden = true;
      account.hidden = false;
      const user = data.user;
      document.getElementById('subscription-account-title').textContent = user.email;
      const remaining = Number(data.available_credits || 0);
      document.getElementById('subscription-overview').innerHTML = `<div class="lab10-metrics">${metric('Plan', user.plan.toUpperCase())}${metric('Wallet', inr(data.wallet?.balance))}${metric('Credits remaining', remaining)}${metric('Monthly allocation', user.credits_allocated)}${metric('Renewal date', date(user.renewal_at))}</div>`;
      const plans = await call('/plans');
      document.getElementById('subscription-plans').innerHTML = plans.plans.map(plan => `<article class="subscription-plan"><h3>${esc(plan.name)}</h3><p>${inr(plan.price)} / month</p><p>${Number(plan.monthly_credits).toLocaleString()} monthly credits</p><button class="xs-btn" data-plan="${esc(plan.name)}" type="button">${user.plan === plan.name ? 'Renew plan' : 'Select plan'}</button></article>`).join('');
      document.querySelectorAll('#subscription-plans [data-plan]').forEach(button => button.addEventListener('click', () => buy(button.dataset.plan)));
      await loadHistory();
      const admin = document.getElementById('subscription-admin');
      admin.hidden = user.role !== 'admin';
      document.getElementById('admin-nav-title').hidden = user.role !== 'admin';
      document.getElementById('admin-nav-link').hidden = user.role !== 'admin';
      if (user.role === 'admin') await loadAdmin();
    } catch (error) {
      auth.hidden = false;
      account.hidden = true;
      if (!/sign in|session has expired/i.test(error.message)) message(error.message, 'error');
    }
  }

  function metric(label, value) {
    return `<div class="lab10-metric"><span>${esc(label)}</span><strong>${esc(value)}</strong></div>`;
  }

  async function submitCredentials(form, endpoint) {
    const fields = Object.fromEntries(new FormData(form));
    try {
      const result = await call(endpoint, { method: 'POST', body: JSON.stringify(fields) });
      if (endpoint === '/login') {
        message(`Signed in as ${result.user.email}.`, 'success');
        await refresh();
      } else {
        message('Account created with a ₹1,000 demonstration opening balance. Sign in to continue.', 'success');
        document.querySelector('#subscription-login [name="email"]').value = fields.email;
      }
    } catch (error) { message(error.message, 'error'); }
  }

  async function buy(plan) {
    if (!window.confirm(`Confirm the ${plan.toUpperCase()} demonstration plan? This will use the displayed wallet balance. No real payment is made.`)) return;
    try {
      const result = await call('/purchase', { method: 'POST', body: JSON.stringify({ plan, idempotency_key: key() }) });
      message(`${plan.toUpperCase()} plan activated. Receipt ${result.receipt_id}; transaction attempts: ${result.attempts}.`, 'success');
      await refresh();
    } catch (error) { message(error.message, 'error'); }
  }

  async function loadHistory() {
    const data = await call('/history');
    const rows = (data.receipts || []).map(receipt => `<tr><td><code>${esc(receipt.receipt_id)}</code></td><td>${esc(receipt.operation.replaceAll('_', ' '))}</td><td>${esc(receipt.status)}</td><td>${esc(receipt.attempts)}</td><td>${esc(date(receipt.created_at))}</td><td><button class="xs-btn receipt-print" data-id="${esc(receipt.receipt_id)}" type="button">Print</button></td></tr>`).join('');
    const paymentRows = (data.payments || []).map(payment => `<tr><td>${esc(payment.plan.toUpperCase())}</td><td>${inr(payment.amount)}</td><td>${esc(payment.status)}</td><td>${esc(date(payment.created_at))}</td></tr>`).join('');
    const planRows = (data.subscription_history || []).map(change => `<tr><td>${esc(change.from_plan.toUpperCase())} → ${esc(change.to_plan.toUpperCase())}</td><td>${inr(change.price)}</td><td>${Number(change.credits_allocated).toLocaleString()}</td><td>${esc(date(change.changed_at))}</td></tr>`).join('');
    document.getElementById('subscription-history').innerHTML = `<h3 class="lab10-subheading">Payment history</h3>${paymentRows ? `<div class="lab10-table-wrap"><table class="lab10-table"><thead><tr><th>Plan</th><th>Amount</th><th>Status</th><th>Date</th></tr></thead><tbody>${paymentRows}</tbody></table></div>` : '<p class="lab10-explainer">No payments yet.</p>'}<h3 class="lab10-subheading">Plan changes</h3>${planRows ? `<div class="lab10-table-wrap"><table class="lab10-table"><thead><tr><th>Change</th><th>Price</th><th>Monthly credits</th><th>Date</th></tr></thead><tbody>${planRows}</tbody></table></div>` : '<p class="lab10-explainer">No subscription changes yet.</p>'}<h3 class="lab10-subheading">Transaction receipts</h3>${rows ? `<div class="lab10-table-wrap"><table class="lab10-table"><thead><tr><th>Receipt</th><th>Operation</th><th>Status</th><th>Attempts</th><th>Date</th><th>Invoice</th></tr></thead><tbody>${rows}</tbody></table></div>` : '<div class="empty-prompt">No wallet operations yet.</div>'}`;
    document.querySelectorAll('.receipt-print').forEach(button => button.addEventListener('click', () => printReceipt(button.dataset.id, data.receipts)));
  }

  function printReceipt(id, receipts) {
    const receipt = receipts.find(item => item.receipt_id === id);
    if (!receipt) return;
    const details = Object.entries(receipt.result || {}).map(([label, value]) => `<tr><th>${esc(label.replaceAll('_', ' '))}</th><td>${esc(typeof value === 'object' ? JSON.stringify(value) : value)}</td></tr>`).join('');
    const tab = window.open('', '_blank');
    if (!tab) { message('Allow pop-ups to print your receipt.', 'error'); return; }
    tab.opener = null;
    tab.document.write(`<!doctype html><title>AEGIS receipt ${esc(id)}</title><meta charset="utf-8"><h1>AEGIS demonstration receipt</h1><p>Receipt ${esc(id)} · ${esc(date(receipt.created_at))}</p><p>No real payment was processed.</p><table>${details}</table><button onclick="print()">Print</button><style>body{font:16px Arial;max-width:760px;margin:40px auto}table{border-collapse:collapse;width:100%}th,td{text-align:left;padding:10px;border-bottom:1px solid #ddd}</style>`);
    tab.document.close();
  }

  async function loadAdmin() {
    try {
      const [data, audit] = await Promise.all([call('/admin/summary'), call('/admin/audit')]);
      const plans = Object.entries(data.users_by_plan || {}).map(([plan, count]) => `${plan}: ${count}`).join(' · ') || 'No users';
      const auditText = audit.passed ? 'Wallet and payment ledgers are consistent' : 'Discrepancies need review';
      document.getElementById('subscription-admin-summary').innerHTML = `<div class="lab10-metrics">${metric('Demo revenue', inr(data.revenue_total))}${metric('Committed receipts', data.successful_operations)}${metric('Negative wallets', data.negative_wallets)}${metric('Users by plan', plans)}${metric('Consistency audit', auditText)}${metric('Ledger mismatches', audit.wallet_mismatches?.length || 0)}</div><p class="lab10-explainer">${esc(audit.note || 'Read-only audit; records are not automatically changed.')}</p>`;
    } catch (error) { document.getElementById('subscription-admin-summary').textContent = error.message; }
  }

  document.getElementById('subscription-login').addEventListener('submit', event => { event.preventDefault(); submitCredentials(event.currentTarget, '/login'); });
  document.getElementById('subscription-register').addEventListener('submit', event => { event.preventDefault(); submitCredentials(event.currentTarget, '/register'); });
  document.getElementById('subscription-logout').addEventListener('click', async () => {
    try { await call('/logout', { method: 'POST' }); message('Signed out.', 'success'); await refresh(); }
    catch (error) { message(error.message, 'error'); }
  });
  document.getElementById('wallet-transfer-form').addEventListener('submit', async event => {
    event.preventDefault();
    const payload = Object.fromEntries(new FormData(event.currentTarget));
    payload.amount = Number(payload.amount);
    payload.idempotency_key = key();
    if (!window.confirm(`Transfer ${inr(payload.amount)} to ${payload.recipient_email}?`)) return;
    try {
      const result = await call('/transfer', { method: 'POST', body: JSON.stringify(payload) });
      message(`Transfer committed. Receipt ${result.receipt_id}.`, 'success');
      event.currentTarget.reset();
      await refresh();
    } catch (error) { message(error.message, 'error'); }
  });
  document.querySelectorAll('[data-view="subscriptions"]').forEach(button => button.addEventListener('click', refresh));
  refresh();
});
