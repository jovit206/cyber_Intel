document.addEventListener('DOMContentLoaded', async () => {
  const message = document.getElementById('account-summary-message');
  if (!message) return;
  const base = (window.AEGIS_API_BASE_URL || '').replace(/\/+$/, '');
  const planSummary = document.getElementById('account-plan-summary');
  fetch(`${base}/api/subscriptions/plans`, { credentials: 'same-origin' }).then(response => {
    if (!response.ok) throw new Error('Plan catalog is unavailable');
    return response.json();
  }).then(data => {
    const format = value => new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR', maximumFractionDigits: 0 }).format(Number(value || 0));
    planSummary.replaceChildren(...data.plans.map(plan => {
      const link = document.createElement('a');
      link.href = '/account#plans';
      link.className = 'account-plan-chip';
      const label = document.createElement('strong');
      label.textContent = plan.name.toUpperCase();
      const details = document.createElement('span');
      details.textContent = `${format(plan.price)} · ${Number(plan.monthly_credits).toLocaleString()} credits`;
      link.append(label, details);
      return link;
    }));
  }).catch(() => { planSummary.textContent = 'Subscription packages are temporarily unavailable.'; });
  try {
    const response = await fetch(`${base}/api/subscriptions/me`, { credentials: 'same-origin' });
    if (!response.ok) return;
    const data = await response.json();
    const money = value => new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR', minimumFractionDigits: 2 }).format(Number(value || 0));
    const date = value => value ? new Date(value).toLocaleDateString() : 'Not set';
    message.textContent = `${data.user.plan.toUpperCase()} plan · Wallet ${money(data.wallet?.balance)} · ${Number(data.available_credits || 0).toLocaleString()} of ${Number(data.user.credits_allocated || 0).toLocaleString()} credits available · ${Number(data.user.credits_used || 0).toLocaleString()} used · Renews ${date(data.user.renewal_at)}.`;
    document.getElementById('account-summary-link').textContent = 'Manage subscription';
    document.getElementById('account-summary-topup').hidden = false;
    document.getElementById('account-summary-history').hidden = false;
  } catch {
    message.textContent = 'Account information is temporarily unavailable.';
  }
});
