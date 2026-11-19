/* ============================================================
   charts.js —— 各类图表的 ECharts 配置构造
   把「配置」与「取数/渲染」分开：这里只产出 option，不碰 DOM
   统一深色主题，配色取自 screen.css 的 CSS 变量
   ============================================================ */

const PALETTE = [
  '#22d3ee', '#38bdf8', '#818cf8', '#a78bfa',
  '#f472b6', '#fb923c', '#facc15', '#4ade80',
];

const AXIS_STYLE = {
  axisLine:  { lineStyle: { color: 'rgba(125,155,196,0.45)' } },
  axisTick:  { show: false },
  axisLabel: { color: '#7d9bc4', fontSize: 11 },
  splitLine: { lineStyle: { color: 'rgba(125,155,196,0.12)' } },
};

const TOOLTIP_STYLE = {
  backgroundColor: 'rgba(8,20,40,0.94)',
  borderColor: 'rgba(34,211,238,0.45)',
  borderWidth: 1,
  textStyle: { color: '#e2f1ff', fontSize: 12 },
};

/* 统一的图表基础配置，各 option 用 merge 叠加 */
function baseOption() {
  return {
    backgroundColor: 'transparent',
    textStyle: { fontFamily: 'Microsoft YaHei, PingFang SC, sans-serif' },
    color: PALETTE,
    animationDuration: 700,
    animationEasing: 'cubicOut',
  };
}

const CHARTS = {

  /* ------------------------------------------------------------
     1. GMV 日趋势：折线 + 7 日均线 + 异常日散点标注
     ------------------------------------------------------------ */
  trend(data) {
    const anomalies = data.anomalies || [];
    // 异常日在折线图上的坐标：用日期匹配回原序列的索引
    const idxMap = new Map(data.dates.map((d, i) => [d, i]));
    const anoPoints = anomalies
      .filter(a => idxMap.has(a.order_date))
      .map(a => ({
        value: [idxMap.get(a.order_date), a.gmv],
        name: a.order_date,
        z: a.z_score,
        type: a.anomaly_type,
        itemStyle: {
          color: a.anomaly_type === '偏高' ? '#fb923c' : '#f43f5e',
        },
      }));

    return Object.assign(baseOption(), {
      grid: { left: 52, right: 18, top: 34, bottom: 30 },
      tooltip: Object.assign({ trigger: 'axis' }, TOOLTIP_STYLE),
      legend: {
        data: ['GMV', '7日均线', '异常日'],
        right: 8, top: 2,
        itemWidth: 12, itemHeight: 8,
        textStyle: { color: '#7d9bc4', fontSize: 11 },
      },
      xAxis: Object.assign({
        type: 'category',
        data: data.dates,
        boundaryGap: false,
        axisLabel: Object.assign({}, AXIS_STYLE.axisLabel, {
          // 366 个点太密，按月抽稀显示
          interval: Math.max(1, Math.floor(data.dates.length / 12)),
          formatter: v => v.slice(5),
        }),
      }, AXIS_STYLE),
      yAxis: Object.assign({
        type: 'value',
        name: '元',
        nameTextStyle: { color: '#7d9bc4', fontSize: 11 },
        axisLabel: Object.assign({}, AXIS_STYLE.axisLabel, {
          formatter: v => FMT.money(v),
        }),
      }, AXIS_STYLE),
      series: [
        {
          name: 'GMV',
          type: 'line',
          smooth: true,
          showSymbol: false,
          lineStyle: { width: 2, color: '#22d3ee' },
          areaStyle: {
            color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
              { offset: 0, color: 'rgba(34,211,238,0.35)' },
              { offset: 1, color: 'rgba(34,211,238,0.02)' },
            ]),
          },
          data: data.gmv,
        },
        {
          name: '7日均线',
          type: 'line',
          smooth: true,
          showSymbol: false,
          lineStyle: { width: 1.5, color: '#a78bfa', type: 'dashed' },
          data: data.ma7,
        },
        {
          name: '异常日',
          type: 'scatter',
          symbolSize: v => 6 + Math.min(8, Math.abs(v[2] || 0) * 2),
          data: anoPoints,
          tooltip: {
            formatter: p => `${p.data.name}<br/>GMV ${FMT.money(p.data.value[1])}<br/>`
                          + `类型 ${p.data.type}（z=${FMT.dec(p.data.z)}）`,
          },
        },
      ],
    });
  },

  /* ------------------------------------------------------------
     2. 品类排行：横向条形图（品类名较长，横向更易读）
     ------------------------------------------------------------ */
  category(rows) {
    // ECharts 横向柱状：y 轴从下往上画，所以反转一次让第一名在顶部
    const sorted = [...rows].sort((a, b) => a.gmv - b.gmv);
    return Object.assign(baseOption(), {
      grid: { left: 74, right: 62, top: 12, bottom: 12 },
      tooltip: Object.assign({
        trigger: 'axis',
        axisPointer: { type: 'shadow' },
        formatter: p => {
          const r = rows.find(x => x.category === p[0].name);
          return `${r.category}<br/>GMV ${FMT.money(r.gmv)}<br/>`
               + `占比 ${FMT.pct(r.gmv_share_pct)}<br/>订单 ${FMT.int(r.order_cnt)}`;
        },
      }, TOOLTIP_STYLE),
      xAxis: Object.assign({ type: 'value', show: false }, AXIS_STYLE),
      yAxis: Object.assign({
        type: 'category',
        data: sorted.map(r => r.category),
        axisLabel: { color: '#c9dcf5', fontSize: 12 },
      }, AXIS_STYLE, { axisLabel: { color: '#c9dcf5', fontSize: 12 } }),
      series: [{
        type: 'bar',
        barWidth: '58%',
        itemStyle: {
          borderRadius: [0, 4, 4, 0],
          color: new echarts.graphic.LinearGradient(0, 0, 1, 0, [
            { offset: 0, color: 'rgba(34,211,238,0.35)' },
            { offset: 1, color: '#22d3ee' },
          ]),
        },
        label: {
          show: true,
          position: 'right',
          color: '#22d3ee',
          fontSize: 11,
          formatter: p => FMT.money(p.value),
        },
        data: sorted.map(r => r.gmv),
      }],
    });
  },

  /* ------------------------------------------------------------
     3. 全国区域分布：地图 + 视觉映射
     ------------------------------------------------------------ */
  map(rows, stats) {
    // 省份名必须与 geoJSON 完全一致，不要做任何删减。
    //
    // 踩过的坑：之前这里把「省/市/自治区」后缀去掉了（陕西省→陕西），
    // 但 china.json 的 properties.name 是带后缀的（陕西省），
    // 两边对不上 → 每个省份都匹配不到数据 → 悬浮框一律显示「暂无数据」、
    // 地图整体也不着色。GeoJSON 用什么名字，这里就必须用什么名字。
    const mapData = rows.map(r => ({ name: r.province, value: r.gmv }));
    const detail = new Map(rows.map(r => [r.province, r]));

    return Object.assign(baseOption(), {
      tooltip: Object.assign({
        trigger: 'item',
        formatter: p => {
          const r = detail.get(p.name);
          if (!r) return `${p.name}<br/>暂无数据`;
          return `${r.province}<br/>GMV ${FMT.money(r.gmv)}<br/>`
               + `占比 ${FMT.pct(r.share_pct)}<br/>买家 ${FMT.int(r.buyer_cnt)} 人`;
        },
      }, TOOLTIP_STYLE),
      visualMap: {
        min: stats.min_gmv || 0,
        max: stats.max_gmv || 1,
        left: 10, bottom: 12,
        orient: 'vertical',
        text: ['高', '低'],
        textStyle: { color: '#7d9bc4', fontSize: 11 },
        itemWidth: 10, itemHeight: 76,
        calculable: true,
        inRange: {
          color: ['#0b2447', '#12507a', '#1a7fa8', '#22d3ee', '#7ff0ff'],
        },
      },
      series: [{
        type: 'map',
        map: 'china',
        roam: false,
        zoom: 1.16,
        top: 18,
        label: { show: false },
        itemStyle: {
          areaColor: '#0d1f3a',
          borderColor: 'rgba(34,211,238,0.45)',
          borderWidth: 0.8,
        },
        emphasis: {
          label: { show: true, color: '#fff', fontSize: 11 },
          itemStyle: { areaColor: '#22d3ee', borderColor: '#fff' },
        },
        data: mapData,
      }],
    });
  },

  /* ------------------------------------------------------------
     4. 时段 × 品类 热力图
     ------------------------------------------------------------ */
  heatmap(data) {
    return Object.assign(baseOption(), {
      grid: { left: 84, right: 16, top: 12, bottom: 62 },
      tooltip: Object.assign({
        position: 'top',
        formatter: p => `${data.hours[p.value[0]]} · ${data.categories[p.value[1]]}<br/>`
                     + `订单量 ${FMT.int(p.value[2])}`,
      }, TOOLTIP_STYLE),
      xAxis: Object.assign({
        type: 'category',
        data: data.hours,
        splitArea: { show: true },
        axisLabel: { color: '#7d9bc4', fontSize: 10, interval: 1 },
      }, AXIS_STYLE),
      yAxis: Object.assign({
        type: 'category',
        data: data.categories,
        splitArea: { show: true },
        axisLabel: { color: '#c9dcf5', fontSize: 11 },
      }, AXIS_STYLE),
      visualMap: {
        min: 0,
        max: data.max || 1,
        calculable: true,
        orient: 'horizontal',
        left: 'center',
        bottom: 6,
        itemWidth: 12, itemHeight: 90,
        textStyle: { color: '#7d9bc4', fontSize: 10 },
        inRange: { color: ['#0b2447', '#12507a', '#1a7fa8', '#22d3ee', '#facc15'] },
      },
      series: [{
        type: 'heatmap',
        data: data.data,
        label: { show: false },
        itemStyle: { borderColor: 'rgba(5,11,26,0.85)', borderWidth: 1 },
        emphasis: {
          itemStyle: { shadowBlur: 8, shadowColor: 'rgba(34,211,238,0.9)' },
        },
      }],
    });
  },

  /* ------------------------------------------------------------
     5. 支付方式占比：环形饼图
     ------------------------------------------------------------ */
  paytype(rows) {
    return Object.assign(baseOption(), {
      tooltip: Object.assign({
        trigger: 'item',
        formatter: p => `${p.name}<br/>GMV ${FMT.money(p.value)}<br/>占比 ${p.percent}%`,
      }, TOOLTIP_STYLE),
      legend: {
        orient: 'vertical',
        right: 6,
        top: 'center',
        itemWidth: 9,
        itemHeight: 9,
        textStyle: { color: '#c9dcf5', fontSize: 11 },
      },
      series: [{
        type: 'pie',
        radius: ['46%', '68%'],
        center: ['38%', '52%'],
        avoidLabelOverlap: true,
        itemStyle: {
          borderColor: '#050b1a',
          borderWidth: 2,
          borderRadius: 3,
        },
        label: {
          show: true,
          color: '#c9dcf5',
          fontSize: 10,
          formatter: '{d}%',
        },
        labelLine: { length: 8, length2: 8, lineStyle: { color: 'rgba(125,155,196,0.5)' } },
        emphasis: {
          label: { show: true, fontSize: 13, fontWeight: 'bold', color: '#fff' },
          itemStyle: { shadowBlur: 14, shadowColor: 'rgba(34,211,238,0.7)' },
        },
        data: rows.map(r => ({ name: r.pay_type, value: r.gmv })),
      }],
    });
  },

  /* ------------------------------------------------------------
     6. 客单价 × 订单量 散点（观察规模与客单的负相关）
     ------------------------------------------------------------ */
  scatter(data) {
    return Object.assign(baseOption(), {
      grid: { left: 58, right: 18, top: 18, bottom: 40 },
      tooltip: Object.assign({
        trigger: 'item',
        formatter: p => `${p.value[2]}<br/>订单 ${FMT.int(p.value[0])}<br/>`
                     + `客单价 ${FMT.dec(p.value[1])} 元<br/>GMV ${FMT.money(p.value[3])}`,
      }, TOOLTIP_STYLE),
      xAxis: Object.assign({
        type: 'value',
        name: '订单量',
        nameTextStyle: { color: '#7d9bc4', fontSize: 10 },
        nameLocation: 'middle',
        nameGap: 24,
        axisLabel: Object.assign({}, AXIS_STYLE.axisLabel, { formatter: v => FMT.int(v) }),
      }, AXIS_STYLE),
      yAxis: Object.assign({
        type: 'value',
        name: '客单价(元)',
        nameTextStyle: { color: '#7d9bc4', fontSize: 10 },
        scale: true,
      }, AXIS_STYLE),
      series: [{
        type: 'scatter',
        symbolSize: 6,
        itemStyle: {
          color: 'rgba(56,189,248,0.72)',
          borderColor: 'rgba(34,211,238,0.9)',
          borderWidth: 0.5,
        },
        emphasis: { itemStyle: { color: '#facc15' } },
        data: data,
      }],
    });
  },

  /* ------------------------------------------------------------
     7. 品类月度结构：堆叠面积图
     ------------------------------------------------------------ */
  stack(data) {
    return Object.assign(baseOption(), {
      grid: { left: 54, right: 16, top: 30, bottom: 24 },
      tooltip: Object.assign({
        trigger: 'axis',
        axisPointer: { type: 'line', lineStyle: { color: 'rgba(34,211,238,0.5)' } },
        formatter: ps => {
          const total = ps.reduce((s, p) => s + (p.value || 0), 0);
          return `${ps[0].axisValue}<br/>` +
            ps.map(p => `${p.marker}${p.seriesName} ${FMT.money(p.value)}`).join('<br/>') +
            `<br/><b>合计 ${FMT.money(total)}</b>`;
        },
      }, TOOLTIP_STYLE),
      legend: {
        type: 'scroll',
        top: 0,
        itemWidth: 10,
        itemHeight: 8,
        textStyle: { color: '#7d9bc4', fontSize: 10 },
      },
      xAxis: Object.assign({
        type: 'category',
        data: data.months,
        boundaryGap: false,
        axisLabel: Object.assign({}, AXIS_STYLE.axisLabel, { formatter: v => v.slice(5) }),
      }, AXIS_STYLE),
      yAxis: Object.assign({
        type: 'value',
        axisLabel: Object.assign({}, AXIS_STYLE.axisLabel, { formatter: v => FMT.money(v) }),
      }, AXIS_STYLE),
      series: data.series.map(s => ({
        name: s.name,
        type: 'line',
        stack: 'total',
        smooth: true,
        showSymbol: false,
        lineStyle: { width: 0.8 },
        areaStyle: { opacity: 0.72 },
        data: s.data,
      })),
    });
  },

  /* ------------------------------------------------------------
     8. 渠道月度结构：多系列折线
     ------------------------------------------------------------ */
  channel(data) {
    return Object.assign(baseOption(), {
      grid: { left: 56, right: 14, top: 28, bottom: 24 },
      tooltip: Object.assign({ trigger: 'axis' }, TOOLTIP_STYLE),
      legend: {
        top: 0,
        itemWidth: 10,
        itemHeight: 8,
        textStyle: { color: '#7d9bc4', fontSize: 10 },
      },
      xAxis: Object.assign({
        type: 'category',
        data: data.months,
        boundaryGap: false,
        axisLabel: Object.assign({}, AXIS_STYLE.axisLabel, { formatter: v => v.slice(5) }),
      }, AXIS_STYLE),
      yAxis: Object.assign({
        type: 'value',
        axisLabel: Object.assign({}, AXIS_STYLE.axisLabel, { formatter: v => FMT.money(v) }),
      }, AXIS_STYLE),
      series: data.series.map(s => ({
        name: s.name,
        type: 'line',
        smooth: true,
        symbolSize: 4,
        lineStyle: { width: 2 },
        data: s.data,
      })),
    });
  },
};
