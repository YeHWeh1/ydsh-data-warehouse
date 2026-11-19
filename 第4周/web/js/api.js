/* ============================================================
   api.js —— 大屏取数封装
   统一处理：超时、错误、加载态、重试
   ============================================================ */

const API_BASE = (location.protocol === 'file:')
  ? 'http://127.0.0.1:8000'      // 本地直接双击打开时走本机 API
  : '';                          // 由 FastAPI 托管时同源

const API = {
  /**
   * 取数：返回 json，失败抛异常（由调用方决定是否降级/提示）
   * @param {string} path  形如 '/api/kpi'
   * @param {number} timeoutMs
   */
  async get(path, timeoutMs = 15000) {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), timeoutMs);
    try {
      const res = await fetch(API_BASE + path, { signal: ctrl.signal });
      const json = await res.json();
      if (!res.ok || json.ok === false) {
        throw new Error(json.error || `HTTP ${res.status}`);
      }
      return json;
    } finally {
      clearTimeout(timer);
    }
  },

  health:        ()      => API.get('/api/health', 5000),
  kpi:           ()      => API.get('/api/kpi'),
  trend:         ()      => API.get('/api/trend'),
  category:      ()      => API.get('/api/category'),
  region:        ()      => API.get('/api/region'),
  paytype:       ()      => API.get('/api/paytype'),
  heatmap:       (m)     => API.get(`/api/heatmap?metric=${m || 'order_cnt'}`),
  categoryMonth: ()      => API.get('/api/category-month'),
  store:         (top)   => API.get(`/api/store?top=${top || 10}`),
  channel:       ()      => API.get('/api/channel'),
  scatter:       ()      => API.get('/api/scatter'),
  nl2sql:        (q)     => API.get(`/api/nl2sql?q=${encodeURIComponent(q)}`, 60000),
};

/* ---------- 数字格式化 ---------- */
const FMT = {
  /** 金额：自动万/亿单位 */
  money(v) {
    if (v == null || isNaN(v)) return '--';
    const n = Number(v);
    if (Math.abs(n) >= 1e8) return (n / 1e8).toFixed(2) + ' 亿';
    if (Math.abs(n) >= 1e4) return (n / 1e4).toFixed(1) + ' 万';
    return n.toFixed(2);
  },
  /** 整数千分位 */
  int(v) {
    if (v == null || isNaN(v)) return '--';
    return Math.round(Number(v)).toLocaleString('zh-CN');
  },
  /** 两位小数 */
  dec(v) {
    if (v == null || isNaN(v)) return '--';
    return Number(v).toFixed(2);
  },
  pct(v) {
    if (v == null || isNaN(v)) return '--';
    return Number(v).toFixed(2) + '%';
  },
};

/* ---------- 面板状态 ---------- */
function setPanelState(el, state) {
  const panel = el.closest('.panel');
  if (!panel) return;
  panel.classList.remove('is-loading', 'is-error');
  if (state) panel.classList.add('is-' + state);
}
