/**
 * verify_screen.js —— 用 Chrome DevTools Protocol 检查大屏真实渲染结果
 *
 * 检查项：
 *   1. 每个图表容器是否拿到了非零尺寸（尺寸为 0 说明 flex 布局塌了）
 *   2. ECharts 实例是否存在、series 是否真的渲染出了数据
 *   3. 页面上是否有 JS 报错或失败请求
 *   4. 缩放后画布是否清晰（devicePixelRatio 与 canvas 实际像素）
 */
const CDP_URL = process.env.CDP_URL || 'http://127.0.0.1:9222';
const TARGET = process.env.TARGET_URL || 'http://127.0.0.1:8000/';

const CHART_IDS = [
  'chartTrend', 'chartCategory', 'chartMap', 'chartHeat',
  'chartPay', 'chartScatter', 'chartStack', 'chartChannel',
];

async function main() {
  // ---- 1. 新建标签页 ----
  const created = await fetch(`${CDP_URL}/json/new?${encodeURIComponent(TARGET)}`,
                              { method: 'PUT' });
  const tab = await created.json();
  const ws = new WebSocket(tab.webSocketDebuggerUrl);

  const errors = [];
  const failedRequests = [];
  let msgId = 0;
  const pending = new Map();

  const send = (method, params = {}) => new Promise((resolve, reject) => {
    const id = ++msgId;
    pending.set(id, { resolve, reject });
    ws.send(JSON.stringify({ id, method, params }));
  });

  await new Promise((res, rej) => {
    ws.onopen = res;
    ws.onerror = rej;
  });

  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.id && pending.has(msg.id)) {
      const { resolve, reject } = pending.get(msg.id);
      pending.delete(msg.id);
      msg.error ? reject(new Error(JSON.stringify(msg.error))) : resolve(msg.result);
      return;
    }
    if (msg.method === 'Runtime.exceptionThrown') {
      const d = msg.params.exceptionDetails;
      errors.push(d.exception?.description || d.text);
    }
    if (msg.method === 'Runtime.consoleAPICalled' && msg.params.type === 'error') {
      errors.push(msg.params.args.map(a => a.value || a.description).join(' '));
    }
    if (msg.method === 'Network.loadingFailed') {
      failedRequests.push(msg.params);
    }
  };

  await send('Runtime.enable');
  await send('Network.enable');
  await send('Page.enable');

  // 重新加载，确保事件监听生效
  await send('Page.reload', { ignoreCache: true });
  await new Promise(r => setTimeout(r, 9000));   // 等图表动画与接口返回

  // ---- 2. 采集运行时状态 ----
  const expr = `(() => {
    const ids = ${JSON.stringify(CHART_IDS)};
    const out = {};
    for (const id of ids) {
      const el = document.getElementById(id);
      if (!el) { out[id] = { missing: true }; continue; }
      const rect = el.getBoundingClientRect();
      const canvas = el.querySelector('canvas');
      // ECharts 把实例挂在 DOM 的 _echarts_instance_ 属性上
      const inst = window.echarts && window.echarts.getInstanceByDom(el);
      let seriesInfo = null;
      if (inst) {
        const opt = inst.getOption();
        seriesInfo = (opt.series || []).map(s => ({
          type: s.type,
          dataLen: Array.isArray(s.data) ? s.data.length : null,
        }));
      }
      out[id] = {
        w: Math.round(rect.width),
        h: Math.round(rect.height),
        hasCanvas: !!canvas,
        canvasW: canvas ? canvas.width : 0,
        canvasH: canvas ? canvas.height : 0,
        hasInstance: !!inst,
        series: seriesInfo,
        panelClass: el.closest('.panel')?.className || '',
      };
    }
    // KPI 文本
    const kpi = ['kpiGmv', 'kpiOrder', 'kpiAov', 'kpiBuyer']
      .reduce((o, k) => (o[k] = document.getElementById(k)?.textContent, o), {});
    // 门店表格行数
    const rows = document.querySelectorAll('#tableStore tbody tr').length;
    // 屏幕缩放
    const screenEl = document.getElementById('screen');
    return {
      charts: out,
      kpi,
      storeRows: rows,
      transform: screenEl?.style.transform || '',
      dpr: window.devicePixelRatio,
      innerW: window.innerWidth,
      innerH: window.innerHeight,
    };
  })()`;

  const { result } = await send('Runtime.evaluate', {
    expression: expr, returnByValue: true, awaitPromise: false,
  });

  const data = result.value;
  await send('Page.close');
  ws.close();

  // ---- 3. 输出判定 ----
  console.log('=== 视口 ===');
  console.log(`  ${data.innerW} × ${data.innerH}  dpr=${data.dpr}`);
  console.log(`  缩放: ${data.transform}`);

  console.log('\n=== KPI ===');
  Object.entries(data.kpi).forEach(([k, v]) => console.log(`  ${k}: ${v}`));

  console.log(`\n=== 门店表格：${data.storeRows} 行 ===`);

  console.log('\n=== 图表 ===');
  let allOk = true;
  for (const id of CHART_IDS) {
    const c = data.charts[id];
    if (c.missing) { console.log(`  ✗ ${id}  元素不存在`); allOk = false; continue; }
    const sizeOk = c.w > 50 && c.h > 50;
    const instOk = c.hasInstance;
    const dataOk = (c.series || []).every(s => s.dataLen === null || s.dataLen > 0);
    const ok = sizeOk && instOk && dataOk && c.hasCanvas;
    if (!ok) allOk = false;
    console.log(
      `  ${ok ? '✓' : '✗'} ${id.padEnd(14)} ${String(c.w).padStart(4)}×${String(c.h).padStart(3)}` +
      `  canvas ${c.canvasW}×${c.canvasH}` +
      `  实例=${instOk ? 'Y' : 'N'}` +
      `  series=${JSON.stringify(c.series)}`
    );
  }

  if (errors.length) {
    console.log('\n=== JS 错误 ===');
    errors.forEach(e => console.log('  ✗ ' + String(e).split('\n')[0]));
    allOk = false;
  } else {
    console.log('\n=== JS 错误：无 ===');
  }

  if (failedRequests.length) {
    console.log('\n=== 失败请求 ===');
    failedRequests.forEach(r => console.log(`  ✗ ${r.errorText} ${r.type}`));
    allOk = false;
  } else {
    console.log('=== 失败请求：无 ===');
  }

  console.log(`\n结论：${allOk ? '全部通过' : '存在异常'}`);
  process.exit(allOk ? 0 : 1);
}

main().catch(e => { console.error('验证脚本本身出错：', e); process.exit(2); });
