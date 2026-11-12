import {renderCharts, renderFinancial} from './charts.js';
const $ = (selector, parent = document) => parent.querySelector(selector);
const $$ = (selector, parent = document) => [...parent.querySelectorAll(selector)];
function node(tag, text, cls) { const el = document.createElement(tag); if (text !== undefined) el.textContent = text; if (cls) el.className = cls; return el; }
function notice(text) { const el = $('#notice'); el.textContent = text; el.hidden = false; }
async function api(url, body) {
  const response = await fetch(url, body === undefined ? {} : {method: 'POST', headers: {'Content-Type': 'application/json', 'X-CSRF-Token': $('meta[name=csrf-token]').content}, body: JSON.stringify(body)});
  const value = await response.json();
  if (!response.ok) throw new Error(typeof value.detail === 'string' ? value.detail : 'Check the supplied values and try again.');
  return value;
}
function prefs(form) { const data = new FormData(form); return {preset: data.get('preset'), horizon: data.get('horizon'), depth: data.get('depth'), sections: data.getAll('sections')}; }
async function busy(button, work) { if (button.disabled) return; button.disabled = true; try { await work(); } catch (error) { notice(error.message); } finally { button.disabled = false; } }
const page = $('[data-page]');
if (page?.dataset.page === 'onboarding') {
  const {steps, unknown, state} = JSON.parse($('#onboarding-state').textContent);
  let step = state.step ?? 0, answers = state.answers ?? {}, saveChain = Promise.resolve();
  const save = (action = 'save') => {
    const snapshot = {answers: {...answers}, step, action, personalise: action !== 'skip'};
    // Preserve request ordering even when the user selects cards quickly.
    saveChain = saveChain.catch(() => {}).then(async () => { $('#save-status').textContent = 'Saving…'; await api('/api/onboarding', snapshot); $('#save-status').textContent = 'Progress saved on this computer'; });
    return saveChain;
  };
  function render(focus = false) {
    $$('#step-list li').forEach((li, index) => { li.classList.toggle('active', index === Math.min(step, 4)); if (index === Math.min(step,4)) li.setAttribute('aria-current','step'); else li.removeAttribute('aria-current'); });
    $('#step-count').textContent = step === 5 ? 'Your editable summary' : `Step ${step + 1} of 5`;
    $('#progress').value = Math.min(step + 1, 5); $('#back').disabled = step === 0;
    $('#next').textContent = step === 5 ? 'Start exploring →' : step === 4 ? 'Review my answers →' : 'Next step →';
    $('#step-heading').textContent = step === 5 ? 'A starting point, shaped by you.' : steps[step].title;
    $('#step-description').textContent = step === 5 ? 'Review your answers. These tailor explanations and examples; they do not choose investments for you.' : steps[step].description;
    const container = $('#questions'); container.replaceChildren();
    if (step === 5) {
      steps.forEach((s, index) => {
        const block = node('div'); block.append(node('h3', s.title)); const dl = node('dl', undefined, 'summary-list');
        s.fields.forEach(([key, label]) => { const row = node('div'); row.append(node('dt', label), node('dd', answers[key] ?? 'Not supplied')); dl.append(row); });
        const edit = node('button', 'Edit answers', 'text-button'); edit.type = 'button'; edit.addEventListener('click', () => { step = index; render(true); save().catch(e => notice(e.message)); }); block.append(dl, edit); container.append(block);
      });
    } else {
      steps[step].fields.forEach(([key, label, options]) => {
        const fieldset = node('fieldset'); fieldset.append(node('legend', label)); const grid = node('div', undefined, 'choice-grid');
        [...options, ...unknown].forEach(value => {
          const card = node('label', undefined, 'choice-card'), input = node('input'); input.type = 'radio'; input.name = key; input.value = value; input.checked = answers[key] === value;
          input.addEventListener('change', () => { answers[key] = value; save().catch(e => notice(e.message)); }); card.append(input, node('span', value)); grid.append(card);
        }); fieldset.append(grid); container.append(fieldset);
      });
    }
    if (focus) $('#step-heading').focus();
  }
  $('#questionnaire').addEventListener('submit', event => { event.preventDefault(); busy($('#next'), async () => { if (step === 5) { await save('complete'); location.href = '/'; } else { step++; render(true); await save(); } }); });
  $('#back').addEventListener('click', () => { if (step > 0) { step--; render(true); save().catch(e => notice(e.message)); } });
  $('#skip').addEventListener('click', () => busy($('#skip'), async () => { await save('skip'); location.href = '/'; })); render();
}
if (page?.dataset.page === 'workspace') {
  let searchRequest = 0;
  async function search() {
    const request = ++searchRequest, value = await api(`/api/search?q=${encodeURIComponent($('#search').value)}`);
    if (request !== searchRequest) return; const container = $('#search-results'); container.replaceChildren();
    if (!value.length) container.append(node('p','No match available. Try an investment ticker.', 'muted small'));
    value.forEach(result => { const a = node('a', `${result.name} · ${result.ticker}`); a.href = `/research/${encodeURIComponent(result.ticker)}`; container.append(a); });
  }
  let searchTimer; $('#search').addEventListener('input', () => { clearTimeout(searchTimer); searchTimer = setTimeout(() => search().catch(e => notice(e.message)), 220); });
  $('#search-form').addEventListener('submit', e => { e.preventDefault(); search().then(() => $('#search-results a')?.focus()).catch(e => notice(e.message)); });
  if ($('#charts')) {
    let chartRequest = 0;
    async function load(window) {
      const request = ++chartRequest; $('#chart-status').textContent = 'Loading daily market data…';
      $$('.range').forEach(button => button.setAttribute('aria-pressed', button.dataset.range === window ? 'true' : 'false'));
      try { const data = await api(`/api/charts/${encodeURIComponent(page.dataset.ticker)}?window=${window}`); if (request !== chartRequest) return; renderCharts($('#charts'), data, page.dataset.depth); $('#chart-status').textContent = ''; }
      catch (e) { if (request === chartRequest) { $('#chart-status').textContent = e.message; $('#charts').replaceChildren(); } }
    }
    $$('.range').forEach(button => button.addEventListener('click', () => load(button.dataset.range))); load('1M');
  }
  $('#financial-load')?.addEventListener('click', e => busy(e.currentTarget, async () => {
    $('#financial-context').textContent = 'Loading source context…';
    try { renderFinancial($('#financial-context'), await api(`/api/financial/${encodeURIComponent(page.dataset.ticker)}`)); }
    catch (error) { $('#financial-context').textContent = error.message; }
  }));
  $('#outlook-build')?.addEventListener('click', e => busy(e.currentTarget, async () => {
    await api(`/api/scenarios/${encodeURIComponent(page.dataset.ticker)}`, {});
    location.reload();
  }));
  $('#prepare-form')?.addEventListener('submit', e => { e.preventDefault(); const form = e.currentTarget; busy($('button[type=submit]', form), async () => { $('#prepare-status').textContent = 'Collecting and caching source evidence. This can take a little while…'; const result = await api(`/api/prepare/${encodeURIComponent(page.dataset.ticker)}`, prefs(form)); location.href = result.url; }); });
  $('#generate-form')?.addEventListener('submit', e => { e.preventDefault(); const form = e.currentTarget; busy($('button[type=submit]', form), async () => { const result = await api(`/api/generate/${form.dataset.preparation}`, {confirm_paid: $('[name=confirm_paid]', form).checked}); sessionStorage.setItem(`job:${page.dataset.ticker}`, result.job.id); followJob(result.job.id); }); });
  async function followJob(identity) {
    const status = $('#job-status'); if (!status) return;
    status.hidden = false;
    try { const job = await api(`/api/jobs/${identity}`); status.textContent = `${job.status}: ${job.message}`;
      if (job.status === 'completed') { const a = node('a', ' Open saved report →'); a.href = `/reports/${job.report_id}`; status.append(a); sessionStorage.removeItem(`job:${page.dataset.ticker}`); }
      else if (['queued', 'running'].includes(job.status)) setTimeout(() => followJob(identity), 1800);
    } catch (e) { status.textContent = e.message; }
  }
  const existingJob = $('#job-status')?.dataset.job ?? sessionStorage.getItem(`job:${page.dataset.ticker}`); if (existingJob) followJob(existingJob);
}
if (page?.dataset.page === 'profile') {
  let ticker;
  const labels = {already_owns_stock:'Already owns this investment', average_purchase_price:'Average purchase price', amount_invested:'Amount invested', current_position_value:'Current position value', intended_additional_investment:'Intended additional investment', intended_holding_period:'Intended holding period', risk_tolerance:'Explicit risk tolerance', maximum_acceptable_loss:'Maximum acceptable loss (include units)', maximum_portfolio_percentage:'Maximum portfolio percentage (0–100)', investment_objective:'Investment objective', reason_for_investing:'Reason for investing', position_description:'Position description', trading_preference:'Trading preference', investment_currency:'Investment currency'};
  async function loadProfile() {
    ticker = $('#profile-ticker').value.trim().toUpperCase(); const value = await api(`/api/profile/${encodeURIComponent(ticker)}`); const fields = $('#profile-fields'); fields.replaceChildren();
    Object.entries(labels).forEach(([key, label]) => {
      const wrapper = node('label', label); let input;
      if (['already_owns_stock','risk_tolerance'].includes(key)) { input = node('select'); const options = key === 'risk_tolerance' ? [['','Use shared default / unknown'],['low','Low'],['medium','Medium'],['high','High']] : [['','Unknown'],['true','Yes'],['false','No']]; options.forEach(([v,t]) => { const option=node('option',t); option.value=v; input.append(option); }); }
      else { input = node('input'); input.type = 'text'; input.maxLength = 1000; }
      input.name = key; input.value = value.explicit[key] === null ? '' : String(value.explicit[key]); wrapper.append(input); fields.append(wrapper);
    }); $('#ticker-profile-form').hidden = false; $('#effective-profile').textContent = JSON.stringify(value.effective, null, 2);
  }
  $('#ticker-profile-lookup').addEventListener('submit', e => { e.preventDefault(); busy($('button', e.currentTarget), loadProfile); });
  $('#ticker-profile-form').addEventListener('submit', e => { e.preventDefault(); const form = e.currentTarget; busy($('button[type=submit]', form), async () => { const fields = Object.fromEntries(new FormData(form)); Object.keys(fields).forEach(key => { if (fields[key] === '') fields[key] = null; }); if (fields.already_owns_stock !== null) fields.already_owns_stock = fields.already_owns_stock === 'true'; await api(`/api/profile/${encodeURIComponent(ticker)}`, fields); $('#profile-status').textContent = 'Saved. Prepare evidence again to use the updated context.'; await loadProfile(); }); });
  loadProfile().catch(e => notice(e.message));
}
if (page?.dataset.page === 'report') {
  const identity = page.dataset.report;
  $$('.note-form').forEach(form => form.addEventListener('submit', e => { e.preventDefault(); busy($('button[type=submit]', form), async () => { await api(`/api/reports/${identity}/notes`, {section: form.dataset.section, text: $('[name=text]', form).value, included: $('[name=included]', form).checked}); $('.note-status', form).textContent = 'Saved privately. Included-note changes require fresh evidence.'; }); }));
  function applyReading(value) {
    $('.reading-guide').hidden = value.depth === 'Concise'; $('#reading-horizon').textContent = `Reading emphasis: ${value.horizon ?? 'Use my profile'}. The saved assessment has not been regenerated.`;
    const matching = {Overview:/overview|summary|conclusion|takeaway/i, Developments:/event|development|news/i, 'Financial context':/financial|valuation|fundamental|commodity/i, Scenarios:/scenario|horizon|outlook/i, 'Next questions':/question|watch|next|action/i};
    $$('.report-section').forEach(section => {
      const selected = (value.sections ?? Object.keys(matching)).some(key => matching[key]?.test(section.dataset.title));
      const optional = Object.values(matching).some(pattern => pattern.test(section.dataset.title));
      const mandatory = /evidence|source|risk|limitation|uncertain|confidence|bear case|威胁|风险|证据|限制/i.test(section.textContent);
      section.hidden = optional && !selected && !mandatory;
      section.classList.toggle('reading-emphasis', selected);
    });
  }
  applyReading(JSON.parse($('#report-preferences').textContent));
  $('#reading-preferences').addEventListener('submit', e => { e.preventDefault(); const form = e.currentTarget; busy($('button[type=submit]', form), async () => { const value = prefs(form); await api(`/api/reports/${identity}/preferences`, value); applyReading(value); notice('Reading preferences saved. No paid request was made.'); }); });
}
