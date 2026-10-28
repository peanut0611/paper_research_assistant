const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

class Element {
  constructor(tag = 'div') { this.tag = tag; this.children = []; this.listeners = {}; this.textContent = ''; this.value = ''; this.checked = false;
    this.disabled = false; this.hidden = false; this.classList = {toggle() {}}; }
  set innerHTML(_) { throw new Error('Source text must never become HTML'); }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children = [...nodes]; }
  setAttribute() {}
  addEventListener(name, callback) { this.listeners[name] = callback; }
  click() { return this.listeners.click?.(); }
  requestSubmit() { return this.listeners.submit({preventDefault() {}}); }
}
const texts = node => [node.textContent, ...node.children.flatMap(texts)];
const id = 'web-20261024T000000-aaaaaaaaaaaa';
const settings = {topic: 'retrieval augmented generation', start_date: '2023-01-01', end_date: '2024-01-01',
  questions: ['RAGTruth 如何构建和标注幻觉数据？'], source_mode: 'cached_example', target_papers: 1, max_cost_usd: .1};
const status = {key_ready: true, cached_example_ready: true};
const reply = data => ({ok: true, json: async () => data});
function snapshot(finished = false) {
  return {job: {task_id: id, status: finished ? 'finished' : 'running', request: settings, created_at: new Date().toISOString(), stop_requested: false},
    state: {steps: finished ? 6 : 1, usage: {tokens: 130, cost_usd: .001}, tool_limits: {downloaded_papers: 1},
      pending: finished ? null : {phase: 'decision'}, candidates: [{arxiv_id: '2401.00396', version: 2, title: '<img onerror=alert(1)>', status: 'fulltext_ready', page_count: 16}],
      selections: {'2401.00396': {selected: true, reason: '<script>reason</script>'}},
      stop_reason: finished ? 'sufficient_evidence' : null, gaps: [], evidence_answer: finished ? {answer: '<script>answer</script> [E1]',
        citations: [{evidence_id: 'E1', quote: '<script>quote</script>', source: {title: 'RAGTruth'}, sources: [{page_number: 1}]}]} : null},
    events: [{event_id: 1, tool: 'search_literature', purpose: '<script>purpose</script>', status: 'completed', success: true, duration_ms: 10}]};
}
async function setup(fetch, stored = null) {
  const html = fs.readFileSync(path.join(__dirname, '../../src/paper_assistant/web/static/index.html'), 'utf8');
  const ids = [...html.matchAll(/id="([^"]+)"/g)].map(match => match[1]);
  const nodes = Object.fromEntries(ids.map(id => [id, new Element()]));
  nodes['research-source'].value = 'online'; nodes['research-papers'].value = '1';
  const timers = new Map(); let sequence = 0;
  const storage = new Map(stored ? [['paper-assistant-research-job', stored]] : []);
  const context = vm.createContext({document: {getElementById: id => nodes[id], createElement: tag => new Element(tag)}, fetch,
    localStorage: {getItem: key => storage.get(key), setItem: (key, value) => storage.set(key, value)},
    setTimeout: callback => { timers.set(++sequence, callback); return sequence; }, clearTimeout: id => timers.delete(id), URL, Blob, TypeError});
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../../src/paper_assistant/web/static/research.js'), 'utf8'), context);
  await new Promise(resolve => setImmediate(resolve));
  return {nodes, storage, async tick() { const [key, callback] = timers.entries().next().value; timers.delete(key); await callback(); }};
}

test('load checks research readiness without calling models', async () => {
  const calls = [];
  const {nodes} = await setup(async url => { calls.push(url); return reply(status); });
  assert.deepEqual(calls, ['/api/research/status']);
  assert.equal(nodes['research-submit'].disabled, false);
});

test('example only fills inputs; explicit submit starts exactly one job and polling never resubmits', async () => {
  const calls = []; let finish;
  const app = await setup(async (url, options) => {
    calls.push({url, options});
    if (url === '/api/research/status') return reply(status);
    if (options?.method === 'POST') return new Promise(resolve => { finish = resolve; });
    return reply(snapshot(true));
  });
  const n = app.nodes;
  n['research-example'].click(); assert.equal(calls.length, 1);
  const pending = n['research-form'].requestSubmit();
  assert.equal(n['research-submit'].disabled, true);
  await n['research-form'].requestSubmit(); assert.equal(calls.length, 2);
  const {max_cost_usd, ...formSettings} = settings;
  assert.deepEqual(JSON.parse(calls[1].options.body), formSettings);
  finish(reply(snapshot())); await pending;
  assert.equal(app.storage.get('paper-assistant-research-job'), id);
  await app.tick();
  assert.equal(calls.length, 3); assert.equal(calls[2].url, `/api/research/jobs/${id}`);
  assert.equal(n['research-submit'].disabled, false);
  assert.ok(texts(n['research-answer']).join('').includes('<script>answer</script>'));
  assert.ok(texts(n['research-citations']).join('').includes('<script>quote</script>'));
  assert.ok(texts(n['research-candidates']).join('').includes('<script>reason</script>'));
  assert.ok(texts(n['research-events']).join('').includes('<script>purpose</script>'));
});

test('refresh reconnects read-only, restores task inputs and stop requests do not create a second job', async () => {
  const calls = [];
  const {nodes} = await setup(async (url, options) => {
    calls.push({url, options});
    if (url === '/api/research/status') return reply(status);
    const data = snapshot();
    if (url.endsWith('/stop')) { data.job.stop_requested = true; data.job.status = 'stopping'; }
    return reply(data);
  }, id);
  assert.equal(calls.length, 2); assert.equal(calls[1].options?.method, undefined);
  assert.equal(nodes['research-topic'].value, settings.topic);
  await nodes['research-stop'].click();
  assert.equal(calls[2].url, `/api/research/jobs/${id}/stop`);
  assert.equal(calls[2].options.body, '{}'); assert.equal(nodes['research-stop'].disabled, true);
  assert.ok(nodes['research-current'].textContent.includes('当前步骤'));
});

test('missing key keeps start disabled while the example remains usable', async () => {
  let calls = 0;
  const {nodes} = await setup(async () => { calls++; return reply({...status, key_ready: false}); });
  nodes['research-example'].click(); await nodes['research-form'].requestSubmit();
  assert.equal(calls, 1); assert.equal(nodes['research-submit'].disabled, true);
  assert.ok(nodes['research-notice'].textContent.includes('OPENAI_API_KEY'));
});

test('polling failure offers reconnection and never automatically creates or retries a job', async () => {
  const calls = [];
  const {nodes} = await setup(async (url, options) => {
    calls.push({url, options});
    if (url === '/api/research/status') return reply(status);
    throw new TypeError('offline');
  }, id);
  assert.equal(calls.length, 2); assert.equal(nodes['research-reconnect'].hidden, false);
  assert.equal(nodes['research-result'].hidden, false);
  assert.ok(nodes['research-current'].textContent.includes('不会重新启动'));
});

test('finished run without checked evidence replaces the in-progress placeholder', async () => {
  let completed = false;
  const app = await setup(async url => {
    if (url === '/api/research/status') return reply(status);
    const data = snapshot(completed); data.state.evidence_answer = null;
    return reply(data);
  }, id);
  completed = true; await app.tick();
  assert.ok(app.nodes['research-answer'].textContent.includes('没有通过引用核对'));
});

test('lost submit response can be recovered from the active server task without another POST', async () => {
  const calls = [];
  const {nodes} = await setup(async (url, options) => {
    calls.push({url, options});
    return reply(url === '/api/research/status' ? {...status, active_job_id: id} : snapshot());
  });
  assert.deepEqual(calls.map(call => call.url), ['/api/research/status', `/api/research/jobs/${id}`]);
  assert.ok(calls.every(call => !call.options.method));
  assert.equal(nodes['research-submit'].disabled, true);
});

test('context limit after download explains that PDF may still need indexing', async () => {
  const {nodes} = await setup(async url => {
    if (url === '/api/research/status') return reply(status);
    const data = snapshot(true);
    data.state.stop_reason = 'model_context_too_large'; data.state.evidence_answer = null;
    return reply(data);
  }, id);
  assert.ok(texts(nodes['research-result']).some(text => text.includes('尚未入库')) ||
    Object.values(nodes).some(node => node.textContent.includes('尚未入库')));
  assert.equal(nodes['research-submit'].disabled, false);
});

test('reconnecting focuses results while polling preserves manually expanded settings', async () => {
  const app = await setup(async url => reply(url === '/api/research/status' ? status : snapshot()), id);
  assert.equal(app.nodes['research-editor'].open, false);
  app.nodes['research-editor'].open = true;
  await app.tick();
  assert.equal(app.nodes['research-editor'].open, true);
});
