const NS = 'http://www.w3.org/2000/svg';
const main = document.querySelector('#main');
const navigator = document.querySelector('#company-navigator');
const list = document.querySelector('#company-list');
const scroll = document.querySelector('#company-list-scroll');
const search = document.querySelector('#company-search');
const filter = document.querySelector('#company-filter');
const more = document.querySelector('#company-load-more');
const directoryStatus = document.querySelector('#directory-status');
const picker = document.querySelector('#company-picker');
const mobile = window.matchMedia('(max-width: 820px)');
let selected = main.dataset.securityId, selectionToken = 0, reportAbort;
let directoryToken = 0, directoryAbort, cursor = null, directoryLoading = false;
let pollTimer, activeJob = null, pollDelay = 2000;
const preferences = {
  get(key, fallback) { try { return JSON.parse(localStorage.getItem(key)) ?? fallback; } catch { return fallback; } },
  set(key, value) { try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* storage can be disabled */ } }
};
let saved = preferences.get('equity-lens-saved', []);
if (!Array.isArray(saved)) saved = [];
saved = saved.filter(s => typeof s === 'string').slice(0, 200);

function highlight() {
  for (const row of list.querySelectorAll('[data-security-id]')) {
    if (row.dataset.securityId === selected) row.setAttribute('aria-current', 'true');
    else row.removeAttribute('aria-current');
  }
}
function closePicker() {
  navigator.classList.remove('open');
  navigator.removeAttribute('role'); navigator.removeAttribute('aria-modal');
  picker.setAttribute('aria-expanded', 'false');
  for (const el of [main, document.querySelector('.topbar'), document.querySelector('footer')]) el.inert = false;
}
picker.addEventListener('click', () => {
  navigator.classList.add('open'); navigator.setAttribute('role', 'dialog'); navigator.setAttribute('aria-modal', 'true');
  picker.setAttribute('aria-expanded', 'true');
  for (const el of [main, document.querySelector('.topbar'), document.querySelector('footer')]) el.inert = true;
  search.focus();
});
document.querySelector('#company-picker-close').addEventListener('click', () => { closePicker(); picker.focus(); });
mobile.addEventListener('change', () => closePicker());
document.addEventListener('keydown', event => {
  if (!navigator.classList.contains('open')) return;
  if (event.key === 'Escape') { closePicker(); picker.focus(); }
  if (event.key === 'Tab') {
    const controls = [...navigator.querySelectorAll('button:not([disabled]):not([hidden]),input,select,a')].filter(e => e.offsetParent);
    const first = controls[0], last = controls.at(-1);
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  }
});

async function loadDirectory(append = false) {
  if (append && (directoryLoading || !cursor)) return;
  if (!append) { directoryAbort?.abort(); cursor = null; }
  directoryAbort = new AbortController();
  const token = ++directoryToken;
  directoryLoading = true; more.disabled = true;
  directoryStatus.textContent = append ? 'Loading more companies…' : 'Searching local directory…';
  const params = new URLSearchParams({q: search.value, filter: filter.value, limit: '50'});
  if (filter.value === 'saved') params.set('saved', saved.join(','));
  if (append) params.set('cursor', cursor);
  try {
    const response = await fetch(`/api/securities?${params}`, {signal: directoryAbort.signal});
    if (!response.ok) throw new Error();
    const data = await response.json();
    if (token !== directoryToken) return;
    const fragment = document.createDocumentFragment();
    for (const item of data.items) {
      const row = document.createElement('a');
      row.className = 'company-row'; row.dataset.securityId = item.security_id;
      row.href = `/company?security_id=${encodeURIComponent(item.security_id)}`;
      for (const [tag, text] of [['strong', item.ticker], ['span', item.name], ['small', `${item.exchange}${item.share_class ? ` · ${item.share_class}` : ''} · ${item.availability}`]]) {
        const child = document.createElement(tag); child.textContent = text; row.append(child);
      }
      fragment.append(row);
    }
    if (!append) { list.replaceChildren(); scroll.scrollTop = 0; }
    list.append(fragment); highlight(); cursor = data.next_cursor;
    more.hidden = !cursor;
    directoryStatus.textContent = list.children.length ? `${list.children.length} shown · identity does not imply analysis coverage` : 'No companies match your search.';
    document.querySelector('#directory-counts').textContent = `${data.counts.directory.toLocaleString()} directory · ${data.counts.analysed} analysed · ${data.counts.template_supported} template supported`;
    document.querySelector('#directory-date').textContent = data.imported_at?.slice(0, 10) || 'Unavailable';
    document.querySelector('#directory-retry').hidden = true;
  } catch (error) {
    if (token === directoryToken && error.name !== 'AbortError') {
      directoryStatus.textContent = list.children.length ? 'Directory unavailable. Showing the previously loaded list; it may be stale.' : 'Directory unavailable. Retry to load companies.';
      document.querySelector('#directory-retry').hidden = false;
    }
  } finally { if (token === directoryToken) { directoryLoading = false; more.disabled = false; } }
}
let debounce;
search.addEventListener('input', () => { clearTimeout(debounce); debounce = setTimeout(() => loadDirectory(), 180); });
filter.addEventListener('change', () => loadDirectory());
more.addEventListener('click', () => loadDirectory(true));
document.querySelector('#directory-retry').addEventListener('click', () => loadDirectory());
new IntersectionObserver(entries => { if (entries.some(e => e.isIntersecting)) loadDirectory(true); }, {root: scroll}).observe(document.querySelector('#company-list-sentinel'));
list.addEventListener('click', event => {
  const row = event.target.closest('a[data-security-id]');
  if (!row || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
  event.preventDefault(); closePicker(); selectCompany(row.dataset.securityId, true);
  if (mobile.matches) main.focus();
});
list.addEventListener('keydown', event => {
  const rows = [...list.querySelectorAll('a')], index = rows.indexOf(document.activeElement);
  if (index < 0) return;
  if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
    event.preventDefault(); rows[Math.max(0, Math.min(rows.length - 1, index + (event.key === 'ArrowDown' ? 1 : -1)))].focus();
  }
});

async function selectCompany(sid, push = false) {
  reportAbort?.abort(); chartAbort?.abort(); ++chartRequest;
  clearTimeout(pollTimer); activeJob = null;
  const token = ++selectionToken;
  selected = sid; main.dataset.securityId = sid; highlight();
  if (push) history.pushState({security_id: sid}, '', `/company?security_id=${encodeURIComponent(sid)}`);
  const loading = document.createElement('section'); loading.className = 'empty-state company-skeleton';
  loading.setAttribute('role', 'status'); loading.textContent = 'Loading selected company’s stored report…';
  main.replaceChildren(loading); main.setAttribute('aria-busy', 'true');
  reportAbort = new AbortController();
  try {
    const response = await fetch(`/api/securities/${encodeURIComponent(sid)}/report`, {signal: reportAbort.signal});
    const data = await response.json();
    if (token !== selectionToken || sid !== selected) return;
    if (!response.ok) throw new Error(data.detail || 'Stored report unavailable.');
    if (data.security_id !== selected) throw new Error('Report identity mismatch. Please retry.');
    main.innerHTML = data.html;
    preferences.set('equity-lens-last', sid);
    document.title = `${main.querySelector('h1')?.textContent || 'Company'} · Company analysis · Fieldnotes`;
    updateSave(); renderChart(); watchJob(data.job);
  } catch (error) {
    if (token === selectionToken && error.name !== 'AbortError') {
      const state = document.createElement('section'); state.className = 'empty-state';
      const h = document.createElement('h1'); h.textContent = 'Company could not be loaded';
      const p = document.createElement('p'); p.textContent = error.message;
      const retry = document.createElement('button'); retry.textContent = 'Retry selected company';
      retry.addEventListener('click', () => selectCompany(sid)); state.append(h, p, retry); main.replaceChildren(state);
    }
  } finally { if (token === selectionToken) main.removeAttribute('aria-busy'); }
}
window.addEventListener('popstate', () => selectCompany(new URL(location.href).searchParams.get('security_id') || 'sec-0001045810-nvda'));
function updateSave() {
  const button = main.querySelector('[data-save-company]');
  if (button) { button.setAttribute('aria-pressed', String(saved.includes(selected))); button.textContent = saved.includes(selected) ? 'Saved on this device' : 'Save on this device'; }
}
main.addEventListener('change', event => { if (event.target.id === 'company-range') renderChart(); });
main.addEventListener('click', async event => {
  if (event.target.closest('[data-save-company]')) {
    saved = saved.includes(selected) ? saved.filter(s => s !== selected) : [...saved, selected].slice(-200);
    preferences.set('equity-lens-saved', saved); updateSave(); if (filter.value === 'saved') loadDirectory();
  }
  const button = event.target.closest('[data-company-action]');
  if (!button) return;
  const sid = selected, token = selectionToken; button.disabled = true;
  const status = main.querySelector('#company-job-status');
  try {
    const response = await fetch(`/api/securities/${encodeURIComponent(sid)}/${button.dataset.companyAction}`, {method: 'POST', headers: {'X-CSRF-Token': document.querySelector('meta[name="csrf-token"]').content}});
    const job = await response.json();
    if (token !== selectionToken || sid !== selected) return;
    if (!response.ok) throw new Error(job.detail || 'Preparation could not be queued.');
    if (job.security_id !== sid) throw new Error('Preparation identity mismatch.');
    status.textContent = `${job.state} · The local company worker processes queued jobs.`;
    watchJob(job);
  } catch (error) { if (token === selectionToken) status.textContent = error.message; }
  finally { if (token === selectionToken) button.disabled = false; }
});
function watchJob(job) {
  clearTimeout(pollTimer);
  activeJob = job;
  if (!job || !['queued', 'running', 'waiting'].includes(job.state)) return;
  pollDelay = 2000;
  schedulePoll();
}
function schedulePoll() {
  if (document.hidden || !activeJob) return;
  pollTimer = setTimeout(pollJob, pollDelay);
}
async function pollJob() {
  const job = activeJob, token = selectionToken;
  if (!job || document.hidden) return;
  try {
    const response = await fetch(`/api/analysis-jobs/${encodeURIComponent(job.job_id)}`);
    if (!response.ok) throw new Error();
    const next = await response.json();
    if (next.security_id !== selected && !['queued', 'running', 'waiting'].includes(next.state)) updateRowBadge(next.security_id, next.state);
    if (token !== selectionToken || next.security_id !== selected) return;
    const status = main.querySelector('#company-job-status');
    if (status) status.textContent = `${next.state}${next.safe_error ? ` · ${next.safe_error}` : ''}`;
    activeJob = next;
    if (!['queued', 'running', 'waiting'].includes(next.state)) {
      activeJob = null; await selectCompany(selected); updateRowBadge(next.security_id, next.state);
      fetch('/api/securities?limit=1').then(r => r.json()).then(data => {
        if (data.counts) document.querySelector('#directory-counts').textContent = `${data.counts.directory.toLocaleString()} directory · ${data.counts.analysed} analysed · ${data.counts.template_supported} template supported`;
      }).catch(() => {});
      return;
    }
  } catch { /* Transient polling failures retain the last good report. */ }
  if (token === selectionToken) { pollDelay = Math.min(pollDelay * 1.5, 30000); schedulePoll(); }
}
function updateRowBadge(sid, state) {
  const row = [...list.querySelectorAll('a')].find(row => row.dataset.securityId === sid);
  if (row) {
    const badge = row.querySelector('small'), exchange = badge.textContent.split(' · ')[0];
    badge.textContent = `${exchange} · ${state === 'success' ? 'Stored analysis' : state}`;
  }
}
document.addEventListener('visibilitychange', () => { clearTimeout(pollTimer); if (!document.hidden) schedulePoll(); });

function svgElement(tag, attrs, text) {
  const element = document.createElementNS(NS, tag);
  Object.entries(attrs).forEach(([key, value]) => element.setAttribute(key, value));
  if (text !== undefined) element.textContent = text;
  return element;
}
let chartRequest = 0, chartAbort;
async function renderChart() {
  chartAbort?.abort();
  chartAbort = new AbortController();
  const sequence = ++chartRequest, sid = selected;
  const chart = main.querySelector('#company-chart'), range = main.querySelector('#company-range');
  const status = main.querySelector('#company-chart-status');
  if (!chart || !range) return;
  status.textContent = 'Loading stored prices…';
  try {
    const response = await fetch(`/api/securities/${encodeURIComponent(sid)}/prices?range=${encodeURIComponent(range.value)}`, {signal: chartAbort.signal});
    if (!response.ok) throw new Error('Stored prices could not be loaded.');
    const data = await response.json();
    if (sequence !== chartRequest || sid !== selected || data.security_id !== sid) return;
    chart.replaceChildren();
    if (data.rows.length < 2) { status.textContent = 'More stored sessions are needed for the chart.'; return; }
    const keys = ['company', 'market', 'sector'].filter(key => data.rows.every(row => row[key] !== null && row[key] > 0));
    const lines = keys.map(key => data.rows.map(row => 100 * row[key] / data.rows[0][key]));
    const minimum = Math.min(...lines.flat()), maximum = Math.max(...lines.flat());
    const padding = Math.max((maximum - minimum) * .08, 1);
    const low = minimum - padding, high = maximum + padding;
    const svg = svgElement('svg', {viewBox: '0 0 900 300', role: 'img', 'aria-label': 'Adjusted prices rebased to 100'});
    for (let i = 0; i < 5; i++) {
      const value = low + (high - low) * i / 4, y = 260 - 225 * i / 4;
      svg.append(svgElement('line', {x1: 55, x2: 875, y1: y, y2: y, class: 'company-gridline'}));
      svg.append(svgElement('text', {x: 4, y: y + 4, class: 'company-chart-label'}, value.toFixed(1)));
    }
    lines.forEach((values, index) => {
      const points = values.map((v, i) => `${55 + 820 * i / (values.length - 1)},${260 - 225 * (v - low) / (high - low)}`).join(' ');
      svg.append(svgElement('polyline', {points, fill: 'none', class: `company-line company-line-${index}`}));
      svg.append(svgElement('text', {x: 55 + index * 220, y: 20, class: `company-legend company-line-${index}`}, `${data.company[keys[index] === 'company' ? 'ticker' : keys[index]]}  ${values.at(-1).toFixed(1)}`));
    });
    svg.append(svgElement('text', {x: 55, y: 290, class: 'company-chart-label'}, data.rows[0].date));
    svg.append(svgElement('text', {x: 790, y: 290, class: 'company-chart-label'}, data.rows.at(-1).date));
    chart.append(svg);
    status.textContent = `${data.rows.length} matching sessions · ${data.data_status} · ${data.adjustments_verified ? 'adjustment review recorded' : 'adjustments unverified'}`;
  } catch (error) { if (sequence === chartRequest && sid === selected && error.name !== 'AbortError') status.textContent = error.message; }
}

loadDirectory(); updateSave(); renderChart();
const last = preferences.get('equity-lens-last', null);
if (document.querySelector('.company-layout').dataset.explicitSelection === 'false' && last && last !== selected) {
  fetch(`/api/securities/${encodeURIComponent(last)}`).then(r => r.ok ? r.json() : null).then(data => {
    if (selectionToken !== 0) return;
    if (data?.identity?.status === 'active' && data.identity.instrument_type !== 'benchmark') {
      history.replaceState({security_id: last}, '', `/company?security_id=${encodeURIComponent(last)}`); selectCompany(last);
    }
  }).catch(() => {});
} else {
  if (main.querySelector('article')) preferences.set('equity-lens-last', selected);
  const initial = main.querySelector('#company-job-status');
  if (initial) fetch(`/api/securities/${encodeURIComponent(selected)}/analysis/latest`).then(r => r.json()).then(data => { if (selectionToken === 0 && data.security_id === selected) watchJob(data.job); }).catch(() => {});
}
