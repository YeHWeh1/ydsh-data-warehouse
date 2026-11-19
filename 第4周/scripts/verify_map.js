/**
 * verify_map.js —— 验证大屏地图的省份数据是否真的匹配上了
 *
 * 为什么单独验证：
 *   verify_screen.js 只检查「图表实例存在、series 有数据」，
 *   但地图的 series.data 里就算有 15 条记录，只要 name 和 geoJSON 对不上，
 *   悬浮框照样全显示「暂无数据」、地图也不着色。
 *   这是之前漏掉的问题，所以这里直接比对名称并模拟 tooltip。
 */
const CDP_URL = 'http://127.0.0.1:9222';
const TARGET = 'http://127.0.0.1:8000/';

async function main() {
  const created = await fetch(`${CDP_URL}/json/new?${encodeURIComponent(TARGET)}`,
                              { method: 'PUT' });
  const tab = await created.json();
  const ws = new WebSocket(tab.webSocketDebuggerUrl);

  let id = 0;
  const pending = new Map();
  const send = (m, p = {}) => new Promise((res, rej) => {
    const i = ++id;
    pending.set(i, { res, rej });
    ws.send(JSON.stringify({ id: i, method: m, params: p }));
  });

  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
  ws.onmessage = (ev) => {
    const m = JSON.parse(ev.data);
    if (m.id && pending.has(m.id)) {
      const { res, rej } = pending.get(m.id);
      pending.delete(m.id);
      m.error ? rej(new Error(JSON.stringify(m.error))) : res(m.result);
    }
  };

  await send('Runtime.enable');
  await send('Page.enable');
  await send('Page.reload', { ignoreCache: true });
  await new Promise(r => setTimeout(r, 11000));

  const expr = `(() => {
    const el = document.getElementById('chartMap');
    if (!el || !window.echarts) return JSON.stringify({ error: '图表未初始化' });
    const inst = window.echarts.getInstanceByDom(el);
    if (!inst) return JSON.stringify({ error: '无 echarts 实例' });

    const opt = inst.getOption();
    const series = (opt.series || [])[0] || {};
    const data = series.data || [];

    // geoJSON 里的官方名字（从已注册的地图数据取）
    let geoNames = [];
    try {
      const geo = echarts.getMap('china');
      geoNames = (geo.geoJson.features || [])
        .map(f => f.properties && f.properties.name)
        .filter(Boolean);
    } catch (e) { /* ignore */ }

    const geoSet = new Set(geoNames);
    const matched = data.filter(d => geoSet.has(d.name));
    const unmatched = data.filter(d => !geoSet.has(d.name));

    // 模拟 tooltip：用图表的 formatter 对第一个有数据的省跑一遍。
    // formatter 可能在 series.tooltip 或顶层 tooltip 上，两处都找。
    let sample = null;
    const s0 = data.find(d => geoSet.has(d.name));
    if (s0) {
      const fmtr = (series.tooltip && series.tooltip.formatter)
                || (opt.tooltip && opt.tooltip[0] && opt.tooltip[0].formatter)
                || (opt.tooltip && opt.tooltip.formatter);
      try {
        sample = typeof fmtr === 'function'
          ? String(fmtr({ name: s0.name, value: s0.value, data: s0 }))
          : (fmtr ? 'formatter 不是函数' : null);
      } catch (e) { sample = 'formatter 报错: ' + e.message; }
    }

    return JSON.stringify({
      dataCount: data.length,
      geoCount: geoNames.length,
      matchedCount: matched.length,
      unmatchedCount: unmatched.length,
      unmatchedNames: unmatched.map(d => d.name).slice(0, 10),
      matchedNames: matched.map(d => d.name).slice(0, 20),
      sampleTooltip: sample,
      visualMapMin: (opt.visualMap || [{}])[0].min,
      visualMapMax: (opt.visualMap || [{}])[0].max,
    });
  })()`;

  const { result } = await send('Runtime.evaluate', {
    expression: expr, returnByValue: true,
  });
  const d = JSON.parse(result.value);
  await send('Page.close');
  ws.close();

  if (d.error) {
    console.log('✗ ' + d.error);
    process.exit(1);
  }

  console.log('=== 大屏地图数据匹配验证 ===');
  console.log(`  地图 series 数据条数：${d.dataCount}`);
  console.log(`  geoJSON 区域数：${d.geoCount}`);
  console.log(`  名称匹配成功：${d.matchedCount} 个`);
  console.log(`  名称匹配失败：${d.unmatchedCount} 个`);
  if (d.unmatchedCount) console.log(`    未匹配：${d.unmatchedNames.join(', ')}`);
  console.log(`  匹配到的省份：${d.matchedNames.join(', ')}`);
  console.log(`  visualMap 区间：${d.visualMapMin} ~ ${d.visualMapMax}`);
  console.log(`  悬浮框示例：${(d.sampleTooltip || '(取不到)').replace(/<br\/?>/g, ' / ')}`);

  const ok = d.matchedCount === d.dataCount && d.dataCount > 0
             && d.sampleTooltip && !/暂无数据/.test(d.sampleTooltip);
  console.log(`\n结论：${ok ? '地图数据全部匹配，悬浮框有真实数值' : '地图数据未正确匹配'}`);
  process.exit(ok ? 0 : 1);
}

main().catch(e => { console.error('验证脚本出错：', e); process.exit(2); });
