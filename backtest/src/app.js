/* 똑재TV 백테스트 계산기 — 화면 (계산은 engine.js 의 BT.simulate) */
'use strict';

const DATA = '../data/';                         // portfolio-sheet-data/data/
const MAX_HOLDINGS = 10;
const KR_STOCK_SAFE_START = '2000-01';           // 국내 주식 2000년 이전 데이터는 불안정
const FX_START = '1990-03';
const COLORS = ['#D9542B', '#8B6F47', '#3A6F8F', '#C4A57B', '#5A6B5A', '#9C3D54', '#E89A6F', '#7A9A3A', '#7E6BA0', '#4F8A8B'];

// 검색창을 비워 두면 보여 줄 추천 종목
const RECO = [
  ['국내상장 국내 ETF', ['K:069500', 'K:161510', 'K:091170']],
  ['국내상장 해외 ETF', ['K:360750', 'K:133690', 'K:458730', 'K:441640']],
  ['국내 개별주', ['K:005930', 'K:000660', 'K:105560', 'K:033780']],
  ['미국 직투 (주식·ETF)', ['U:SPY', 'U:QQQ', 'U:SCHD', 'U:JEPI', 'U:TLT', 'U:GLD', 'U:O', 'U:KO', 'U:AAPL', 'U:MSFT']],
];

// ── 상태 ────────────────────────────────────────────────
const S = {
  holdings: [],                  // {id, key, w}
  mode: 'mix', initial: 1000, monthly: 50,
  start: '', end: '', periodAuto: true,
  reinvest: true, rebalance: false, fxKRW: true,
};
let nextId = 1;
let searchMarket = 'ALL';
const U = new Map();             // 키 → 종목 정보
let UL = [];                     // 검색용 배열
let FX = new Map();
let FLAGS = null;                // 키 → [{m, type, val}]
let UPDATED = '';
// 이번 달 값 — 종목별 이력에는 '끝난 달'만 있고, 이번 달은 30분마다 갱신되는 시세 파일에서 붙인다
const LIVE = { month: '', fx: null, fxDate: '', files: new Map() };
const LIVE_FILE = { KE: ['etf_prices.csv', '종목코드', '현재가'], KS: ['kr_stocks.csv', '종목코드', '현재가'], U: ['us_prices.csv', '티커', '현재가USD'] };
const ASSET = new Map();         // 키 → Promise<{px, dv}>
const charts = {};

// ── 유틸 ────────────────────────────────────────────────
const $ = id => document.getElementById(id);
function escapeHtml(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}
const normText = s => String(s || '').toLowerCase().replace(/[\s()·.\-_&]/g, '');
const ym = n => `${String(n).slice(0, 4)}-${String(n).slice(4, 6)}`;
const mnum = m => +m.slice(0, 4) * 12 + (+m.slice(5, 7) - 1);
const fromNum = x => `${Math.floor(x / 12)}-${String(x % 12 + 1).padStart(2, '0')}`;
const maxM = (a, b) => (a > b ? a : b), minM = (a, b) => (a < b ? a : b);
function prevMonth() { const d = new Date(); return fromNum(d.getFullYear() * 12 + d.getMonth() - 1); }
function spanText(n) { const y = Math.floor(n / 12), m = n % 12; return (y ? `${y}년` : '') + (m ? ` ${m}개월` : '') || '0개월'; }

function fmtWon(n) {                              // 결과 카드: 1억 4,748만원
  n = Number(n); if (!isFinite(n)) return '-';
  const neg = n < 0, a = Math.abs(n);
  let s;
  if (a >= 1e4) {
    const man = Math.round(a / 1e4), eok = Math.floor(man / 10000), rem = man % 10000;
    s = eok ? `${eok.toLocaleString()}억${rem ? ' ' + rem.toLocaleString() + '만' : ''}원` : `${rem.toLocaleString()}만원`;
  } else s = `${Math.round(a).toLocaleString()}원`;
  return (neg ? '-' : '') + s;
}
function fmtShort(n) {                            // 차트 축: 1.5억 / 3,000만
  const a = Math.abs(n), sg = n < 0 ? '-' : '';
  if (a >= 1e8) return sg + (a / 1e8).toFixed(a >= 1e9 ? 0 : 1).replace(/\.0$/, '') + '억';
  if (a >= 1e4) return sg + Math.round(a / 1e4).toLocaleString() + '만';
  return sg + Math.round(a).toLocaleString();
}
const pct = (v, d = 1) => (v == null || !isFinite(v)) ? '-' : (v > 0 ? '+' : '') + (v * 100).toFixed(d) + '%';
const pctPlain = (v, d = 1) => (v == null || !isFinite(v)) ? '-' : (v * 100).toFixed(d) + '%';
const cls = v => v > 0 ? 'up' : v < 0 ? 'down' : '';

function parseCSV(text) {
  const lines = text.replace(/\r/g, '').trim().split('\n');
  const head = lines.shift().replace(/^﻿/, '').split(',');
  return lines.map(l => { const c = l.split(','); const o = {}; head.forEach((h, i) => { o[h] = c[i]; }); return o; });
}
async function getText(path) {
  const r = await fetch(DATA + path, { cache: 'default' });
  if (!r.ok) throw new Error(`${path} ${r.status}`);
  return r.text();
}

// ── 데이터 ──────────────────────────────────────────────
async function loadIndex() {
  const idx = JSON.parse(await getText('bt/index.json'));
  UPDATED = idx.updated;
  idx.rows.forEach(([key, name, kind, s0, s1, div, q, src]) => {
    const [mk, code] = key.split(':');
    const it = { key, name, mk, code, kind: kind === 'E' ? 'ETF' : '주식', start: ym(s0), end: ym(s1), div: !!div, q, src: src || '' };
    it.norm = normText(name); it.codeNorm = normText(code);
    it.label = mk === 'U' ? `${code} · ${name}` : name;
    U.set(key, it);
  });
  UL = [...U.values()];
  const fx = parseCSV(await getText(idx.fx));
  FX = new Map(fx.map(r => [r['월'], +r['매매기준율']]));
}
// 이번 달: 매매기준율(매일) + 시장별 시세 파일(30분마다)
async function loadLiveFx() {
  try {
    const rows = parseCSV(await getText('fx_std.csv')).filter(r => r['매매기준율']);
    const last = rows[rows.length - 1];
    LIVE.fx = +last['매매기준율']; LIVE.fxDate = last['날짜'];
  } catch (e) { /* 없으면 이번 달 미국 종목은 지난달 환율로 */ }
  const d = new Date(Date.now() + 9 * 3600e3);          // 한국 시간
  LIVE.month = `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, '0')}`;
}
function liveKind(it) { return it.mk === 'U' ? 'U' : it.kind === 'ETF' ? 'KE' : 'KS'; }
function loadLive(kind) {
  if (!LIVE.files.has(kind)) {
    const [file, kcol, vcol] = LIVE_FILE[kind];
    LIVE.files.set(kind, getText(file).then(t => {
      const m = new Map(); let stamp = '';
      parseCSV(t).forEach(r => { const v = +r[vcol]; if (v > 0) m.set(r[kcol], v); if ((r['갱신시각'] || '') > stamp) stamp = r['갱신시각']; });
      return { m, stamp };
    }).catch(() => ({ m: new Map(), stamp: '' })));
  }
  return LIVE.files.get(kind);
}
// 이력이 지난달까지 차 있는 종목만 이번 달로 늘릴 수 있다
const liveOk = it => LIVE.month && it.end === fromNum(mnum(LIVE.month) - 1);
const endOf = it => (liveOk(it) ? LIVE.month : it.end);

async function loadFlags() {
  if (FLAGS) return FLAGS;
  FLAGS = new Map();
  try {
    parseCSV(await getText('history/quality_flags.csv')).forEach(r => {
      const mk = r['구분'] === 'us' ? 'U' : 'K';
      const k = `${mk}:${r['종목코드']}`;
      if (!FLAGS.has(k)) FLAGS.set(k, []);
      FLAGS.get(k).push({ m: r['월'], type: r['유형'], val: r['값'] });
    });
  } catch (e) { /* 품질표를 못 읽어도 계산은 한다 */ }
  return FLAGS;
}
// 파일명 규칙 — scripts/winsafe.py 와 같다 (Windows 예약어 CON·AUX… 뒤에 '_')
const RESERVED = new Set(['CON', 'PRN', 'AUX', 'NUL', ...[1, 2, 3, 4, 5, 6, 7, 8, 9].flatMap(i => ['COM' + i, 'LPT' + i])]);
function toFilename(code) {
  const s = String(code).replace(/\//g, '_'), i = s.indexOf('.');
  const base = i < 0 ? s : s.slice(0, i), rest = i < 0 ? '' : s.slice(i);
  return (RESERVED.has(base.toUpperCase()) ? base + '_' : base) + rest;
}
function pricePath(it) {
  if (it.mk === 'U') return `history/us/${toFilename(it.code)}.csv`;
  if (it.kind === 'ETF') return it.src === 'n' ? `history/kr_etf/${it.code}.csv` : `history/kr_etf_krx/${it.code}.csv`;
  return `history/kr_stock/${it.code}.csv`;
}
function loadAsset(key) {
  if (!ASSET.has(key)) {
    const it = U.get(key);
    ASSET.set(key, (async () => {
      const px = new Map(parseCSV(await getText(pricePath(it))).filter(r => r['종가']).map(r => [r['월'], +r['종가']]));
      let dv = new Map();
      if (it.div) dv = new Map(parseCSV(await getText(`bt/div/${it.mk}/${toFilename(it.code)}.csv`)).map(r => [r['월'], +r['금액']]));
      return { px, dv };
    })().catch(e => { ASSET.delete(key); throw e; }));
  }
  return ASSET.get(key);
}

// ── 검색 ────────────────────────────────────────────────
function searchUniverse(q) {
  const nq = normText(q); if (!nq) return [];
  const tokens = q.toLowerCase().split(/\s+/).map(normText).filter(Boolean);
  const reco = new Set(RECO.flatMap(g => g[1]));
  const res = [];
  for (const it of UL) {
    if (searchMarket !== 'ALL' && it.mk !== searchMarket) continue;
    let s;
    if (it.codeNorm === nq) s = 1000;
    else if (it.norm === nq) s = 900;
    else if (it.codeNorm.startsWith(nq)) s = 500;
    else if (it.norm.startsWith(nq)) s = 450;
    else if (it.norm.includes(nq)) s = 300;
    else if (tokens.length > 1 && tokens.every(t => it.norm.includes(t) || it.codeNorm.includes(t))) s = 200;
    else continue;
    if (reco.has(it.key)) s += 80;
    if (it.mk === 'K') s += 10;
    s += Math.min(40, (mnum(it.end) - mnum(it.start)) / 12);  // 이력이 긴 종목을 조금 위로
    s -= it.name.length * 0.3;
    res.push([s, it]);
  }
  res.sort((a, b) => b[0] - a[0]);
  return res.slice(0, 50).map(x => x[1]);
}
function optionHtml(key, idx) {
  const it = U.get(key); if (!it) return '';
  const badge = it.mk === 'K' ? `<span class="kbadge kr">국내 ${it.kind}</span>` : `<span class="kbadge us">미국 ${it.kind}</span>`;
  return `<div class="combo-opt" data-key="${escapeHtml(key)}" data-idx="${idx}">
    <div class="opt-name">${escapeHtml(it.mk === 'U' ? it.code + ' · ' + it.name : it.name)}</div>
    <div class="opt-sub">${badge}<span>${escapeHtml(it.code)}</span><span>${it.start} ~</span>${it.div ? '' : '<span>배당 기록 없음</span>'}</div>
  </div>`;
}
function renderComboList(listEl, query) {
  const q = query.trim();
  const tabs = [['ALL', '전체'], ['K', '국내상장'], ['U', '미국상장']]
    .map(([v, t]) => `<button type="button" class="mkt-tab ${searchMarket === v ? 'on' : ''}" data-mkt="${v}">${t}</button>`).join('');
  let html = `<div class="combo-tabs">${tabs}</div>`, idx = 0;
  if (!UL.length) html += `<div class="combo-empty">종목 데이터를 불러오는 중입니다…</div>`;
  else if (!q) {
    html += `<div class="combo-hint">종목명이나 코드를 입력하면 <b>국내·미국 1만 6천여 종목</b>에서 찾습니다</div>`;
    RECO.forEach(([g, keys]) => {
      const ks = keys.filter(k => U.has(k) && (searchMarket === 'ALL' || k[0] === searchMarket));
      if (!ks.length) return;
      html += `<div class="combo-group">추천 · ${escapeHtml(g)}</div>`;
      ks.forEach(k => { html += optionHtml(k, idx++); });
    });
  } else {
    const r = searchUniverse(q);
    if (r.length) r.forEach(it => { html += optionHtml(it.key, idx++); });
    else html += `<div class="combo-empty">‘${escapeHtml(q)}’ 검색 결과가 없습니다</div>`;
  }
  listEl.innerHTML = html;
  listEl.dataset.active = '-1';
}
function bindCombo(input) {
  const list = input.parentElement.querySelector('.combo-list');
  const id = Number(input.dataset.id);
  let timer = null;
  const open = q => { renderComboList(list, q); list.hidden = false; };
  const close = () => { list.hidden = true; };
  const choose = opt => {
    const h = S.holdings.find(x => x.id === id);
    if (h && opt.dataset.key) { h.key = opt.dataset.key; S.periodAuto = true; }
    close(); input.blur(); renderHoldings(); schedule();
  };
  input.addEventListener('focus', () => { input.value = ''; open(''); });
  input.addEventListener('blur', () => setTimeout(() => { close(); if (document.activeElement !== input) input.value = input.dataset.shown; }, 150));
  input.addEventListener('input', () => { clearTimeout(timer); timer = setTimeout(() => open(input.value), 80); });
  input.addEventListener('keydown', e => {
    const opts = [...list.querySelectorAll('.combo-opt')];
    let a = Number(list.dataset.active || -1);
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault(); if (list.hidden) open(input.value);
      a = e.key === 'ArrowDown' ? Math.min(opts.length - 1, a + 1) : Math.max(0, a - 1);
      opts.forEach((o, i) => o.classList.toggle('active', i === a));
      if (opts[a]) opts[a].scrollIntoView({ block: 'nearest' });
      list.dataset.active = String(a);
    } else if (e.key === 'Enter') {
      e.preventDefault(); const pick = a >= 0 ? opts[a] : opts[0]; if (pick) choose(pick);
    } else if (e.key === 'Escape') input.blur();
  });
  list.addEventListener('mousedown', e => {
    e.preventDefault();
    const tab = e.target.closest('.mkt-tab');
    if (tab) { searchMarket = tab.dataset.mkt; open(input.value); return; }
    const opt = e.target.closest('.combo-opt'); if (opt) choose(opt);
  });
}

// ── 입력칸 단위 표시 (배당 재투자 계산기와 같은 방식) ─────────────
const unitCanvas = document.createElement('canvas').getContext('2d');
function placeUnits() {
  document.querySelectorAll('.unit-wrap input[data-unit]').forEach(inp => {
    let tag = inp.parentNode.querySelector('.input-unit');
    if (!tag) { tag = document.createElement('span'); tag.className = 'input-unit'; tag.textContent = inp.dataset.unit; inp.after(tag); }
    const cs = getComputedStyle(inp);
    if (inp.value === '' || !inp.offsetWidth || inp.scrollWidth > inp.clientWidth + 1) { tag.style.visibility = 'hidden'; return; }
    unitCanvas.font = `${cs.fontWeight} ${cs.fontSize} ${cs.fontFamily}`;
    const w = unitCanvas.measureText(inp.value).width;
    const left = parseFloat(cs.paddingLeft) + parseFloat(cs.borderLeftWidth) + w + 4 - inp.scrollLeft;
    tag.style.left = Math.round(left) + 'px';
    tag.style.fontSize = cs.fontSize;
    const spin = inp.type === 'number' && matchMedia('(hover: hover) and (pointer: fine)').matches ? 16 : 0;
    const limit = inp.clientWidth - parseFloat(cs.paddingRight) + (spin ? -spin : 8);
    tag.style.visibility = left + tag.offsetWidth < limit ? 'visible' : 'hidden';
  });
}

// ── 종목 행 ────────────────────────────────────────────
function holdingInfo(h) {
  const it = U.get(h.key);
  if (!it) return '<div class="bt-span">종목을 골라 주세요</div>';
  const parts = [`<b>${it.start}</b>부터 데이터`];
  if (it.mk === 'U') parts.push('달러 종목');
  if (it.src === 'n') parts.push('분배금이 가격에 이미 반영된 네이버 가격');
  else if (!it.div) parts.push('배당 기록 없음');
  if (it.mk === 'K' && it.kind === '주식' && it.start < KR_STOCK_SAFE_START) parts.push('2000년 이전은 오류 가능');
  const flag = it.q ? `<span class="bt-flag">데이터 확인 구간 ${it.q}곳</span>` : '';
  return `<div class="bt-span">${parts.join(' · ')}${flag}</div>`;
}
function renderHoldings() {
  const c = $('holdings-container'); c.innerHTML = '';
  S.holdings.forEach(h => {
    const it = U.get(h.key);
    const shown = it ? it.label : (h.key ? h.key.split(':')[1] : '');
    const row = document.createElement('div');
    row.className = 'holding-row';
    row.innerHTML = `
      <div class="bt-grid">
        <div>
          <label class="label">종목 검색</label>
          <div class="combo">
            <input type="text" class="combo-input" data-id="${h.id}" value="${escapeHtml(shown)}" data-shown="${escapeHtml(shown)}"
              placeholder="종목명·코드 (예: SCHD, 삼성전자)" autocomplete="off" spellcheck="false">
            <svg class="combo-icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><circle cx="11" cy="11" r="7"/><path d="M21 21l-4.3-4.3"/></svg>
            <div class="combo-list" hidden></div>
          </div>
        </div>
        <div>
          <label class="label">비중</label>
          <span class="unit-wrap"><input type="number" data-id="${h.id}" data-field="w" data-unit="%" value="${h.w}" min="0" max="100" step="5" inputmode="decimal"></span>
        </div>
        <button type="button" class="btn-icon holding-del" data-del="${h.id}" title="종목 빼기" ${S.holdings.length <= 1 ? 'disabled' : ''}>
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><path d="M18 6L6 18M6 6l12 12"/></svg>
        </button>
      </div>
      ${holdingInfo(h)}`;
    c.appendChild(row);
  });
  c.querySelectorAll('.combo-input').forEach(bindCombo);
  c.querySelectorAll('input[data-field="w"]').forEach(inp => inp.addEventListener('input', () => {
    const h = S.holdings.find(x => x.id === Number(inp.dataset.id)); h.w = Math.max(0, Number(inp.value) || 0);
    updateWeightTotal(); placeUnits(); schedule();
  }));
  c.querySelectorAll('[data-del]').forEach(b => b.addEventListener('click', () => {
    S.holdings = S.holdings.filter(x => x.id !== Number(b.dataset.del)); S.periodAuto = true;
    renderHoldings(); schedule();
  }));
  $('add-holding-btn').disabled = S.holdings.length >= MAX_HOLDINGS;
  $('fx-section').style.display = S.holdings.some(h => h.key.startsWith('U:')) ? '' : 'none';
  updateWeightTotal(); placeUnits();
}
function updateWeightTotal() {
  const t = Math.round(S.holdings.reduce((s, h) => s + (h.w || 0), 0) * 100) / 100;
  $('weight-total').textContent = t + '%';
  $('weight-warn').style.display = t === 100 ? 'none' : 'inline';
  $('weight-total').style.color = t === 100 ? 'var(--accent)' : '#C44';
}
function setCombo(str) {
  S.holdings = str.split(',').map(s => { const [k, w] = s.split('*'); return { id: nextId++, key: k, w: +w || 0 }; }).slice(0, MAX_HOLDINGS);
  S.periodAuto = true; renderHoldings(); schedule();
}

// ── 기간 ────────────────────────────────────────────────
function periodRange() {
  const its = S.holdings.map(h => U.get(h.key)).filter(Boolean);
  if (!its.length) return null;
  let lo = its.reduce((m, it) => maxM(m, it.start), '0000-00'), limiter = its.find(it => it.start === lo);
  const hi = its.reduce((m, it) => minM(m, endOf(it)), '9999-99');
  if (its.some(it => it.mk === 'U') && lo < FX_START) { lo = FX_START; limiter = null; }
  // 시작 달은 그 달 종가를 쓰므로 데이터 첫 달부터 가능
  const safeLo = its.some(it => it.mk === 'K' && it.kind === '주식') ? maxM(lo, KR_STOCK_SAFE_START) : lo;
  return { lo, hi, safeLo, limiter: its.length > 1 ? limiter : null };
}
function applyPeriod() {
  const r = periodRange();
  if (!r) return null;
  const $s = $('start'), $e = $('end');
  $s.min = $e.min = r.lo; $s.max = $e.max = r.hi;
  if (S.periodAuto || !S.start || !S.end) {
    S.start = r.safeLo; S.end = r.hi;
  }
  S.start = minM(maxM(S.start, r.lo), r.hi); S.end = minM(maxM(S.end, r.lo), r.hi);
  if (mnum(S.end) - mnum(S.start) < 1) { S.start = fromNum(Math.max(mnum(r.lo), mnum(r.hi) - 1)); S.end = r.hi; }
  $s.value = S.start; $e.value = S.end;
  const n = mnum(S.end) - mnum(S.start);
  $('period-hint').textContent = `${spanText(n)} · 가능 기간 ${r.lo} ~ ${r.hi}`;
  return r;
}
function quickPeriod(q) {
  const r = periodRange(); if (!r) return;
  const end = r.hi;
  if (q === 'max') { S.start = r.safeLo; S.end = end; }
  else if (q.includes(':')) { [S.start, S.end] = q.split(':'); }
  else { S.end = end; S.start = fromNum(mnum(end) - 12 * +q); }
  S.periodAuto = false; schedule();
}

// ── 실행 ────────────────────────────────────────────────
let timer = null, runSeq = 0;
function schedule() { clearTimeout(timer); timer = setTimeout(run, 250); }
function readInputs() {
  S.initial = Math.max(0, Number($('initial').value) || 0);
  S.monthly = Math.max(0, Number($('monthly').value) || 0);
  S.reinvest = $('reinvest').checked; S.rebalance = $('rebalance').checked; S.fxKRW = $('fxmode').checked;
  $('initial-hint').textContent = fmtWon(S.initial * 1e4);
  $('monthly-hint').textContent = `매달 말 ${fmtWon(S.monthly * 1e4)}`;
  $('initial-box').style.display = S.mode === 'dca' ? 'none' : '';
  $('monthly-box').style.display = S.mode === 'lump' ? 'none' : '';
}
function showEmpty(msg) {
  $('results-empty').style.display = msg ? '' : 'none';
  $('results-empty').innerHTML = msg || '';
  $('results-section').style.display = msg ? 'none' : '';
}
async function run() {
  const seq = ++runSeq;
  readInputs();
  const hs = S.holdings.filter(h => U.has(h.key) && h.w > 0);
  if (!UL.length) return;
  if (!hs.length) return showEmpty('종목을 하나 이상 고르고 비중을 넣어 주세요.');
  const initial = S.mode === 'dca' ? 0 : S.initial * 1e4;
  const monthly = S.mode === 'lump' ? 0 : S.monthly * 1e4;
  if (initial + monthly <= 0) return showEmpty('초기 투자금이나 월 적립액을 넣어 주세요.');
  const range = applyPeriod();
  let data;
  const useLive = S.end === LIVE.month;
  let live = [];
  try {
    data = await Promise.all(hs.map(h => loadAsset(h.key)));
    await loadFlags();
    if (useLive) live = await Promise.all(hs.map(h => loadLive(liveKind(U.get(h.key)))));
  }
  catch (e) { return showEmpty('종목 데이터를 불러오지 못했습니다. 잠시 뒤 새로고침해 주세요.'); }
  if (seq !== runSeq) return;
  const assets = hs.map((h, i) => {
    const it = U.get(h.key);
    let px = data[i].px;
    if (useLive && liveOk(it) && live[i].m.has(it.code)) {     // 이번 달 = 최신 시세
      px = new Map(px); px.set(LIVE.month, live[i].m.get(it.code));
    }
    return { key: h.key, name: it.label, ccy: it.mk === 'U' ? 'USD' : 'KRW', px, dv: data[i].dv, w: h.w };
  });
  const missing = useLive ? assets.filter(a => !a.px.has(LIVE.month)).map(a => a.name) : [];
  if (missing.length) return showEmpty(`${escapeHtml(missing.join(', '))}의 이번 달 시세가 아직 없습니다. 끝 월을 지난달로 바꿔 주세요.`);
  let fx = FX;
  if (useLive && LIVE.fx) { fx = new Map(FX); fx.set(LIVE.month, LIVE.fx); }
  S.liveStamp = useLive ? live.map(l => l.stamp).filter(Boolean).sort()[0] || '' : '';
  let r;
  try {
    r = BT.simulate({ assets, fx, start: S.start, end: S.end, initial, monthly, reinvest: S.reinvest,
      rebalance: S.rebalance ? 12 : 0, fxMode: S.fxKRW ? 'krw' : 'fixed' });
  } catch (e) { return showEmpty(escapeHtml(e.message)); }
  showEmpty('');
  render(r, hs, range, { initial, monthly });
  syncUrl();
}

// ── 결과 그리기 ─────────────────────────────────────────
function recovery(r) {
  // 최대 낙폭 바닥 이후 이전 고점을 처음 넘은 달
  const t0 = r.months.indexOf(r.mddAt);
  let peak = 0; for (let t = 0; t <= t0; t++) peak = Math.max(peak, r.idx[t]);
  for (let t = t0 + 1; t <= r.n; t++) if (r.idx[t] >= peak) return { m: r.months[t], gap: t - t0 };
  return null;
}
function warnings(r, hs, range) {
  const out = [];
  if (range.limiter && range.lo === S.start)
    out.push(`가장 늦게 상장한 <b>${escapeHtml(range.limiter.label)}</b>(${range.limiter.start}) 때문에 그보다 앞선 기간은 계산할 수 없습니다.`);
  if (r.n < 36) out.push(`기간이 <b>${spanText(r.n)}</b>밖에 안 됩니다. 짧은 기간의 결과는 우연에 크게 좌우되니 긴 기간과 함께 보세요.`);
  const krS = hs.map(h => U.get(h.key)).filter(it => it.mk === 'K' && it.kind === '주식');
  if (krS.length && S.start < KR_STOCK_SAFE_START)
    out.push('국내 주식의 <b>2000년 이전 데이터</b>에는 값이 튀는 오류가 섞여 있을 수 있습니다.');
  const flagged = [];
  hs.forEach(h => (FLAGS.get(h.key) || []).forEach(f => {
    if (f.m >= S.start && f.m <= S.end && f.type !== 'gap') flagged.push(`${U.get(h.key).label} ${f.m}`);
  }));
  if (flagged.length)
    out.push(`한 달에 5배 이상 오르거나 80% 이상 빠진 구간이 있습니다: <b>${escapeHtml(flagged.slice(0, 4).join(', '))}</b>${flagged.length > 4 ? ` 외 ${flagged.length - 4}곳` : ''}. 실제 급등락일 수도, 데이터 오류일 수도 있습니다.`);
  if (S.end === LIVE.month)
    out.push(`끝 월(${S.end})은 아직 끝나지 않은 달이라 <b>${escapeHtml(S.liveStamp || '최근')} 시세</b>로 계산했습니다${hs.some(h => h.key.startsWith('U:')) && LIVE.fxDate ? ` (환율 ${escapeHtml(LIVE.fxDate)} 매매기준율)` : ''}. 이번 달 미국 종목 배당은 매주 토요일에 반영됩니다.`);
  return out;
}
function render(r, hs, range, amt) {
  const yrs = spanText(r.n);
  const usdAny = hs.some(h => h.key.startsWith('U:'));
  $('data-basis').innerHTML = `${S.start} ~ ${S.end} (${yrs}) · 매달 말 종가${S.end === LIVE.month && S.liveStamp ? ` (이번 달은 ${escapeHtml(S.liveStamp)} 시세)` : ''}` +
    (usdAny ? ` · 미국 종목 ${S.fxKRW ? '원화 환산(매매기준율)' : `시작 달 환율 ${r.fx0.toLocaleString()}원 고정`}` : '');
  $('bt-warnings').innerHTML = warnings(r, hs, range).map(w => `<div class="bt-warn${(w.startsWith('가장 늦게') || w.startsWith('끝 월')) ? ' soft' : ''}">${w}</div>`).join('');
  $('group-desc').textContent = `${S.reinvest ? '배당 재투자' : '배당 현금 수령'} · ${S.rebalance ? '매년 리밸런싱' : '리밸런싱 안 함'} · 수수료·세금 제외`;

  const gain = r.finalValue - r.invested;
  $('r-final').textContent = fmtWon(r.finalValue);
  $('r-final-sub').textContent = S.reinvest ? `${yrs} 뒤 평가액` : `보유 ${fmtWon(r.holdings)} + 쌓인 배당 현금 ${fmtWon(r.cash)}`;
  $('r-invested').textContent = fmtWon(r.invested);
  $('r-invested-sub').textContent = [amt.initial ? `처음 ${fmtWon(amt.initial)}` : '', amt.monthly ? `월 ${fmtWon(amt.monthly)} × ${r.n + 1}번` : ''].filter(Boolean).join(' + ');
  $('r-gain').textContent = (gain >= 0 ? '+' : '') + fmtWon(gain);
  $('r-gain').className = 'stat-value ' + cls(gain);
  $('r-gain-sub').textContent = `원금 대비 ${pct(gain / r.invested)}`;
  $('r-cagr').textContent = pct(r.cagr);
  $('r-cagr').className = 'stat-value ' + cls(r.cagr);
  $('r-cagr-sub').textContent = r.irr != null && amt.monthly ? `내 돈 기준 연 ${pct(r.irr)}` : '상품 자체 성과(배당 포함)';
  $('r-mdd').textContent = pctPlain(r.mdd);
  const rec = recovery(r);
  $('r-mdd-sub').textContent = r.mdd > -0.001 ? '빠진 적 없음' : `${r.mddAt} 바닥 · ${rec ? `${spanText(rec.gap)} 만에 회복` : '아직 회복 전'}`;
  if (r.worst) {
    $('r-worst').textContent = pct(r.worst.ret); $('r-worst').className = 'stat-value ' + cls(r.worst.ret); $('r-worst-sub').textContent = `${r.worst.year}년`;
    $('r-best').textContent = pct(r.best.ret); $('r-best').className = 'stat-value ' + cls(r.best.ret); $('r-best-sub').textContent = `${r.best.year}년`;
  } else {
    ['r-worst', 'r-best'].forEach(id => { $(id).textContent = '-'; $(id).className = 'stat-value'; $(id + '-sub').textContent = '1월~12월이 다 들어간 해가 없음'; });
  }
  $('r-div').textContent = fmtWon(r.totalDiv);
  $('r-div-sub').textContent = r.totalDiv > 0 ? `최근 12개월 월평균 ${fmtWon(r.monthlyDivAvg)}` : '받은 배당 없음';

  drawCharts(r);
  drawTables(r, hs);
  drawInsight(r, hs, rec);
}

function baseOpts(yFmt) {
  return {
    responsive: true, maintainAspectRatio: false, animation: false,
    interaction: { mode: 'index', intersect: false },
    plugins: { legend: { labels: { font: { family: 'Pretendard', weight: 700 }, boxWidth: 12 } },
      tooltip: { callbacks: { label: c => `${c.dataset.label}: ${yFmt(c.parsed.y)}` } } },
    scales: { x: { grid: { display: false }, ticks: { font: { family: 'Pretendard' }, maxTicksLimit: 8, autoSkip: true } },
      y: { ticks: { font: { family: 'Pretendard' }, callback: v => yFmt(v, true) }, grid: { color: 'rgba(58,47,38,.07)' } } },
  };
}
function chart(id, cfg) { if (charts[id]) charts[id].destroy(); charts[id] = new Chart($(id), cfg); }
function drawCharts(r) {
  const won = (v, axis) => axis ? fmtShort(v) : fmtWon(v);
  chart('c1', { type: 'line', data: { labels: r.months, datasets: [
    { label: '최종 금액', data: r.rows.map(x => x.value + x.cash), borderColor: COLORS[0], backgroundColor: 'rgba(217,84,43,.08)', fill: true, borderWidth: 2.2, pointRadius: 0, tension: .15 },
    { label: '넣은 돈', data: r.rows.map(x => x.invested), borderColor: COLORS[3], borderDash: [6, 4], borderWidth: 1.8, pointRadius: 0, fill: false, stepped: true },
  ] }, options: baseOpts(won) });
  const p = (v, axis) => axis ? Math.round(v * 100) + '%' : (v * 100).toFixed(1) + '%';
  chart('c2', { type: 'line', data: { labels: r.months, datasets: [
    { label: '고점 대비', data: r.dd, borderColor: '#2E5E8A', backgroundColor: 'rgba(46,94,138,.15)', fill: true, borderWidth: 1.6, pointRadius: 0, tension: .1 },
  ] }, options: Object.assign(baseOpts(p), { plugins: { legend: { display: false }, tooltip: { callbacks: { label: c => `고점 대비 ${p(c.parsed.y)}` } } } }) });
  const ys = r.yearly;
  chart('c3', { type: 'bar', data: { labels: ys.map(y => y.partial ? `${y.year}*` : y.year), datasets: [
    { label: '연 수익률', data: ys.map(y => y.ret), backgroundColor: ys.map(y => y.ret >= 0 ? 'rgba(200,64,42,.8)' : 'rgba(46,94,138,.8)'), borderRadius: 4 },
  ] }, options: Object.assign(baseOpts(p), { plugins: { legend: { display: false }, tooltip: { callbacks: { label: c => `연 수익률 ${(c.parsed.y * 100).toFixed(1)}%` } } } }) });
  $('c4-card').style.display = r.totalDiv > 0 ? '' : 'none';
  if (r.totalDiv > 0) {
    $('c4-sub').textContent = S.reinvest ? '받자마자 다시 투자한 배당 (재투자 덕에 해마다 늘어나는지 보세요)' : '현금으로 받은 배당 — 이 돈을 생활비로 썼다고 보면 됩니다';
    chart('c4', { type: 'bar', data: { labels: ys.map(y => y.partial ? `${y.year}*` : y.year), datasets: [
      { label: '받은 배당', data: ys.map(y => y.div), backgroundColor: 'rgba(139,111,71,.75)', borderRadius: 4 },
    ] }, options: Object.assign(baseOpts(won), { plugins: { legend: { display: false }, tooltip: { callbacks: { label: c => `받은 배당 ${fmtWon(c.parsed.y)}` } } } }) });
  }
}
function drawTables(r, hs) {
  const tot = r.perAsset.reduce((s, a) => s + a.value, 0);
  $('asset-table').innerHTML = `<thead><tr><th>종목</th><th>목표 비중</th><th>가격 변동</th><th>받은 배당</th><th>마지막 평가액</th></tr></thead><tbody>` +
    r.perAsset.map((a, i) => {
      const it = U.get(a.key);
      return `<tr><td class="name">${escapeHtml(it.label)}<span class="sub">${it.mk === 'K' ? '국내' : '미국'} ${it.kind} · ${escapeHtml(it.code)}</span></td>
        <td>${pctPlain(a.w, 0)}</td><td class="${cls(a.priceRet)}">${pct(a.priceRet)}</td><td>${fmtWon(a.div)}</td>
        <td>${fmtWon(a.value)}<span class="sub">지금 비중 ${tot ? pctPlain(a.value / tot) : '-'}</span></td></tr>`;
    }).join('') + '</tbody>';
  $('year-table').innerHTML = `<thead><tr><th>연도</th><th>수익률</th><th>연말 금액</th><th>넣은 돈(누적)</th><th>받은 배당</th></tr></thead><tbody>` +
    r.yearly.map(y => `<tr class="${y.partial ? 'partial' : ''}"><td>${y.year}${y.partial ? '<span class="sub">1년 미만</span>' : ''}</td>
      <td class="${cls(y.ret)}">${pct(y.ret)}</td><td>${fmtWon(y.value)}</td><td>${fmtWon(y.invested)}</td><td>${fmtWon(y.div)}</td></tr>`).join('') + '</tbody>';
}
function drawInsight(r, hs, rec) {
  const names = hs.map(h => U.get(h.key).label);
  const who = names.length === 1 ? names[0] : `${names[0]} 등 ${names.length}종목`;
  $('insight-title').textContent = `${S.start}부터 ${spanText(r.n)}, 넣은 돈 ${fmtWon(r.invested)} → ${fmtWon(r.finalValue)}`;
  let d = `${escapeHtml(who)}에 투자했다면 연평균 <strong>${pct(r.cagr)}</strong>(배당 포함, 상품 자체 성과)였습니다. `;
  if (r.mdd < -0.001) d += `하지만 그 사이 고점 대비 <strong>${pctPlain(r.mdd)}</strong>까지 빠진 적이 있고(${r.mddAt}), ${rec ? `원래 수준을 되찾는 데 <strong>${spanText(rec.gap)}</strong>이 걸렸습니다` : '아직 그 고점을 되찾지 못했습니다'}. `;
  if (r.worst) d += `최악의 해는 ${r.worst.year}년(${pct(r.worst.ret)})이었습니다. `;
  d += '<br><br>최종 금액만큼 중요한 건 <strong>그 하락 구간을 실제로 버틸 수 있었는지</strong>입니다. 같은 종목도 기간을 바꾸면 결과가 크게 달라지니, 위의 빠른 기간 버튼으로 하락장 전후도 꼭 돌려 보세요.';
  if (hs.some(h => h.key.startsWith('U:')) && S.fxKRW && r.fxN) {
    const fxr = r.fxN / r.fx0 - 1;
    d += `<br><br>💱 같은 기간 원/달러 환율은 ${r.fx0.toLocaleString()}원 → ${r.fxN.toLocaleString()}원(${pct(fxr)})으로, 미국 종목 결과에 그만큼 ${fxr >= 0 ? '보탬이' : '손해가'} 됐습니다. 위 환율 스위치를 끄면 달러 기준 성과를 볼 수 있습니다.`;
  }
  if (!S.reinvest && r.totalDiv > 0) d += `<br><br>💵 배당을 현금으로 받았다면 최근 12개월 동안 한 달 평균 <strong>${fmtWon(r.monthlyDivAvg)}</strong>을 받은 셈입니다.`;
  $('insight-desc').innerHTML = d;
}

// ── 공유 ────────────────────────────────────────────────
function stateQuery() {
  const p = new URLSearchParams();
  p.set('a', S.holdings.filter(h => h.key).map(h => `${h.key}*${h.w}`).join(','));
  p.set('mode', S.mode); p.set('i', S.initial); p.set('m', S.monthly);
  if (!S.periodAuto) { p.set('s', S.start); p.set('e', S.end); }
  p.set('r', S.reinvest ? 1 : 0); p.set('b', S.rebalance ? 1 : 0); p.set('fx', S.fxKRW ? 'krw' : 'usd');
  return p.toString();
}
function syncUrl() { try { history.replaceState(null, '', '?' + stateQuery()); } catch (e) { /* file:// 등 */ } }
function readUrl() {
  const p = new URLSearchParams(location.search);
  if (!p.get('a')) return false;
  const ok = p.get('a').split(',').filter(s => /^[KU]:[0-9A-Za-z.\-_/]+\*\d+(\.\d+)?$/.test(s));
  if (!ok.length) return false;
  S.holdings = ok.slice(0, MAX_HOLDINGS).map(s => { const [k, w] = s.split('*'); return { id: nextId++, key: k, w: +w }; });
  if (['lump', 'dca', 'mix'].includes(p.get('mode'))) S.mode = p.get('mode');
  if (p.has('i')) $('initial').value = Math.max(0, +p.get('i') || 0);
  if (p.has('m')) $('monthly').value = Math.max(0, +p.get('m') || 0);
  const re = /^\d{4}-\d{2}$/;
  if (re.test(p.get('s') || '') && re.test(p.get('e') || '')) { S.start = p.get('s'); S.end = p.get('e'); S.periodAuto = false; }
  $('reinvest').checked = p.get('r') !== '0';
  $('rebalance').checked = p.get('b') === '1';
  $('fxmode').checked = p.get('fx') !== 'usd';
  return true;
}
function copyText() {
  const v = id => $(id).textContent;
  const names = S.holdings.filter(h => U.has(h.key)).map(h => `${U.get(h.key).label} ${h.w}%`).join(', ');
  return [`[백테스트] ${names}`, `${S.start} ~ ${S.end} · ${$('group-desc').textContent}`,
    `넣은 돈 ${v('r-invested')} → 최종 ${v('r-final')} (${v('r-gain-sub')})`,
    `연평균 ${v('r-cagr')} · 최대 낙폭 ${v('r-mdd')} · 최악의 해 ${v('r-worst')} (${v('r-worst-sub')})`,
    `받은 배당 ${v('r-div')}`, location.href].join('\n');
}
async function copy(s, btn) {
  try { await navigator.clipboard.writeText(s); } catch (e) {
    const t = document.createElement('textarea'); t.value = s; document.body.appendChild(t); t.select(); document.execCommand('copy'); t.remove();
  }
  const o = btn.textContent; btn.textContent = '✅ 복사했습니다'; setTimeout(() => { btn.textContent = o; }, 1500);
}

// ── 시작 ────────────────────────────────────────────────
function bind() {
  $('add-holding-btn').addEventListener('click', () => {
    if (S.holdings.length >= MAX_HOLDINGS) return;
    S.holdings.push({ id: nextId++, key: '', w: 0 }); renderHoldings();
    const inputs = document.querySelectorAll('.combo-input'); inputs[inputs.length - 1].focus();
  });
  document.querySelectorAll('[data-combo]').forEach(b => b.addEventListener('click', () => setCombo(b.dataset.combo)));
  document.querySelectorAll('#mode-seg button').forEach(b => b.addEventListener('click', () => {
    S.mode = b.dataset.v; document.querySelectorAll('#mode-seg button').forEach(x => x.classList.toggle('on', x === b)); schedule();
  }));
  ['initial', 'monthly'].forEach(id => $(id).addEventListener('input', () => { placeUnits(); schedule(); }));
  ['reinvest', 'rebalance', 'fxmode'].forEach(id => $(id).addEventListener('change', schedule));
  $('start').addEventListener('change', () => { if ($('start').value) { S.start = $('start').value; S.periodAuto = false; schedule(); } });
  $('end').addEventListener('change', () => { if ($('end').value) { S.end = $('end').value; S.periodAuto = false; schedule(); } });
  document.querySelectorAll('#quick-periods [data-q]').forEach(b => b.addEventListener('click', () => quickPeriod(b.dataset.q)));
  $('share-link').addEventListener('click', e => copy(location.href, e.currentTarget));
  $('share-copy').addEventListener('click', e => copy(copyText(), e.currentTarget));
  window.addEventListener('resize', placeUnits);
}
async function init() {
  bind();
  if (!readUrl()) S.holdings = [{ id: nextId++, key: 'U:SCHD', w: 50 }, { id: nextId++, key: 'U:QQQ', w: 50 }];
  document.querySelectorAll('#mode-seg button').forEach(x => x.classList.toggle('on', x.dataset.v === S.mode));
  renderHoldings();
  try {
    await Promise.all([loadIndex(), loadLiveFx()]);
    const n = UL.length.toLocaleString();
    $('data-status').innerHTML = `<span class="ds-dot ok"></span>국내·미국 ${n}종목 <span class="ds-date">· 가격 매일·배당 국내 매일/미국 매주 갱신</span>`;
  } catch (e) {
    $('data-status').innerHTML = '<span class="ds-dot"></span>종목 데이터를 불러오지 못했습니다. 새로고침해 주세요.';
    return showEmpty('종목 데이터를 불러오지 못했습니다. 잠시 뒤 새로고침해 주세요.');
  }
  S.holdings = S.holdings.filter(h => U.has(h.key));
  if (!S.holdings.length) S.holdings = [{ id: nextId++, key: 'U:SCHD', w: 50 }, { id: nextId++, key: 'U:QQQ', w: 50 }];
  renderHoldings();
  run();
}
document.addEventListener('DOMContentLoaded', init);
