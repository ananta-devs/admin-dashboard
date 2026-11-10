/**
 * VIBE ECharts Server-Side SVG Rendering CLI Script
 *
 * Consumes declarative JSON payload on stdin and emits JSON array of SVG strings on stdout.
 * Operates purely without browser/DOM dependencies.
 */

const fs = require('fs');
const path = require('path');

// Resolve echarts: prefer local node_modules, fall back to frontend/node_modules if developing on host
let echarts;
try {
  echarts = require('echarts');
} catch (e) {
  const hostFrontendPath = path.resolve(__dirname, '../../../frontend/node_modules/echarts');
  if (fs.existsSync(hostFrontendPath)) {
    echarts = require(hostFrontendPath);
  } else {
    const backendPath = path.resolve(__dirname, '../../node_modules/echarts');
    if (fs.existsSync(backendPath)) {
      echarts = require(backendPath);
    } else {
      throw new Error('ECharts module could not be resolved. Please run npm install in renderers/echarts_ssr.');
    }
  }
}

/**
 * Format metric value using declarative format token without executable user callbacks.
 */
function formatValue(value, token) {
  if (value === null || value === undefined) return '';
  const num = Number(value);
  if (isNaN(num)) return String(value);

  switch (token) {
    case 'percentage':
      return `${(num * 100).toFixed(1)}%`;
    case 'currency_usd':
      return `$${num.toLocaleString('en-US', { minimumFractionDigits: 0, maximumFractionDigits: 2 })}`;
    case 'integer':
      return Math.round(num).toLocaleString('en-US');
    case 'decimal_2':
      return num.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    case 'number_compact':
      if (Math.abs(num) >= 1e9) return `${(num / 1e9).toFixed(1)}B`;
      if (Math.abs(num) >= 1e6) return `${(num / 1e6).toFixed(1)}M`;
      if (Math.abs(num) >= 1e3) return `${(num / 1e3).toFixed(1)}K`;
      return num.toLocaleString('en-US');
    default:
      return num.toLocaleString('en-US');
  }
}

/**
 * Build declarative ECharts option for any supported VIBE chart type.
 */
function buildChartOption(chartType, spec, data) {
  const theme = {
    colorPalette: spec.color_palette || [
      '#3b82f6', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6',
      '#ec4899', '#06b6d4', '#84cc16', '#6366f1', '#14b8a6'
    ],
    textColor: '#334155',
    axisColor: '#94a3b8',
    splitLineColor: '#e2e8f0',
    backgroundColor: 'transparent'
  };

  const rows = Array.isArray(data) ? data : [];
  const dimCol = (Array.isArray(spec.dimensions) && spec.dimensions[0]) ?
    (typeof spec.dimensions[0] === 'string' ? spec.dimensions[0] : spec.dimensions[0].column) : 'x';
  const metricCol = (Array.isArray(spec.metrics) && spec.metrics[0]) ?
    (typeof spec.metrics[0] === 'string' ? spec.metrics[0] : spec.metrics[0].column) : 'y';
  const metricToken = spec.formatter || 'number_compact';

  // Base options with ANIMATION STRICTLY DISABLED to prevent keyframe generation
  const baseOption = {
    animation: false,
    color: theme.colorPalette,
    backgroundColor: theme.backgroundColor,
    textStyle: {
      fontFamily: 'Inter, -apple-system, sans-serif'
    },
    grid: {
      top: spec.title ? 45 : 25,
      right: 25,
      bottom: 35,
      left: 55,
      containLabel: true
    }
  };

  if (spec.title) {
    baseOption.title = {
      text: spec.title,
      textStyle: {
        fontSize: 13,
        fontWeight: 600,
        color: theme.textColor
      },
      top: 5,
      left: 10
    };
  }

  const categories = rows.map(r => String(r[dimCol] !== undefined ? r[dimCol] : ''));
  const values = rows.map(r => Number(r[metricCol] || 0));

  switch (chartType) {
    case 'bar':
    case 'grouped_bar':
    case 'stacked_bar': {
      const isHorizontal = spec.horizontal || spec.chart_type === 'horizontal_bar' || chartType === 'horizontal_bar';
      const categoryAxis = {
        type: 'category',
        data: categories,
        axisLine: { lineStyle: { color: theme.axisColor } },
        axisLabel: { color: theme.textColor, fontSize: 10 }
      };
      const valueAxis = {
        type: 'value',
        splitLine: { lineStyle: { color: theme.splitLineColor, type: 'dashed' } },
        axisLabel: {
          color: theme.textColor,
          fontSize: 10
        }
      };

      return {
        ...baseOption,
        xAxis: isHorizontal ? valueAxis : categoryAxis,
        yAxis: isHorizontal ? categoryAxis : valueAxis,
        series: [{
          type: 'bar',
          data: values,
          itemStyle: { borderRadius: [3, 3, 0, 0] }
        }]
      };
    }

    case 'horizontal_bar': {
      return {
        ...baseOption,
        xAxis: {
          type: 'value',
          splitLine: { lineStyle: { color: theme.splitLineColor, type: 'dashed' } },
          axisLabel: { color: theme.textColor, fontSize: 10 }
        },
        yAxis: {
          type: 'category',
          data: categories,
          axisLine: { lineStyle: { color: theme.axisColor } },
          axisLabel: { color: theme.textColor, fontSize: 10 }
        },
        series: [{
          type: 'bar',
          data: values,
          itemStyle: { borderRadius: [0, 3, 3, 0] }
        }]
      };
    }

    case 'line':
    case 'multi_axis_line': {
      return {
        ...baseOption,
        xAxis: {
          type: 'category',
          data: categories,
          axisLine: { lineStyle: { color: theme.axisColor } },
          axisLabel: { color: theme.textColor, fontSize: 10 }
        },
        yAxis: {
          type: 'value',
          splitLine: { lineStyle: { color: theme.splitLineColor, type: 'dashed' } },
          axisLabel: { color: theme.textColor, fontSize: 10 }
        },
        series: [{
          type: 'line',
          data: values,
          smooth: false,
          symbolSize: 6,
          lineStyle: { width: 2.5 }
        }]
      };
    }

    case 'smooth_multi_axis_line': {
      return {
        ...baseOption,
        xAxis: {
          type: 'category',
          data: categories,
          axisLine: { lineStyle: { color: theme.axisColor } },
          axisLabel: { color: theme.textColor, fontSize: 10 }
        },
        yAxis: {
          type: 'value',
          splitLine: { lineStyle: { color: theme.splitLineColor, type: 'dashed' } },
          axisLabel: { color: theme.textColor, fontSize: 10 }
        },
        series: [{
          type: 'line',
          data: values,
          smooth: true,
          symbolSize: 6,
          lineStyle: { width: 2.5 }
        }]
      };
    }

    case 'stacked_area': {
      return {
        ...baseOption,
        xAxis: {
          type: 'category',
          data: categories,
          axisLine: { lineStyle: { color: theme.axisColor } },
          axisLabel: { color: theme.textColor, fontSize: 10 }
        },
        yAxis: {
          type: 'value',
          splitLine: { lineStyle: { color: theme.splitLineColor, type: 'dashed' } },
          axisLabel: { color: theme.textColor, fontSize: 10 }
        },
        series: [{
          type: 'line',
          data: values,
          areaStyle: { opacity: 0.35 },
          lineStyle: { width: 2 }
        }]
      };
    }

    case 'pie':
    case 'donut': {
      const isDonut = chartType === 'donut' || spec.pie_variant === 'donut' || spec.is_donut;
      const pieData = rows.map(r => ({
        name: String(r[dimCol] !== undefined ? r[dimCol] : ''),
        value: Number(r[metricCol] || 0)
      }));

      return {
        ...baseOption,
        series: [{
          type: 'pie',
          radius: isDonut ? ['40%', '70%'] : '70%',
          center: ['50%', '50%'],
          data: pieData,
          label: {
            show: true,
            fontSize: 10,
            color: theme.textColor
          }
        }]
      };
    }

    case 'scatter': {
      const scatterData = rows.map(r => [
        Number(r[dimCol] || 0),
        Number(r[metricCol] || 0)
      ]);

      return {
        ...baseOption,
        xAxis: {
          type: 'value',
          splitLine: { lineStyle: { color: theme.splitLineColor, type: 'dashed' } }
        },
        yAxis: {
          type: 'value',
          splitLine: { lineStyle: { color: theme.splitLineColor, type: 'dashed' } }
        },
        series: [{
          type: 'scatter',
          data: scatterData,
          symbolSize: 8
        }]
      };
    }

    case 'heatmap': {
      return {
        ...baseOption,
        xAxis: {
          type: 'category',
          data: categories,
          splitArea: { show: true }
        },
        yAxis: {
          type: 'category',
          data: ['Metric'],
          splitArea: { show: true }
        },
        visualMap: {
          min: 0,
          max: Math.max(...values, 100),
          calculable: false,
          show: false,
          inRange: { color: ['#eff6ff', '#3b82f6', '#1d4ed8'] }
        },
        series: [{
          type: 'heatmap',
          data: values.map((v, i) => [i, 0, v]),
          label: { show: true, fontSize: 9 }
        }]
      };
    }

    default:
      throw new Error(`Unsupported chart type: ${chartType}`);
  }
}

async function main() {
  const chunks = [];
  for await (const chunk of process.stdin) {
    chunks.push(chunk);
  }
  const inputStr = Buffer.concat(chunks).toString('utf-8');
  if (!inputStr.trim()) {
    console.error('Empty input payload received on stdin');
    process.exit(1);
  }

  const payload = JSON.parse(inputStr);
  const charts = payload.charts || [];
  const results = [];

  for (const item of charts) {
    try {
      const width = item.width || 680;
      const height = item.height || 320;
      const option = buildChartOption(item.chart_type, item.spec || {}, item.data || []);

      const chart = echarts.init(null, null, {
        renderer: 'svg',
        ssr: true,
        width: width,
        height: height
      });

      chart.setOption(option);
      const svg = chart.renderToSVGString();
      chart.dispose();

      results.push({
        section_id: item.section_id,
        svg: svg,
        error: null
      });
    } catch (err) {
      results.push({
        section_id: item.section_id,
        svg: null,
        error: String(err.message || err)
      });
    }
  }

  process.stdout.write(JSON.stringify({ results }));
}

main().catch(err => {
  console.error('Fatal SSR error:', err);
  process.exit(1);
});
