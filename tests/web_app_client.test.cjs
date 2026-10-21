const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

class Element {
  constructor(tag = 'div') {
    this.tag = tag; this.children = []; this.listeners = {}; this.textContent = '';
    this.classList = {toggle() {}};
    this.value = ''; this.checked = false; this.disabled = true;
  }
  set innerHTML(_) { throw new Error('Untrusted content must not use innerHTML'); }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children = [...nodes]; }
  addEventListener(event, listener) { this.listeners[event] = listener; }
  scrollIntoView() {}
  focus() {}
  requestSubmit() { return this.listeners.submit({preventDefault() {}}); }
}
function texts(node) { return [node.textContent, ...node.children.flatMap(texts)]; }
async function setup(fetch) {
  const ids = ['question-form', 'question', 'conversation', 'notice', 'submit', 'method',
    'top-k', 'exact-count', 'clear', 'empty', 'files', 'corpus-summary'];
  const nodes = Object.fromEntries(ids.map(id => [id, new Element()]));
  nodes.method.value = 'hybrid'; nodes['top-k'].value = '3';
  const document = {getElementById: id => nodes[id], createElement: tag => new Element(tag)};
  const context = vm.createContext({document, fetch, setTimeout, URL, Blob, TypeError});
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../src/paper_assistant/web/app.js'), 'utf8'), context);
  await new Promise(resolve => setImmediate(resolve));
  return nodes;
}
const status = {ready: true, key_ready: true, files: ['paper.pdf'], chunk_count: 4};
const payload = {retrieval_method: 'hybrid', result: {status: 'answered', answer: '<img src=x onerror=alert(1)> [E1]',
  citations: [{evidence_id: 'E1', quote: '<script>quote</script>', sources: [{filename: 'paper.pdf', page_number: 1}]}],
  usage: {input_tokens: 100, output_tokens: 30}, estimated_cost_usd: .0002},
  context: {evidence: [{evidence_id: 'E1', text: '<script>evidence</script>'}]}};
const response = data => ({ok: true, json: async () => data});

test('initial status loads files without triggering an answer', async () => {
  const calls = [];
  const nodes = await setup(async url => { calls.push(url); return response(status); });
  assert.deepEqual(calls, ['/api/status']);
  assert.equal(nodes.question.disabled, false);
  assert.equal(nodes.files.children[0].textContent, 'paper.pdf');
});

test('one submit calls RAG once, blocks duplicate submissions and renders untrusted text literally', async () => {
  const calls = []; let finish;
  const nodes = await setup(async (url, options) => {
    calls.push({url, options});
    return url === '/api/status' ? response(status) : new Promise(resolve => { finish = resolve; });
  });
  nodes.question.value = '中文问题';
  const running = nodes['question-form'].requestSubmit();
  assert.equal(nodes.submit.disabled, true);
  nodes.question.value = 'duplicate question';
  await nodes['question-form'].requestSubmit();
  assert.equal(calls.length, 2);
  assert.deepEqual(JSON.parse(calls[1].options.body),
    {question: '中文问题', method: 'hybrid', top_k: 3, token_count_mode: 'local'});
  finish(response(payload)); await running;
  assert.equal(nodes.submit.disabled, false);
  const output = texts(nodes.conversation).join('\n');
  assert.ok(output.includes(payload.result.answer));
  assert.ok(output.includes('paper.pdf · 第 1 页'));
  assert.ok(output.includes('<script>quote</script>'));
  nodes.clear.listeners.click();
  assert.equal(nodes.conversation.children.length, 0);
  assert.equal(nodes.empty.hidden, false);
});

test('API failures display the error and never automatically retry', async () => {
  let count = 0;
  const nodes = await setup(async url => {
    if (url === '/api/status') return response(status);
    count += 1;
    return {ok: false, json: async () => ({error: '接口连接失败'})};
  });
  nodes.question.value = 'question';
  await nodes['question-form'].requestSubmit();
  assert.equal(count, 1);
  assert.ok(texts(nodes.conversation).includes('接口连接失败'));
  assert.equal(nodes.question.disabled, false);
});

test('missing key disables questions and a composing Enter does not submit Chinese text', async () => {
  let count = 0;
  const nodes = await setup(async () => { count += 1; return response({...status, key_ready: false}); });
  assert.equal(nodes.question.disabled, true);
  nodes.question.value = '中文';
  nodes.question.listeners.keydown({key: 'Enter', shiftKey: false, isComposing: true});
  await nodes['question-form'].requestSubmit();
  assert.equal(count, 1);
});
