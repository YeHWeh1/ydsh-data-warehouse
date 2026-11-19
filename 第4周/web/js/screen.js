/* ============================================================
   screen.js —— 大屏主控：自适应缩放、取数、渲染、定时刷新

   三件事：
     1. fit()     把 1920×1080 的设计稿整体缩放到当前窗口（手册步骤 3 的 scale 方案）
     2. boot()    并发取全部接口，逐个渲染，单个失败不影响其它
     3. 定时器    每 60s 刷新一次（大屏长时间挂着的场景）
   ============================================================ */

const DESIGN_W = 1920;
const DESIGN_H = 1080;
const REFRESH_MS = 60 * 1000;

/* 图表实例表：key → echarts 实例，便于 resize 与重绘 */
const instances = {};

/* ============================================================
   一、自适应缩放
   ============================================================ */
function fit() {
  const stage = document.getElementById('stage');
  const screen = document.getElementById('screen');
  if (!stage || !screen) return;

  const scaleX = stage.clientWidth / DESIGN_W;
  const scaleY = stage.clientHeight / DESIGN_H;
  // 取较小值：保证内容完整可见（宁可留边，也不要裁切）
  const scale = Math.min(scaleX, scaleY);

  screen.style.transform = `translate(-50%, -50%) scale(${scale})`;
  screen.style.left = '50%';
  screen.style.top = '50%';
  screen.style.transformOrigin = 'center center';

  // 缩放后需要让每个图表按新尺寸重算，否则 canvas 内部分辨率会模糊
  Object.values(instances).forEach(c => c && c.resize());
}

/* 防抖：窗口拖动时不要每像素都重算 */
let fitTimer = null;
function onResize() {
  clearTimeout(fitTimer);
  fitTimer = setTimeout(fit, 120);
}

/* ============================================================
   二、图表挂载
   ============================================================ */
function mount(id, option) {
  const el = document.getElementById(id);
  if (!el) return null;

  if (!instances[id]) {
    instances[id] = echarts.init(el, null, { renderer: 'canvas' });
  }
  const chart = instances[id];
  // notMerge=true：切换 metric / 刷新时不要残留上一次的 series
  chart.setOption(option, true);
  setPanelState(el, null);
  return chart;
}

function fail(id, err) {
  console.error(`[${id}]`, err);
  setPanelState(document.getElementById(id), 'error');
}

/* ============================================================
   三、各模块渲染
   ============================================================ */
async function renderKpi() {
  try {
    const { data } = await API.kpi();
    document.getElementById('kpiGmv').textContent   = FMT.money(data.total_gmv);
    document.getElementById('kpiOrder').textContent = FMT.int(data.order_cnt);
    document.getElementById('kpiAov').textContent   = FMT.dec(data.aov);
    document.getElementById('kpiBuyer').textContent = FMT.int(data.buyer_cnt);
  } catch (e) {
    ['kpiGmv', 'kpiOrder', 'kpiAov', 'kpiBuyer'].forEach(k => {
      document.getElementById(k).textContent = '--';
    });
    console.error('[kpi]', e);
  }
}

async function renderTrend() {
  const id = 'chartTrend';
  setPanelState(document.getElementById(id), 'loading');
  try {
    mount(id, CHARTS.trend(await API.trend()));
  } catch (e) { fail(id, e); }
}

async function renderCategory() {
  const id = 'chartCategory';
  setPanelState(document.getElementById(id), 'loading');
  try {
    const { rows } = await API.category();
    mount(id, CHARTS.category(rows));
  } catch (e) { fail(id, e); }
}

async function renderMap() {
  const id = 'chartMap';
  setPanelState(document.getElementById(id), 'loading');
  try {
    // 地图需要先注册 geoJSON；只注册一次
    if (!window.__chinaRegistered) {
      const res = await fetch('/static/vendor/china.json');
      const geo = await res.json();
      echarts.registerMap('china', geo);
      window.__chinaRegistered = true;
    }
    const r = await API.region();
    mount(id, CHARTS.map(r.rows, { max_gmv: r.max_gmv, min_gmv: r.min_gmv }));
  } catch (e) { fail(id, e); }
}

async function renderPay() {
  const id = 'chartPay';
  setPanelState(document.getElementById(id), 'loading');
  try {
    const { rows } = await API.paytype();
    mount(id, CHARTS.paytype(rows));
  } catch (e) { fail(id, e); }
}

async function renderHeat() {
  const id = 'chartHeat';
  setPanelState(document.getElementById(id), 'loading');
  try {
    mount(id, CHARTS.heatmap(await API.heatmap('order_cnt')));
  } catch (e) { fail(id, e); }
}

async function renderScatter() {
  const id = 'chartScatter';
  setPanelState(document.getElementById(id), 'loading');
  try {
    const { data } = await API.scatter();
    mount(id, CHARTS.scatter(data));
  } catch (e) { fail(id, e); }
}

async function renderStack() {
  const id = 'chartStack';
  setPanelState(document.getElementById(id), 'loading');
  try {
    mount(id, CHARTS.stack(await API.categoryMonth()));
  } catch (e) { fail(id, e); }
}

async function renderChannel() {
  const id = 'chartChannel';
  setPanelState(document.getElementById(id), 'loading');
  try {
    mount(id, CHARTS.channel(await API.channel()));
  } catch (e) { fail(id, e); }
}

async function renderStoreTable() {
  const box = document.getElementById('tableStore');
  try {
    const { rows } = await API.store(10);
    const badge = r => {
      const cls = r === 1 ? 'rank-badge--top1' : r === 2 ? 'rank-badge--top2'
                : r === 3 ? 'rank-badge--top3' : '';
      return `<span class="rank-badge ${cls}">${r}</span>`;
    };
    box.innerHTML = `
      <table>
        <thead>
          <tr>
            <th>排名</th><th>门店</th><th>省份</th>
            <th style="text-align:right">GMV</th>
            <th style="text-align:right">客单价</th>
            <th style="text-align:right">坪效</th>
          </tr>
        </thead>
        <tbody>
          ${rows.map(r => `
            <tr>
              <td>${badge(r.rank_no)}</td>
              <td>${r.store_name}</td>
              <td>${r.province}</td>
              <td class="num">${FMT.money(r.gmv)}</td>
              <td class="num">${FMT.dec(r.aov)}</td>
              <td class="num">${FMT.dec(r.gmv_per_sqm)}</td>
            </tr>`).join('')}
        </tbody>
      </table>`;
  } catch (e) {
    box.innerHTML = '<div style="color:#f43f5e;padding:12px">门店数据加载失败</div>';
    console.error('[store]', e);
  }
}

/* ============================================================
   四、顶部状态与时钟
   ============================================================ */
async function renderHealth() {
  const dot = document.getElementById('engineDot');
  const text = document.getElementById('engineText');
  try {
    const h = await API.health();
    const ch = h.engines.clickhouse;
    const hv = h.engines.hive;
    if (ch.ok) {
      dot.className = 'dot dot--ok';
      text.textContent = `数据引擎：ClickHouse 加速层 · 正常`;
    } else if (hv.ok) {
      dot.className = 'dot dot--warn';
      text.textContent = `数据引擎：降级至 Hive（ClickHouse 不可用）`;
    } else {
      dot.className = 'dot dot--err';
      text.textContent = `数据引擎：全部不可用`;
    }
  } catch (e) {
    dot.className = 'dot dot--err';
    text.textContent = '数据引擎：接口无响应';
  }
}

function startClock() {
  const el = document.getElementById('clock');
  const tick = () => {
    const d = new Date();
    const p = n => String(n).padStart(2, '0');
    el.textContent =
      `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} `
      + `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
  };
  tick();
  setInterval(tick, 1000);
}

/* ============================================================
   五、启动
   ============================================================ */
async function refreshAll() {
  // 并发发起，互不阻塞：某个接口慢不影响其它图表先出来
  await Promise.allSettled([
    renderHealth(),
    renderKpi(),
    renderTrend(),
    renderCategory(),
    renderMap(),
    renderPay(),
    renderHeat(),
    renderScatter(),
    renderStack(),
    renderChannel(),
    renderStoreTable(),
  ]);
}

function boot() {
  window.addEventListener('resize', onResize);
  fit();
  startClock();
  refreshAll();
  setInterval(refreshAll, REFRESH_MS);
}

// DOM 就绪后再跑（脚本放在 body 末尾，正常已就绪）
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', boot);
} else {
  boot();
}
