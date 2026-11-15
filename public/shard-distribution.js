document.addEventListener('DOMContentLoaded', () => {
  const box = document.getElementById('demo-shard-results');
  if (!box) return;
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const names = {
    transactions_by_order_id: ['Order ID', 'order_id'],
    transactions_by_customer_id: ['Customer ID range', 'customer_id'],
    transactions_by_order_date: ['Order date range', 'order_date'],
    transactions_by_hashed_customer_id: ['Hashed customer ID', 'hashed customer_id'],
  };
  const humanBytes = n => { let v = Number(n || 0); const units = ['B', 'KB', 'MB', 'GB']; let i = 0; while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; } return `${v.toFixed(i ? 1 : 0)} ${units[i]}`; };
  const humanDate = value => { const d = new Date(value); return Number.isNaN(d.getTime()) ? 'Time unavailable' : `${d.toLocaleString()} (local time)`; };
  async function refresh() {
    box.innerHTML = '<div class="loading-state">Reading actual sharding metadata and explain plans...</div>';
    try {
      const response = await fetch('/api/chapter11/demo-transactions', { credentials: 'same-origin' });
      const data = await response.json();
      document.getElementById('demo-shard-captured').textContent = data.available
        ? `MongoDB ${data.mongodb_version} · ${data.shards.length} shards · measured ${humanDate(data.captured_at_utc)}${data.ready ? ' · all four collections ready' : ' · setup or data incomplete'}`
        : data.message || 'Sharding cluster unavailable.';
      if (!data.available) throw new Error(data.message || 'Sharding cluster unavailable.');
      box.innerHTML = Object.entries(names).map(([name, [label, key]]) => {
        const item = data.collections?.[name] || {};
        if (!item.ready) return `<article class="demo-shard-card"><h3>${esc(label)}</h3><p>Shard key: <code>${esc(key)}</code></p><p>Not yet measured. Run the comparison script shown above.</p></article>`;
        const shards = (item.per_shard || []).map(row => `<div class="demo-shard-row"><span>${esc(row.shard)}</span><strong>${Number(row.owned_documents || 0).toLocaleString()} docs</strong><small>${humanBytes(row.storage_size_bytes)} storage</small></div>`).join('');
        const queries = Object.entries(item.queries || {}).map(([query, result]) => `<div class="demo-query"><strong>${esc(query.replaceAll('_', ' '))}</strong><span>${result.targeted_shards?.length ? esc(result.targeted_shards.join(', ')) : 'Target unavailable'}</span><small>${result.scatter_gather === null ? 'Scatter/gather unknown' : result.scatter_gather ? 'Scatter/gather' : 'Targeted'} · ${Number(result.n_returned || 0)} returned · ${Number(result.docs_examined || 0)} examined · ${result.execution_millis ?? '—'} ms</small></div>`).join('');
        return `<article class="demo-shard-card"><div class="demo-shard-card-heading"><h3>${esc(label)}</h3><span class="rel-pill ${item.total_documents === 1400 ? 'ok' : 'warn'}">${Number(item.total_documents || 0).toLocaleString()} / 1,400</span></div><p>Shard key: <code>${esc(JSON.stringify(item.shard_key || key))}</code></p><div class="demo-shard-rows">${shards || '<span>Shard statistics unavailable</span>'}</div><h4>Live query plans</h4>${queries}</article>`;
      }).join('');
    } catch (error) { box.innerHTML = `<div class="empty-prompt">${esc(error.message || 'Live measurements are temporarily unavailable.')}</div>`; }
  }
  document.getElementById('demo-shard-refresh')?.addEventListener('click', refresh);
  refresh();
});
