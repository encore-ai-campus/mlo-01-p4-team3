/* Node VM tests exercise the real browser client with deterministic time/socket seams. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../../web/static/js/paldo_ws.js'), 'utf8');
function harness(storage = new Map()) {
  const sockets = [], events = {}, timers = new Map();
  let timerId = 0, uuid = 0;
  class Socket {
    static OPEN = 1;
    constructor(url) { this.url = url; this.readyState = 0; this.sent = []; sockets.push(this); }
    send(data) { this.sent.push(JSON.parse(data)); }
    open() { this.readyState = 1; this.onopen(); }
    close(code = 1006) { this.readyState = 3; this.onclose({code}); }
    reply(data) { this.onmessage({data: JSON.stringify(data)}); }
  }
  const win = {addEventListener: (type, fn) => {events[type] = fn;}};
  const context = vm.createContext({window: win, WebSocket: Socket, location: {protocol: 'https:', host: 'game.test'},
    crypto: {randomUUID: () => `uuid-${++uuid}`},
    sessionStorage: {getItem: key => storage.get(key), setItem: (key, val) => storage.set(key, val)},
    setTimeout: (fn, ms) => {timers.set(++timerId, {fn, ms, repeat: false}); return timerId;},
    setInterval: (fn, ms) => {timers.set(++timerId, {fn, ms, repeat: true}); return timerId;},
    clearTimeout: id => timers.delete(id), clearInterval: id => timers.delete(id)});
  vm.runInContext(source, context);
  function tick(repeat) {
    const [id, timer] = [...timers].find(([, value]) => value.repeat === repeat);
    if (!repeat) timers.delete(id);
    timer.fn(); return timer.ms;
  }
  return {win, sockets, events, timers, storage, tick, context};
}
function beacon(h, token) {h.win.paldoWS.impression({token, visible_ratio: .5, visible_ms: 1000, occurred_at_ms: 1});}
function hello(queueFull, pageExit) {
  return {type: 'hello', dropped: queueFull + pageExit,
    dropped_by_reason: {queue_full: queueFull, page_exit: pageExit}};
}
function stored(h) {return JSON.parse(h.storage.get('paldo.ad.dropped'));}
{
  const h = harness();
  vm.runInContext(source, h.context);
  assert.equal(h.sockets.length, 1, 'one connection per page');
  assert.equal(h.sockets[0].url, 'wss://game.test/ws/game/');
  for (let n = 0; n < 25; n++) beacon(h, `token-${n}`);
  h.sockets[0].open();
  assert.deepEqual(h.sockets[0].sent[0], hello(5, 0));
  for (let n = 0; n < 4; n++) assert.equal(h.tick(true), 250);
  assert.equal(h.sockets[0].sent.length, 5, 'four beacons per second');
  assert.equal(h.sockets[0].sent[1].token, 'token-5', 'overflow discards oldest');
  const retryId = h.sockets[0].sent[1].msg_id;
  h.sockets[0].close();
  assert.equal(h.tick(false), 1000);
  h.sockets[1].open(); h.tick(true);
  assert.deepEqual(h.sockets[1].sent[0], hello(0, 0), 'sent counters were reset');
  assert.equal(h.sockets[1].sent[1].msg_id, retryId, 'unacknowledged beacon retains ID');
  h.sockets[1].reply({type: 'ack', msg_id: retryId, relayed: false});
  h.sockets[1].close(); h.tick(false); h.sockets[2].open(); h.tick(true);
  assert.notEqual(h.sockets[2].sent[1].msg_id, retryId, 'negative ack is never retried');
  h.sockets[2].reply({type: 'error', msg_id: h.sockets[2].sent[1].msg_id, code: 'rate_limited'});
  h.events.pagehide();
  assert.deepEqual(stored(h), {queue_full: 0, page_exit: 18}, 'server rate limit is not reported by the browser');
  assert.equal(h.timers.size, 0, 'page exit cancels all timers');
  const nextPage = harness(h.storage);
  for (let n = 0; n < 22; n++) beacon(nextPage, `next-${n}`);
  nextPage.sockets[0].open();
  assert.deepEqual(nextPage.sockets[0].sent[0], hello(2, 18), 'both causes are reported together');
  assert.deepEqual(stored(nextPage), {queue_full: 0, page_exit: 0});
}
{
  const h = harness();
  for (const delay of [1000, 2000, 4000, 8000, 16000, 30000, 30000]) {
    h.sockets.at(-1).close(); assert.equal(h.tick(false), delay);
  }
  h.sockets.at(-1).open();
  beacon(h, 'one'); h.tick(true);
  h.sockets.at(-1).close(4401);
  assert.equal(h.timers.size, 0, '4401 never reconnects');
  assert.deepEqual(stored(h), {queue_full: 0, page_exit: 0}, '4401 is not a page exit');
  h.events.pagehide();
  assert.deepEqual(stored(h), {queue_full: 0, page_exit: 1}, 'page exit counts the remaining beacon');
  h.events.pageshow({persisted: true});
  assert.equal(h.timers.size, 0, 'restoring the page does not reconnect a revoked session');
}
{
  const h = harness(new Map([['paldo.ad.dropped', JSON.stringify({queue_full: 3, page_exit: 2})]]));
  h.sockets[0].send = () => {throw new Error('send failed');};
  h.sockets[0].open();
  assert.deepEqual(stored(h), {queue_full: 3, page_exit: 2}, 'failed hello preserves the report');
  h.tick(false);
  h.sockets[1].open();
  assert.deepEqual(h.sockets[1].sent[0], hello(3, 2));
}
console.log('paldo_ws client: reconnect, queue, ack, per-reason report, reset, send failure, 4401 passed');
