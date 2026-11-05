// Trusted frontend tests supply their own fetch. Deny accidental real I/O.
'use strict';
const deny = () => { throw new Error('Offline frontend tests prohibit network access'); };
for (const name of ['node:http', 'node:https']) {
  const module = require(name);
  module.request = deny; module.get = deny;
}
const net = require('node:net');
net.Socket.prototype.connect = deny;
require('node:dgram').Socket.prototype.send = deny;
globalThis.fetch = deny;
