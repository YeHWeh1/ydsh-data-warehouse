/**
 * verify_filters.js —— 验证 Superset 仪表板筛选器是否真的出现在页面上
 *
 * 为什么单独验证：
 *   native_filter_configuration 写进 json_metadata 只是「数据存在」，
 *   前端会不会渲染出筛选器控件、能不能联动，必须真看页面。
 */
const CDP_URL = 'http://127.0.0.1:9222';
const BASE = 'http://127.0.0.1:8088';

async function newTab(url) {
  const r = await fetch(`${CDP_URL}/json/new?${encodeURIComponent(url)}`, { method: 'PUT' });
  return r.json();
}

function connect(tab) {
  const ws = new WebSocket(tab.webSocketDebuggerUrl);
  let id = 0;
  const pending = new Map();
  const send = (m, p = {}) => new Promise((res, rej) => {
    const i = ++id;
    pending.set(i, { res, rej });
    ws.send(JSON.stringify({ id: i, method: m, params: p }));
  });
  ws.onmessage = (ev) => {
    const m = JSON.parse(ev.data);
    if (m.id && pending.has(m.id)) {
      const { res, rej } = pending.get(m.id);
      pending.delete(m.id);
      m.error ? rej(new Error(JSON.stringify(m.error))) : res(m.result);
    }
  };
  return { ws, send, ready: new Promise((r, j) => { ws.onopen = r; ws.onerror = j; }) };
}

async function main() {
  const user = process.env.SUPERSET_ADMIN_USER || 'admin';
  const pass = process.env.SUPERSET_ADMIN_PASSWORD || 'admin';

  // 登录
  let t = await newTab(`${BASE}/login/`);
  let c = connect(t);
  await c.ready;
  await c.send('Runtime.enable');
  await c.send('Page.enable');
  await new Promise(r => setTimeout(r, 2500));
  await c.send('Runtime.evaluate', {
    expression: `(() => {
      document.querySelector('input[name=username]').value = ${JSON.stringify(user)};
      document.querySelector('input[name=password]').value = ${JSON.stringify(pass)};
      document.querySelector('form').submit();
    })()`,
  });
  await new Promise(r => setTimeout(r, 5500));
  await c.send('Page.close');
  c.ws.close();

  // 打开仪表板
  t = await newTab(`${BASE}/superset/dashboard/1/`);
  c = connect(t);
  await c.ready;
  await c.send('Runtime.enable');
  await c.send('Page.enable');
  await c.send('Emulation.setDeviceMetricsOverride',
               { width: 1920, height: 1400, deviceScaleFactor: 1, mobile: false });
  await new Promise(r => setTimeout(r, 24000));

  const expr = `(function () {
    var txt = document.body.innerText || '';
    // 筛选栏容器（不同版本 class 略有差异，用属性选择器兜底）
    var bars = document.querySelectorAll(
      '[data-test="dashboard-filters-bar"], .dashboard-filters-bar, ' +
      '.filter-bar, [class*="FilterBar"]'
    );
    // 筛选器控件：下拉 / 时间选择
    var selects = document.querySelectorAll(
      '.filter-select, [data-test="filter-select"], .ant-select, ' +
      '[class*="FilterControl"], [class*="filter-control"]'
    );
    var holders = document.querySelectorAll(
      '.dashboard-component-chart-holder, [data-test="chart-container"], .chart-container'
    );
    var errs = (txt.match(/意外错误/g) || []).length;

    return JSON.stringify({
      hasDateFilter: txt.indexOf('日期范围') >= 0,
      hasCategoryFilter: txt.indexOf('品类') >= 0,
      filterBarCount: bars.length,
      selectCount: selects.length,
      holders: holders.length,
      errors: errs,
      snippet: txt.slice(0, 260).replace(/\\n+/g, ' | '),
    });
  })()`;

  const { result } = await c.send('Runtime.evaluate', {
    expression: expr, returnByValue: true,
  });
  const d = JSON.parse(result.value);
  await c.send('Page.close');
  c.ws.close();

  console.log('=== Superset 筛选器验证 ===');
  console.log(`  出现「日期范围」：${d.hasDateFilter ? '是' : '否'}`);
  console.log(`  出现「品类」：${d.hasCategoryFilter ? '是' : '否'}`);
  console.log(`  筛选栏容器：${d.filterBarCount} 个`);
  console.log(`  筛选控件：${d.selectCount} 个`);
  console.log(`  图表容器：${d.holders} 个`);
  console.log(`  页面报错：${d.errors} 处`);
  if (!d.hasDateFilter && !d.hasCategoryFilter) {
    console.log(`  页面文本：${d.snippet}`);
  }

  const ok = d.hasDateFilter && d.hasCategoryFilter && d.errors === 0;
  console.log(`\n结论：${ok ? '筛选器已正常显示' : '筛选器未显示'}`);
  process.exit(ok ? 0 : 1);
}

main().catch(e => { console.error('脚本出错：', e); process.exit(2); });
