// Locally served SVG charts. The accompanying tables carry the same data for assistive tools.
const NS = 'http://www.w3.org/2000/svg';
function el(name, attrs = {}, text) {
  const node = document.createElementNS(NS, name);
  Object.entries(attrs).forEach(([key, val]) => node.setAttribute(key, val));
  if (text !== undefined) node.textContent = text;
  return node;
}
function element(name, text, cls) {
  const node = document.createElement(name);
  if (text !== undefined) node.textContent = text;
  if (cls) node.className = cls;
  return node;
}
function svgChart(rows, keys, title, volume = false) {
  const compact = window.matchMedia('(max-width: 720px)').matches;
  const width = compact ? 350 : 800;
  const svg = el('svg', {viewBox: `0 0 ${width} 230`, role: 'img', class: 'chart'});
  svg.append(el('title', {}, title));
  const values = rows.flatMap(row => keys.map(key => row[key])).filter(Number.isFinite);
  if (!rows.length || !values.length) { svg.append(el('text', {x: 50, y: 100}, 'No aligned data available')); return svg; }
  let low = volume ? 0 : Math.min(...values), high = Math.max(...values);
  const span = high - low || Math.abs(high) * .05 || 1;
  if (!volume) { low -= span * .08; high += span * .08; }
  const x = i => 60 + i / Math.max(rows.length - 1, 1) * (width - 85);
  const y = v => 193 - (v - low) / (high - low || 1) * 168;
  for (let i = 0; i < 4; i++) {
    const v = low + (high - low) * i / 3, yy = y(v);
    svg.append(el('line', {x1: 60, x2: width - 25, y1: yy, y2: yy, class: 'grid'}));
    svg.append(el('text', {x: 49, y: yy + 4, 'text-anchor': 'end'}, volume ? Intl.NumberFormat('en', {notation: 'compact'}).format(v) : v.toFixed(1)));
  }
  keys.forEach((key, series) => {
    if (volume) {
      rows.forEach((row, i) => {
        if (row[key] === null) return;
        const bar = el('rect', {x: x(i) - 2, y: y(row[key]), width: Math.max(.6, (width - 110) / rows.length), height: Math.max(0, y(0) - y(row[key])), fill: '#829b70'});
        bar.append(el('title', {}, `${row.date}: ${Number(row[key]).toLocaleString('en')} shares or units`)); svg.append(bar);
      });
    } else {
      const path = rows.map((row, i) => `${i ? 'L' : 'M'}${x(i)},${y(row[key])}`).join(' ');
      svg.append(el('path', {d: path, class: series ? 'benchmark-line' : 'main-line'}));
    }
  });
  const ticks = compact ? [0, rows.length - 1] : [0, Math.floor((rows.length - 1) / 2), rows.length - 1];
  ticks.forEach((i, idx) => svg.append(el('text', {x: x(i), y: 222, 'text-anchor': idx === 0 ? 'start' : idx === ticks.length - 1 ? 'end' : 'middle'}, rows[i].date)));
  return svg;
}
function table(rows, columns) {
  const details = element('details'); details.append(element('summary', 'View accessible data table'));
  const scroll = element('div', undefined, 'table-scroll'), table = element('table');
  const caption = element('caption', 'Daily observations, ordered by trading date'); caption.className = 'sr-only'; table.append(caption);
  const head = element('thead'), tr = element('tr');
  columns.forEach(([, label]) => { const th = element('th', label); th.scope = 'col'; tr.append(th); }); head.append(tr); table.append(head);
  const body = element('tbody');
  rows.forEach(row => { const tr = element('tr'); columns.forEach(([key]) => tr.append(element('td', typeof row[key] === 'number' ? row[key].toLocaleString('en', {maximumFractionDigits: 3}) : row[key] ?? 'Unavailable'))); body.append(tr); });
  table.append(body); scroll.append(table); details.append(scroll); return details;
}
export function renderCharts(container, data, depth) {
  container.replaceChildren();
  const first = data.rows[0], last = data.rows.at(-1);
  const change = first && last ? (last.close / first.close - 1) * 100 : null;
  const panels = [
    {title: `Daily adjusted price · ${data.currency}`, rows: data.rows, keys: ['close'],
     takeaway: change === null ? 'Price data is unavailable.' : `The adjusted price changed ${change >= 0 ? '+' : ''}${change.toFixed(2)}% between ${first.date} and ${last.date}. This describes the past; it does not establish why prices moved.`,
     explanation: 'Each point is an adjusted daily closing price. Adjustments account for splits and distributions. The range is measured between the first and last available trading observations; markets do not trade every day.',
     columns: [['date', 'Date'], ['close', `Adjusted close (${data.currency})`]]},
    {title: 'Relative performance · change from a common start (%)', rows: data.comparison, keys: ['return', 'benchmark_return'],
     takeaway: data.comparison.length ? `Across ${data.comparison.length} common trading dates, ${data.ticker} changed ${data.comparison.at(-1).return.toFixed(2)}% and ${data.benchmark} changed ${data.comparison.at(-1).benchmark_return.toFixed(2)}%. ${data.context}` : data.comparison_status,
     explanation: 'Both series begin at 0% on their first common date. Only dates available for both series are compared. Values are not forward-filled. These are returns in their own quote units, not currency-adjusted investor returns.',
     columns: [['date', 'Common date'], ['return', `${data.ticker} (%)`], ['benchmark_return', `${data.benchmark} (%)`]]},
    {title: 'Trading volume · shares or units traded', rows: data.rows, keys: ['volume'], volume: true,
     takeaway: last?.volume === null ? 'Volume is unavailable for the latest observation.' : `The latest daily volume was ${last?.volume.toLocaleString('en')} shares or units. Volume measures activity and does not reveal whether an investment is attractive.`,
     explanation: 'Each bar shows how many shares or units traded in a session. Compare unusual bars with recent trading activity. A volume spike can have many causes and is not a buy or sell signal. Missing values are marked unavailable.',
     columns: [['date', 'Date'], ['volume', 'Volume (shares or units)']]}
  ];
  panels.forEach(panel => {
    const section = element('section', undefined, 'chart-panel'); section.append(element('h3', panel.title));
    section.append(element('p', `${data.source} · Last observation ${last?.date ?? 'Unavailable'} · Retrieved ${data.retrieved_at}`, 'chart-meta'));
    section.append(svgChart(panel.rows, panel.keys, panel.title, panel.volume));
    if (panel.keys.length > 1) { const legend = element('div', undefined, 'chart-legend'); legend.append(element('span', data.ticker), element('span', `${data.benchmark} · ${data.benchmark_currency}`)); section.append(legend); }
    section.append(element('p', panel.takeaway, 'chart-takeaway'));
    section.append(element('p', `${data.adjustment} Quote unit: ${data.currency}.`, 'chart-meta'));
    const details = element('details'); details.open = depth === 'Step by step'; details.append(element('summary', 'How to read this'), element('p', panel.explanation));
    section.append(details, table(panel.rows, panel.columns)); container.append(section);
  });
}

export function renderFinancial(container, data) {
  container.replaceChildren();
  if (data.kind === 'unavailable') { container.append(element('p', data.message)); return; }
  const context = data.context;
  if (data.kind === 'commodity') {
    const product = context.product;
    container.append(element('h3', product.product_name));
    container.append(element('p', `${product.structure}. ${product.replication_method}. Benchmark: ${product.benchmark}.`));
    container.append(element('p', `Management fee: ${product.management_fee_percent}% annually. Trading currency: ${product.trading_currency}. Base currency: ${product.base_currency}. Currency hedged: ${product.currency_hedged ? 'Yes' : 'No'}.`));
    container.append(element('p', `${context.curve_note} ${product.counterparty_note}`));
    container.append(element('p', `Product source: ${product.source} · Source date: ${product.source_as_of}. Futures observation: ${context.as_of}. ${context.data_quality}`, 'chart-meta'));
    container.append(table(context.futures_curve, [['contract','Contract'],['price','Price'],['currency','Currency / units'],['as_of','Timestamp'],['status','Availability']]));
    const how = element('details'); how.append(element('summary','How to read this'),element('p','An ETC is a security providing commodity exposure. Futures contracts, roll methodology, fees and exchange rates can make its return differ from the spot commodity. A single futures point cannot establish the shape of the curve or roll yield.')); container.append(how); return;
  }
  container.append(element('p', `${context.source} · ${context.overall_quality}`, 'chart-meta'));
  let available = 0;
  ['revenue growth', 'operating margin', 'P/E', 'price-to-sales'].forEach(name => {
    const own = context.metrics.find(m => m.name === name), peer = context.peer_metrics.find(m => m.name === `${data.peer} ${name}`);
    if (!own || !peer || own.value === null || peer.value === null) return;
    const percent = own.unit === 'ratio', factor = percent ? 100 : 1;
    const a = Number(own.value) * factor, b = Number(peer.value) * factor;
    if (!Number.isFinite(a) || !Number.isFinite(b)) return;
    available++;
    const section = element('section', undefined, 'chart-panel'); section.append(element('h3', `${name} · ${percent ? '%' : 'multiple'}`));
    const width = window.matchMedia('(max-width: 720px)').matches ? 350 : 800;
    const svg = el('svg', {viewBox:`0 0 ${width} 135`, role:'img', class:'chart'}); svg.append(el('title',{},`${context.ticker} and ${data.peer}: ${name}`));
    const low = Math.min(0,a,b), high = Math.max(0,a,b), span=high-low||1, x=v=>80+(v-low)/span*(width-150), zero=x(0);
    [[context.ticker,a],[data.peer,b]].forEach(([label,value],i)=>{const y=25+i*50;svg.append(el('text',{x:10,y:y+17},label),el('rect',{x:Math.min(zero,x(value)),y,width:Math.max(1,Math.abs(x(value)-zero)),height:25,fill:i?'#c6a35b':'#2d624d'}),el('text',{x:width-60,y:y+17},value.toFixed(2)));}); section.append(svg);
    section.append(element('p', `${name} compares a single reported measure. Differences in periods, definitions or accounting can limit comparability; this does not establish which investment is better.`, 'chart-takeaway'));
    section.append(element('p', `${own.note} ${peer.note} Retrieved: ${own.as_of} / ${peer.as_of}. Reporting periods: ${own.reporting_period ?? 'not verified'} / ${peer.reporting_period ?? 'not verified'}. Currency: ${own.currency ?? 'unknown'} / ${peer.currency ?? 'unknown'}.`, 'chart-meta'));
    const how=element('details');how.append(element('summary','How to read this'),element('p',percent?'A percentage describes a growth rate or share of revenue. Compare both companies only after checking that periods and definitions match.':'A valuation multiple relates market value or price to a financial measure. A higher multiple may reflect different growth expectations or risks; it does not by itself prove overvaluation.'));section.append(how);
    section.append(table([{company:context.ticker,value:a},{company:data.peer,value:b}],[['company','Company'],['value',percent?'Percent':'Multiple']]));container.append(section);
  });
  if (!available) container.append(element('p','Comparable peer metrics are unavailable. No values were estimated.'));
}
