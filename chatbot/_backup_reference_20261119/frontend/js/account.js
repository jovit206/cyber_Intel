document.addEventListener('DOMContentLoaded', () => {
  const base = (window.AEGIS_API_BASE_URL || '').replace(/\/+$/, '');
  const auth = document.getElementById('account-auth');
  const accountPanel = document.getElementById('account-overview-panel');
  const notice = document.getElementById('account-notice');
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const money = value => new Intl.NumberFormat('en-IN', {style:'currency',currency:'INR',maximumFractionDigits:0}).format(Number(value || 0));
  let currentAccount = null;
  let purchaseBusy = false;

  function setAuthView(view) {
    const registering = view === 'register';
    document.body.classList.add('auth-pending');
    document.getElementById('account-login').hidden = registering;
    document.getElementById('account-register').hidden = !registering;
    document.getElementById('auth-heading').textContent = registering ? 'Create your account' : 'Welcome back';
    document.getElementById('auth-description').textContent = registering
      ? 'Create a secure account to access the Threat Intelligence Platform.'
      : 'Sign in to access the Threat Intelligence Platform.';
    notice.replaceChildren();
  }

  async function api(path, options = {}) {
    const response = await fetch(`${base}/api/subscriptions${path}`, {
      credentials: 'same-origin', headers: { 'Content-Type': 'application/json', ...(options.headers || {}) }, ...options,
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      const detail = payload.detail;
      throw new Error(typeof detail === 'string' ? detail : detail?.message || `Request failed (${response.status})`);
    }
    return payload;
  }

  function showNotice(text, kind = '') {
    notice.textContent = text;
    notice.className = `account-notice ${kind}`;
  }

  async function refresh() {
    try {
      const data = await api('/me');
      currentAccount = data;
      auth.hidden = true; accountPanel.hidden = false;
      document.body.classList.remove('auth-pending');
      document.getElementById('account-brand').textContent = 'THREAT INTELLIGENCE PLATFORM';
      document.getElementById('account-email').textContent = data.user.email;
      const adminBadge = document.getElementById('account-role-indicator');
      if (adminBadge) { adminBadge.hidden = !['admin', 'manager'].includes(data.user.role); adminBadge.textContent = data.user.role === 'manager' ? 'MANAGER' : 'ADMIN'; }
      document.getElementById('account-overview').innerHTML = [
        ['Email address', data.user.email], ['Account ID', data.user.user_id], ['Access role', data.user.role || 'user'],
        ['Current plan', (data.user.plan || 'free').toUpperCase()], ['Wallet balance', money(data.wallet?.balance)],
        ['Plan renewal', data.user.renewal_at ? new Date(data.user.renewal_at).toLocaleDateString() : 'Free plan'],
      ].map(([label, value]) => `<div class="account-metric"><span>${esc(label)}</span><strong>${esc(value)}</strong></div>`).join('');
      renderEntitlements(data.entitlements);
      try { await loadPlans(); }
      catch { document.getElementById('account-plan-cards').innerHTML = '<div class="account-empty">Subscription options are temporarily unavailable.</div>'; }
      if (location.hash === '#subscriptions') document.getElementById('subscriptions').scrollIntoView({behavior:'smooth',block:'start'});
    } catch (error) {
      auth.hidden = false; accountPanel.hidden = true;
      if (error.message && !/sign in|session has expired/i.test(error.message)) showNotice('Account information is temporarily unavailable.', 'error');
    }
  }

  function renderEntitlements(entitlements = {}) {
    document.getElementById('account-entitlement-period').textContent = entitlements.wallet_limit
      ? `Usage period ${entitlements.period || 'current month'} · Free wallet balance limit ${money(entitlements.wallet_limit)}.`
      : `Usage period ${entitlements.period || 'current month'} · ${entitlements.plan === 'admin' ? 'Administrator access includes all supported platform features.' : entitlements.plan === 'pro' ? 'Pro includes unlimited feature use.' : 'Usage resets monthly.'}`;
    const rows = Object.values(entitlements.features || {}).map(feature => {
      const usage = feature.limit === null ? 'Unlimited' : `${feature.remaining} of ${feature.limit} uses remaining`;
      return `<article class="account-entitlement"><strong>${esc(feature.label)}</strong><span class="rel-pill ${feature.allowed ? 'ok' : 'warn'}">${feature.allowed ? 'Available' : 'Locked'}</span><small>${esc(usage)} · resets monthly</small></article>`;
    });
    rows.unshift('<article class="account-entitlement"><strong>Live Threat Stream</strong><span class="rel-pill ok">Available</span><small>Included with every plan</small></article>');
    rows.unshift('<article class="account-entitlement"><strong>Top Threats</strong><span class="rel-pill ok">Available</span><small>Included with every plan</small></article>');
    document.getElementById('account-feature-entitlements').innerHTML = rows.join('');
  }

  async function loadPlans() {
    const data = await api('/plans');
    document.getElementById('account-plan-cards').innerHTML = data.plans.map(plan => {
      const active = currentAccount?.user?.plan === plan.name && (plan.name === 'free' || new Date(currentAccount.user.renewal_at || 0).getTime() > Date.now());
      return `<article class="account-plan-card ${active ? 'current' : ''}">${active ? '<span class="account-current-badge">Current plan</span>' : ''}<h3>${esc(plan.name)}</h3><p class="account-plan-price">${money(plan.price)} <small>/ month</small></p><ul>${(plan.features || []).map(item => `<li>${esc(item)}</li>`).join('')}</ul><button class="account-primary-button" type="button" data-account-plan="${esc(plan.name)}" ${active ? 'disabled' : ''}>${active ? 'Current plan' : currentAccount?.user?.plan === plan.name ? 'Renew plan' : 'Choose plan'}</button></article>`;
    }).join('');
    document.querySelectorAll('[data-account-plan]').forEach(button => button.addEventListener('click', () => buyPlan(button.dataset.accountPlan, button)));
  }

  async function buyPlan(plan, button) {
    if (purchaseBusy) return;
    purchaseBusy = true;
    const buttons = [...document.querySelectorAll('[data-account-plan]')];
    buttons.forEach(item => { item.disabled = true; });
    try {
      const quote = await api(`/purchase-quote/${encodeURIComponent(plan)}`);
      if (!quote.sufficient_balance) {
        showNotice(`Insufficient balance. Required ${money(quote.amount_charged)}, available ${money(quote.wallet_balance)}, shortfall ${money(quote.amount_charged - quote.wallet_balance)}. Add funds in Banking Operations.`, 'error');
        return;
      }
      if (!window.confirm(`Purchase ${plan.toUpperCase()} for ${money(quote.price)} per month using your wallet?`)) return;
      const idempotencyKey = crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
      const result = await api('/purchase', {method:'POST', body:JSON.stringify({plan,idempotency_key:idempotencyKey})});
      showNotice(`${plan.toUpperCase()} plan activated. New wallet balance ${money(result.wallet_after)}.`, 'success');
      await refresh();
    } catch (error) { showNotice(error.message, 'error'); }
    finally { purchaseBusy = false; buttons.forEach(item => { if (item.isConnected) item.disabled = false; }); }
  }

  async function submitCredentials(form, endpoint) {
    const payload = Object.fromEntries(new FormData(form));
    if (endpoint === '/register') {
      document.querySelectorAll('.field-error').forEach(field => { field.textContent = ''; });
      if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(payload.email || '')) {
        form.elements.email.setCustomValidity('Enter a valid email address.');
        form.elements.email.reportValidity(); form.elements.email.setCustomValidity(''); return;
      }
      const strong = payload.password.length >= 12 && /[a-z]/.test(payload.password)
        && /[A-Z]/.test(payload.password) && /\d/.test(payload.password) && /[^A-Za-z0-9]/.test(payload.password);
      if (!strong) {
        form.querySelector('[data-error-for="password"]').textContent = 'Use at least 12 characters with upper and lowercase letters, a number, and a symbol.';
        form.elements.password.focus(); return;
      }
      if (payload.password !== payload.confirm_password) {
        form.querySelector('[data-error-for="confirm_password"]').textContent = 'Passwords do not match.';
        form.elements.confirm_password.focus(); return;
      }
      delete payload.confirm_password;
    }
    try {
      const result = await api(endpoint, { method: 'POST', body: JSON.stringify(payload) });
      if (endpoint === '/login') {
        const role = result.user?.role;
        const requestedNext = new URLSearchParams(window.location.search).get('next');
        // Same-origin paths only: browsers treat "/\host" like "//host", so reject backslashes and control characters too.
        const safeNext = requestedNext && /^\/(?![\/\\])/.test(requestedNext) && !/[\\\u0000-\u001f]/.test(requestedNext)
          && !requestedNext.startsWith('/account') ? requestedNext : null;
        const roleHome = role === 'admin' ? '/?view=admin' : role === 'manager' ? '/?view=manager' : '/?view=lab10-banking#financial-activity';
        // Return every role to the page that required sign-in; the server still enforces each route's permissions.
        const destination = safeNext || roleHome;
        showNotice('Signed in. Opening your dashboard…', 'success');
        window.location.assign(destination);
      } else {
        form.reset(); setAuthView('login');
        showNotice('Account created. Sign in to continue.', 'success');
        document.querySelector('#account-login [name="email"]').value = payload.email;
      }
    } catch (error) { showNotice(error.message, 'error'); }
  }

  document.getElementById('account-login').addEventListener('submit', event => { event.preventDefault(); submitCredentials(event.currentTarget, '/login'); });
  document.getElementById('account-register').addEventListener('submit', event => { event.preventDefault(); submitCredentials(event.currentTarget, '/register'); });
  document.querySelectorAll('[data-auth-view]').forEach(button => button.addEventListener('click', () => setAuthView(button.dataset.authView)));
  document.querySelectorAll('.password-toggle').forEach(button => button.addEventListener('click', () => {
    const input = button.parentElement.querySelector('input');
    const visible = input.type === 'password'; input.type = visible ? 'text' : 'password';
    button.textContent = visible ? 'Hide' : 'Show'; button.setAttribute('aria-label', visible ? 'Hide password' : 'Show password');
  }));
  document.getElementById('account-signout').addEventListener('click', async () => {
    try { await api('/logout', { method: 'POST' }); window.location.replace('/account'); }
    catch (error) { showNotice(error.message, 'error'); }
  });
  setAuthView('login');
  refresh();
});
