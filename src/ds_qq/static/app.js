const $ = (id) => document.getElementById(id);
const integer = new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 0 });
const money = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', minimumFractionDigits: 4, maximumFractionDigits: 6 });
const text = (id, value) => { $(id).textContent = value; };
const escapeHtml = (value) => String(value).replace(/[&<>'"]/g, (char) => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[char]));

function distribution(id, rows, key) {
  if (!rows.length) { $(id).innerHTML = '<p class="empty">暂无 API 调用</p>'; return; }
  const max = Math.max(...rows.map((row) => row.tokens), 1);
  $(id).innerHTML = rows.map((row) => `<div class="dist-row"><span>${escapeHtml(row[key])}</span><div class="dist-track"><div class="dist-fill" style="width:${row.tokens / max * 100}%"></div></div><span class="dist-value">${integer.format(row.tokens)}</span></div>`).join('');
}

function chart(rows) {
  if (!rows.length) { $('chart').innerHTML = '<p class="empty">暂无趋势数据</p>'; return; }
  const max = Math.max(...rows.map((row) => row.tokens), 1);
  $('chart').innerHTML = rows.map((row) => `<div class="bar-wrap" title="${row.date} · ${integer.format(row.tokens)} tokens · ${money.format(row.cost)}"><div class="bar" style="height:${Math.max(row.tokens / max * 100, 1)}%"></div><span class="bar-label">${row.date.slice(5)}</span></div>`).join('');
}

function recent(rows) {
  if (!rows.length) { $('recent').innerHTML = '<tr><td colspan="9" class="empty">暂无调用，@机器人后刷新即可看到数据</td></tr>'; return; }
  $('recent').innerHTML = rows.map((row) => {
    const date = new Date(row.created_at * 1000).toLocaleString('zh-CN', { hour12: false });
    const error = row.error ? ` title="${escapeHtml(row.error)}"` : '';
    const purpose = row.purpose === 'memory' ? '记忆整理' : '群聊';
    return `<tr${error}><td>${date}</td><td>${row.group_id}</td><td>${escapeHtml(row.model)}</td><td>${purpose}</td><td class="status ${row.status === 'success' ? '' : 'error-status'}">${row.status === 'success' ? '成功' : '失败'}</td><td>${integer.format(row.prompt_tokens)} / ${integer.format(row.completion_tokens)}</td><td>${integer.format(row.cache_hit_tokens)}</td><td>${integer.format(row.latency_ms)} ms</td><td>${money.format(row.estimated_cost_usd)}</td></tr>`;
  }).join('');
}

async function refresh() {
  try {
    const response = await fetch(`/api/overview?days=${$('days').value}`, { cache: 'no-store' });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    text('today-requests', integer.format(data.today.requests));
    text('today-success', `成功率 ${data.today.success_rate.toFixed(1)}%`);
    text('week-tokens', integer.format(data.week.total_tokens));
    text('week-split', `输入 ${integer.format(data.week.prompt_tokens)} · 输出 ${integer.format(data.week.completion_tokens)}`);
    text('week-cache', `${data.week.cache_rate.toFixed(1)}%`);
    text('all-cost', money.format(data.all.cost));
    text('all-requests', `共 ${integer.format(data.all.requests)} 次请求`);
    text('average-latency', `近 7 日平均延迟 ${integer.format(data.week.average_latency_ms)} ms`);
    chart(data.daily);
    distribution('groups', data.groups, 'group_id');
    distribution('models', data.models, 'model');
    recent(data.recent);
    text('updated-at', `更新于 ${new Date().toLocaleTimeString('zh-CN', { hour12: false })}`);
    text('health', '运行正常');
    $('health').classList.remove('offline');
    $('error').hidden = true;
  } catch (error) {
    text('health', '连接失败');
    $('health').classList.add('offline');
    $('error').textContent = `无法读取用量数据：${error.message}`;
    $('error').hidden = false;
  }
}

$('days').addEventListener('change', refresh);
refresh();
setInterval(refresh, 15000);
