'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
test('real network functions are blocked before an external request', () => {
  assert.throws(() => fetch('https://api.openai.com'), /Offline frontend/);
  assert.throws(() => require('node:https').get('https://arxiv.org'), /Offline frontend/);
  assert.throws(() => require('node:net').connect(443, '8.8.8.8'), /Offline frontend/);
});
