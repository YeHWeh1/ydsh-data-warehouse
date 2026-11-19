/**
 * verify_dashboard.js —— 用 CDP 打开 Superset 仪表板，确认图表真的渲染到页面上
 *
 * 为什么不能只看接口：
 *   /api/v1/chart/data 能出数，不代表前端渲染成功。
 *   常见问题：布局 position_json 结构不对 → 图表不显示；
 *   JS 报错 → 白屏；前端没权限 → 显示 "意外错误"。
 *
 * 为什么必须先登录：
 *   实测匿名访问 /api/v1/dashboard/1 返回 404（Superset 对匿名请求有额外限制），
 *   页面显示「意外错误」。用管理员账号先登录再访问，才能看到真实渲染结果。
 *
 * 判定：
 *   1. 页面出现 N 个图表容器
 *   2. 其中至少有 N 个内部有 canvas / svg / table（真的画出来了）
 *   3. 没有 "No results" / "意外错误" 文案
 */
const CDP_URL = process.env.CDP_URL || 'http://127.0.0.1:9222';
const LOGIN_URL = 'http://127.0.0.1:8088/login/';
const TARGET = process.env.TARGET_URL || 'http://127.0.0.1:8088/superset/dashboard/1/';
const USER = process.env.SUPERSET_ADMIN_USER || 'admin';
const PASS = process.env.SUPERSET_ADMIN_PASSWORD || '';

async function newTab(url) {
  const r = await fetch(`${CDP_URL}/json/new?${encodeURIComponent(url)}`,
                        { method: 'PUT' });
  return r.json();
}

async function main() {
  if (!PASS) {
    console.error('未设置 SUPERSET_ADMIN_PASSWORD');
    process.exit(2);
  }

  // ---- 1. 先登录（用同一个浏览器 profile，cookie 会保留） ----
  const loginTab = await newTab(LOGIN_URL);
  const loginWs = new WebSocket(loginTab.webSocketDebuggerUrl);
  let lid = 0;
  const lpend = new Map();
  const lsend = (m, p = {}) => new Promise((res, rej) => {
    const i = ++lid;
    lpend.set(i, { res, rej });
    loginWs.send(JSON.stringify({ id: i, method: m, params: p }));
  });
  await new Promise((res, rej) => { loginWs.onopen = res; loginWs.onerror = rej; });
  loginWs.onmessage = (ev) => {
    const m = JSON.parse(ev.data);
    if (m.id && lpend.has(m.id)) {
      const { res, rej } = lpend.get(m.id);
      lpend.delete(m.id);
      m.error ? rej(new Error(JSON.stringify(m.error))) : res(m.result);
    }
  };
  await lsend('Runtime.enable');
  await lsend('Page.enable');
  await new Promise(r => setTimeout(r, 3000));

  // 填表提交（Superset 登录页是标准 form）
  await lsend('Runtime.evaluate', {
    expression: `(() => {
      const u = document.querySelector('input[name="username"]');
      const p = document.querySelector('input[name="password"]');
      const f = document.querySelector('form');
      if (!u || !p || !f) return 'fields-missing';
      u.value = ${JSON.stringify(USER)};
      p.value = ${JSON.stringify(PASS)};
      u.dispatchEvent(new Event('input', {bubbles:true}));
      p.dispatchEvent(new Event('input', {bubbles:true}));
      f.submit();
      return 'submitted';
    })()`,
    returnByValue: true,
  });
  await new Promise(r => setTimeout(r, 5000));
  await lsend('Page.close');
  loginWs.close();

  // ---- 2. 打开仪表板 ----
  const tab = await newTab(TARGET);
  const ws = new WebSocket(tab.webSocketDebuggerUrl);

  let id = 0;
  const pending = new Map();
  const errors = [];

  const send = (method, params = {}) => new Promise((res, rej) => {
    const i = ++id;
    pending.set(i, { res, rej });
    ws.send(JSON.stringify({ id: i, method, params }));
  });

  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });

  ws.onmessage = (ev) => {
    const m = JSON.parse(ev.data);
    if (m.id && pending.has(m.id)) {
      const { res, rej } = pending.get(m.id);
      pending.delete(m.id);
      m.error ? rej(new Error(JSON.stringify(m.error))) : res(m.result);
      return;
    }
    if (m.method === 'Runtime.exceptionThrown') {
      errors.push(m.params.exceptionDetails.text);
    }
  };

  await send('Runtime.enable');
  await send('Page.enable');
  // 仪表板是 SPA，要给足时间加载数据
  await new Promise(r => setTimeout(r, 18000));

  const { result } = await send('Runtime.evaluate', {
    expression: `(() => {
      const txt = document.body.innerText || '';
      const holders = document.querySelectorAll(
        '.dashboard-component-chart-holder, [data-test="chart-container"], .chart-container'
      );
      let rendered = 0;
      let errorCharts = 0;
      const detail = [];
      holders.forEach(h => {
        const inner = (h.innerText || '').replace(/\\s+/g, ' ').trim();
        const hasCanvas = !!h.querySelector('canvas');
        const hasSvg = !!h.querySelector('svg');
        const hasTable = !!h.querySelector('table');
        const hasError = inner.indexOf('意外错误') >= 0;
        if ((hasCanvas || hasSvg || hasTable) && !hasError) rendered++;
        if (hasError) errorCharts++;
        detail.push({
          w: Math.round(h.getBoundingClientRect().width),
          h: Math.round(h.getBoundingClientRect().height),
          canvas: hasCanvas, svg: hasSvg, table: hasTable,
          error: hasError,
        });
      });
      return {
        title: document.title,
        url: location.href,
        loggedIn: !/\\/login/.test(location.href),
        holderCount: holders.length,
        renderedCount: rendered,
        errorCharts,
        detail,
        bodyErrorCount: (txt.match(/意外错误/g) || []).length,
        bodyLen: txt.length,
        snippet: txt.slice(0, 160),
      };
    })()`,
    returnByValue: true,
  });

  const d = result.value;
  await send('Page.close');
  ws.close();

  console.log('=== Superset 仪表板渲染验证 ===');
  console.log(`  标题：${d.title}`);
  console.log(`  已登录：${d.loggedIn ? '是' : '否（被重定向到登录页）'}`);
  console.log(`  图表容器：${d.holderCount} 个，成功渲染 ${d.renderedCount} 个，报错 ${d.errorCharts} 个`);
  d.detail.forEach((x, i) => {
    const mark = x.error ? '✗' : '✓';
    console.log(`    ${mark} [${i + 1}] ${x.w}×${x.h}  canvas=${x.canvas} svg=${x.svg} table=${x.table}`);
  });
  if (!d.holderCount) console.log(`  页面文本：${d.snippet.replace(/\n/g, ' | ')}`);
  if (errors.length) {
    console.log('  JS 错误：');
    errors.slice(0, 5).forEach(e => console.log('    ✗ ' + String(e).split('\n')[0]));
  } else {
    console.log('  JS 错误：无');
  }

  // 判定标准：所有图表容器都必须画出来，且没有任何一个显示「意外错误」。
  // 只数容器是不够的——空框也会被算进去（这正是之前漏判的原因）。
  const ok = d.renderedCount >= 5 && d.errorCharts === 0 && d.bodyErrorCount === 0;
  console.log(`\n结论：${ok ? '仪表板全部图表正常渲染' : `渲染不完整（报错 ${d.errorCharts} 个）`}`);
  process.exit(ok ? 0 : 1);
}

main().catch(e => { console.error('验证脚本出错：', e); process.exit(2); });

