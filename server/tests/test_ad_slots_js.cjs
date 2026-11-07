/* 실제 지면 스크립트에 가상 시계·IntersectionObserver를 주입하는 회귀 시험. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const {randomUUID} = require('node:crypto');
const adToken = randomUUID(), nextToken = randomUUID();
let clock = 0, serial = 0, observer;
const timers = new Map(), listeners = {}, beacons = [];
const imageEvents = {};
const image = {isConnected: true, dataset: {}, complete: false, naturalWidth: 0, addEventListener: (name, cb) => imageEvents[name] = cb};
const section = {dataset: {adToken}, querySelector: () => image};
const sections = [section];
const document = {hidden: false, querySelectorAll: () => sections, addEventListener: (name, cb) => listeners[name] = cb};
const window = {setTimeout: (fn, delay) => {const id = ++serial; timers.set(id, {fn, time: clock + delay}); return id;},
  clearTimeout: id => timers.delete(id), addEventListener: (name, cb) => listeners[name] = cb,
  paldoWS: {impression: data => beacons.push(data)}};
function tick(ms) {clock += ms; for (const [id, timer] of timers) if (timer.time <= clock) {timers.delete(id); timer.fn();}}
class IntersectionObserver {
  constructor(callback) {this.callback = callback; observer = this;}
  observe() {}
  unobserve() {}
}
vm.runInNewContext(fs.readFileSync(path.resolve(__dirname, '../../web/static/js/ad_slots.js'), 'utf8'),
  {window, document, IntersectionObserver, performance: {now: () => clock}, Date, Map});
function visibility(ratio) {observer.callback([{target: image, isIntersecting: ratio > 0, intersectionRatio: ratio}]);}
visibility(1); tick(2000); assert.equal(beacons.length, 0); // broken/loading images never count
image.complete = true; image.naturalWidth = 728; imageEvents.load();
visibility(0.49); tick(2000); assert.equal(beacons.length, 0);
visibility(0.5); tick(999); assert.equal(beacons.length, 0);
visibility(0.4); tick(1); assert.equal(beacons.length, 0);
visibility(0.7); tick(500); document.hidden = true; listeners.visibilitychange(); tick(1000); assert.equal(beacons.length, 0);
document.hidden = false; listeners.visibilitychange(); tick(999); assert.equal(beacons.length, 0);
const socket = window.paldoWS; delete window.paldoWS;
tick(1); assert.equal(beacons.length, 0); // late client script must not lose the beacon
window.paldoWS = socket;
tick(1000); assert.equal(beacons.length, 1); assert.equal(beacons[0].token, adToken); assert.equal(beacons[0].visible_ms, 1000);
visibility(0); visibility(1); listeners['htmx:afterSettle'](); tick(2000); assert.equal(beacons.length, 1);
const replacement = {...image, dataset: {}, addEventListener() {}};
sections.push({dataset: {adToken}, querySelector: () => replacement});
listeners['htmx:afterSettle']();
observer.callback([{target: replacement, isIntersecting: true, intersectionRatio: 1}]);
tick(1000); assert.equal(beacons.length, 1, 'same serve must not count again after fragment replacement');
const next = {...replacement, dataset: {}};
sections.push({dataset: {adToken: nextToken}, querySelector: () => next});
listeners['htmx:afterSettle']();
observer.callback([{target: next, isIntersecting: true, intersectionRatio: 1}]);
tick(1000); assert.equal(beacons.length, 2); assert.equal(beacons[1].token, nextToken);
console.log('ad_slots.js: PASS (threshold, continuous second, hidden tab, single beacon, htmx rescan)');
