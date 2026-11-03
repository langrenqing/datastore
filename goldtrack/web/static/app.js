/* global echarts */
const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

const state = {
  meta: null,
  status: null,
  priceSymbol: 'XAU',
  overlaySymbol: 'XAU',
  priceDays: 180,
  overlayDays: 90,
  priceCustom: null,
  overlayCustom: null,
  overlayLevel: 1,
  priceChart: null,
  overlayChart: null,
  eventOffset: 0,
  eventLimit: 50,
  eventTotal: 0,
  pollTimer: null,
  activeJobId: null,
  loaded: { prices: false, overlay: false, events: false },
  typeColor: {},
};

const STATUS_LABELS = {
  queued: '排队中',
  running: '运行中',
  succeeded: '成功',
  partial: '部分成功',
  failed: '失败',
  interrupted: '已中断',
};

const KIND_LABELS = { prices: '价格采集', events: '事件采集' };

/* ------------------------------------------------------------------ utils */

const HTML_ESCAPES = {
  '&': '&amp;',
  '<': '&lt;',
  '>': '&gt;',
  '"': '&quot;',
  "'": '&#39;',
};

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, (char) => HTML_ESCAPES[char]);
}

function fmtNum(value, digits = 2) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '—';
  return Number(value).toLocaleString('zh-CN', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

function fmtPct(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '—';
  const number = Number(value);
  return `${number >= 0 ? '+' : ''}${number.toFixed(2)}%`;
}

function changeClass(value) {
  if (value === null || value === undefined) return 'muted';
  return Number(value) >= 0 ? 'up' : 'down';
}

function daysAgoIso(days) {
  const day = new Date();
  day.setUTCDate(day.getUTCDate() - days);
  return day.toISOString().slice(0, 10);
}

function fmtDateTime(value) {
  if (!value) return '—';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return String(value);
  const pad = (number) => String(number).padStart(2, '0');
  return `${parsed.getFullYear()}-${pad(parsed.getMonth() + 1)}-${pad(parsed.getDate())} ${pad(parsed.getHours())}:${pad(parsed.getMinutes())}`;
}

function fmtDuration(seconds) {
  if (seconds === null || seconds === undefined) return '—';
  const value = Math.max(0, Math.round(Number(seconds)));
  if (value < 60) return `${value} 秒`;
  const minutes = Math.floor(value / 60);
  const rest = value % 60;
  return rest ? `${minutes} 分 ${rest} 秒` : `${minutes} 分`;
}

function safeUrl(value) {
  if (!value) return null;
  try {
    const url = new URL(value);
    return ['http:', 'https:'].includes(url.protocol) ? url.href : null;
  } catch {
    return null;
  }
}

function typeColor(name) {
  return state.typeColor[name] || '#95a5a6';
}

function toast(message, kind = 'error') {
  const element = $('#toast');
  element.textContent = message;
  element.className = `toast ${kind}`;
  element.hidden = false;
  window.clearTimeout(toast.timer);
  toast.timer = window.setTimeout(() => {
    element.hidden = true;
  }, kind === 'ok' ? 2800 : 6000);
}

async function api(path, options = {}) {
  const response = await fetch(path, options);
  const text = await response.text();
  let body = null;
  if (text) {
    try {
      body = JSON.parse(text);
    } catch {
      body = text;
    }
  }
  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`;
    if (body && body.detail) {
      message = Array.isArray(body.detail)
        ? body.detail.map((item) => item.msg || JSON.stringify(item)).join('；')
        : body.detail;
    } else if (typeof body === 'string' && body) {
      message = body;
    }
    throw new Error(message);
  }
  return body;
}

/* ------------------------------------------------------------- rendering */

function renderDataStamp(status) {
  const dates = [];
  for (const instrument of status.instruments || []) {
    if (instrument.latest_date) dates.push(instrument.latest_date);
  }
  if (status.events && status.events.latest_date) dates.push(status.events.latest_date);
  dates.sort();
  const latest = dates[dates.length - 1];
  const stamp = $('#data-stamp');
  stamp.textContent = latest ? `数据截至 ${latest}` : '暂无数据';
  stamp.title = status.events
    ? `事件源最新日期 ${status.events.latest_published_at || '—'}`
    : '';
}

function renderSummary(instruments) {
  const container = $('#summary');
  container.innerHTML = (instruments || [])
    .map((instrument) => {
      const hasData = Boolean(instrument.latest_date);
      const stale = instrument.stale
        ? '<span class="badge stale">滞后</span>'
        : '<span class="badge fresh">正常</span>';
      return `
        <article class="summary-card${hasData ? '' : ' empty'}">
          <div class="symbol"><span>${escapeHtml(instrument.symbol)}</span><span>${escapeHtml(instrument.currency)}</span></div>
          <div class="name">${escapeHtml(instrument.name)}</div>
          <div class="quote">
            <span class="price">${hasData ? fmtNum(instrument.latest_close) : '暂无数据'}</span>
            ${hasData ? `<span class="unit">${escapeHtml(instrument.unit)}</span>` : ''}
            ${hasData ? `<span class="change ${changeClass(instrument.daily_change_pct)}">${fmtPct(instrument.daily_change_pct)}</span>` : ''}
          </div>
          <div class="meta">
            <span>${hasData ? instrument.latest_date : '运行采集后显示'} · ${hasData ? instrument.count.toLocaleString('zh-CN') : 0} 根</span>
            ${hasData ? stale : ''}
          </div>
        </article>`;
    })
    .join('');
}

function renderPriceStats(data) {
  const element = $('#price-stats');
  if (!data.bars || !data.bars.length) {
    element.innerHTML = '<span>该品种暂无价格数据。请在“数据管理”中采集。</span>';
    $('#price-note').textContent = '';
    return;
  }
  const last = data.bars[data.bars.length - 1];
  element.innerHTML = `
    <span>最新 <b>${last.date}</b></span>
    <span class="sep">|</span>
    <span>收盘 <b>${fmtNum(last.close)}</b> ${escapeHtml(data.unit)}</span>
    <span>日涨跌 <b class="${changeClass(last.daily_change_pct)}">${fmtPct(last.daily_change_pct)}</b></span>
    <span>MA5 <b>${fmtNum(last.ma5)}</b></span>
    <span>MA20 <b>${fmtNum(last.ma20)}</b></span>
    <span>MA60 <b>${fmtNum(last.ma60)}</b></span>
    <span>20日波动 <b>${fmtPct(last.volatility_pct)}</b></span>
    <span>趋势 <b>${escapeHtml(last.trend)}</b></span>
    <span>样本 <b>${data.total_count.toLocaleString('zh-CN')}</b></span>`;
  const suffix = data.downsampled ? '，已降采样显示' : '';
  $('#price-note').textContent = `区间 ${data.start} ~ ${data.end}，显示 ${data.count} 个交易日${suffix}；指标在完整历史上计算。`;
}

function ensurePriceChart() {
  if (!window.echarts) throw new Error('ECharts 资源未加载');
  if (!state.priceChart) {
    state.priceChart = echarts.init($('#price-chart'), null, { renderer: 'canvas' });
  }
  return state.priceChart;
}

function emptyChart(chart, text) {
  chart.clear();
  chart.setOption({
    graphic: [
      {
        type: 'text',
        left: 'center',
        top: 'middle',
        style: { text, fill: '#8a94a3', fontSize: 14 },
      },
    ],
  });
}

function renderPriceChart(data) {
  let chart;
  try {
    chart = ensurePriceChart();
  } catch (error) {
    toast(error.message);
    return;
  }
  if (!data.bars || !data.bars.length) {
    emptyChart(chart, '暂无价格数据');
    return;
  }
  const bars = data.bars;
  const dates = bars.map((bar) => bar.date);
  const close = bars.map((bar) => bar.close);
  const ma5 = bars.map((bar) => bar.ma5);
  const ma20 = bars.map((bar) => bar.ma20);
  const ma60 = bars.map((bar) => bar.ma60);
  const change = bars.map((bar) => ({
    value: bar.daily_change_pct,
    itemStyle: { color: (bar.daily_change_pct ?? 0) >= 0 ? '#d62728' : '#2ca02c' },
  }));
  const volatility = bars.map((bar) => bar.volatility_pct);

  const tooltip = (params) => {
    const list = Array.isArray(params) ? params : [params];
    if (!list.length) return '';
    const date = list[0].axisValue;
    let html = `<div class="tt-date">${escapeHtml(date)}</div>`;
    for (const item of list) {
      if (item.value === null || item.value === undefined) continue;
      let value = item.value;
      if (value && typeof value === 'object' && 'value' in value) value = value.value;
      if (value === null || value === undefined) continue;
      const isPercent = item.seriesName.includes('%') || item.seriesName.includes('涨跌');
      const text = isPercent ? fmtPct(value) : fmtNum(value);
      html += `<div class="tt-row"><span>${item.marker}${escapeHtml(item.seriesName)}</span><b>${text}</b></div>`;
    }
    return html;
  };

  chart.setOption(
    {
      animation: false,
      textStyle: { fontFamily: '-apple-system, BlinkMacSystemFont, "PingFang SC", "Microsoft YaHei", sans-serif' },
      tooltip: {
        trigger: 'axis',
        axisPointer: { type: 'cross' },
        confine: true,
        backgroundColor: 'rgba(255,255,255,0.97)',
        borderColor: '#e3e8ef',
        textStyle: { color: '#1f2d3d', fontSize: 12 },
        formatter: tooltip,
      },
      axisPointer: { link: [{ xAxisIndex: 'all' }] },
      legend: {
        top: 6,
        right: 18,
        itemWidth: 16,
        itemHeight: 8,
        textStyle: { color: '#5b6675', fontSize: 11.5 },
        data: ['收盘价', 'MA5', 'MA20', 'MA60', '日涨跌幅', '20日波动率'],
      },
      grid: [
        { left: 70, right: 30, top: 40, height: '49%' },
        { left: 70, right: 62, top: '64%', height: '17%' },
      ],
      xAxis: [
        {
          type: 'category',
          data: dates,
          boundaryGap: false,
          gridIndex: 0,
          axisLine: { lineStyle: { color: '#cfd7e2' } },
          axisLabel: { show: false },
          axisTick: { show: false },
        },
        {
          type: 'category',
          data: dates,
          boundaryGap: true,
          gridIndex: 1,
          axisLine: { lineStyle: { color: '#cfd7e2' } },
          axisLabel: {
            color: '#7b8794',
            fontSize: 11,
            formatter: (value) => String(value).slice(5),
          },
          axisTick: { show: false },
        },
      ],
      yAxis: [
        {
          type: 'value',
          scale: true,
          gridIndex: 0,
          name: data.unit,
          nameTextStyle: { color: '#7b8794', fontSize: 11 },
          axisLabel: { color: '#7b8794', fontSize: 11, formatter: (value) => Number(value).toLocaleString('zh-CN') },
          splitLine: { lineStyle: { color: '#edf1f6' } },
        },
        {
          type: 'value',
          gridIndex: 1,
          name: '涨跌幅',
          nameTextStyle: { color: '#7b8794', fontSize: 11 },
          axisLabel: { color: '#7b8794', fontSize: 11, formatter: '{value}%' },
          splitLine: { lineStyle: { color: '#f2f5f9' } },
        },
        {
          type: 'value',
          gridIndex: 1,
          position: 'right',
          name: '波动率',
          nameTextStyle: { color: '#e67e22', fontSize: 11 },
          axisLabel: { color: '#e67e22', fontSize: 11, formatter: '{value}%' },
          splitLine: { show: false },
        },
      ],
      dataZoom: [
        { type: 'inside', xAxisIndex: [0, 1], start: 0, end: 100 },
        {
          type: 'slider',
          xAxisIndex: [0, 1],
          bottom: 6,
          height: 18,
          borderColor: '#e3e8ef',
          backgroundColor: '#f8fafc',
          fillerColor: 'rgba(184,134,11,0.12)',
          handleStyle: { color: '#b8860b' },
          textStyle: { color: '#7b8794', fontSize: 10 },
        },
      ],
      series: [
        {
          name: '收盘价',
          type: 'line',
          data: close,
          showSymbol: false,
          sampling: 'lttb',
          lineStyle: { width: 2, color: '#1f2d3d' },
          itemStyle: { color: '#1f2d3d' },
        },
        {
          name: 'MA5',
          type: 'line',
          data: ma5,
          showSymbol: false,
          sampling: 'lttb',
          connectNulls: false,
          lineStyle: { width: 1.2, color: '#f39c12' },
          itemStyle: { color: '#f39c12' },
        },
        {
          name: 'MA20',
          type: 'line',
          data: ma20,
          showSymbol: false,
          sampling: 'lttb',
          connectNulls: false,
          lineStyle: { width: 1.2, color: '#2980b9' },
          itemStyle: { color: '#2980b9' },
        },
        {
          name: 'MA60',
          type: 'line',
          data: ma60,
          showSymbol: false,
          sampling: 'lttb',
          connectNulls: false,
          lineStyle: { width: 1.2, color: '#8e44ad' },
          itemStyle: { color: '#8e44ad' },
        },
        {
          name: '日涨跌幅',
          type: 'bar',
          xAxisIndex: 1,
          yAxisIndex: 1,
          data: change,
          barMaxWidth: 8,
        },
        {
          name: '20日波动率',
          type: 'line',
          xAxisIndex: 1,
          yAxisIndex: 2,
          data: volatility,
          showSymbol: false,
          sampling: 'lttb',
          lineStyle: { width: 1.3, color: '#e67e22' },
          itemStyle: { color: '#e67e22' },
        },
      ],
    },
    { notMerge: true }
  );
}

function renderOverlayStats(data) {
  const element = $('#overlay-stats');
  if (!data.price || !data.price.length) {
    element.innerHTML = '<span>该品种暂无价格数据。请在“数据管理”中采集。</span>';
    return;
  }
  const top = data.top && data.top.length ? data.top[0] : null;
  element.innerHTML = `
    <span>区间 <b>${data.start}</b> ~ <b>${data.end}</b></span>
    <span class="sep">|</span>
    <span>事件 <b>${data.event_count.toLocaleString('zh-CN')}</b> 条</span>
    <span>每日最强 <b>${data.events.length}</b></span>
    <span>地区道 <b>${data.regions.length}</b></span>
    <span>时间轴 <b>${data.timeline_count}</b></span>
    ${top ? `<span class="sep">|</span><span>峰值 <b>${escapeHtml(top.event_type)}</b> · ${fmtNum(top.intensity, 1)} · ${escapeHtml(top.country)}</span>` : ''}`;
}

function renderOverlayLegend(data) {
  const container = $('#overlay-legend');
  const present = new Set();
  for (const item of data.events || []) present.add(item.event_type);
  for (const item of data.timeline || []) present.add(item.event_type);
  const types = (state.meta ? state.meta.event_types : [])
    .filter((item) => present.has(item.name))
    .map(
      (item) =>
        `<span class="item"><span class="dot" style="background:${item.color}"></span>${escapeHtml(item.name)}</span>`
    );
  container.innerHTML = [
    '<span class="item"><span class="line-swatch" style="background:#1f2d3d"></span>收盘价</span>',
    '<span class="item"><span class="line-swatch" style="background:#2980b9"></span>MA20</span>',
    ...types,
    '<span class="item muted">气泡大小 = 强度（0–100）</span>',
  ].join('');
}

function renderOverlayChart(data) {
  let chart;
  try {
    chart = state.overlayChart || echarts.init($('#overlay-chart'), null, { renderer: 'canvas' });
    state.overlayChart = chart;
  } catch (error) {
    toast(error.message);
    return;
  }
  if (!data.price || !data.price.length) {
    emptyChart(chart, '暂无价格数据');
    return;
  }

  const dates = data.price.map((item) => item.date);
  const close = data.price.map((item) => item.close);
  const ma20 = data.price.map((item) => item.ma20);
  const bubbles = (data.events || [])
    .filter((item) => item.price !== null && item.price !== undefined)
    .map((item) => ({
      value: [item.market_date, item.price],
      event: item,
      itemStyle: { color: typeColor(item.event_type) },
    }));
  const timelineData = (data.timeline || []).map((item) => ({
    value: [item.market_date, item.region],
    event: item,
    itemStyle: { color: typeColor(item.event_type) },
  }));
  const topPoints = (data.top || [])
    .filter((item) => item.price !== null && item.price !== undefined)
    .map((item) => ({
      coord: [item.market_date, item.price],
      value: item.intensity,
      symbol: 'pin',
      symbolSize: 42,
      itemStyle: { color: typeColor(item.event_type) },
      label: {
        show: true,
        formatter: `${item.occurred_at.slice(5)} ${item.country}`,
        fontSize: 9,
        color: '#fff',
        width: 64,
        overflow: 'truncate',
      },
    }));

  const tooltip = (params) => {
    const list = Array.isArray(params) ? params : [params];
    if (!list.length) return '';
    const date = list[0].axisValue;
    const events = [];
    let closeText = null;
    let maText = null;
    for (const item of list) {
      if (item.seriesName === '收盘价') closeText = fmtNum(item.value);
      if (item.seriesName === 'MA20') maText = fmtNum(item.value);
      if (item.data && item.data.event) events.push(item.data.event);
    }
    let html = `<div class="tt-date">${escapeHtml(date)}</div>`;
    if (closeText !== null) {
      html += `<div class="tt-row"><span>收盘价</span><b>${closeText}</b></div>`;
    }
    if (maText !== null && maText !== '—') {
      html += `<div class="tt-row"><span>MA20</span><b>${maText}</b></div>`;
    }
    const seen = new Set();
    for (const event of events) {
      if (seen.has(event.source_event_id)) continue;
      seen.add(event.source_event_id);
      html += `
        <div class="tt-event">
          <b>${escapeHtml(event.event_type)} · ${escapeHtml(event.region)} · L${event.level}</b>
          <div>${escapeHtml(event.summary)}</div>
          <div class="tt-meta">强度 ${fmtNum(event.intensity, 1)} · ${escapeHtml(event.country)} · ${escapeHtml(event.occurred_at)}</div>
        </div>`;
    }
    return html;
  };

  chart.setOption(
    {
      animation: false,
      textStyle: { fontFamily: '-apple-system, BlinkMacSystemFont, "PingFang SC", "Microsoft YaHei", sans-serif' },
      tooltip: {
        trigger: 'axis',
        axisPointer: { type: 'line' },
        confine: true,
        backgroundColor: 'rgba(255,255,255,0.97)',
        borderColor: '#e3e8ef',
        textStyle: { color: '#1f2d3d', fontSize: 12 },
        formatter: tooltip,
      },
      axisPointer: { link: [{ xAxisIndex: 'all' }] },
      grid: [
        { left: 70, right: 30, top: 30, height: '52%' },
        { left: 70, right: 30, top: '62%', height: '24%' },
      ],
      xAxis: [
        {
          type: 'category',
          data: dates,
          boundaryGap: false,
          gridIndex: 0,
          axisLine: { lineStyle: { color: '#cfd7e2' } },
          axisLabel: { show: false },
          axisTick: { show: false },
        },
        {
          type: 'category',
          data: dates,
          boundaryGap: false,
          gridIndex: 1,
          axisLine: { lineStyle: { color: '#cfd7e2' } },
          axisLabel: {
            color: '#7b8794',
            fontSize: 11,
            formatter: (value) => String(value).slice(5),
          },
          axisTick: { show: false },
        },
      ],
      yAxis: [
        {
          type: 'value',
          scale: true,
          gridIndex: 0,
          name: data.unit,
          nameTextStyle: { color: '#7b8794', fontSize: 11 },
          axisLabel: { color: '#7b8794', fontSize: 11, formatter: (value) => Number(value).toLocaleString('zh-CN') },
          splitLine: { lineStyle: { color: '#edf1f6' } },
        },
        {
          type: 'category',
          gridIndex: 1,
          data: data.regions,
          inverse: true,
          axisLabel: { color: '#5b6675', fontSize: 10.5, interval: 0 },
          axisLine: { lineStyle: { color: '#cfd7e2' } },
          axisTick: { show: false },
          splitLine: { lineStyle: { color: '#f5f7fa' } },
        },
      ],
      dataZoom: [
        { type: 'inside', xAxisIndex: [0, 1], start: 0, end: 100 },
        {
          type: 'slider',
          xAxisIndex: [0, 1],
          bottom: 6,
          height: 18,
          borderColor: '#e3e8ef',
          backgroundColor: '#f8fafc',
          fillerColor: 'rgba(184,134,11,0.12)',
          handleStyle: { color: '#b8860b' },
          textStyle: { color: '#7b8794', fontSize: 10 },
        },
      ],
      series: [
        {
          name: '收盘价',
          type: 'line',
          data: close,
          showSymbol: false,
          sampling: 'lttb',
          lineStyle: { width: 1.9, color: '#1f2d3d' },
          itemStyle: { color: '#1f2d3d' },
          markPoint: { data: topPoints },
        },
        {
          name: 'MA20',
          type: 'line',
          data: ma20,
          showSymbol: false,
          sampling: 'lttb',
          connectNulls: false,
          lineStyle: { width: 1.1, color: '#2980b9' },
          itemStyle: { color: '#2980b9' },
        },
        {
          name: '事件',
          type: 'scatter',
          data: bubbles,
          symbolSize: (value, params) => 6 + Math.min(Number(params.data.event.intensity) || 0, 100) * 0.12,
          itemStyle: { opacity: 0.62, borderColor: '#fff', borderWidth: 0.6 },
          z: 5,
        },
        {
          name: '地区时间轴',
          type: 'scatter',
          xAxisIndex: 1,
          yAxisIndex: 1,
          data: timelineData,
          symbolSize: (value, params) => 4 + Math.min(Number(params.data.event.intensity) || 0, 100) * 0.05,
          itemStyle: { opacity: 0.8, borderColor: '#fff', borderWidth: 0.4 },
          z: 5,
        },
      ],
    },
    { notMerge: true }
  );
}

function renderEventRows(events) {
  const body = $('#event-rows');
  if (!events.length) {
    body.innerHTML = '<tr><td colspan="9" class="empty">没有匹配的事件</td></tr>';
    return;
  }
  body.innerHTML = events
    .map((event) => {
      const url = safeUrl(event.url);
      return `
        <tr>
          <td>${escapeHtml(event.occurred_at)}</td>
          <td>${escapeHtml(event.market_date || '—')}</td>
          <td>${escapeHtml(event.region)}</td>
          <td>${escapeHtml(event.event_type)}</td>
          <td>${event.level} ${escapeHtml(event.level_name)}</td>
          <td class="num">${fmtNum(event.intensity, 1)}</td>
          <td>${escapeHtml(event.country)}</td>
          <td class="summary"><span title="${escapeHtml(event.summary)}">${escapeHtml(event.summary)}</span></td>
          <td>${url ? `<a href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">原文</a>` : ''}</td>
        </tr>`;
    })
    .join('');
}

function renderEventPager() {
  const limit = state.eventLimit;
  const offset = state.eventOffset;
  const total = state.eventTotal;
  if (!total) {
    $('#event-page').textContent = '共 0 条';
  } else {
    const first = offset + 1;
    const last = Math.min(offset + limit, total);
    $('#event-page').textContent = `第 ${first}–${last} 条 / 共 ${total.toLocaleString('zh-CN')} 条`;
  }
  $('#event-prev').disabled = offset <= 0;
  $('#event-next').disabled = offset + limit >= total;
}

function statusLabel(status) {
  return STATUS_LABELS[status] || status;
}

function lastRunText(run) {
  if (!run) return '—';
  return `${fmtDateTime(run.finished_at || run.started_at)} · ${statusLabel(run.status)}`;
}

function lastRunTitle(run) {
  if (!run) return '';
  return run.error || run.message || '';
}

function renderStatusCards(status) {
  const container = $('#status-cards');
  const eventsSuccess = status.last_runs ? status.last_runs.events_success : null;
  const eventsFailure = status.last_runs ? status.last_runs.events_failure : null;

  const instrumentCards = (status.instruments || []).map((instrument) => {
    const hasData = Boolean(instrument.latest_date);
    const success = instrument.last_success || null;
    const failure = instrument.last_failure || null;
    const badge = !hasData
      ? '<span class="badge interrupted">无数据</span>'
      : instrument.stale
        ? '<span class="badge stale">滞后</span>'
        : '<span class="badge fresh">正常</span>';
    return `
      <div class="status-card">
        <h3><span>${escapeHtml(instrument.symbol)} · ${escapeHtml(instrument.name)}</span>${badge}</h3>
        <div class="rows">
          <span>最新数据</span><b>${escapeHtml(instrument.latest_date || '—')}</b>
          <span>样本</span><b>${instrument.count.toLocaleString('zh-CN')} 根</b>
          <span>最近成功</span><b title="${escapeHtml(lastRunTitle(success))}">${escapeHtml(lastRunText(success))}</b>
          <span>最近失败</span><b title="${escapeHtml(lastRunTitle(failure))}">${escapeHtml(lastRunText(failure))}</b>
        </div>
      </div>`;
  });

  const eventStatus = status.events || {};
  const eventCard = `
    <div class="status-card">
      <h3><span>GDELT 事件库</span><span class="badge ${eventStatus.count ? 'fresh' : 'interrupted'}">${eventStatus.count ? '有数据' : '无数据'}</span></h3>
      <div class="rows">
        <span>事件数</span><b>${(eventStatus.count || 0).toLocaleString('zh-CN')} 条</b>
        <span>最新发生日</span><b>${escapeHtml(eventStatus.latest_date || '—')}</b>
        <span>最新源日期</span><b>${escapeHtml(eventStatus.latest_published_at || '—')}</b>
        <span>最近成功</span><b title="${escapeHtml(lastRunTitle(eventsSuccess))}">${escapeHtml(lastRunText(eventsSuccess))}</b>
        <span>最近失败</span><b title="${escapeHtml(lastRunTitle(eventsFailure))}">${escapeHtml(lastRunText(eventsFailure))}</b>
      </div>
    </div>`;

  container.innerHTML = [...instrumentCards, eventCard].join('');
}

function runResultText(run) {
  if (run.error) return run.error;
  const summary = run.summary;
  if (!summary) return run.message || '—';
  if (run.kind === 'prices') {
    const parts = (summary.results || []).map((item) => `${item.symbol} +${item.stored ?? 0}`);
    const errors = (summary.results || []).flatMap((item) => item.errors || []);
    return parts.join(' · ') + (errors.length ? `；${errors[0]}` : '');
  }
  if (run.kind === 'events') {
    const failed = summary.failure_count || 0;
    const days = summary.days || 0;
    const okDays = Math.max(0, days - failed);
    return `${okDays}/${days} 天成功 · 入库 ${(summary.stored || 0).toLocaleString('zh-CN')} 条${failed ? ` · ${failed} 天失败` : ''}`;
  }
  return '—';
}

function renderRuns(runs) {
  const body = $('#run-rows');
  if (!runs || !runs.length) {
    body.innerHTML = '<tr><td colspan="7" class="empty">暂无记录</td></tr>';
    return;
  }
  body.innerHTML = runs
    .map((run) => {
      const progress = run.progress_total
        ? `${run.progress_current}/${run.progress_total}`
        : '—';
      const result = runResultText(run);
      return `
        <tr>
          <td>${run.id}</td>
          <td>${KIND_LABELS[run.kind] || escapeHtml(run.kind)}</td>
          <td><span class="badge ${run.status}">${escapeHtml(statusLabel(run.status))}</span></td>
          <td>${escapeHtml(fmtDateTime(run.started_at))}</td>
          <td class="num">${fmtDuration(run.duration_seconds)}</td>
          <td>${progress}</td>
          <td class="summary"><span title="${escapeHtml(result)}">${escapeHtml(result)}</span></td>
        </tr>`;
    })
    .join('');
}

function renderActiveJob(job) {
  const container = $('#active-job');
  if (!job) {
    container.innerHTML = '';
    return;
  }
  const terminal = ['succeeded', 'partial', 'failed', 'interrupted'].includes(job.status);
  const total = Number(job.progress_total) || 0;
  const current = Number(job.progress_current) || 0;
  const percent = total ? Math.min(100, Math.round((current / total) * 100)) : terminal ? 100 : 6;
  const message = job.error || job.message || (job.status === 'queued' ? '等待子进程启动…' : '正在采集…');
  container.innerHTML = `
    <div class="job-banner">
      <div class="job-top">
        <span class="job-title">${terminal ? '采集结束' : '正在采集'}：${KIND_LABELS[job.kind] || escapeHtml(job.kind)} #${job.id}</span>
        <span class="badge ${job.status}">${escapeHtml(statusLabel(job.status))}</span>
      </div>
      <div class="progress"><span style="width:${percent}%"></span></div>
      <div class="job-meta">
        <span>${escapeHtml(message)}</span>
        <span>${total ? `${current}/${total}` : ''} ${job.duration_seconds ? `· ${fmtDuration(job.duration_seconds)}` : ''}</span>
      </div>
    </div>`;
}

/* --------------------------------------------------------------- loaders */

function fillSelect(select, options, selected) {
  select.innerHTML = options
    .map((option) => `<option value="${escapeHtml(option.value)}">${escapeHtml(option.label)}</option>`)
    .join('');
  if (selected !== undefined && selected !== null) select.value = String(selected);
}

function populateControls() {
  const meta = state.meta;
  const instrumentOptions = meta.instruments.map((instrument) => ({
    value: instrument.symbol,
    label: `${instrument.symbol} · ${instrument.name}`,
  }));
  fillSelect($('#price-symbol'), instrumentOptions, state.priceSymbol);
  fillSelect($('#overlay-symbol'), instrumentOptions, state.overlaySymbol);

  const levelOptions = [
    { value: 1, label: '全部（≥1）' },
    ...meta.levels
      .filter((level) => level.value > 1)
      .sort((left, right) => left.value - right.value)
      .map((level) => ({ value: level.value, label: `≥${level.value} ${level.name}` })),
  ];
  fillSelect($('#overlay-level'), levelOptions, 1);
  fillSelect($('#event-level'), levelOptions, 1);

  fillSelect(
    $('#event-region'),
    [{ value: '', label: '全部' }, ...meta.event_regions.map((region) => ({ value: region, label: region }))],
    ''
  );
  fillSelect(
    $('#event-type'),
    [{ value: '', label: '全部' }, ...meta.event_types.map((item) => ({ value: item.name, label: item.name }))],
    ''
  );

  $('#price-symbols').innerHTML = meta.instruments
    .map(
      (instrument) => `
        <label><input type="checkbox" value="${escapeHtml(instrument.symbol)}" ${
          meta.default_symbols.includes(instrument.symbol) ? 'checked' : ''
        }> ${escapeHtml(instrument.symbol)} ${escapeHtml(instrument.name)}</label>`
    )
    .join('');

  $('#event-start').value = daysAgoIso(29);
  $('#event-end').value = daysAgoIso(0);
}

async function loadPrices() {
  const params = new URLSearchParams({
    symbol: state.priceSymbol,
    days: String(state.priceDays),
    max_points: '2000',
  });
  if (state.priceCustom) {
    if (state.priceCustom.start) params.set('start', state.priceCustom.start);
    if (state.priceCustom.end) params.set('end', state.priceCustom.end);
  }
  try {
    const data = await api(`/api/prices?${params}`);
    renderPriceStats(data);
    renderPriceChart(data);
    state.loaded.prices = true;
    $('#price-note').dataset.loaded = '1';
  } catch (error) {
    toast(`价格数据加载失败：${error.message}`);
  }
}

async function loadOverlay() {
  const params = new URLSearchParams({
    symbol: state.overlaySymbol,
    days: String(state.overlayDays),
    min_level: String(state.overlayLevel),
    max_points: '2000',
  });
  if (state.overlayCustom) {
    if (state.overlayCustom.start) params.set('start', state.overlayCustom.start);
    if (state.overlayCustom.end) params.set('end', state.overlayCustom.end);
  }
  try {
    const data = await api(`/api/events/overlay?${params}`);
    renderOverlayStats(data);
    renderOverlayChart(data);
    renderOverlayLegend(data);
    state.loaded.overlay = true;
  } catch (error) {
    toast(`事件叠加加载失败：${error.message}`);
  }
}

async function loadEvents() {
  const params = new URLSearchParams({
    symbol: state.overlaySymbol,
    limit: String(state.eventLimit),
    offset: String(state.eventOffset),
  });
  const start = $('#event-start').value;
  const end = $('#event-end').value;
  if (start) params.set('start', start);
  if (end) params.set('end', end);
  if (!start && !end) params.set('days', '30');
  const region = $('#event-region').value;
  if (region) params.append('region', region);
  const level = $('#event-level').value;
  if (level) params.set('min_level', level);
  const eventType = $('#event-type').value;
  if (eventType) params.set('event_type', eventType);
  const keyword = $('#event-q').value.trim();
  if (keyword) params.set('q', keyword);

  try {
    const data = await api(`/api/events?${params}`);
    state.eventTotal = data.total;
    renderEventRows(data.events);
    renderEventPager();
    state.loaded.events = true;
  } catch (error) {
    toast(`事件列表加载失败：${error.message}`);
  }
}

async function loadStatus() {
  try {
    const status = await api('/api/status');
    state.status = status;
    renderSummary(status.instruments);
    renderStatusCards(status);
    renderRuns(status.runs);
    renderDataStamp(status);
    if (status.active_run) {
      renderActiveJob(status.active_run);
      if (state.activeJobId !== status.active_run.id) startPolling(status.active_run.id);
    } else if (!state.activeJobId) {
      renderActiveJob(null);
    }
  } catch (error) {
    toast(`状态加载失败：${error.message}`);
  }
}

/* --------------------------------------------------------------- polling */

function stopPolling() {
  if (state.pollTimer) {
    window.clearInterval(state.pollTimer);
    state.pollTimer = null;
  }
}

async function pollJob(runId) {
  try {
    const job = await api(`/api/jobs/${runId}`);
    renderActiveJob(job);
    const terminal = ['succeeded', 'partial', 'failed', 'interrupted'].includes(job.status);
    if (terminal) {
      stopPolling();
      state.activeJobId = null;
      const kind = job.status === 'succeeded' ? 'ok' : job.status === 'partial' ? 'warn' : 'error';
      toast(`任务 #${job.id} ${statusLabel(job.status)}：${runResultText(job)}`, kind);
      state.loaded.prices = false;
      state.loaded.overlay = false;
      state.loaded.events = false;
      await loadStatus();
      if ($('#tab-prices').classList.contains('is-active')) {
        await loadPrices();
      }
      if ($('#tab-events').classList.contains('is-active')) {
        await loadOverlay();
        await loadEvents();
      }
    }
  } catch (error) {
    stopPolling();
    state.activeJobId = null;
    toast(`任务状态获取失败：${error.message}`);
  }
}

function startPolling(runId) {
  stopPolling();
  state.activeJobId = runId;
  pollJob(runId);
  state.pollTimer = window.setInterval(() => pollJob(runId), 1500);
}

/* ---------------------------------------------------------------- actions */

async function submitJob(payload) {
  if (state.activeJobId) {
    toast('已有采集任务在运行，请等待其结束', 'warn');
    return;
  }
  try {
    const result = await api('/api/collect', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    toast(`任务已启动：#${result.run_id}`, 'ok');
    await loadStatus();
    startPolling(result.run_id);
  } catch (error) {
    toast(`启动失败：${error.message}`);
  }
}

async function submitPrices(event) {
  event.preventDefault();
  const symbols = $$('#price-symbols input:checked').map((input) => input.value);
  if (!symbols.length) {
    toast('至少选择一个品种', 'warn');
    return;
  }
  const mode = $('input[name="price-mode"]:checked').value;
  const full = mode === 'full';
  if (full && !window.confirm('全量重采会重新拉取全部历史，耗时更长。继续？')) return;
  const payload = { kind: 'prices', symbols, full };
  const start = $('#collect-price-start').value;
  const end = $('#collect-price-end').value;
  if (start && end && start > end) {
    toast('起始日期不能晚于结束日期', 'warn');
    return;
  }
  if (start) payload.start = start;
  if (end) payload.end = end;
  await submitJob(payload);
}

async function submitEvents(event) {
  event.preventDefault();
  if (state.activeJobId) {
    toast('已有采集任务在运行，请等待其结束', 'warn');
    return;
  }
  const mode = $('#collect-events-mode').value;
  const days = Number.parseInt($('#collect-events-days').value, 10) || 30;
  const payload = { kind: 'events', days };
  if (mode === 'backfill') {
    if (days < 1 || days > 365) {
      toast('天数应在 1–365 之间', 'warn');
      return;
    }
    if (days > 31 && !window.confirm(`将补采最近 ${days} 天，需要下载并解析 ${days} 个 GDELT 日文件，可能需要数分钟。继续？`)) {
      return;
    }
    payload.start = daysAgoIso(days);
    payload.days = days;
  } else if (mode === 'range') {
    const start = $('#collect-events-start').value;
    const end = $('#collect-events-end').value;
    if (!start || !end) {
      toast('请选择完整的起止日期', 'warn');
      return;
    }
    if (start > end) {
      toast('起始日期不能晚于结束日期', 'warn');
      return;
    }
    const span = Math.round((new Date(end) - new Date(start)) / 86400000) + 1;
    if (span > 31 && !window.confirm(`区间约 ${span} 天，需要下载并解析对应数量的 GDELT 日文件。继续？`)) {
      return;
    }
    payload.start = start;
    payload.end = end;
    payload.days = span;
  }
  await submitJob(payload);
}

/* ---------------------------------------------------------------- events */

function switchTab(name, { load = true } = {}) {
  if (!['prices', 'events', 'data'].includes(name)) name = 'prices';
  $$('.tab').forEach((tab) => tab.classList.toggle('is-active', tab.dataset.tab === name));
  $$('.tab-panel').forEach((panel) => panel.classList.toggle('is-active', panel.id === `tab-${name}`));
  if (window.location.hash !== `#${name}`) {
    window.history.replaceState(null, '', `#${name}`);
  }
  window.setTimeout(() => {
    if (state.priceChart) state.priceChart.resize();
    if (state.overlayChart) state.overlayChart.resize();
  }, 40);
  if (!load) return;
  if (name === 'events') {
    if (!state.loaded.overlay) loadOverlay();
    if (!state.loaded.events) loadEvents();
  }
  if (name === 'data') loadStatus();
}

function bindEvents() {
  $$('.tab').forEach((tab) => {
    tab.addEventListener('click', () => switchTab(tab.dataset.tab));
  });

  $('#price-symbol').addEventListener('change', (event) => {
    state.priceSymbol = event.target.value;
    loadPrices();
  });

  $$('#price-range button').forEach((button) => {
    button.addEventListener('click', () => {
      state.priceDays = Number(button.dataset.days);
      state.priceCustom = null;
      $('#price-start').value = '';
      $('#price-end').value = '';
      $$('#price-range button').forEach((item) => item.classList.toggle('is-active', item === button));
      loadPrices();
    });
  });

  $('#price-apply').addEventListener('click', () => {
    const start = $('#price-start').value;
    const end = $('#price-end').value;
    if (start && end && start > end) {
      toast('起始日期不能晚于结束日期', 'warn');
      return;
    }
    state.priceCustom = start || end ? { start, end } : null;
    $$('#price-range button').forEach((item) => item.classList.remove('is-active'));
    loadPrices();
  });

  $('#overlay-symbol').addEventListener('change', (event) => {
    state.overlaySymbol = event.target.value;
    state.loaded.overlay = false;
    state.loaded.events = false;
    loadOverlay();
    loadEvents();
  });

  $$('#overlay-range button').forEach((button) => {
    button.addEventListener('click', () => {
      state.overlayDays = Number(button.dataset.days);
      state.overlayCustom = null;
      $('#overlay-start').value = '';
      $('#overlay-end').value = '';
      $$('#overlay-range button').forEach((item) => item.classList.toggle('is-active', item === button));
      loadOverlay();
    });
  });

  $('#overlay-apply').addEventListener('click', () => {
    const start = $('#overlay-start').value;
    const end = $('#overlay-end').value;
    if (start && end && start > end) {
      toast('起始日期不能晚于结束日期', 'warn');
      return;
    }
    state.overlayCustom = start || end ? { start, end } : null;
    $$('#overlay-range button').forEach((item) => item.classList.remove('is-active'));
    loadOverlay();
  });

  $('#overlay-level').addEventListener('change', (event) => {
    state.overlayLevel = Number(event.target.value);
    loadOverlay();
  });

  $('#event-apply').addEventListener('click', () => {
    state.eventOffset = 0;
    loadEvents();
  });
  $('#event-prev').addEventListener('click', () => {
    state.eventOffset = Math.max(0, state.eventOffset - state.eventLimit);
    loadEvents();
  });
  $('#event-next').addEventListener('click', () => {
    if (state.eventOffset + state.eventLimit < state.eventTotal) {
      state.eventOffset += state.eventLimit;
      loadEvents();
    }
  });
  $('#event-q').addEventListener('keydown', (event) => {
    if (event.key === 'Enter') {
      state.eventOffset = 0;
      loadEvents();
    }
  });

  $('#prices-form').addEventListener('submit', submitPrices);
  $('#events-form').addEventListener('submit', submitEvents);

  $('#collect-events-mode').addEventListener('change', (event) => {
    const mode = event.target.value;
    $('#collect-events-days-field').hidden = mode === 'range';
    $('#collect-events-range-field').hidden = mode !== 'range';
  });

  $('#status-refresh').addEventListener('click', () => loadStatus());

  window.addEventListener('resize', () => {
    window.clearTimeout(bindEvents.resizeTimer);
    bindEvents.resizeTimer = window.setTimeout(() => {
      if (state.priceChart) state.priceChart.resize();
      if (state.overlayChart) state.overlayChart.resize();
    }, 150);
  });

  window.addEventListener('hashchange', () => {
    switchTab(window.location.hash.replace('#', ''), { load: true });
  });
}

async function init() {
  try {
    const [meta, status] = await Promise.all([api('/api/meta'), api('/api/status')]);
    state.meta = meta;
    state.status = status;
    state.typeColor = Object.fromEntries(meta.event_types.map((item) => [item.name, item.color]));
    populateControls();
    renderSummary(status.instruments);
    renderStatusCards(status);
    renderRuns(status.runs);
    renderDataStamp(status);
    bindEvents();
    if (status.active_run) startPolling(status.active_run.id);
    const initialTab = (window.location.hash || '#prices').replace('#', '');
    switchTab(initialTab, { load: true });
    await loadPrices();
  } catch (error) {
    toast(`初始化失败：${error.message}`);
  }
}

document.addEventListener('DOMContentLoaded', init);
