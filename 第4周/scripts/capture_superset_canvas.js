/**
 * capture_superset_canvas.js —— 把 Superset 仪表板上的 canvas 内容导出并拼图
 *
 * 背景：
 *   Superset 的图表用 canvas 渲染，headless / 普通 Chrome 的
 *   Page.captureScreenshot 都截不到这些图层（截出来是纯白页）。
 *   但 canvas 内部的像素是好的（可用 getImageData 读到）。
 *
 * 做法：
 *   1. 登录 + 打开仪表板
 *   2. 逐个读取每个图表容器的位置与 canvas 内容
 *   3. 把 canvas.toDataURL() 导出的 PNG 传回 node
 *   4. 用 canvas 合成：先铺页面底色，再按坐标贴每个图表
 *   5. 输出整张仪表板图片
 *
 * 局限（已在文档注明）：
 *   只还原图表本身的像素，表格/文字用 DOM 截图补不上；
 *   所以最终图里图表区是真实的，其余区域是简化底色。
 */
const fs = require('fs');

const CDP_URL = 'http://127.0.0.1:9222';
const BASE = 'http://127.0.0.1:8088';
const OUT = process.env.OUT || 'E:/dsh project/_temp/superset_composed.png';

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

  // ---- 登录 ----
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
  await new Promise(r => setTimeout(r, 6000));
  await c.send('Page.close');
  c.ws.close();

  // ---- 打开仪表板 ----
  t = await newTab(`${BASE}/superset/dashboard/1/`);
  c = connect(t);
  await c.ready;
  await c.send('Runtime.enable');
  await c.send('Page.enable');
  await c.send('Emulation.setDeviceMetricsOverride',
               { width: 1920, height: 1400, deviceScaleFactor: 1, mobile: false });
  await new Promise(r => setTimeout(r, 25000));

  // ---- 采集每个图表的 canvas 与位置 ----
  const expr = `(function () {
    var holders = document.querySelectorAll(
      '.dashboard-component-chart-holder, [data-test="chart-container"], .chart-container');
    var out = [];
    for (var i = 0; i < holders.length; i++) {
      var h = holders[i];
      var rect = h.getBoundingClientRect();
      var cv = h.querySelector('canvas');
      var item = {
        idx: i + 1,
        x: Math.round(rect.x), y: Math.round(rect.y),
        w: Math.round(rect.width), h: Math.round(rect.height),
        title: (h.innerText || '').split('\\n')[0].slice(0, 40),
        dataUrl: null,
        hasTable: !!h.querySelector('table'),
        tableText: h.querySelector('table') ? (h.querySelector('table').innerText || '').slice(0, 500) : null,
      };
      if (cv && cv.width > 0) {
        try { item.dataUrl = cv.toDataURL('image/png'); } catch (e) { item.err = e.message; }
      }
      out.push(item);
    }
    return JSON.stringify({ total: out.length, items: out,
      bodyW: document.body.scrollWidth, bodyH: document.body.scrollHeight });
  })()`;

  const { result } = await c.send('Runtime.evaluate', {
    expression: expr, returnByValue: true,
  });
  const data = JSON.parse(result.value);
  await c.send('Page.close');
  c.ws.close();

  console.log(`采集到 ${data.total} 个图表容器，页面 ${data.bodyW}×${data.bodyH}`);
  const withCanvas = data.items.filter(x => x.dataUrl).length;
  const withTable = data.items.filter(x => x.hasTable).length;
  console.log(`  有 canvas：${withCanvas} 个；有表格：${withTable} 个`);

  // ---- 用 node 拼图 ----
  // 没有 canvas 库，改用纯 SVG + HTML 的方式：生成一个 HTML，
  // 里面用 <img> 贴 canvas 导出的图 + 表格文字，再让浏览器截图。
  const W = Math.min(1920, Math.max(1600, data.bodyW));
  const H = Math.min(1400, Math.max(900, data.bodyH));
  const cells = data.items.map(it => {
    const inner = it.dataUrl
      ? `<img src="${it.dataUrl}" style="width:100%;height:100%;object-fit:contain">`
      : (it.hasTable
          ? `<pre style="font-size:11px;margin:0;white-space:pre-wrap;overflow:hidden;height:100%">${
              (it.tableText || '').replace(/[<>&]/g, ch => ({'<':'&lt;','>':'&gt;','&':'&amp;'}[ch]))}</pre>`
          : '<div style="color:#999;font-size:12px;padding:8px">（无 canvas）</div>');
    return `<div style="position:absolute;left:${it.x}px;top:${it.y}px;width:${it.w}px;height:${it.h}px;
      border:1px solid #d0d7de;background:#fff;box-sizing:border-box;overflow:hidden">
      <div style="font-size:11px;color:#57606a;padding:2px 6px;border-bottom:1px solid #eaeef2;background:#f6f8fa">
        ${it.idx}. ${it.title.replace(/[<>&]/g, '')}</div>
      <div style="height:calc(100% - 20px)">${inner}</div>
    </div>`;
  }).join('\n');

  const html = `<!DOCTYPE html><html><head><meta charset="utf-8">
  <style>body{margin:0;width:${W}px;height:${H}px;background:#f6f8fa;position:relative;
  font-family:"Microsoft YaHei",sans-serif}</style></head>
  <body>
    <div style="padding:10px 14px;font-size:16px;font-weight:600;color:#24292f">
      云购生活 · 经营分析仪表板（图表由 canvas 像素还原）</div>
    ${cells}
  </body></html>`;

  const htmlPath = 'E:/dsh project/_temp/composed_dash.html';
  fs.writeFileSync(htmlPath, html, 'utf8');
  console.log('合成页面已写入：', htmlPath);

  // ---- 截图合成页 ----
  const t2 = await newTab('file:///' + htmlPath.replace(/\\/g, '/'));
  const c2 = connect(t2);
  await c2.ready;
  await c2.send('Runtime.enable');
  await c2.send('Page.enable');
  await c2.send('Emulation.setDeviceMetricsOverride',
                { width: W, height: H, deviceScaleFactor: 2, mobile: false });
  await new Promise(r => setTimeout(r, 3000));
  const shot = await c2.send('Page.captureScreenshot',
                             { format: 'png', captureBeyondViewport: true });
  fs.writeFileSync(OUT, Buffer.from(shot.data, 'base64'));
  await c2.send('Page.close');
  c2.ws.close();

  console.log('合成图已保存：', OUT, fs.statSync(OUT).size, '字节');
}

main().catch(e => { console.error('脚本出错：', e); process.exit(2); });
