document.addEventListener('DOMContentLoaded', () => {
  const base = (window.AEGIS_API_BASE_URL || '').replace(/\/+$/, '');
  const panel = document.getElementById('banking-finance');
  if (!panel) return;
  const authNotice = document.getElementById('banking-finance-auth');
  const notice = document.getElementById('banking-finance-message');
  const topupDialog = document.getElementById('banking-topup-dialog');
  const confirmDialog = document.getElementById('banking-confirm-dialog');
  const currency = new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR', minimumFractionDigits: 2 });
  const money = value => currency.format(Number(value || 0));
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const key = () => crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
  let current;
  let purchaseProcessing = false;
  let topupProcessing = false;

  async function api(path, options = {}) {
    const response = await fetch(`${base}/api/subscriptions${path}`, {
      credentials: 'same-origin', headers: { 'Content-Type': 'application/json', ...(options.headers || {}) }, ...options,
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      const detail = data.detail;
      const error = new Error(typeof detail === 'string' ? detail : detail?.message || `Request failed (${response.status})`);
      error.status = response.status;
      error.details = detail && typeof detail === 'object' ? detail : {};
      throw error;
    }
    return data;
  }

  function announce(text, kind = '') {
    notice.className = `account-notice ${kind}`;
    notice.textContent = text;
  }

  function announceInsufficient(details = {}) {
    const required = Number(details.amount_required ?? details.amount_charged ?? 0);
    const available = Number(details.wallet_balance ?? 0);
    const shortfall = Number(details.shortfall ?? Math.max(0, required - available));
    notice.className = 'account-notice error';
    notice.replaceChildren(document.createTextNode(`Insufficient wallet balance. Required ${money(required)}, available ${money(available)}, shortfall ${money(shortfall)}. `));
    const action = document.createElement('button');
    action.type = 'button'; action.className = 'account-summary-secondary-link'; action.textContent = 'Add money';
    action.addEventListener('click', () => document.getElementById('banking-add-money').click());
    notice.append(action);
  }

  function askConfirmation(title, lines, label) {
    document.getElementById('banking-confirm-title').textContent = title;
    document.getElementById('banking-confirm-details').replaceChildren(...lines.map(text => {
      const row = document.createElement('p'); row.textContent = text; return row;
    }));
    document.getElementById('banking-confirm-error').textContent = '';
    document.getElementById('banking-confirm-submit').textContent = label;
    return new Promise(resolve => {
      confirmDialog.addEventListener('close', () => resolve(confirmDialog.returnValue === 'confirm'), { once: true });
      confirmDialog.showModal();
    });
  }

  async function refresh() {
    try {
      current = await api('/me');
      panel.hidden = false; authNotice.hidden = true;
      document.getElementById('financial-activity').hidden = false;
      document.getElementById('banking-current-plan').textContent = `Current plan: ${current.user.plan.toUpperCase()}`;
      await Promise.all([loadPlans(), loadAdmin()]);
      return true;
    } catch (error) {
      current = null; panel.hidden = true;
      authNotice.hidden = false;
      document.getElementById('financial-activity').hidden = true;
      if (error.status !== 401) authNotice.textContent = 'Wallet and subscription information is temporarily unavailable.';
      return false;
    }
  }

  async function loadPlans() {
    const data = await api('/plans');
    const plansNode = document.getElementById('banking-plans');
    const activeUntil = current.user.renewal_at ? new Date(current.user.renewal_at).getTime() : 0;
    plansNode.innerHTML = data.plans.map(plan => {
      const selected = current.user.plan === plan.name;
      const active = selected && (plan.name === 'free' || activeUntil > Date.now());
      const features = (plan.features || []).map(feature => `<li>${esc(feature)}</li>`).join('');
      const label = active ? 'Current plan' : selected ? 'Renew plan' : current.user.plan === 'free' ? 'Choose plan' : 'Upgrade / change';
      return `<article class="account-plan-card ${selected ? 'current' : ''}">${selected ? '<span class="account-current-badge">Current plan</span>' : ''}<h3>${esc(plan.name)}</h3><p class="account-plan-price">${money(plan.price)} <small>/ month</small></p><p class="account-plan-credits">${Number(plan.monthly_credits).toLocaleString()} credits / month</p><ul>${features}</ul><button class="account-primary-button" type="button" data-banking-plan="${esc(plan.name)}" ${active ? 'disabled' : ''}>${label}</button></article>`;
    }).join('');
    document.getElementById('banking-plan-policy').textContent = data.policy || '';
    plansNode.querySelectorAll('[data-banking-plan]').forEach(button => button.addEventListener('click', () => buyPlan(button.dataset.bankingPlan, button)));
  }

  async function buyPlan(plan, button) {
    if (purchaseProcessing) return;
    purchaseProcessing = true;
    const oldLabel = button.textContent;
    const planButtons = [...document.querySelectorAll('[data-banking-plan]')];
    const disabledBefore = planButtons.map(item => item.disabled);
    const addMoneyButton = document.getElementById('banking-add-money');
    planButtons.forEach(item => { item.disabled = true; });
    addMoneyButton.disabled = true;
    button.textContent = 'Checking balance…';
    try {
      const quote = await api(`/purchase-quote/${encodeURIComponent(plan)}`);
      if (!quote.sufficient_balance) {
        announceInsufficient({wallet_balance:quote.wallet_balance,amount_required:quote.amount_charged});
        return;
      }
      const confirmed = await askConfirmation('Confirm subscription', [
        `Plan: ${quote.plan.toUpperCase()}`, `Monthly price: ${money(quote.price)}`,
        `Monthly credits: ${Number(quote.credits_granted).toLocaleString()}`,
        `Current wallet balance: ${money(quote.wallet_balance)}`, `Balance after purchase: ${money(quote.wallet_after)}`,
        'Simulated wallet funds only. No payment gateway will be charged.',
      ], 'Confirm purchase');
      if (!confirmed) return;
      const confirmButton = document.getElementById('banking-confirm-submit');
      confirmButton.disabled = true; confirmButton.textContent = 'Processing…';
      try {
        const result = await api('/purchase', { method: 'POST', body: JSON.stringify({ plan, idempotency_key: key() }) });
        announce(`${plan.toUpperCase()} subscription purchased. Receipt ${result.receipt_id}.`, 'success');
        try { await window.refreshPlanEntitlements?.(); } catch { /* financial purchase already committed; access refresh can be retried */ }
        await refresh();
        await window.loadFinancialActivity?.(1);
        if (result.transaction_id) await window.showFinancialDetails?.(result.transaction_id);
      } catch (error) {
        if (error.details?.amount_required != null) announceInsufficient(error.details);
        else announce(error.message, 'error');
      } finally { confirmButton.disabled = false; confirmButton.textContent = 'Confirm purchase'; }
    } catch (error) {
      announce(error.message, 'error');
    } finally {
      purchaseProcessing = false;
      addMoneyButton.disabled = false;
      planButtons.forEach((item, index) => {
        if (!item.isConnected) return;
        item.disabled = disabledBefore[index];
        if (item === button) item.textContent = oldLabel;
      });
    }
  }

  async function addMoney(event) {
    event.preventDefault();
    if (topupProcessing) return;
    const input = document.getElementById('banking-topup-amount');
    const amount = Number(input.value);
    const submit = document.getElementById('banking-topup-submit');
    if (!Number.isInteger(amount) || amount < 1 || amount > 100000) {
      document.getElementById('banking-topup-balance').textContent = 'Enter a whole amount from ₹1 to ₹100,000.';
      input.focus(); return;
    }
    const walletLimit = current?.wallet_limit;
    const permitted = walletLimit == null ? 100000 : Math.max(0, Number(walletLimit) - Number(current?.wallet?.balance || 0));
    if (amount > permitted) {
      document.getElementById('banking-topup-balance').textContent = `This ${current?.user?.plan || 'Free'} plan permits a wallet balance up to ${money(walletLimit)}. You can add up to ${money(permitted)}.`;
      input.focus(); return;
    }
    topupProcessing = true;
    submit.disabled = true; submit.textContent = 'Adding…';
    try {
      const result = await api('/wallet/top-up', { method: 'POST', body: JSON.stringify({ amount, idempotency_key: key() }) });
      topupDialog.close(); input.value = '';
      announce(`${money(amount)} in simulated funds was added. New wallet balance: ${money(result.wallet_after)}. Receipt ${result.receipt_id}.`, 'success');
      await refresh();
      await window.loadFinancialActivity?.(1);
      if (result.transaction_id) await window.showFinancialDetails?.(result.transaction_id);
    } catch (error) { announce(error.message, 'error'); }
    finally { topupProcessing = false; submit.disabled = false; submit.textContent = 'Confirm deposit'; }
  }

  async function loadAdmin() {
    const section = document.getElementById('banking-admin-panel');
    section.hidden = current.user.role !== 'admin';
    if (section.hidden) return;
    try {
      const [summary, audit] = await Promise.all([api('/admin/summary'), api('/admin/audit')]);
      const rows = [
        ['Demonstration revenue', money(summary.revenue_total)], ['Platform wallet', money(summary.platform_wallet_balance)],
        ['Successful operations', summary.successful_operations], ['Negative wallets', summary.negative_wallets],
        ['Consistency audit', audit.passed ? 'Consistent' : 'Discrepancy found'],
      ];
      document.getElementById('banking-admin-summary').innerHTML = `<div class="lab10-metrics">${rows.map(([name, value]) => `<div class="lab10-metric"><span>${esc(name)}</span><strong>${esc(value)}</strong></div>`).join('')}</div>`;
    } catch { document.getElementById('banking-admin-summary').textContent = 'Administrator financial diagnostics are temporarily unavailable.'; }
  }

  document.getElementById('banking-add-money').addEventListener('click', () => {
    const limit = current?.wallet_limit;
    const permitted = limit == null ? 100000 : Math.max(0, Number(limit) - Number(current?.wallet?.balance || 0));
    document.getElementById('banking-topup-balance').textContent = `Current balance: ${money(current?.wallet?.balance)}${limit == null ? '' : ` · Free balance cap: ${money(limit)} · Maximum deposit now: ${money(permitted)}`}`;
    topupDialog.showModal();
  });
  document.getElementById('banking-topup-amount').addEventListener('input', event => {
    const amount = Number(event.currentTarget.value || 0);
    document.getElementById('banking-topup-balance').textContent = `Current balance: ${money(current?.wallet?.balance)} · Expected balance after deposit: ${money(Number(current?.wallet?.balance || 0) + amount)}`;
  });
  document.querySelectorAll('[data-banking-amount]').forEach(button => button.addEventListener('click', () => {
    const input = document.getElementById('banking-topup-amount'); input.value = button.dataset.bankingAmount; input.dispatchEvent(new Event('input'));
  }));
  document.getElementById('banking-topup-form').addEventListener('submit', addMoney);
  document.querySelectorAll('[data-dialog-close="banking-topup-dialog"]').forEach(button => button.addEventListener('click', () => topupDialog.close()));
  document.querySelector('[data-view="lab10-banking"]')?.addEventListener('click', refresh);
  if (new URLSearchParams(location.search).get('view') === 'lab10-banking') refresh();
});
