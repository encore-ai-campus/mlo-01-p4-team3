/* B-09-07 7번: 진술 체크 시 행적 질문의 밤·시간 칸을 맞추는 화면 스크립트 회귀 시험. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const listeners = {};
const document = {
  addEventListener: (name, cb) => (listeners[name] ||= []).push(cb),
  querySelectorAll: () => [],
  getElementById: () => null,
};
const windowListeners = {};
const beacons = [];
vm.runInNewContext(fs.readFileSync(path.resolve(__dirname, '../../web/static/js/game.js'), 'utf8'),
  {document, crypto: {randomUUID: () => 'id'}, URLSearchParams,
   addEventListener: (name, cb) => (windowListeners[name] ||= []).push(cb),
   navigator: {sendBeacon: (url, body) => { beacons.push([url, String(body)]); return true; }}});

function makeForm() {
  const form = {};
  const evidence = [];
  form.elements = {
    command_id: {value: ''},
    interrogation_kind: {value: 'whereabouts', setCustomValidity() {}},
    night: {value: '0', disabled: false, validity: {valid: true}, options: [{value: '0'}, {value: '1'}]},
    slot: {value: 'evening', disabled: false},
  };
  form.querySelectorAll = () => evidence.filter(input => input.checked);
  form.querySelector = () => ({disabled: false});
  const own = el => Object.assign(el, {closest: selector => (selector === '.command-form' ? form : null)});
  Object.values(form.elements).forEach(own);
  form.claim = (night, slot, value) => {
    const input = own({name: 'evidence', value, checked: false, dataset: slot ? {night, slot} : {}});
    evidence.push(input);
    return input;
  };
  return form;
}
const change = target => listeners.change.forEach(cb => cb({target}));

// 한밤 진술을 체크하면 시간 칸이 한밤, 밤 칸이 진술의 밤이 된다.
let form = makeForm();
const midnight = form.claim('1', 'midnight', 'claim:cl02');
midnight.checked = true;
change(midnight);
assert.equal(form.elements.slot.value, 'midnight');
assert.equal(form.elements.night.value, '1');

// 진술 없이 직접 고른 시간은 그대로 둔다.
form = makeForm();
form.claim('0', 'midnight', 'claim:cl03');
form.elements.slot.value = 'evening';
change(form.elements.slot);
assert.equal(form.elements.slot.value, 'evening');

// 체크를 풀거나 시간이 없는 진술(점괘 공개)은 칸을 바꾸지 않는다.
form = makeForm();
const unchecked = form.claim('0', 'midnight', 'claim:cl04');
change(unchecked);
const divination = form.claim('', '', 'divination:cl09');
divination.checked = true;
change(divination);
assert.equal(form.elements.slot.value, 'evening');

// 목록에 없는 밤의 진술은 밤·시간 칸을 모두 그대로 둔다(한쪽만 바뀌지 않게).
form = makeForm();
const unknownNight = form.claim('5', 'midnight', 'claim:cl05');
unknownNight.checked = true;
change(unknownNight);
assert.equal(form.elements.night.value, '0');
assert.equal(form.elements.slot.value, 'evening');
// 8번: 발언 카드 조각을 바꿔 그리면 명령 막대 안에서 카드가 보이게 스크롤한다.
let scrolled = 0;
const cardsBox = {id: 'speech-cards', closest: s => (s === '.command-bar' ? {} : null), scrollIntoView: () => scrolled++};
document.getElementById = id => (id === 'speech-cards' ? cardsBox : null);
listeners['htmx:afterSwap'].forEach(cb => cb({detail: {target: cardsBox}}));
assert.equal(scrolled, 1);
listeners['htmx:afterSwap'].forEach(cb => cb({detail: {target: {id: 'meeting'}}}));
assert.equal(scrolled, 1);
// 9번: 설문 양식이 남은 결과 화면을 떠나면(pagehide) 건너뜀 비콘을 보낸다. 제출 뒤(양식 없음)·설문 없는 화면은 보내지 않는다.
const survey = {dataset: {leaveUrl: '/games/g1/survey/leave', csrf: 'tok', gameId: 'g1'}, querySelector: () => ({})};
document.getElementById = id => (id === 'survey' ? survey : null);
windowListeners.pagehide.forEach(cb => cb({}));
assert.deepEqual(beacons, [['/games/g1/survey/leave', 'csrfmiddlewaretoken=tok&game_id=g1']]);
survey.querySelector = () => null;
windowListeners.pagehide.forEach(cb => cb({}));
document.getElementById = () => null;
windowListeners.pagehide.forEach(cb => cb({}));
assert.equal(beacons.length, 1);
console.log('game.js whereabouts sync 4 cases, command bar cards scroll, survey leave beacon ok');
