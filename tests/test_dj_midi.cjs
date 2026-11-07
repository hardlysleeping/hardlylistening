// MIDI mapping smoke test: no browser, no audio, no device.
const fs = require('fs');
const vm = require('vm');
const assert = require('assert');
const html = fs.readFileSync('web/dj.html', 'utf8');

const start = html.indexOf('// ---------------------------------------------------------------- MIDI');
const end = html.indexOf('// ---------------------------------------------------------------- guided steps');
assert(start > 0 && end > start, 'MIDI block not found');

const makeInput = (id, ctl, min, max, value) => ({
  id, min, max, value: String(value), type: 'range', tagName: 'INPUT',
  dataset: ctl ? { ctl } : {}, closest: sel => (sel === '.deck' ? { dataset: { deck: id[0] } } : null),
  dispatchEvent(){ this.dispatched = (this.dispatched || 0) + 1; return true; }
});
function makeNode(){ return {innerHTML: '', textContent: '', classList: {toggle(){}, add(){}, remove(){}},
  addEventListener(){}, onclick: null}; }

const elements = {};
const deckA = {dataset: {deck: 'A'}};
const controls = {
  '#crossfader': makeInput('crossfader', null, 0, 100, 50),
  '#master': makeInput('master', null, 0, 100, 80),
  '#midimap': makeNode(), '#midistatus': makeNode(), '#midich': makeNode(),
  '#midiout': Object.assign(makeNode(), {value: ''}), '#midiin': Object.assign(makeNode(), {value: ''}),
  '#midienable': makeNode(), '#midireset': makeNode(), '#midibtn': makeNode(), '#midipanel': makeNode(),
  '#audioA': Object.assign(makeNode(), {addEventListener(){}}),
  '#audioB': Object.assign(makeNode(), {addEventListener(){}}),
  '#search': makeNode()
};
const ranges = {};
for (const deck of ['A', 'B'])
  for (const [ctl, min, max] of [['low', -20, 12], ['volume', 0, 100], ['pitch', -8, 8]])
    ranges[`.deck[data-deck="${deck}"] [data-ctl="${ctl}"]`] = Object.assign(makeInput(deck + '.x', ctl, min, max, min), {closest: () => deckA});

const store = {};
const context = vm.createContext({
  console, Event: class { constructor(type) { this.type = type; } },
  localStorage: {getItem: k => store[k] ?? null, setItem: (k, v) => { store[k] = String(v); }},
  navigator: {},
  window: {},
  document: {
    addEventListener(){},
    querySelector: sel => controls[sel] || ranges[sel] || null
  },
  setTimeout, clearTimeout
});
vm.runInContext('function $(s){ return document.querySelector(s); }', context);
// Renderers are exercised elsewhere; keep the map/list wiring quiet here.
vm.runInContext('function esc(s){ return String(s); }', context);
vm.runInContext('document.addEventListener = () => {};', context);
vm.runInContext(html.slice(start, end), context);

// Control identification and normalisation across asymmetric ranges.
assert.strictEqual(context.midiControl(controls['#crossfader']), 'cross');
assert.strictEqual(context.midiControl(controls['#master']), 'master');
assert.strictEqual(context.midiControl(ranges['.deck[data-deck="B"] [data-ctl="volume"]']), 'B.x'.slice(0, 0) + 'A.volume');
assert.strictEqual(context.midiControl({tagName: 'BUTTON', type: 'range'}), null);
assert.strictEqual(context.midiNorm({value: '50', min: '0', max: '100'}), 0.5);
assert.strictEqual(context.midiNorm({value: '-20', min: '-20', max: '12'}), 0);
assert.strictEqual(context.midiNorm({value: '12', min: '-20', max: '12'}), 1);

// Out: a CC per change, nothing sent twice, channel respected.
const sent = [];
vm.runInContext('midiOut = {send: m => __sent.push(m), name: "Test"}', Object.assign(context, {__sent: sent}));
context.midiSend('A.low', 0);
context.midiSend('A.low', 0);
context.midiSend('A.low', 0.5);
// Arrays come from the vm realm, so compare values rather than prototypes.
assert.strictEqual(JSON.stringify(sent), JSON.stringify([[0xB0, 16, 0], [0xB0, 16, 64]]));
vm.runInContext('midiChannel = 3', context);
vm.runInContext('delete midiLast["A.low"]', context);
context.midiSend('A.low', 1);
assert.strictEqual(JSON.stringify(sent[2]), JSON.stringify([0xB2, 16, 127]));

// In: learn assigns the CC, then the same CC moves the control.
vm.runInContext('midiChannel = 1', context);
let handler = null;
context.midiBindIn({set onmidimessage(fn){ handler = fn; }, get onmidimessage(){ return handler; }, id: 'in', name: 'In'});
vm.runInContext('midiLearning = "cross"', context);
handler({data: [0xB0, 64, 100]});
assert.strictEqual(JSON.parse(store['hl.midiMap']).cross, 64);
assert.strictEqual(vm.runInContext('midiLearning', context), null);
const cross = controls['#crossfader'];
cross.value = '50';
handler({data: [0xB0, 64, 127]});
assert.strictEqual(cross.value, '100');
assert.strictEqual(cross.dispatched, 1);

// A CC on another channel is ignored rather than moving the wrong fader.
const before = cross.value;
handler({data: [0xB1, 64, 0]});
assert.strictEqual(cross.value, before);

// Notes are only sent when a port exists.
sent.length = 0;
context.midiNote('A', true);
assert.strictEqual(JSON.stringify(sent[0]), JSON.stringify([0x90, 36, 127]));
context.midiNote('B', false);
assert.strictEqual(JSON.stringify(sent[1]), JSON.stringify([0x90, 37, 0]));

console.log('MIDI smoke PASS: control identity, range normalisation, change-only CC output, learn, CC input, channel filter, deck notes');
