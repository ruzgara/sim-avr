// Circuit viewer: draws a Wokwi diagram.json with the <wokwi-*> web components and
// animates it from the simulator's event stream (/events, served by server.py).
//
// Overview:
//   1. loadDiagram()   fetch /diagram.json, create one element per part
//   2. routeWire()     turn each connection (+ Wokwi routing hints) into a polyline
//   3. buildNets()     union-find over connected pins -> electrical nets
//   4. connectEvents() EventSource: update port state, recompute which LEDs are lit

const SVG_NS = 'http://www.w3.org/2000/svg';
const $ = (id) => document.getElementById(id);

// ---------------------------------------------------------------------------
// Wire colours (Wokwi names -> CSS). Unknown names are passed to CSS unchanged.
// ---------------------------------------------------------------------------
const WIRE_COLORS = {
  green: '#1c9c2a', red: '#e0201b', black: '#303030', blue: '#2a62d6',
  yellow: '#e3c400', orange: '#f28a00', white: '#f2f2f2', gray: '#8a8a8a',
  brown: '#7a4a21', purple: '#8a2be2', cyan: '#00b8d4', magenta: '#e0208c',
};

// ---------------------------------------------------------------------------
// State shared by the functions below
// ---------------------------------------------------------------------------
const parts = new Map();   // part id -> { def, el, w, h, cx, cy }
const wires = [];          // { from, to, points }
let leds = [];             // { el, anode: "part:pin", cathode: "part:pin" }
let nano = null;           // the arduino-nano part (if any), for its on-board LED
let netOf = new Map();     // "part:pin" -> net root key
const ports = { B: { ddr: 0, port: 0 }, C: { ddr: 0, port: 0 }, D: { ddr: 0, port: 0 } };

// ---------------------------------------------------------------------------
// 1. Parts
// ---------------------------------------------------------------------------
async function createPart(def) {
  const tag = 'wokwi-' + def.type.replace(/^wokwi-/, '');
  if (!customElements.get(tag)) {
    console.warn(`No element ${tag} for part ${def.id}; skipped`);
    return;
  }
  const el = document.createElement(tag);
  // Apply attrs: use the element property if it exists (LED color, resistor value...),
  // otherwise fall back to an HTML attribute.
  for (const [k, v] of Object.entries(def.attrs || {})) {
    if (k in el) el[k] = v; else el.setAttribute(k, v);
  }
  el.style.left = (def.left || 0) + 'px';
  el.style.top = (def.top || 0) + 'px';
  // Wokwi rotates parts (clockwise degrees) around their centre.
  el.style.transformOrigin = 'center';
  if (def.rotate) el.style.transform = `rotate(${def.rotate}deg)`;
  $('stage').appendChild(el);
  await el.updateComplete;                       // wait for Lit to render so we can measure
  const w = el.offsetWidth, h = el.offsetHeight; // size ignoring rotation
  parts.set(def.id, { def, el, w, h, cx: w / 2, cy: h / 2 });
}

// Absolute (diagram) position of a pin, taking the part's rotation into account.
function pinPos(ref) {
  const [id, ...rest] = ref.split(':');
  const name = rest.join(':');
  const p = parts.get(id);
  if (!p) return null;
  const pin = (p.el.pinInfo || []).find((q) => q.name === name);
  if (!pin) { console.warn(`Pin ${ref} not found`); return null; }
  const a = ((p.def.rotate || 0) * Math.PI) / 180;
  const dx = pin.x - p.cx, dy = pin.y - p.cy;
  return {
    x: (p.def.left || 0) + p.cx + dx * Math.cos(a) - dy * Math.sin(a),
    y: (p.def.top || 0) + p.cy + dx * Math.sin(a) + dy * Math.cos(a),
  };
}

// ---------------------------------------------------------------------------
// 2. Wire routing
// ---------------------------------------------------------------------------
// Hints are strings like "v86.4" (move down 86.4) or "h-12" (move left 12), relative to the
// previous point. Hints before "*" start at the source pin; hints after "*" start at the
// target pin (and walk away from it). With no "*", all hints belong to the source.
// Whatever gap remains between the two chains is closed with an orthogonal elbow.
function walk(start, hints) {
  const pts = [start];
  let last = null;      // direction of the last move: 'h' or 'v'
  for (const hint of hints) {
    const m = /^([hv])(-?[\d.]+)$/.exec(hint);
    if (!m) continue;
    const q = { ...pts[pts.length - 1] };
    if (m[1] === 'h') q.x += parseFloat(m[2]); else q.y += parseFloat(m[2]);
    pts.push(q);
    last = m[1];
  }
  return { pts, last };
}

function routeWire(conn) {
  const a = pinPos(conn[0]), b = pinPos(conn[1]);
  if (!a || !b) return null;
  const hints = conn[3] || [];
  const star = hints.indexOf('*');
  const srcHints = star < 0 ? hints : hints.slice(0, star);
  const dstHints = star < 0 ? [] : hints.slice(star + 1);

  const src = walk(a, srcHints);
  const dst = walk(b, dstHints);
  const p = src.pts[src.pts.length - 1];   // end of source chain
  const q = dst.pts[dst.pts.length - 1];   // end of target chain

  // Elbow between p and q. After a vertical move we continue vertically (down, across, down)
  // which suits pins that point up/down; after a horizontal move we go straight across and
  // then finish vertically.
  const w = { from: conn[0], to: conn[1], color: conn[2], src: src.pts, dst: dst.pts, p, q, z: false };
  if (p.x !== q.x && Math.abs(q.y - p.y) > 12 && src.last !== 'h') {
    w.z = true;                 // down-across-down shape; its middle level is set by spreadZ()
    w.my = (p.y + q.y) / 2;
  }
  buildPoints(w);
  return w;
}

// Assemble a wire's polyline from its source chain, elbow and target chain.
function buildPoints(w) {
  const { p, q } = w;
  const mid = [];
  if (p.x !== q.x && p.y !== q.y) {
    if (w.z) mid.push({ x: p.x, y: w.my }, { x: q.x, y: w.my });
    else mid.push({ x: q.x, y: p.y });   // straight across, then finish vertically
  }
  w.points = tidy([...w.src, ...mid, ...w.dst.slice().reverse()]);
}

// Several down-across-down wires would share one horizontal level and look merged. Give each
// wire in such a group its own level, ordered so that wires do not cross each other:
// right-going wires first (rightmost start highest), then left-going ones (leftmost start highest).
function spreadZ(list) {
  const groups = new Map();
  for (const w of list.filter((w) => w.z)) {
    const key = Math.round(w.my);
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(w);
  }
  const STEP = 6;
  for (const g of groups.values()) {
    const right = g.filter((w) => w.q.x > w.p.x).sort((a, b) => b.p.x - a.p.x);
    const left = g.filter((w) => w.q.x <= w.p.x).sort((a, b) => a.p.x - b.p.x);
    const order = [...right, ...left];
    order.forEach((w, i) => { w.my += (i - (order.length - 1) / 2) * STEP; buildPoints(w); });
  }
}

// Drop repeated points and points in the middle of a straight run.
function tidy(pts) {
  const out = [];
  for (const p of pts) {
    const l = out[out.length - 1];
    if (l && l.x === p.x && l.y === p.y) continue;
    out.push(p);
    const n = out.length;
    if (n >= 3) {
      const [u, v, w] = out.slice(n - 3);
      if ((u.x === v.x && v.x === w.x) || (u.y === v.y && v.y === w.y)) out.splice(n - 2, 1);
    }
  }
  return out;
}

function drawWires() {
  const svg = $('wires');
  for (const w of wires) {
    const pl = document.createElementNS(SVG_NS, 'polyline');
    pl.setAttribute('points', w.points.map((p) => `${p.x},${p.y}`).join(' '));
    pl.setAttribute('stroke', WIRE_COLORS[w.color] || w.color || '#1c9c2a');
    svg.appendChild(pl);
  }
}

// ---------------------------------------------------------------------------
// 3. Nets and LED logic
// ---------------------------------------------------------------------------
// Union-find: every "part:pin" string belongs to a set; connected pins share a root.
function buildNets(diagram) {
  const parent = new Map();
  const find = (x) => {
    if (!parent.has(x)) parent.set(x, x);
    while (parent.get(x) !== x) {
      parent.set(x, parent.get(parent.get(x)));   // path compression
      x = parent.get(x);
    }
    return x;
  };
  const union = (a, b) => parent.set(find(a), find(b));

  for (const c of diagram.connections || []) union(c[0], c[1]);
  for (const [id, p] of parts) {
    const type = p.def.type;
    // A resistor conducts: its two ends are one net as far as LED logic cares.
    if (type === 'wokwi-resistor') union(`${id}:1`, `${id}:2`);
    // Pins named GND.1, GND.2 ... are connected inside the board.
    if (type === 'wokwi-arduino-nano') {
      for (const pin of p.el.pinInfo) if (/^GND(\.\d+)?$/.test(pin.name)) union(`${id}:GND`, `${id}:${pin.name}`);
    }
  }
  netOf = new Map();
  for (const k of parent.keys()) netOf.set(k, find(k));
  return find;
}

// Nano pin name -> { port: 'B'|'C'|'D', bit } or null.  D0-7 -> PD0-7, D8-13 -> PB0-5, A0-5 -> PC0-5
function mcuPin(name) {
  let m = /^D?(\d+)$/.exec(name);
  if (m) {
    const n = +m[1];
    if (n <= 7) return { port: 'D', bit: n };
    if (n <= 13) return { port: 'B', bit: n - 8 };
    return null;
  }
  m = /^A([0-5])$/.exec(name);
  return m ? { port: 'C', bit: +m[1] } : null;
}

// Is any MCU pin on this net an output that is currently high?
let nanoPinsByNet = new Map();   // net root -> [{port, bit}]
function netDrivenHigh(net) {
  return (nanoPinsByNet.get(net) || []).some(({ port, bit }) => {
    const s = ports[port];
    return (s.ddr >> bit & 1) && (s.port >> bit & 1);
  });
}

function setupLeds(find) {
  nanoPinsByNet = new Map();
  for (const [id, p] of parts) {
    if (p.def.type !== 'wokwi-arduino-nano') continue;
    for (const pin of p.el.pinInfo) {
      const m = mcuPin(pin.name);
      if (!m) continue;
      const net = find(`${id}:${pin.name}`);
      if (!nanoPinsByNet.has(net)) nanoPinsByNet.set(net, []);
      nanoPinsByNet.get(net).push(m);
    }
  }
  const gndNets = new Set();
  for (const [id, p] of parts) {
    if (p.def.type === 'wokwi-arduino-nano') gndNets.add(find(`${id}:GND`));
  }
  leds = [];
  for (const [id, p] of parts) {
    if (p.def.type !== 'wokwi-led') continue;
    leds.push({ el: p.el, anode: find(`${id}:A`), cathodeGnd: gndNets.has(find(`${id}:C`)) });
  }
}

function updateLeds() {
  for (const led of leds) led.el.value = led.cathodeGnd && netDrivenHigh(led.anode);
  // The Nano's on-board "L" LED is on PB5 (D13).
  if (nano) nano.el.led13 = !!((ports.B.ddr & ports.B.port) >> 5 & 1);
}

// ---------------------------------------------------------------------------
// 4. Live events
// ---------------------------------------------------------------------------
const badge = $('badge');
function setStatus(kind, text) { badge.className = kind; badge.textContent = text; }

// Stopwatch. Totals come from "time <cycles> <instructions> <freq>" lines, the lap (between
// the last two gdb stops, e.g. breakpoint to breakpoint) from "lap <cycles> <instructions>".
// While running, the lap counts up live from the last stop.
const clock = { cycles: 0, instr: 0, freq: 16e6, mark: null, lap: null, reset: null, running: true };
const clockEl = $('clocktext');
let clockHeight = 0;
// Reset button: the server records the reset point (shared with gdb's `sw reset`)
$('clockreset').addEventListener('click', () => { fetch('/reset', { method: 'POST' }).catch(() => {}); });
const fmtMs = (c) => (c * 1000 / clock.freq).toFixed(3) + ' ms';
const fmtN = (n) => n.toLocaleString('en-US');
function renderClock() {
  let text = `<span class="big">${fmtMs(clock.cycles)}</span>\n` +
             `<span class="dim">${fmtN(clock.cycles)} cycles · ${fmtN(clock.instr)} instr</span>`;
  if (clock.running && clock.mark) {
    text += `\nlap  ${fmtMs(clock.cycles - clock.mark.cycles)} <span class="dim">(running)</span>`;
  } else if (clock.lap) {
    text += `\nlap  ${fmtMs(clock.lap.cycles)} <span class="dim">` +
            `(${fmtN(clock.lap.cycles)} cyc, ${fmtN(clock.lap.instr)} instr)</span>`;
  }
  if (clock.reset) {
    const c = clock.cycles - clock.reset.cycles, i = clock.instr - clock.reset.instr;
    text += `\nreset ${fmtMs(c)} <span class="dim">(${fmtN(c)} cyc, ${fmtN(i)} instr)</span>`;
  }
  clockEl.innerHTML = text;
  // The box grows when a lap/reset line appears: re-fit unless the user has panned/zoomed
  const h = $('clock').offsetHeight;
  if (h !== clockHeight) { clockHeight = h; if (!view.touched) fitView(); }
}
function clockStopped() {
  // Remember where this stop was, for the live lap once the program runs again
  clock.mark = { cycles: clock.cycles, instr: clock.instr };
}

let ended = false;
function connectEvents() {
  const es = new EventSource('/events');
  es.onopen = () => { if (!ended) setStatus('running', 'connected'); };
  es.onmessage = (ev) => {
    const f = ev.data.trim().split(/\s+/);
    if (f[0] === 'io' && ports[f[1]]) {
      // io <port letter> <ddr> <port> <pin>  (hex)
      ports[f[1]].ddr = parseInt(f[2], 16);
      ports[f[1]].port = parseInt(f[3], 16);
      updateLeds();
    } else if (f[0] === 'time') {
      // time <cycles> <instructions> <freq>
      clock.cycles = Number(f[1]);
      clock.instr = Number(f[2]);
      clock.freq = Number(f[3]) || clock.freq;
      renderClock();
    } else if (f[0] === 'mark') {
      // mark <cycles> <instructions>: stopwatch reset point
      clock.reset = { cycles: Number(f[1]), instr: Number(f[2]) };
      renderClock();
    } else if (f[0] === 'lap') {
      // lap <cycles> <instructions>
      clock.lap = { cycles: Number(f[1]), instr: Number(f[2]) };
      renderClock();
    } else if (f[0] === 'state') {
      clock.running = f[1] === 'running';
      if (!clock.running) clockStopped();
      renderClock();
      if (f[1] === 'running') setStatus('running', 'running');
      else setStatus('stopped', 'stopped (gdb)');
    } else if (f[0] === 'exit') {
      ended = true;
      setStatus('ended', 'ended' + (f[1] ? ` (${f[1]})` : ''));
      es.close();
    }
  };
  es.onerror = () => {
    if (ended) return;
    setStatus('disconnected', 'disconnected');
    // EventSource retries by itself; if it gave up, start a new one after a pause.
    if (es.readyState === EventSource.CLOSED) { es.close(); setTimeout(connectEvents, 1500); }
  };
}

// ---------------------------------------------------------------------------
// View: fit to window, wheel zoom, drag pan
// ---------------------------------------------------------------------------
const view = { x: 0, y: 0, k: 1, touched: false };
function applyView() {
  $('stage').style.transform = `translate(${view.x}px, ${view.y}px) scale(${view.k})`;
}

function diagramBounds() {
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  const add = (x, y) => { x0 = Math.min(x0, x); y0 = Math.min(y0, y); x1 = Math.max(x1, x); y1 = Math.max(y1, y); };
  for (const p of parts.values()) {
    const a = ((p.def.rotate || 0) * Math.PI) / 180;
    for (const [px, py] of [[0, 0], [p.w, 0], [0, p.h], [p.w, p.h]]) {
      const dx = px - p.cx, dy = py - p.cy;
      add((p.def.left || 0) + p.cx + dx * Math.cos(a) - dy * Math.sin(a),
          (p.def.top || 0) + p.cy + dx * Math.sin(a) + dy * Math.cos(a));
    }
  }
  for (const w of wires) for (const p of w.points) add(p.x, p.y);
  return { x0, y0, x1, y1 };
}

function fitView() {
  const b = diagramBounds();
  if (!isFinite(b.x0)) return;
  const pad = 40, vw = innerWidth, vh = innerHeight;
  // Keep the strip under the stopwatch box clear, so it never covers the circuit
  const top = Math.max(pad, $('clock').getBoundingClientRect().bottom + 16);
  const bw = b.x1 - b.x0, bh = b.y1 - b.y0;
  // Never magnify beyond 2x; small circuits stay crisp and roughly Wokwi-sized.
  view.k = Math.min((vw - 2 * pad) / bw, (vh - top - pad) / bh, 2);
  view.x = (vw - bw * view.k) / 2 - b.x0 * view.k;
  view.y = top + (vh - top - pad - bh * view.k) / 2 - b.y0 * view.k;
  applyView();
}

function setupInteraction() {
  const vp = $('viewport');
  vp.addEventListener('wheel', (e) => {
    e.preventDefault();
    const f = Math.exp(-e.deltaY * 0.0015);
    const k = Math.min(8, Math.max(0.2, view.k * f));
    // keep the point under the cursor fixed
    view.x = e.clientX - (e.clientX - view.x) * (k / view.k);
    view.y = e.clientY - (e.clientY - view.y) * (k / view.k);
    view.k = k; view.touched = true; applyView();
  }, { passive: false });

  let drag = null;
  vp.addEventListener('pointerdown', (e) => {
    drag = { x: e.clientX, y: e.clientY, vx: view.x, vy: view.y };
    vp.classList.add('dragging');
  });
  addEventListener('pointermove', (e) => {
    if (!drag) return;
    view.x = drag.vx + e.clientX - drag.x;
    view.y = drag.vy + e.clientY - drag.y;
    view.touched = true; applyView();
  });
  addEventListener('pointerup', () => { drag = null; vp.classList.remove('dragging'); });
  vp.addEventListener('dblclick', () => { view.touched = false; fitView(); });
  addEventListener('resize', () => { if (!view.touched) fitView(); });
}

// ---------------------------------------------------------------------------
// Start-up
// ---------------------------------------------------------------------------
async function main() {
  setupInteraction();
  const diagram = await (await fetch('/diagram.json')).json();
  for (const def of diagram.parts || []) await createPart(def);
  nano = [...parts.values()].find((p) => p.def.type === 'wokwi-arduino-nano') || null;
  if (nano) nano.el.ledPower = true;

  for (const c of diagram.connections || []) {
    const w = routeWire(c);
    if (w) wires.push(w);
  }
  spreadZ(wires);
  drawWires();
  setupLeds(buildNets(diagram));
  updateLeds();
  fitView();
  connectEvents();
}

main().catch((err) => {
  console.error(err);
  $('msg').textContent = 'Viewer error: ' + err.message;
});
