'use strict';
// A small browser stand-in for the step 7b JS tests. Usage: node fviews_jsrun.js scenario.json
// The scenario holds the parsed page (tree), the script to run and a list of operations; the output is a JSON list
// with one result per operation. It implements just enough DOM, timers, fetch and XMLHttpRequest for compose.js and
// poll.js. Time is virtual: timers run only when an operation says so.
const fs = require('fs');
const vm = require('vm');

const scenario = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const realSetImmediate = setImmediate;

// ---------------------------------------------------------------------------------------------------------------
const state = {
  now: 0, timerSeq: 0, timers: [], fetchLog: [], fetchQueue: [], htmlLog: [], focusLog: [], reloads: 0,
  errors: [], consoleLog: [], byEid: new Map(), activeElement: null, submits: [],
};

const decode = (s) => s.replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&quot;/g, '"').replace(/&#0?39;/g, "'").replace(/&#x27;/g, "'").replace(/&nbsp;/g, ' ').replace(/&amp;/g, '&');
const stripTags = (s) => decode(String(s).replace(/<[^>]*>/g, ''));
const escapeHtml = (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
const kebab = (s) => s.replace(/[A-Z]/g, (c) => '-' + c.toLowerCase());
const camel = (s) => s.replace(/-([a-z])/g, (_, c) => c.toUpperCase());

const VOID = new Set(['area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr']);
function parseHTML(str) {
  // Just enough of an HTML parser for the fragments the page scripts insert: tags, quoted attributes, text, entities.
  const frag = new El('#fragment', {});
  let cur = frag;
  const re = /<!--[\s\S]*?-->|<\/([A-Za-z][^\s>]*)\s*>|<([A-Za-z][^\s\/>]*)((?:\s+[^\s=>\/]+(?:\s*=\s*(?:"[^"]*"|'[^']*'|[^\s>]+))?)*)\s*(\/?)>|([^<]+)|</g;
  let m;
  while ((m = re.exec(str)) !== null) {
    if (m[1]) { let n = cur; while (n && n.localName !== m[1].toLowerCase()) n = n.parentNode; if (n && n.parentNode) cur = n.parentNode; }
    else if (m[2]) {
      const attrs = {};
      const are = /([^\s=]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+)))?/g; let a;
      while ((a = are.exec(m[3] || '')) !== null) attrs[a[1]] = decode(a[2] !== undefined ? a[2] : (a[3] !== undefined ? a[3] : (a[4] !== undefined ? a[4] : '')));
      const el = new El(m[2], attrs);
      cur._append(el);
      if (!VOID.has(m[2].toLowerCase()) && !m[4]) cur = el;
    } else if (m[5] !== undefined) cur._append(new Text(decode(m[5])));
  }
  return frag;
}

class Ev {
  constructor(type, init) {
    init = init || {};
    this.type = type; this.bubbles = !!init.bubbles; this.cancelable = !!init.cancelable; this.detail = init.detail;
    this.defaultPrevented = false; this.target = null; this.currentTarget = null; this.key = init.key; this.keyCode = init.keyCode;
  }
  preventDefault() { this.defaultPrevented = true; }
  stopPropagation() { this._stopped = true; }
  stopImmediatePropagation() { this._stopped = true; }
}

class Text {
  constructor(data) { this.nodeType = 3; this.data = String(data); this.parentNode = null; this.nodeName = '#text'; }
  get textContent() { return this.data; }
  set textContent(v) { this.data = String(v); }
  get nodeValue() { return this.data; }
  remove() { if (this.parentNode) this.parentNode.removeChild(this); }
}

function parseSimple(sel) {
  // one compound selector -> {tag, id, classes, attrs, not[]}
  const out = { tag: null, id: null, classes: [], attrs: [], nots: [] };
  let i = 0;
  const readName = () => { const m = /^[A-Za-z0-9_\-\\]+/.exec(sel.slice(i)); if (!m) return ''; i += m[0].length; return m[0].replace(/\\/g, ''); };
  if (/^[A-Za-z*]/.test(sel)) out.tag = readName().toLowerCase();
  while (i < sel.length) {
    const c = sel[i];
    if (c === '#') { i++; out.id = readName(); }
    else if (c === '.') { i++; out.classes.push(readName()); }
    else if (c === '[') {
      const end = sel.indexOf(']', i);
      const body = sel.slice(i + 1, end);
      const m = /^([^\s~|^$*=]+)\s*(?:([~|^$*]?=)\s*(?:"([^"]*)"|'([^']*)'|([^\]\s]*)))?/.exec(body);
      out.attrs.push({ name: m[1], op: m[2] || null, value: m[3] !== undefined ? m[3] : (m[4] !== undefined ? m[4] : m[5]) });
      i = end + 1;
    } else if (c === ':') {
      i++;
      const name = readName();
      if (sel[i] === '(') {
        let depth = 1, j = i + 1;
        while (j < sel.length && depth) { if (sel[j] === '(') depth++; else if (sel[j] === ')') depth--; j++; }
        const arg = sel.slice(i + 1, j - 1); i = j;
        if (name === 'not') out.nots.push(arg);
      }
    } else i++;
  }
  return out;
}

function matchCompound(el, sel) {
  if (!el || el.nodeType !== 1) return false;
  const p = parseSimple(sel);
  if (p.tag && p.tag !== '*' && p.tag !== el.localName) return false;
  if (p.id && el.attrs.id !== p.id) return false;
  const classes = (el.attrs['class'] || '').split(/\s+/).filter(Boolean);
  if (!p.classes.every((c) => classes.includes(c))) return false;
  for (const a of p.attrs) {
    const v = el.attrs[a.name];
    if (v === undefined) return false;
    if (a.op === '=' && v !== a.value) return false;
    if (a.op === '^=' && !v.startsWith(a.value)) return false;
    if (a.op === '$=' && !v.endsWith(a.value)) return false;
    if (a.op === '*=' && !v.includes(a.value)) return false;
    if (a.op === '~=' && !v.split(/\s+/).includes(a.value)) return false;
  }
  for (const n of p.nots) if (matchGroup(el, n)) return false;
  return true;
}

function splitTop(s, sep) {
  const parts = []; let depth = 0, cur = '';
  for (const ch of s) {
    if (ch === '(' || ch === '[') depth++;
    if (ch === ')' || ch === ']') depth--;
    if (ch === sep && depth === 0) { parts.push(cur); cur = ''; } else cur += ch;
  }
  parts.push(cur);
  return parts.map((p) => p.trim()).filter(Boolean);
}

function matchComplex(el, complex) {
  const tokens = complex.replace(/\s*>\s*/g, ' > ').split(/\s+/).filter(Boolean);
  const rec = (node, idx) => {
    if (!matchCompound(node, tokens[idx])) return false;
    if (idx === 0) return true;
    let sep = ' ', j = idx - 1;
    if (tokens[j] === '>') { sep = '>'; j--; }
    if (sep === '>') return node.parentNode ? rec(node.parentNode, j) : false;
    for (let p = node.parentNode; p; p = p.parentNode) if (rec(p, j)) return true;
    return false;
  };
  return rec(el, tokens.length - 1);
}

function matchGroup(el, group) { return splitTop(group, ',').some((c) => matchComplex(el, c)); }

class El {
  constructor(tag, attrs) {
    this.nodeType = 1; this.localName = tag.toLowerCase(); this.tagName = tag.toUpperCase(); this.nodeName = this.tagName;
    this.attrs = Object.assign({}, attrs || {}); this.childNodes = []; this.parentNode = null; this.listeners = {};
    this.style = {}; this._value = undefined; this.eid = null; this.scrollTop = 0; this.scrollHeight = 100; this.clientHeight = 100;
    this.offsetHeight = 10; this.offsetWidth = 10; this.selectionStart = 0; this.selectionEnd = 0;
    const self = this;
    this.dataset = new Proxy({}, {
      get: (_, p) => self.attrs['data-' + kebab(String(p))],
      set: (_, p, v) => { self.attrs['data-' + kebab(String(p))] = String(v); return true; },
      has: (_, p) => ('data-' + kebab(String(p))) in self.attrs,
      ownKeys: () => Object.keys(self.attrs).filter((k) => k.startsWith('data-')).map((k) => camel(k.slice(5))),
      getOwnPropertyDescriptor: (_, p) => ({ enumerable: true, configurable: true, value: self.attrs['data-' + kebab(String(p))] }),
    });
    this.classList = {
      add: (...c) => { const s = new Set(self.className.split(/\s+/).filter(Boolean)); c.forEach((x) => s.add(x)); self.className = [...s].join(' '); },
      remove: (...c) => { const s = new Set(self.className.split(/\s+/).filter(Boolean)); c.forEach((x) => s.delete(x)); self.className = [...s].join(' '); },
      toggle: (c, force) => { const has = self.classList.contains(c); const want = force === undefined ? !has : !!force; if (want) self.classList.add(c); else self.classList.remove(c); return want; },
      contains: (c) => self.className.split(/\s+/).includes(c),
    };
  }
  get id() { return this.attrs.id || ''; }
  set id(v) { this.attrs.id = String(v); }
  get className() { return this.attrs['class'] || ''; }
  set className(v) { this.attrs['class'] = String(v); }
  get name() { return this.attrs.name || ''; }
  get type() { return this.attrs.type || (this.localName === 'textarea' ? 'textarea' : 'text'); }
  set type(v) { this.attrs.type = v; }
  get href() { return this.attrs.href || ''; }
  get disabled() { return 'disabled' in this.attrs; }
  set disabled(v) { if (v) this.attrs.disabled = ''; else delete this.attrs.disabled; }
  get hidden() { return 'hidden' in this.attrs; }
  set hidden(v) { if (v) this.attrs.hidden = ''; else delete this.attrs.hidden; }
  get checked() { return 'checked' in this.attrs; }
  set checked(v) { if (v) this.attrs.checked = ''; else delete this.attrs.checked; }
  get title() { return this.attrs.title || ''; }
  set title(v) { this.attrs.title = v; }
  get maxLength() { return 'maxlength' in this.attrs ? Number(this.attrs.maxlength) : -1; }
  set maxLength(v) { this.attrs.maxlength = String(v); }
  get placeholder() { return this.attrs.placeholder || ''; }
  get form() { for (let p = this.parentNode; p; p = p.parentNode) if (p.localName === 'form') return p; return null; }
  get elements() { return this.querySelectorAll('input,textarea,select,button'); }
  get children() { return this.childNodes.filter((c) => c.nodeType === 1); }
  get firstChild() { return this.childNodes[0] || null; }
  get lastChild() { return this.childNodes[this.childNodes.length - 1] || null; }
  get firstElementChild() { return this.children[0] || null; }
  get lastElementChild() { const c = this.children; return c[c.length - 1] || null; }
  get parentElement() { return this.parentNode && this.parentNode.nodeType === 1 ? this.parentNode : null; }
  get nextSibling() { if (!this.parentNode) return null; const s = this.parentNode.childNodes; return s[s.indexOf(this) + 1] || null; }
  get previousSibling() { if (!this.parentNode) return null; const s = this.parentNode.childNodes; return s[s.indexOf(this) - 1] || null; }
  get nextElementSibling() { let n = this.nextSibling; while (n && n.nodeType !== 1) n = n.nextSibling; return n; }
  get previousElementSibling() { let n = this.previousSibling; while (n && n.nodeType !== 1) n = n.previousSibling; return n; }
  get isConnected() { for (let p = this; p; p = p.parentNode) if (p === documentObj) return true; return false; }
  get ownerDocument() { return documentObj; }
  get value() {
    if (this._value !== undefined) return this._value;
    if (this.localName === 'textarea') { const t = this.textContent; return t.replace(/^\r?\n/, ''); }
    return this.attrs.value !== undefined ? this.attrs.value : '';
  }
  set value(v) { this._value = String(v); if (this.localName !== 'textarea') this.attrs.value = String(v); }
  get textContent() {
    let out = '';
    for (const c of this.childNodes) out += c.textContent;
    return out;
  }
  set textContent(v) { this.childNodes.forEach((c) => { c.parentNode = null; }); this.childNodes = []; if (String(v) !== '') this._append(new Text(v)); }
  get innerText() { return this.textContent; }
  set innerText(v) { this.textContent = v; }
  get innerHTML() { return this.childNodes.map(serialize).join(''); }
  set innerHTML(v) {
    state.htmlLog.push(String(v));
    this.childNodes.forEach((c) => { c.parentNode = null; }); this.childNodes = [];
    parseHTML(String(v)).childNodes.slice().forEach((c) => this._append(c));
  }
  get outerHTML() { return serialize(this); }
  set outerHTML(v) { state.htmlLog.push(String(v)); }
  get content() { return this._content || this; }
  _append(child) {
    if (child.parentNode) child.parentNode.removeChild(child);
    if (child.nodeType === 1 && child.localName === '#fragment') { child.childNodes.slice().forEach((c) => this._append(c)); return child; }
    child.parentNode = this; this.childNodes.push(child); return child;
  }
  appendChild(c) { return this._append(c); }
  append(...items) { items.forEach((i) => this._append(typeof i === 'string' ? new Text(i) : i)); }
  prepend(...items) { items.reverse().forEach((i) => this.insertBefore(typeof i === 'string' ? new Text(i) : i, this.childNodes[0] || null)); }
  insertBefore(child, ref) {
    if (child.parentNode) child.parentNode.removeChild(child);
    if (child.nodeType === 1 && child.localName === '#fragment') { child.childNodes.slice().forEach((c) => this.insertBefore(c, ref)); return child; }
    const idx = ref ? this.childNodes.indexOf(ref) : -1;
    child.parentNode = this;
    if (idx < 0) this.childNodes.push(child); else this.childNodes.splice(idx, 0, child);
    return child;
  }
  replaceChildren(...items) { this.textContent = ''; this.append(...items); }
  removeChild(c) { const i = this.childNodes.indexOf(c); if (i >= 0) this.childNodes.splice(i, 1); c.parentNode = null; return c; }
  remove() { if (this.parentNode) this.parentNode.removeChild(this); }
  replaceWith(n) { const p = this.parentNode; if (p) { p.insertBefore(n, this); p.removeChild(this); } }
  contains(n) { for (let p = n; p; p = p.parentNode) if (p === this) return true; return false; }
  insertAdjacentHTML(pos, html) {
    state.htmlLog.push(String(html));
    const nodes = parseHTML(html).childNodes.slice();
    if (pos === 'beforeend') nodes.forEach((n) => this._append(n));
    else if (pos === 'afterbegin') nodes.reverse().forEach((n) => this.insertBefore(n, this.childNodes[0] || null));
    else if (this.parentNode) nodes.forEach((n) => this.parentNode.insertBefore(n, pos === 'beforebegin' ? this : this.nextSibling));
  }
  insertAdjacentElement(pos, el) {
    if (pos === 'beforeend') return this._append(el);
    if (pos === 'afterbegin') return this.insertBefore(el, this.childNodes[0] || null);
    if (this.parentNode) return this.parentNode.insertBefore(el, pos === 'beforebegin' ? this : this.nextSibling);
    return el;
  }
  cloneNode(deep) {
    const c = new El(this.localName, this.attrs);
    if (this._value !== undefined) c._value = this._value;
    if (this._content) c._content = this._content.cloneNode(true);
    if (deep) this.childNodes.forEach((ch) => { if (ch.nodeType === 1) c._append(ch.cloneNode(true)); else if (ch.nodeType === 3) c._append(new Text(ch.data)); });
    return c;
  }
  getAttribute(n) { return n in this.attrs ? this.attrs[n] : null; }
  setAttribute(n, v) { this.attrs[n] = String(v); if (n === 'value') this._value = String(v); }
  removeAttribute(n) { delete this.attrs[n]; }
  hasAttribute(n) { return n in this.attrs; }
  toggleAttribute(n, force) { const want = force === undefined ? !(n in this.attrs) : !!force; if (want) this.attrs[n] = ''; else delete this.attrs[n]; return want; }
  querySelectorAll(sel) {
    const out = [];
    const walk = (node) => { for (const c of node.childNodes) { if (c.nodeType === 1) { if (matchGroup(c, sel)) out.push(c); walk(c); } } };
    walk(this._content || this);
    return out;
  }
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
  getElementsByTagName(t) { return this.querySelectorAll(t); }
  getElementsByClassName(c) { return this.querySelectorAll('.' + c.split(/\s+/).join('.')); }
  matches(sel) { return matchGroup(this, sel); }
  closest(sel) { for (let p = this; p && p.nodeType === 1; p = p.parentNode) if (matchGroup(p, sel)) return p; return null; }
  addEventListener(type, fn, opts) { (this.listeners[type] = this.listeners[type] || []).push({ fn, once: !!(opts && opts.once) }); }
  removeEventListener(type, fn) { this.listeners[type] = (this.listeners[type] || []).filter((l) => l.fn !== fn); }
  dispatchEvent(ev) {
    ev.target = ev.target || this;
    for (let node = this; node; node = ev.bubbles ? node.parentNode : null) {
      ev.currentTarget = node;
      for (const l of (node.listeners && node.listeners[ev.type] || []).slice()) {
        try { l.fn.call(node, ev); } catch (e) { state.errors.push(String(e && e.stack || e)); }
        if (l.once) node.removeEventListener(ev.type, l.fn);
        if (ev._stopped) return !ev.defaultPrevented;
      }
      if (node === documentObj && ev.bubbles) { fireListeners(windowObj, ev); break; }
    }
    return !ev.defaultPrevented;
  }
  focus() { state.activeElement = this; state.focusLog.push(this.eid !== null ? this.eid : this.localName); }
  blur() { if (state.activeElement === this) state.activeElement = null; }
  click() { this.dispatchEvent(new Ev('click', { bubbles: true, cancelable: true })); }
  select() {}
  setSelectionRange(a, b) { this.selectionStart = a; this.selectionEnd = b; }
  scrollIntoView() {}
  scrollTo() {}
  getBoundingClientRect() { return { top: 0, left: 0, right: 0, bottom: 0, width: 0, height: 0 }; }
  submit() { state.submits.push({ eid: this.eid, fields: collectFields(this) }); }
  reset() {}
  requestSubmit() { const ev = new Ev('submit', { bubbles: true, cancelable: true }); if (this.dispatchEvent(ev)) this.submit(); }
}

function collectFields(form) {
  const out = [];
  for (const el of form.querySelectorAll('input,textarea,select')) {
    if (!el.attrs.name || 'disabled' in el.attrs) continue;
    const type = el.localName === 'input' ? (el.attrs.type || 'text') : el.localName;
    if (['submit', 'button', 'image', 'reset', 'file'].includes(type)) continue;
    if ((type === 'checkbox' || type === 'radio') && !('checked' in el.attrs)) continue;
    out.push([el.attrs.name, el.value]);
  }
  return out;
}

function fireListeners(target, ev) {
  for (const l of (target.listeners && target.listeners[ev.type] || []).slice()) {
    try { l.fn.call(target, ev); } catch (e) { state.errors.push(String(e && e.stack || e)); }
  }
}

function serialize(node) {
  if (node.nodeType === 3) return escapeHtml(node.data);
  const attrs = Object.entries(node.attrs).map(([k, v]) => ` ${k}="${escapeHtml(v)}"`).join('');
  return `<${node.localName}${attrs}>${node.innerHTML}</${node.localName}>`;
}

function build(desc, parent) {
  const el = new El(desc.tag, desc.attrs);
  el.eid = desc.eid; state.byEid.set(desc.eid, el);
  if (desc.tag === 'template') {
    el._content = new El('#fragment', {});
    for (const c of desc.children) { if (typeof c === 'string') el._content._append(new Text(c)); else build(c, el._content); }
  } else {
    for (const c of desc.children) { if (typeof c === 'string') el._append(new Text(c)); else build(c, el); }
  }
  if (parent) { el.parentNode = parent; parent.childNodes.push(el); }
  return el;
}

// ---------------------------------------------------------------------------------------------------------------
const documentObj = new El('#document', {});
documentObj.nodeType = 9; documentObj.readyState = 'loading'; documentObj.cookie = ''; documentObj.title = '';
const root = scenario.tree;
for (const c of root.children) { if (typeof c !== 'string') build(c, documentObj); }
const findTag = (t) => { let f = null; (function walk(n) { for (const c of n.childNodes) { if (c.nodeType === 1) { if (!f && c.localName === t) f = c; walk(c); } } })(documentObj); return f; };
documentObj.documentElement = findTag('html') || documentObj.children[0];
documentObj.body = findTag('body') || documentObj.documentElement;
documentObj.head = findTag('head');
Object.defineProperty(documentObj, 'activeElement', { get: () => state.activeElement || documentObj.body });
documentObj.getElementById = (id) => { let f = null; (function walk(n) { for (const c of n.childNodes) { if (c.nodeType === 1) { if (!f && c.attrs.id === id) f = c; walk(c); } } })(documentObj); return f; };
documentObj.createElement = (t) => new El(t, {});
documentObj.createElementNS = (ns, t) => new El(t, {});
documentObj.createTextNode = (t) => new Text(t);
documentObj.createDocumentFragment = () => new El('#fragment', {});
documentObj.createComment = () => new Text('');
documentObj.write = () => { throw new Error('document.write is not allowed'); };
documentObj.forms = { get length() { return documentObj.querySelectorAll('form').length; } };

// timers ----------------------------------------------------------------------------------------------------------
function addTimer(fn, ms, args, interval) {
  const t = { id: ++state.timerSeq, at: state.now + Math.max(0, Number(ms) || 0), delay: Math.max(0, Number(ms) || 0), fn, args, interval: interval ? Math.max(0, Number(ms) || 0) : null, cancelled: false };
  state.timers.push(t); return t.id;
}
const clearTimer = (id) => { const t = state.timers.find((x) => x.id === id); if (t) t.cancelled = true; };
const pending = () => state.timers.filter((t) => !t.cancelled).sort((a, b) => a.at - b.at || a.id - b.id);

// fetch and XHR ---------------------------------------------------------------------------------------------------
function nextResponse() {
  return state.fetchQueue.length ? state.fetchQueue.shift() : { status: 200, json: { status: 'active', messages: [] } };
}
function makeResponse(r) {
  const body = r.body !== undefined ? r.body : JSON.stringify(r.json === undefined ? {} : r.json);
  const status = r.status === undefined ? 200 : r.status;
  const res = { ok: status >= 200 && status < 300, status, statusText: '', url: '', redirected: false,
    headers: { get: (n) => (n && n.toLowerCase() === 'content-type' ? 'application/json' : null) },
    json: () => new Promise((ok, bad) => { try { ok(JSON.parse(body)); } catch (e) { bad(e); } }),
    text: () => Promise.resolve(body), clone: () => makeResponse(r) };
  return res;
}
function plain(v) {
  if (v instanceof URLSearchParams) return { __urlencoded: v.toString() };
  if (v && v.constructor && v.constructor.name === 'FormData') return { __form: v.d };
  if (v && v.constructor && v.constructor.name === 'Headers') return v.h;
  if (typeof v === 'function') return undefined;
  return v;
}
function fetchStub(url, opts) {
  let logged = null;
  if (opts) {
    logged = {};
    for (const k of Object.keys(opts)) { if (k !== 'signal') logged[k] = k === 'body' ? plain(opts[k]) : (k === 'headers' ? plain(opts[k]) : opts[k]); }
    if (logged.body === undefined && opts.body !== undefined) logged.body = opts.body;
  }
  state.fetchLog.push({ url: String(url && url.url ? url.url : url), options: logged });
  const r = nextResponse();
  if (r.reject) return Promise.reject(new TypeError('Failed to fetch'));
  if (r.hang) {
    return new Promise((_, rej) => {
      if (opts && opts.signal) opts.signal.addEventListener('abort', () => rej(Object.assign(new Error('The operation was aborted'), { name: 'AbortError' })));
    });
  }
  return Promise.resolve(makeResponse(r));
}
class XHR {
  constructor() { this.readyState = 0; this.status = 0; this.responseText = ''; this.response = null; this.listeners = {}; this.headers = {}; this.responseType = ''; }
  open(method, url) { this.method = method; this.url = url; this.readyState = 1; }
  setRequestHeader(k, v) { this.headers[k] = v; }
  addEventListener(t, fn) { (this.listeners[t] = this.listeners[t] || []).push(fn); }
  getResponseHeader() { return 'application/json'; }
  abort() {}
  send() {
    state.fetchLog.push({ url: String(this.url), options: { method: this.method, headers: this.headers, xhr: true } });
    const r = nextResponse();
    Promise.resolve().then(() => {
      if (r.reject) { if (this.onerror) this.onerror({}); (this.listeners.error || []).forEach((f) => f({})); return; }
      const body = r.body !== undefined ? r.body : JSON.stringify(r.json === undefined ? {} : r.json);
      this.status = r.status === undefined ? 200 : r.status; this.responseText = body; this.readyState = 4;
      this.response = this.responseType === 'json' ? JSON.parse(body) : body;
      if (this.onreadystatechange) this.onreadystatechange({});
      if (this.onload) this.onload({});
      (this.listeners.load || []).forEach((f) => f({}));
      (this.listeners.readystatechange || []).forEach((f) => f({}));
    });
  }
}

// window ----------------------------------------------------------------------------------------------------------
const store = () => { const m = new Map(); return { getItem: (k) => (m.has(k) ? m.get(k) : null), setItem: (k, v) => m.set(k, String(v)), removeItem: (k) => m.delete(k), clear: () => m.clear() }; };
const loc = { href: 'http://testserver' + scenario.pathname + (scenario.search || ''), pathname: scenario.pathname, search: scenario.search || '', origin: 'http://testserver', host: 'testserver', hostname: 'testserver', protocol: 'http:', hash: '',
  reload() { state.reloads++; }, assign(u) { state.reloads++; state.assigned = String(u); }, replace(u) { state.reloads++; state.assigned = String(u); } };
const sandbox = {
  document: documentObj, location: loc, history: { pushState() {}, replaceState() {} }, navigator: { userAgent: 'node-harness', language: 'en-US', onLine: true, sendBeacon: () => true },
  localStorage: store(), sessionStorage: store(), fetch: fetchStub, XMLHttpRequest: XHR,
  setTimeout: (fn, ms, ...a) => addTimer(fn, ms, a, false), setInterval: (fn, ms, ...a) => addTimer(fn, ms, a, true),
  clearTimeout: clearTimer, clearInterval: clearTimer, requestAnimationFrame: (fn) => addTimer(fn, 16, [], false), cancelAnimationFrame: clearTimer,
  console: { log: (...a) => state.consoleLog.push(a.map(String).join(' ')), warn: (...a) => state.consoleLog.push(a.map(String).join(' ')), error: (...a) => state.consoleLog.push(a.map(String).join(' ')), info() {}, debug() {} },
  getComputedStyle: () => ({ getPropertyValue: () => '' }), matchMedia: () => ({ matches: false, addEventListener() {}, addListener() {} }),
  Event: Ev, CustomEvent: Ev, KeyboardEvent: Ev, InputEvent: Ev,
  Element: El, HTMLElement: El, HTMLTextAreaElement: El, HTMLInputElement: El, HTMLFormElement: El, Node: { ELEMENT_NODE: 1, TEXT_NODE: 3 },
  MutationObserver: class { observe() {} disconnect() {} }, IntersectionObserver: class { observe() {} disconnect() {} }, ResizeObserver: class { observe() {} disconnect() {} },
  URL, URLSearchParams, AbortController, TextEncoder, TextDecoder,
  performance: { now: () => state.now }, structuredClone: (v) => JSON.parse(JSON.stringify(v)), scrollTo() {}, innerWidth: 390, innerHeight: 800,
  Headers: class { constructor(h) { this.h = h || {}; } get(k) { return this.h[k] || null; } }, FormData: class FormData { constructor(form) { this.d = form && form.querySelectorAll ? collectFields(form) : []; } append(k, v) { this.d.push([k, String(v)]); } set(k, v) { this.d = this.d.filter((x) => x[0] !== k); this.d.push([k, String(v)]); } get(k) { const f = this.d.find((x) => x[0] === k); return f ? f[1] : null; } has(k) { return this.d.some((x) => x[0] === k); } entries() { return this.d[Symbol.iterator](); } forEach(fn) { this.d.forEach(([k, v]) => fn(v, k)); } },
  Request: class { constructor(url, o) { this.url = url; this.o = o; } },
};
sandbox.window = sandbox; sandbox.self = sandbox; sandbox.globalThis = sandbox; sandbox.top = sandbox; sandbox.parent = sandbox;
sandbox.listeners = {};
sandbox.addEventListener = (t, fn, o) => { (sandbox.listeners[t] = sandbox.listeners[t] || []).push({ fn, once: !!(o && o.once) }); };
sandbox.removeEventListener = (t, fn) => { sandbox.listeners[t] = (sandbox.listeners[t] || []).filter((l) => l.fn !== fn); };
sandbox.dispatchEvent = (ev) => { fireListeners(sandbox, ev); return true; };
documentObj.parentNode = null;
const windowObj = sandbox;
const ctx = vm.createContext(sandbox);

const flush = async () => { for (let i = 0; i < 5; i++) await new Promise((r) => realSetImmediate(r)); };

process.on('unhandledRejection', (e) => { state.errors.push('unhandledRejection: ' + String(e && e.stack || e)); });

const aggregate = (node) => {
  let out = '';
  if (node.nodeType === 3) return node.data;
  if (node.localName === 'script' || node.localName === 'style') return '';
  for (const c of node.childNodes) out += aggregate(c) + (c.nodeType === 1 && /^(p|div|li|section|article|h\d|br|header|footer)$/.test(c.localName) ? ' ' : '');
  return out;
};

function findAll(text, tag) {
  const out = [];
  (function walk(n) {
    for (const c of n.childNodes) {
      if (c.nodeType !== 1) continue;
      if ((!tag || c.localName === tag) && (text === undefined || c.textContent.trim() === text)) out.push(c);
      walk(c);
    }
  })(documentObj);
  return out;
}
function describe(el) {
  let visible = true;
  for (let n = el; n && n !== documentObj; n = n.parentNode) { if ('hidden' in n.attrs || (n.style && n.style.display === 'none')) visible = false; }
  return { tag: el.localName, attrs: el.attrs, visible, text: el.textContent.trim(), eid: el.eid, disabled: 'disabled' in el.attrs,
    ancestors: (() => { const a = []; for (let n = el.parentNode; n && n !== documentObj; n = n.parentNode) a.push(n.localName); return a; })() };
}

async function runOp(op) {
  switch (op.op) {
    case 'load': {
      const code = fs.readFileSync(op.script, 'utf8');
      try { vm.runInContext(code, ctx, { filename: op.script }); } catch (e) { state.errors.push('load: ' + String(e && e.stack || e)); }
      documentObj.readyState = 'interactive';
      const dcl = new Ev('DOMContentLoaded', { bubbles: true });
      fireListeners(documentObj, dcl); fireListeners(sandbox, dcl);
      documentObj.readyState = 'complete';
      fireListeners(sandbox, new Ev('load'));
      await flush();
      return { errors: state.errors.slice() };
    }
    case 'set_value': state.byEid.get(op.eid).value = op.value; return null;
    case 'fire': { const el = state.byEid.get(op.eid); const ok = el.dispatchEvent(new Ev(op.type, { bubbles: true, cancelable: true })); await flush(); return ok; }
    case 'focus': state.byEid.get(op.eid).focus(); state.focusLog.length = 0; return null;
    case 'text': return state.byEid.get(op.eid).textContent;
    case 'value': return state.byEid.get(op.eid).value;
    case 'attr': { const v = state.byEid.get(op.eid).getAttribute(op.name); return v; }
    case 'prop': return state.byEid.get(op.eid)[op.name];
    case 'connected': return state.byEid.get(op.eid).isConnected;
    case 'alerts': return findAll(undefined, undefined).filter((e) => e.attrs.role === 'alert').map(describe).filter((d) => d.visible && d.text !== '').map((d) => d.text);
    case 'live_regions': return findAll(undefined, undefined).filter((e) => e.attrs['aria-live'] === 'polite' || e.attrs.role === 'status').map((e) => ({ text: e.textContent.trim(), eid: e.eid, attrs: e.attrs }));
    case 'fire_window': { const ev = new Ev(op.type, { bubbles: false }); ev.persisted = !!op.persisted; fireListeners(sandbox, ev); await flush(); return null; }
    case 'delete_global': delete sandbox[op.name]; return null;
    case 'submits': return state.submits.slice();
    case 'active_desc': { const a = state.activeElement; return a ? { eid: a.eid, tag: a.localName, text: a.textContent.trim(), attrs: a.attrs } : null; }
    case 'find': return findAll(op.text, op.tag).map(describe);
    case 'click_text': { const f = findAll(op.text, op.tag); if (!f.length) return false; f[f.length - 1].click(); await flush(); return true; }
    case 'active': return state.activeElement ? state.activeElement.eid : null;
    case 'dom_text': return aggregate(op.eid === undefined ? documentObj : state.byEid.get(op.eid));
    case 'html_log': return state.htmlLog.slice();
    case 'fetch_log': return state.fetchLog.slice();
    case 'focus_log': return state.focusLog.slice();
    case 'reloads': return state.reloads;
    case 'errors': return state.errors.slice();
    case 'console': return state.consoleLog.slice();
    case 'queue_fetch': state.fetchQueue.push(...op.responses); return null;
    case 'timers': return pending().map((t) => ({ delay: t.delay, interval: t.interval, at: t.at }));
    case 'run_timer': {
      const t = pending()[0];
      if (!t) return { ran: false };
      state.now = Math.max(state.now, t.at);
      if (t.interval !== null) t.at = state.now + t.interval; else t.cancelled = true;
      try { const r = t.fn(...t.args); if (r && r.then) r.catch((e) => state.errors.push(String(e))); } catch (e) { state.errors.push(String(e && e.stack || e)); }
      await flush();
      return { ran: true, delay: t.delay };
    }
    case 'flush': await flush(); return null;
    // Added for the front-end fixes tests: act on elements the page scripts created later (they have no eid).
    case 'fire_sel': { const el = documentObj.querySelectorAll(op.selector)[op.index || 0]; if (!el) throw new Error('no element for ' + op.selector); const ok = el.dispatchEvent(new Ev(op.type, { bubbles: true, cancelable: true })); await flush(); return ok; }
    case 'query': return documentObj.querySelectorAll(op.selector).map((e) => ({ attrs: Object.assign({}, e.attrs), html: e.innerHTML, text: e.textContent.trim() }));
    default: throw new Error('unknown op ' + op.op);
  }
}

(async () => {
  const results = [];
  for (const op of scenario.ops) {
    try { results.push(await runOp(op)); } catch (e) { results.push({ __error: String(e && e.stack || e) }); }
  }
  process.stdout.write(JSON.stringify(results));
})();
