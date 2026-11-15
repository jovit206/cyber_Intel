document.addEventListener('DOMContentLoaded', () => {
  const apiBase = (window.AEGIS_API_BASE_URL || '').replace(/\/+$/, '');
  const api = path => `${apiBase}${path}`;
  const loaded = new Set();
  let pendingReservation;
  const currency = new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR', maximumFractionDigits: 2 });
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const money = value => Number.isFinite(Number(value)) ? currency.format(Number(value)) : '—';
  const dateValue = value => value?.$date || value || '';
  const dateText = value => {
    const date = new Date(dateValue(value));
    return Number.isNaN(date.getTime()) ? 'Date unavailable' : date.toLocaleString();
  };
  const badge = (text, kind = 'info') => `<span class="rel-pill ${kind}">${esc(text)}</span>`;
  const metric = (label, value, note = '') => `<div class="lab10-metric"><span>${esc(label)}</span><strong>${esc(value)}</strong>${note ? `<small>${esc(note)}</small>` : ''}</div>`;
  function sanitizeTechnical(value) {
    if (Array.isArray(value)) return value.map(sanitizeTechnical);
    if (!value || typeof value !== 'object') return value;
    return Object.fromEntries(Object.entries(value)
      .filter(([key]) => key !== 'activity' && key !== 'step')
      .map(([key, item]) => [key, sanitizeTechnical(item)]));
  }
  const technical = value => `<details class="lab10-technical"><summary>View technical details</summary><pre>${esc(JSON.stringify(sanitizeTechnical(value), null, 2))}</pre></details>`;

  async function getJson(path, options) {
    const response = await fetch(api(path), options);
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.detail || `Request failed (${response.status})`);
    return payload;
  }

  function fail(container, error, retry) {
    container.innerHTML = `<div class="empty-prompt lab10-error">${esc(error.message)}<br><button class="xs-btn" type="button">Retry</button></div>`;
    container.querySelector('button')?.addEventListener('click', retry, { once: true });
  }

  function setLiveFormState(live, message, badgeId, formId) {
    const badgeNode = document.getElementById(badgeId);
    const form = document.getElementById(formId);
    badgeNode.className = `rel-pill ${live ? 'ok' : 'warn'}`;
    badgeNode.textContent = live ? 'Live database' : 'Read-only snapshot';
    form.querySelectorAll('input,select,button').forEach(control => { control.disabled = !live; });
    form.dataset.message = message;
  }

  function renderBanking(data) {
    const accounts = data.accounts || [];
    const transfers = data.transfers || [];
    const total = accounts.reduce((sum, account) => sum + Number(account.balance || 0), 0);
    const rows = accounts.map(account => `<tr><td>${esc(account.account_id)}</td><td>${esc(account.holder || '—')}</td><td>${money(account.balance)}</td><td>${badge(account.status || 'unknown', account.status === 'active' ? 'ok' : 'warn')}</td></tr>`).join('');
    const history = transfers.map(transfer => `<tr><td><code>${esc(transfer.transfer_id || '—')}</code></td><td>${esc(transfer.from || '—')}</td><td>${esc(transfer.to || '—')}</td><td>${money(transfer.amount)}</td><td>${badge(transfer.refund_of ? 'Refund' : 'Recorded', transfer.refund_of ? 'warn' : 'info')}</td><td>${esc(dateText(transfer.at))}</td></tr>`).join('');
    const outcomes = (data.transfer_results || []).map(result => {
      const successful = result.status === 'COMMITTED';
      const changes = (result.balance_changes || []).map(change => `<li>${esc(change)}</li>`).join('');
      return `<tr><td>${esc(result.description)}</td><td>${badge(successful ? 'Committed' : result.status === 'ROLLED BACK' ? 'Rolled back' : 'Declined', successful ? 'ok' : result.status === 'ROLLED BACK' ? 'warn' : 'bad')}</td><td>${esc(result.reference || result.reason || '—')}</td><td>${changes ? `<ul class="lab10-change-list">${changes}</ul>` : 'No balance change recorded'}</td></tr>`;
    }).join('');
    const snapshot = data.mode === 'live' ? 'Live account data' : `Saved account snapshot · ${dateText(data.recorded_at)}`;
    document.getElementById('banking-data-badge').className = `rel-pill ${data.mode === 'live' ? 'ok' : 'warn'}`;
    document.getElementById('banking-data-badge').textContent = data.mode === 'live' ? 'Live data' : 'Saved snapshot';
    document.getElementById('banking-data-notice').innerHTML = `<strong>${esc(snapshot)}</strong><br>${esc(data.message || '')}<br>Payment outcomes shown below are from the saved transaction run.`;
    document.getElementById('lab10-bank-data').innerHTML = `
      <div class="lab10-metrics">${metric('Accounts', accounts.length)}${metric('Combined balances', money(total), 'Snapshot total')}${metric('Recorded transfers', transfers.length)}</div>
      <h3 class="lab10-subheading">Account overview</h3>
      ${accounts.length ? `<div class="lab10-table-wrap"><table class="lab10-table"><thead><tr><th>Account</th><th>Holder</th><th>Balance</th><th>Status</th></tr></thead><tbody>${rows}</tbody></table></div>` : '<div class="empty-prompt">No account records are available.</div>'}
      <h3 class="lab10-subheading">Transfer history</h3>
      ${transfers.length ? `<div class="lab10-table-wrap"><table class="lab10-table"><thead><tr><th>Reference</th><th>From</th><th>To</th><th>Amount</th><th>Status</th><th>Date</th></tr></thead><tbody>${history}</tbody></table></div>` : '<div class="empty-prompt">No transactions have been recorded.</div>'}
      <h3 class="lab10-subheading">Payment outcomes</h3>
      ${outcomes ? `<div class="lab10-table-wrap"><table class="lab10-table"><thead><tr><th>Transfer</th><th>Outcome</th><th>Reference or reason</th><th>Balance changes</th></tr></thead><tbody>${outcomes}</tbody></table></div>` : '<div class="empty-prompt">No payment outcomes are available.</div>'}
      ${technical(data.technical)}`;
  }

  const financialMoney = value => currency.format(Number(value || 0));
  const financialLabel = item => {
    if (item.type === 'wallet_top_up') return 'Money added to wallet';
    if (item.type === 'wallet_opening_balance') return 'Simulated opening wallet balance';
    if (item.type === 'subscription_purchase') {
      return item.plan_before && item.plan_before !== 'free'
        ? `Subscription changed: ${String(item.plan_before).toUpperCase()} → ${String(item.plan_after || '').toUpperCase()}`
        : `Purchased ${String(item.plan_after || item.request_data?.plan || 'subscription').toUpperCase()} subscription`;
    }
    if (item.type === 'wallet_transfer') return item.direction === 'debit' ? 'Money sent' : 'Money received';
    if (item.type === 'feature_usage') return `Feature usage · ${item.feature || 'credits'}`;
    if (item.type === 'refund') return 'Refund';
    return item.description || String(item.type || 'Financial activity').replaceAll('_', ' ');
  };

  function renderFinancialActivity(data) {
    const summary = data.summary || {};
    document.getElementById('financial-summary').innerHTML = [
      metric('Current wallet balance', financialMoney(summary.wallet_balance)),
      metric('Total money added', financialMoney(summary.total_money_added)),
      metric('Subscription spending', financialMoney(summary.subscription_spending)),
      metric('Refunds', financialMoney(summary.refunds)),
      metric('Credits consumed', Number(summary.credits_consumed || 0).toLocaleString()),
    ].join('');

    const typeFilter = document.querySelector('#financial-filters [name="transaction_type"]');
    const selectedType = typeFilter.value;
    const types = data.available_types || [];
    typeFilter.innerHTML = `<option value="">All activity</option>${types.map(type => `<option value="${esc(type)}">${esc(String(type).replaceAll('_', ' '))}</option>`).join('')}`;
    typeFilter.value = types.includes(selectedType) ? selectedType : '';

    const entries = data.items || [];
    const rows = entries.map(item => {
      const isCreditCharge = item.type === 'feature_usage' && Number(item.credits_delta) < 0;
      const sign = item.direction === 'debit' ? '−' : item.direction === 'credit' ? '+' : '';
      const amount = isCreditCharge ? `−${Math.abs(Number(item.credits_delta)).toLocaleString()} credits`
        : `${sign}${financialMoney(item.amount)}`;
      const state = item.status || 'unknown';
      const statusText = state === 'committed' ? 'Successful' : state === 'rolled_back' || state === 'failed'
        ? 'Failed · rolled back' : state === 'refunded' ? 'Refunded' : state[0].toUpperCase() + state.slice(1);
      const statusClass = state === 'committed' ? 'ok' : state === 'refunded' ? 'warn'
        : state === 'pending' ? 'info' : 'bad';
      return `<tr><td>${esc(dateText(item.created_at))}</td><td><code>${esc(item.transaction_id)}</code></td>
        <td><strong>${esc(financialLabel(item))}</strong><small>${esc(item.description || '')}</small></td>
        <td class="financial-amount ${item.direction === 'debit' || isCreditCharge ? 'debit' : 'credit'}">${esc(amount)}</td>
        <td>${item.balance_after == null ? '—' : financialMoney(item.balance_after)}</td>
        <td>${badge(statusText, statusClass)}</td>
        <td><button class="financial-details-button" type="button" data-financial-details="${esc(item.transaction_id)}">Details</button></td></tr>`;
    }).join('');
    document.getElementById('financial-history').innerHTML = rows
      ? `<div class="lab10-table-wrap financial-table-wrap"><table class="lab10-table financial-table"><thead><tr><th>Date &amp; time</th><th>Transaction ID</th><th>Activity</th><th>Amount</th><th>Balance after</th><th>Status</th><th>Details</th></tr></thead><tbody>${rows}</tbody></table></div>`
      : '<div class="empty-prompt financial-empty">No personal financial activity matches these filters. Wallet and subscription activity will appear here after a committed operation.</div>';

    const page = Number(data.page || 1);
    const pages = Number(data.total_pages || 0);
    document.getElementById('financial-pagination').innerHTML = pages > 1
      ? `<button class="xs-btn" type="button" data-financial-page="${page - 1}" ${page <= 1 ? 'disabled' : ''}>Previous</button><span>Page ${page} of ${pages} · ${Number(data.total || 0)} transactions</span><button class="xs-btn" type="button" data-financial-page="${page + 1}" ${page >= pages ? 'disabled' : ''}>Next</button>`
      : `<span>${Number(data.total || 0)} transaction${Number(data.total) === 1 ? '' : 's'}</span>`;

    document.querySelectorAll('[data-financial-page]').forEach(button => button.addEventListener('click', () => {
      loadFinancialActivity(Number(button.dataset.financialPage));
    }));
    document.querySelectorAll('[data-financial-details]').forEach(button => button.addEventListener('click', () => {
      showFinancialDetails(button.dataset.financialDetails);
    }));
  }

  async function loadFinancialActivity(page = 1) {
    const container = document.getElementById('financial-history');
    const form = document.getElementById('financial-filters');
    const filters = Object.fromEntries(new FormData(form));
    const params = new URLSearchParams({ page: String(page), page_size: '20' });
    for (const [key, value] of Object.entries(filters)) {
      if (!value) continue;
      params.set(key === 'transaction_type' ? 'transaction_type' : key, value);
    }
    container.innerHTML = '<div class="loading-state">Loading your financial activity…</div>';
    try {
      renderFinancialActivity(await getJson(`/api/subscriptions/financial-activity?${params}`));
    } catch (error) {
      container.innerHTML = `<div class="empty-prompt lab10-error">${esc(error.message || 'Financial activity is temporarily unavailable.')}<br><button id="financial-retry" class="xs-btn" type="button">Retry</button></div>`;
      document.getElementById('financial-retry')?.addEventListener('click', () => loadFinancialActivity(page), { once: true });
    }
  }
  window.loadFinancialActivity = loadFinancialActivity;

  async function showFinancialDetails(transactionId) {
    const dialog = document.getElementById('financial-detail-dialog');
    const content = document.getElementById('financial-detail-content');
    document.getElementById('financial-detail-title').textContent = 'Loading transaction…';
    content.innerHTML = '<div class="loading-state">Loading receipt and ledger details…</div>';
    dialog.showModal();
    try {
      const data = await getJson(`/api/subscriptions/financial-activity/${encodeURIComponent(transactionId)}`);
      const item = data.transaction || {};
      document.getElementById('financial-detail-title').textContent = financialLabel(item);
      const summaryRows = [
        ['Transaction ID', item.transaction_id], ['Date & time', dateText(item.created_at)],
        ['Status', item.status === 'committed' ? 'Successful' : item.status === 'rolled_back' ? 'Failed · rolled back' : item.status],
        ['Description', item.failure_reason || item.description],
        ['Amount', item.type === 'feature_usage' ? `${Math.abs(Number(item.credits_delta || 0))} credits` : financialMoney(item.amount)],
        ['Balance before', item.balance_before == null ? '—' : financialMoney(item.balance_before)],
        ['Balance after', item.balance_after == null ? '—' : financialMoney(item.balance_after)],
        ['Plan change', item.plan_before || item.plan_after ? `${item.plan_before || '—'} → ${item.plan_after || '—'}` : '—'],
        ['Credits changed', item.credits_delta == null ? item.credits_allocated ?? '—' : item.credits_delta],
        ['Failure / rollback', item.failure_reason || (item.rolled_back ? 'All transaction changes were rolled back.' : '—')],
      ];
      const rows = summaryRows.map(([key, value]) => `<div class="financial-detail-row"><strong>${esc(key)}</strong><span>${esc(value ?? '—')}</span></div>`).join('');
      const receipt = data.receipt ? `<section><h3>Receipt</h3><div class="financial-detail-row"><strong>Receipt ID</strong><span>${esc(data.receipt.receipt_id)}</span></div><div class="financial-detail-row"><strong>Receipt status</strong><span>${esc(data.receipt.status)}</span></div></section>` : '';
      const ledgerRows = [...(data.wallet_ledger || []).map(row => ({ kind: row.kind, delta: financialMoney(row.delta), ledger: 'Wallet' })),
        ...(data.credit_ledger || []).map(row => ({ kind: row.kind, delta: `${row.delta > 0 ? '+' : ''}${row.delta} credits`, ledger: 'Credits' }))];
      const ledgers = ledgerRows.length ? `<section><h3>Ledger entries</h3>${ledgerRows.map(row => `<div class="financial-detail-row"><strong>${esc(row.ledger)} · ${esc(row.kind)}</strong><span>${esc(row.delta)}</span></div>`).join('')}</section>` : '';
      const payments = (data.payments || []).length ? `<section><h3>Payment</h3>${data.payments.map(row => `<div class="financial-detail-row"><strong>${esc(row.plan)} · ${esc(row.status)}</strong><span>${esc(financialMoney(row.amount))}</span></div>`).join('')}</section>` : '';
      content.innerHTML = `<section>${rows}</section>${receipt}${payments}${ledgers}`;
    } catch (error) {
      document.getElementById('financial-detail-title').textContent = 'Transaction details';
      content.innerHTML = `<div class="empty-prompt lab10-error">${esc(error.message || 'Transaction details are unavailable.')}</div>`;
    }
  }
  window.showFinancialDetails = showFinancialDetails;

  function renderFailover(failover) {
    const checks = failover.checks || {};
    return `<div class="lab10-metrics">${metric('Payments acknowledged', checks.acknowledged ?? '—', `of ${checks.sent ?? '—'} sent`)}${metric('Payments in history', checks.inHistory ?? '—')}${metric('Unknown outcome', checks.unknownApplied?.length ?? '—')}${metric('Bank total after recovery', money(checks.totalMoney))}</div>
      <p class="lab10-explainer">The captured run recorded ${esc(checks.acknowledged ?? '—')} acknowledged payments and ${esc(checks.unknownApplied?.length ?? 0)} payment with an unknown client outcome. The history contained ${esc(checks.inHistory ?? '—')} records. An unknown outcome is not presented as a confirmed success or failure.</p>
      ${technical(failover.technical)}`;
  }

  function renderInventory(data) {
    const inventory = data.inventory || {};
    const hasCatalog = ['live database', 'demonstration data'].includes(inventory.source) && inventory.catalog_complete === true;
    const isDemo = inventory.source === 'demonstration data';
    const products = hasCatalog && Array.isArray(inventory.products) ? inventory.products : [];
    const runs = inventory.reservation_runs || [];
    const latest = runs[runs.length - 1] || {};
    const consistency = inventory.transaction_consistency || {};
    const perCustomer = latest.perCustomer || {};
    const reservationRows = Object.entries(perCustomer).map(([customer, entry]) => {
      const [state, attempts, conflicts, order] = entry;
      return `<tr><td>${esc(customer)}</td><td>${badge(state === 'RESERVED' ? 'Reserved' : state === 'INSUFFICIENT' ? 'Out of stock' : 'Conflict', state === 'RESERVED' ? 'ok' : state === 'INSUFFICIENT' ? 'warn' : 'bad')}</td><td>${esc(attempts)}</td><td>${esc(order || '—')}</td></tr>`;
    }).join('');
    const runCards = runs.map(run => {
      const counts = run.counts || {};
      return `<div class="lab10-result-panel"><h4>${esc(run.label)}</h4><div class="lab10-metrics compact">${metric('Requests', run.requests)}${metric('Reserved', counts.RESERVED ?? '—')}${metric('Stock left', run.stockLeft ?? '—')}${metric('Conflicts / declined', Number(counts.CONFLICT || 0) + Number(counts.INSUFFICIENT || 0))}</div><p>Observed from ${esc(run.starting_stock)} available units; ${esc(run.orders)} orders created.</p></div>`;
    }).join('');
    const statusOf = product => product.stock_status || (Number(product.stock ?? product.available_stock ?? 0) <= 0 ? 'Out of Stock' : 'In Stock');
    const rowsFor = (list) => list.map(product => `<tr><td>${esc(product.name || product.product_name || 'Unnamed product')}</td><td><code>${esc(product.product_id || product.sku || '—')}</code></td><td>${esc(product.available_stock ?? product.stock ?? '—')}</td><td>${esc(product.reserved_quantity ?? 'Not captured')}</td><td>${money(product.price ?? product.unit_price)}</td><td>${badge(statusOf(product), statusOf(product) === 'In Stock' ? 'ok' : statusOf(product) === 'Low Stock' ? 'warn' : 'bad')}</td><td>${product.last_updated || product.updated_at ? esc(dateText(product.last_updated || product.updated_at)) : 'Not recorded'}</td></tr>`).join('');
    document.getElementById('inventory-data-badge').className = `rel-pill ${hasCatalog ? (isDemo ? 'info' : 'ok') : 'bad'}`;
    document.getElementById('inventory-data-badge').textContent = hasCatalog ? (isDemo ? 'Demonstration Data' : 'Live Data') : 'Unavailable';
    document.getElementById('lab10-inventory-data').innerHTML = `
      <div class="lab10-metrics">${metric('Total products', hasCatalog ? products.length : 'Unavailable', hasCatalog ? `${inventory.database_product_count} in ${inventory.source_database}.inventory` : 'Inventory source required')}${metric('Total available stock', hasCatalog ? products.reduce((sum, product) => sum + Number(product.available_stock ?? product.stock ?? 0), 0) : 'Unavailable')}${metric('Total reserved units', hasCatalog ? (inventory.reserved_quantity ?? 'Not captured') : 'Unavailable')}</div>
      ${hasCatalog ? `<p class="lab10-snapshot-note">${esc(inventory.source_message || (isDemo ? 'Persisted demonstration inventory.' : 'Products are read from the complete shop.inventory collection.'))}</p><h3 class="lab10-subheading">Product catalog</h3>
      <div class="lab10-inventory-filters"><label>Search products<input id="inventory-search" type="search" placeholder="Product name or ID"></label><label>Stock status<select id="inventory-status-filter"><option value="">All statuses</option><option>In Stock</option><option>Low Stock</option><option>Out of Stock</option></select></label></div>
      <div id="inventory-product-table"></div>
      <h3 class="lab10-subheading">Recent reservations</h3>${inventory.recent_orders?.length ? `<div class="lab10-table-wrap"><table class="lab10-table"><thead><tr><th>Order</th><th>Customer</th><th>Quantity</th><th>Status</th><th>Date</th></tr></thead><tbody>${inventory.recent_orders.map(order => `<tr><td><code>${esc(order.order_id)}</code>${order.is_demo ? ` ${badge('Demo reservation', 'info')}` : ''}</td><td>${esc(order.customer_id)}</td><td>${esc((order.items || []).reduce((sum, item) => sum + Number(item.qty || 0), 0))}</td><td>${badge(order.status || 'pending', order.status === 'paid' ? 'ok' : 'info')}</td><td>${esc(dateText(order.created_at))}</td></tr>`).join('')}</tbody></table></div>` : '<div class="empty-prompt">No reservations have been recorded yet.</div>'}` : `<div class="empty-prompt lab10-error" role="alert"><strong>Inventory catalog unavailable.</strong><br>${esc(inventory.catalog_error || data.message || 'The inventory database is not connected.')}<br>No live or demonstration catalog data could be loaded.<br><button id="inventory-retry" class="xs-btn" type="button">Retry connection</button></div>`}
      <h3 class="lab10-subheading">Reservation contention</h3><div class="lab10-comparison-grid">${runCards}</div>
      <h3 class="lab10-subheading">Recent customer outcomes</h3>${reservationRows ? `<div class="lab10-table-wrap"><table class="lab10-table"><thead><tr><th>Customer</th><th>Outcome</th><th>Attempts</th><th>Order reference</th></tr></thead><tbody>${reservationRows}</tbody></table></div>` : '<div class="empty-prompt">No reservation details are available.</div>'}
      <h3 class="lab10-subheading">Order creation failure check</h3><div class="lab10-result-panel"><p>Without a transaction, stock changed ${esc(consistency.unsafe_comparison?.stockBefore ?? '—')} → ${esc(consistency.unsafe_comparison?.stockAfter ?? '—')} while ${esc(consistency.unsafe_comparison?.ordersCreated ?? '—')} orders were created.</p><p>With a transaction, stock changed ${esc(consistency.transactional?.stockBefore ?? '—')} → ${esc(consistency.transactional?.stockAfter ?? '—')} while ${esc(consistency.transactional?.ordersCreated ?? '—')} orders were created.</p>${badge(consistency.transactional?.verdict || 'No consistency result recorded', consistency.transactional?.verdict === 'consistent' ? 'ok' : 'warn')}</div>
      <div class="lab10-result-panel"><strong>Transactional checkout:</strong> ${esc(inventory.checkout.successful.outcome || '—')} · stock ${esc(inventory.checkout.successful.stock?.join(' → ') || '—')} · wallet ${money(inventory.checkout.successful.wallet?.[0])} → ${money(inventory.checkout.successful.wallet?.[1])}<br><strong>Declined checkout:</strong> ${esc(inventory.checkout.declined.outcome || '—')} · stock ${esc(inventory.checkout.declined.stock?.join(' → ') || '—')} · wallet ${money(inventory.checkout.declined.wallet?.[0])} → ${money(inventory.checkout.declined.wallet?.[1])}</div>
      ${technical(inventory.technical)}`;
    const reservationEnabled = hasCatalog && products.length > 0;
    setLiveFormState(reservationEnabled, inventory.source, 'inventory-data-badge', 'reservation-form');
    if (hasCatalog) {
      const inventoryBadge = document.getElementById('inventory-data-badge');
      inventoryBadge.className = `rel-pill ${isDemo ? 'info' : 'ok'}`;
      inventoryBadge.textContent = isDemo ? 'Demonstration Data' : 'Live Data';
    } else {
      const inventoryBadge = document.getElementById('inventory-data-badge');
      inventoryBadge.className = 'rel-pill bad';
      inventoryBadge.textContent = 'Unavailable';
    }
    const select = document.getElementById('reservation-product');
    select.innerHTML = products.length ? products.map(product => `<option value="${esc(product.product_id || product.sku || '')}">${esc(product.name || product.product_name || product.product_id || product.sku)}</option>`).join('') : '<option value="">No products available</option>';
    const table = document.getElementById('inventory-product-table');
    const search = document.getElementById('inventory-search');
    const statusFilter = document.getElementById('inventory-status-filter');
    const applyFilters = () => {
      const query = search.value.trim().toLowerCase();
      const selectedStatus = statusFilter.value;
      const filtered = products.filter(product => {
        const name = String(product.name || product.product_name || '').toLowerCase();
        const id = String(product.product_id || product.sku || '').toLowerCase();
        return (!query || name.includes(query) || id.includes(query)) && (!selectedStatus || statusOf(product) === selectedStatus);
      });
      table.innerHTML = filtered.length
        ? `<div class="lab10-table-wrap"><table class="lab10-table"><thead><tr><th>Product</th><th>Product ID / SKU</th><th>Available stock</th><th>Reserved</th><th>Unit price</th><th>Status</th><th>Last updated</th></tr></thead><tbody>${rowsFor(filtered)}</tbody></table></div><p class="lab10-snapshot-note">Showing ${filtered.length} of ${products.length} products.</p>`
        : `<div class="empty-prompt">${products.length ? 'No products match those filters.' : 'No inventory products were found.'}</div>`;
    };
    search?.addEventListener('input', applyFilters);
    statusFilter?.addEventListener('change', applyFilters);
    if (table) applyFilters();
    document.getElementById('inventory-retry')?.addEventListener('click', event => {
      refreshReliabilityInventory(event.currentTarget);
    }, { once: true });
  }

  function renderPerformance(data) {
    const rows = (data.inventory?.throughput || []).map(row => `<tr><td>${esc(row.threads)}</td><td>${esc(row.txn.reserved)}</td><td>${esc(row.txn.gave_up)}</td><td>${esc(row.txn.conflicts)}</td><td>${esc(row.txn.seconds)} s</td><td>${esc(row.txn.perSecond)}</td><td>${row.txn.consistent ? badge('Consistent', 'ok') : badge('Check required', 'bad')}</td></tr>`).join('');
    document.getElementById('lab10-performance-data').innerHTML = `
      <div class="lab10-chart" role="img" aria-label="Measured reservation throughput across worker counts">${(data.inventory?.throughput || []).map(row => {
        const max = Math.max(1, ...data.inventory.throughput.map(item => Math.max(item.txn.perSecond, item.plain.perSecond)));
        return `<div class="lab10-chart-row"><strong>${esc(row.threads)} workers</strong><div class="lab10-bar-pair"><span class="lab10-bar txn" style="width:${Math.max(1, row.txn.perSecond / max * 100)}%">Transactional ${esc(row.txn.perSecond)} / sec</span><span class="lab10-bar plain" style="width:${Math.max(1, row.plain.perSecond / max * 100)}%">Non-transactional ${esc(row.plain.perSecond)} / sec</span></div></div>`;
      }).join('') || '<div class="empty-prompt">No saved performance measurements are available.</div>'}</div>
      ${rows ? `<div class="lab10-table-wrap"><table class="lab10-table"><thead><tr><th>Workers</th><th>Reserved</th><th>Failed</th><th>Conflicts</th><th>Duration</th><th>Success / sec</th><th>Consistency</th></tr></thead><tbody>${rows}</tbody></table></div>` : ''}
      <p class="lab10-explainer">The non-transactional line is included only to compare benchmark speed. A separate recorded failure showed stock decreasing without an order when order creation failed. Use the transactional reservation operation for stock changes.</p>`;
  }

  async function loadBanking() {
    const dataNode = document.getElementById('lab10-bank-data');
    loadFinancialActivity();
    if (!loaded.has('banking')) {
      loaded.add('banking');
      getJson('/api/lab10/banking/dashboard').then(data => renderBanking(data)).catch(error => {
        loaded.delete('banking'); fail(dataNode, error, loadBanking);
      });
    }
  }

  async function loadReliabilityInventory() {
    const failoverNode = document.getElementById('lab10-failover-data');
    const inventoryNode = document.getElementById('lab10-inventory-data');
    if (loaded.has('reliability-inventory')) return;
    loaded.add('reliability-inventory');
    try {
      const data = await getJson('/api/lab10/reliability/dashboard');
      const live = data.mode === 'live';
      const demoInventory = data.inventory?.source === 'demonstration data';
      const clusterBadge = document.getElementById('lab10-cluster-badge');
      clusterBadge.className = `rel-pill ${live ? 'ok' : demoInventory ? 'info' : 'warn'}`;
      clusterBadge.textContent = live ? 'Live connection' : demoInventory ? 'Demo catalog' : 'Saved results';
      document.getElementById('lab10-cluster-status').innerHTML = `<strong>${live ? 'Live data' : demoInventory ? 'Demonstration inventory' : 'Saved operational data'}:</strong> ${esc(data.message || '')}`;
      failoverNode.innerHTML = renderFailover(data.failover || {});
      renderInventory(data);
      renderPerformance(data);
    } catch (error) {
      loaded.delete('reliability-inventory');
      fail(failoverNode, error, loadReliabilityInventory);
      fail(inventoryNode, error, loadReliabilityInventory);
      fail(document.getElementById('lab10-performance-data'), error, loadReliabilityInventory);
    }
  }

  async function refreshReliabilityInventory(button) {
    button.disabled = true;
    button.textContent = 'Refreshing...';
    document.getElementById('lab10-inventory-data').innerHTML = '<div class="loading-state">Refreshing inventory from MongoDB...</div>';
    loaded.delete('reliability-inventory');
    await loadReliabilityInventory();
    button.disabled = false;
    button.textContent = 'Refresh inventory';
  }

  document.getElementById('inventory-refresh')?.addEventListener('click', event => refreshReliabilityInventory(event.currentTarget));

  document.getElementById('reservation-form')?.addEventListener('submit', async event => {
    event.preventDefault();
    const form = event.currentTarget;
    const result = document.getElementById('reservation-result');
    const customer = document.getElementById('reservation-customer').value.trim();
    const quantity = Number(document.getElementById('reservation-quantity').value);
    if (!customer || !Number.isInteger(quantity) || quantity <= 0) {
      result.innerHTML = '<div class="empty-prompt">Enter a customer ID and a whole quantity greater than zero.</div>'; return;
    }
    const productId = document.getElementById('reservation-product').value;
    if (pendingReservation && (pendingReservation.customer !== customer || pendingReservation.product !== productId || pendingReservation.quantity !== quantity)) {
      result.innerHTML = '<div class="empty-prompt">A previous reservation has an unconfirmed outcome. Retry it with the same details before starting another reservation.</div>'; return;
    }
    if (!pendingReservation) pendingReservation = { customer, product: productId, quantity, key: crypto.randomUUID() };
    const button = document.getElementById('reservation-submit'); button.disabled = true; button.textContent = 'Reserving…';
    result.innerHTML = '<div class="loading-state">Reserving stock and creating the order in one transaction...</div>';
    try {
      const response = await getJson('/api/lab10/inventory/reservations', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ product_id: productId, customer_id: customer, quantity, idempotency_key: pendingReservation.key }) });
      pendingReservation = null;
      result.innerHTML = response.status === 'reserved'
        ? `<div class="lab10-result-panel"><h3>Reservation confirmed</h3>${badge(response.duplicate_request ? 'Already confirmed' : 'Order created', 'ok')}<p>Order ${esc(response.order.order_id)} · ${esc(quantity)} unit(s) · ${response.stock_before == null ? '' : `stock ${esc(response.stock_before)} → `}${esc(response.stock_after)} · ${esc(response.attempts)} attempt(s)</p></div>`
        : `<div class="lab10-result-panel"><h3>Reservation not completed</h3>${badge(response.status === 'conflict' ? 'Conflict' : 'Not reserved', 'warn')}<p>${esc(response.reason)}</p>${response.stock != null ? `<p>Available stock remains ${esc(response.stock)}.</p>` : ''}</div>`;
      loaded.delete('reliability-inventory'); loadReliabilityInventory();
    } catch (error) { fail(result, error, () => form.requestSubmit()); }
    finally { button.textContent = 'Reserve stock'; button.disabled = false; }
  });

  document.querySelector('[data-view="lab10-banking"]')?.addEventListener('click', loadBanking);
  document.querySelector('[data-view="lab10-reliability-inventory"]')?.addEventListener('click', loadReliabilityInventory);
  document.getElementById('financial-filters')?.addEventListener('submit', event => {
    event.preventDefault(); loadFinancialActivity(1);
  });
  document.getElementById('financial-filter-reset')?.addEventListener('click', () => {
    document.getElementById('financial-filters').reset(); loadFinancialActivity(1);
  });
  document.getElementById('financial-detail-close')?.addEventListener('click', () => {
    document.getElementById('financial-detail-dialog').close();
  });
  if (new URLSearchParams(location.search).get('view') === 'lab10-banking') {
    document.querySelector('[data-view="lab10-banking"]')?.click();
    if (location.hash === '#financial-activity') {
      setTimeout(() => document.getElementById('financial-activity')?.scrollIntoView({ behavior: 'smooth' }), 100);
    }
  }
});
