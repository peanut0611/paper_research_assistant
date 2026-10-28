const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

class Element {
  constructor() { this.children = []; this.listeners = {}; this.value = ''; this.textContent = ''; this.hidden = false; this.disabled = false; }
  set innerHTML(_) { throw Error('Model/user text must not become HTML'); }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  addEventListener(name, fn) { this.listeners[name] = fn; }
  click() { return this.listeners.click?.(); }
  submit() { return this.listeners.submit({preventDefault() {}}); }
  change() { return this.listeners.change?.(); }
}
const id = 'web-20261027T000000-aaaaaaaaaaaa';
const turnId = 'turn-aaaaaaaaaaaaaaaaaaaaaaaa';
const text = el => [el.textContent, ...el.children.flatMap(text)].join('\n');
const turn = (changes = {}) => ({id: turnId, request_id: 'request-test-00000001', question: '它有什么局限？', status: 'finished',
  steps: 3, usage: {tokens: 500, cost_usd: .001}, answer: {answer: '<script>literal answer</script> [E1]', insufficient_evidence: false,
    citations: [{evidence_id: 'E1', quote: '<b>literal quote</b>', source: {title: 'Paper'}, sources: [{page_number: 2}]}]}, ...changes});
const snapshot = (changes = {}) => ({task_id: id, turns: [], available: true, reason: '', active_turn_id: null,
  max_turns: 20, max_cost_usd: .5, remaining_usd: .5, usage: {tokens: 0, cost_usd: 0}, ...changes});
const reply = (data, status = 200) => ({ok: status < 400, status, json: async () => data});
const flush = () => new Promise(resolve => setImmediate(resolve));

async function setup(fetch, initialStorage = []) {
  const html = fs.readFileSync(path.join(__dirname, '../../src/paper_assistant/web/static/index.html'), 'utf8');
  const nodes = Object.fromEntries([...html.matchAll(/id="(conversation-[^"]+)"/g)].map(m => [m[1], new Element()]));
  const timers = new Map(), storage = new Map(initialStorage), busy = [];
  let sequence = 0;
  const context = vm.createContext({document: {getElementById: id => nodes[id], createElement: () => new Element()}, fetch,
    localStorage: {getItem: key => storage.get(key), setItem: (key, val) => storage.set(key, val), removeItem: key => storage.delete(key)},
    setTimeout: fn => {timers.set(++sequence, fn); return sequence;}, clearTimeout: key => timers.delete(key), TypeError});
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../../src/paper_assistant/web/static/conversation.js'), 'utf8'), context);
  const view = (task = id, status = 'finished') => context.paperResearchView({job: {task_id: task, status}}, {keyReady: true, setBusy: value => busy.push(value)});
  view(); await flush();
  return {nodes, timers, storage, busy, view};
}

test('restoring the thread only reads; answer and quotations render as literal text', async () => {
  const calls = [];
  const {nodes} = await setup(async (url, options) => {calls.push([url, options]); return reply(snapshot({turns: [turn()]}));});
  assert.equal(calls.length, 1);
  assert.equal(calls[0][1].method, undefined);
  assert.ok(text(nodes['conversation-messages']).includes('<script>literal answer</script>'));
  assert.ok(text(nodes['conversation-messages']).includes('<b>literal quote</b>'));
  assert.equal(nodes['conversation-submit'].disabled, false);
});

test('one explicit submit starts a turn; polling and repeated clicks never duplicate it', async () => {
  const calls = []; let posted = null;
  const ui = await setup(async (url, options) => {
    calls.push([url, options]);
    if (options.method === 'POST') {posted = JSON.parse(options.body); return reply(snapshot({active_turn_id: turnId, turns: [turn({...posted, status: 'running', answer: null})]}));}
    return reply(posted ? snapshot({turns: [turn(posted)], usage: {tokens: 500, cost_usd: .001}}) : snapshot());
  });
  ui.nodes['conversation-question'].value = '它有什么局限？';
  await ui.nodes['conversation-form'].submit(); await ui.nodes['conversation-form'].submit();
  assert.equal(calls.filter(([,o]) => o.method === 'POST').length, 1);
  assert.equal(ui.nodes['conversation-question'].disabled, true);
  assert.ok(posted.request_id.length >= 16);
  const poll = [...ui.timers.values()].at(-1); await poll();
  assert.equal(calls.filter(([,o]) => o.method === 'POST').length, 1);
  assert.equal(ui.nodes['conversation-submit'].disabled, false);
  assert.ok(ui.nodes['conversation-usage'].textContent.includes('500 token'));
});

test('lost submit response reconnects using the same request ID without another POST', async () => {
  let posted = null, posts = 0;
  const ui = await setup(async (url, options) => {
    if (options.method === 'POST') {posts++; posted = JSON.parse(options.body); throw TypeError('lost response');}
    return reply(posted ? snapshot({active_turn_id: turnId, turns: [turn({...posted, status: 'running', answer: null})]}) : snapshot());
  });
  ui.nodes['conversation-question'].value = '继续解释';
  await ui.nodes['conversation-form'].submit();
  assert.equal(posts, 1); assert.equal(ui.storage.size, 1);
  assert.ok(ui.nodes['conversation-status'].textContent.includes('不会自动重试'));
  await ui.nodes['conversation-refresh'].click();
  assert.equal(posts, 1); assert.equal(ui.storage.size, 0);
  assert.equal(ui.nodes['conversation-submit'].disabled, true);
});

test('reload recovers pending submission and messages by GET only', async () => {
  const pending = {question: '继续解释', max_cost_usd: .05, request_id: 'request-test-00000001'};
  let posts = 0;
  const ui = await setup(async (_, options) => { if (options.method === 'POST') posts++; return reply(snapshot({turns: [turn(pending)]})); },
    [[`paper-assistant-chat-pending:${id}`, JSON.stringify(pending)]]);
  assert.equal(posts, 0); assert.equal(ui.storage.size, 0);
  assert.ok(text(ui.nodes['conversation-messages']).includes('继续解释'));
});

test('definitive validation failure unlocks editing; missing index disables discussion', async () => {
  const ui = await setup(async (_, options) => options.method === 'POST' ? reply({error: '预算不足'}, 400) : reply(snapshot()));
  ui.nodes['conversation-question'].value = '追问'; await ui.nodes['conversation-form'].submit();
  assert.equal(ui.storage.size, 0); assert.equal(ui.nodes['conversation-question'].disabled, false);
  const unavailable = await setup(async () => reply(snapshot({available: false, reason: '没有已入库论文'})));
  assert.equal(unavailable.nodes['conversation-submit'].disabled, true);
  assert.ok(unavailable.nodes['conversation-status'].textContent.includes('没有已入库论文'));
});

test('stop targets only the active discussion turn', async () => {
  const calls = [];
  const ui = await setup(async (url, options) => { calls.push([url, options]); return reply(snapshot({active_turn_id: turnId, turns: [turn({status: 'running', answer: null})]})); });
  await ui.nodes['conversation-stop'].click();
  assert.equal(calls.filter(([,o]) => o.method === 'POST').length, 1);
  assert.ok(calls.at(-1)[0].endsWith('/conversation/stop'));
});

test('reset clears current messages and draft, retaining task-wide usage and turn count', async () => {
  const calls = []; let operation;
  const ui = await setup(async (url, options) => {
    calls.push([url, options]);
    if (url.endsWith('/reset')) {
      operation = JSON.parse(options.body);
      return reply(snapshot({thread_id: 'initial', generation: 1, turns: [], all_turn_count: 1,
        usage: {tokens: 500, cost_usd: .001}, remaining_usd: .499,
        acknowledged_actions: [operation.request_id], threads: [{id: 'initial', name: '对话 1', generation: 1, turn_count: 0}]}));
    }
    return reply(snapshot({thread_id: 'initial', generation: 0, turns: [turn()], all_turn_count: 1,
      usage: {tokens: 500, cost_usd: .001}, remaining_usd: .499}));
  });
  ui.nodes['conversation-question'].value = 'unfinished draft';
  await ui.nodes['conversation-reset'].click();
  assert.equal(operation.thread_id, 'initial'); assert.equal(operation.generation, 0);
  assert.equal(ui.nodes['conversation-messages'].children.length, 0);
  assert.equal(ui.nodes['conversation-question'].value, '');
  assert.ok(ui.nodes['conversation-usage'].textContent.includes('本任务追问累计 1 / 20'));
  assert.ok(ui.nodes['conversation-usage'].textContent.includes('500 token'));
  assert.equal(calls.filter(([,o]) => o.method === 'POST').length, 1);
  assert.equal(ui.storage.size, 0);
});

test('new dialogue is blank and switching back restores the previous thread', async () => {
  const calls = [];
  const threads = [{id: 'initial', name: '对话 1', turn_count: 1}, {id: 'chat-new', name: '对话 2', turn_count: 0}];
  const ui = await setup(async (url, options) => {
    calls.push([url, options]);
    if (url.endsWith('/new')) {
      const operation = JSON.parse(options.body);
      return reply(snapshot({thread_id: 'chat-new', generation: 0, threads, turns: [], all_turn_count: 1,
        acknowledged_actions: [operation.request_id]}));
    }
    if (url.endsWith('/select')) {
      assert.equal(JSON.parse(options.body).thread_id, 'initial');
      return reply(snapshot({thread_id: 'initial', generation: 0, threads, turns: [turn()], all_turn_count: 1}));
    }
    return reply(snapshot({thread_id: 'initial', generation: 0, turns: [turn()]}));
  });
  await ui.nodes['conversation-new'].click();
  assert.equal(ui.nodes['conversation-messages'].children.length, 0);
  assert.equal(ui.nodes['conversation-select'].value, 'chat-new');
  assert.equal(ui.nodes['conversation-select'].children.length, 2);
  ui.nodes['conversation-select'].value = 'initial'; await ui.nodes['conversation-select'].change();
  assert.ok(text(ui.nodes['conversation-messages']).includes('它有什么局限'));
  assert.equal(calls.filter(([,o]) => o.method === 'POST').length, 2);
});

test('active followup disables new/reset/select and programmatic clicks do nothing', async () => {
  const calls = [];
  const ui = await setup(async (url, options) => {calls.push([url, options]); return reply(snapshot({active_turn_id: turnId}));});
  assert.equal(ui.nodes['conversation-new'].disabled, true);
  assert.equal(ui.nodes['conversation-reset'].disabled, true);
  assert.equal(ui.nodes['conversation-select'].disabled, true);
  await ui.nodes['conversation-new'].click(); await ui.nodes['conversation-reset'].click(); await ui.nodes['conversation-select'].change();
  assert.equal(calls.length, 1);
});

test('lost context-change response reconnects without creating another dialogue', async () => {
  let operation = null, posts = 0;
  const ui = await setup(async (url, options) => {
    if (options.method === 'POST') {posts++; operation = JSON.parse(options.body); throw TypeError('response lost');}
    return reply(snapshot(operation ? {thread_id: 'chat-new', generation: 0, acknowledged_actions: [operation.request_id],
      threads: [{id: 'initial', name: '对话 1', turn_count: 0}, {id: 'chat-new', name: '对话 2', turn_count: 0}]} : {thread_id: 'initial', generation: 0}));
  });
  await ui.nodes['conversation-new'].click();
  assert.equal(posts, 1); assert.equal(ui.storage.size, 1);
  assert.equal(ui.nodes['conversation-submit'].disabled, true);
  await ui.nodes['conversation-refresh'].click();
  assert.equal(posts, 1); assert.equal(ui.storage.size, 0);
  assert.equal(ui.nodes['conversation-select'].value, 'chat-new');
});

test('archived submit acknowledgement clears pending input after reset; quotas remain global', async () => {
  const pending = {question: 'old turn', max_cost_usd: .05, request_id: 'request-test-00000001', thread_id: 'initial', generation: 0};
  const ui = await setup(async () => reply(snapshot({thread_id: 'initial', generation: 1, turns: [], all_turn_count: 20,
    acknowledged_request_ids: [pending.request_id]})), [[`paper-assistant-chat-pending:${id}`, JSON.stringify(pending)]]);
  assert.equal(ui.storage.size, 0);
  assert.equal(ui.nodes['conversation-submit'].disabled, true);
  assert.equal(ui.nodes['conversation-new'].disabled, false);
  assert.ok(ui.nodes['conversation-usage'].textContent.includes('本任务追问累计 20 / 20'));
});

test('reload acknowledges a saved management request using GET only', async () => {
  const action = {action: 'reset', payload: {request_id: 'manage-reload-00000001', thread_id: 'initial', generation: 0}};
  const calls = [];
  const ui = await setup(async (url, options) => {
    calls.push([url, options]);
    return reply(snapshot({thread_id: 'initial', generation: 1, acknowledged_actions: [action.payload.request_id]}));
  }, [[`paper-assistant-chat-action:${id}`, JSON.stringify(action)]]);
  assert.equal(calls.length, 1); assert.equal(calls[0][1].method, undefined);
  assert.equal(ui.storage.size, 0); assert.equal(ui.nodes['conversation-submit'].disabled, false);
});

test('bibliography answers display registered authors and paper links without pretending to quote body evidence', async () => {
  const answer = {answer_kind: 'bibliography', answer: '上一条回答引用的论文：<literal title>', insufficient_evidence: false,
    citations: [], references: [{document_id: 'paper-1', title: '<literal title>', authors: ['Author A', 'Author B'],
      arxiv_id: '2610.22615', version: 1, abstract_url: 'https://arxiv.org/abs/2610.22615v1'}]};
  const ui = await setup(async () => reply(snapshot({turns: [turn({answer, stop_reason: 'sufficient_evidence'})]})));
  const rendered = text(ui.nodes['conversation-messages']);
  assert.ok(rendered.includes('<literal title>') && rendered.includes('Author A; Author B'));
  assert.ok(rendered.includes('参考论文（1 篇）') && rendered.includes('打开论文'));
  assert.ok(rendered.includes('已核对引用记录与论文元数据'));
  assert.ok(!rendered.includes('已核对正文与引用'));
});

test('missing reference records show a scope explanation without invented citations', async () => {
  const answer = {answer_kind: 'bibliography', answer: '没有记录到上一条回答的引用论文。', insufficient_evidence: true, citations: [], references: []};
  const ui = await setup(async () => reply(snapshot({turns: [turn({answer, stop_reason: 'insufficient_evidence'})]})));
  const rendered = text(ui.nodes['conversation-messages']);
  assert.ok(rendered.includes('未找到该范围的可追溯引用记录'));
  assert.ok(!rendered.includes('查看 1 条引用'));
});
