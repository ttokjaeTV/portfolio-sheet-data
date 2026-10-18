// node backtest/src/test.js  (레포 루트에서)
const fs = require('fs');
const path = require('path');
const BT = require('./engine.js');
const D = path.join(__dirname, '..', '..', 'data');

function csvMap(file, col = '종가') {
  const m = new Map();
  if (!fs.existsSync(file)) return m;
  const lines = fs.readFileSync(file, 'utf8').replace(/\r/g, '').trim().split('\n');
  const head = lines.shift().split(',');
  const ki = head.indexOf('월'), vi = head.indexOf(col);
  lines.forEach(l => { const c = l.split(','); if (c[vi]) m.set(c[ki], +c[vi]); });
  return m;
}
const fx = csvMap(path.join(D, 'history/usdkrw_month.csv'), '매매기준율');
function asset(key, w = 100) {
  const [mk, code] = key.split(':');
  let px, ccy = 'KRW';
  if (mk === 'U') { px = csvMap(path.join(D, `history/us/${code}.csv`)); ccy = 'USD'; }
  else if (fs.existsSync(path.join(D, `history/kr_etf_krx/${code}.csv`))) px = csvMap(path.join(D, `history/kr_etf_krx/${code}.csv`));
  else px = csvMap(path.join(D, `history/kr_stock/${code}.csv`));
  const dv = csvMap(path.join(D, `bt/div/${mk}/${code}.csv`), '금액');
  return { key, name: code, ccy, px, dv, w };
}
let fail = 0;
function check(label, got, want, tol) {
  const ok = Math.abs(got - want) <= tol;
  if (!ok) fail++;
  console.log(`${ok ? '✓' : '✗'} ${label}: ${(got * 100).toFixed(2)}% (기대 ${(want * 100).toFixed(2)}% ±${(tol * 100).toFixed(1)}%p)`);
}
const base = { initial: 10000000, monthly: 0, reinvest: true, rebalance: 0, fx, fxMode: 'fixed' };

// 1) 미국 — 공개 10년 총수익률 (2014-12 → 2024-12)
for (const [t, want] of [['SPY', 0.130], ['SCHD', 0.110], ['QQQ', 0.183]]) {
  const r = BT.simulate({ ...base, assets: [asset('U:' + t)], start: '2014-12', end: '2024-12' });
  check(`${t} 10년 연평균(달러 기준)`, r.cagr, want, 0.003);
}

// 2) 국내 ETF — KRX 실제 종가+세이브로 분배금 재투자  vs  네이버 분배금 보정 가격만으로 구한 값
for (const code of ['069500', '360750', '458730', '161510']) {
  const a = asset('K:' + code);
  const nv = csvMap(path.join(D, `history/kr_etf/${code}.csv`));
  const s = [...a.px.keys()].filter(m => nv.has(m) && m >= '2014-12')[0], e = '2026-09';
  const r = BT.simulate({ ...base, assets: [a], start: s, end: e });
  const nvCagr = Math.pow(nv.get(e) / nv.get(s), 12 / r.n) - 1;
  check(`K:${code} ${s}~ 총수익(KRX+분배금) vs 네이버 보정가`, r.cagr, nvCagr, 0.003);
}

// 3) 국내 주식 — 삼성전자: 네이버 분할보정가 + 단위 맞춘 배당 (가격만보다 배당만큼 높아야)
{
  const r = BT.simulate({ ...base, assets: [asset('K:005930')], start: '2014-12', end: '2024-12' });
  const pr = r.perAsset[0].priceRet;
  console.log(`  삼성전자 2014-12~2024-12 총수익 연 ${(r.cagr * 100).toFixed(2)}% · 가격만 연 ${((Math.pow(1 + pr, 1 / 10) - 1) * 100).toFixed(2)}%`);
  if (!(r.cagr > Math.pow(1 + pr, 0.1) - 1 && r.cagr - (Math.pow(1 + pr, 0.1) - 1) < 0.04)) { fail++; console.log('✗ 삼성전자 배당 기여가 비정상'); }
}

// 4) 환율: SCHD 원화 vs 달러 — 차이 ≈ 환율 변화
{
  const a = asset('U:SCHD');
  const k = BT.simulate({ ...base, fxMode: 'krw', assets: [a], start: '2014-12', end: '2024-12' });
  const u = BT.simulate({ ...base, fxMode: 'fixed', assets: [a], start: '2014-12', end: '2024-12' });
  check('SCHD 원화/달러 최종금액 비율 − 1 = 환율 변화', k.finalValue / u.finalValue - 1, fx.get('2024-12') / fx.get('2014-12') - 1, 0.0001);
}

// 5) 적립식 IRR — 가격 고정·배당 없음이면 0%
{
  const px = new Map(BT.monthsBetween('2020-01', '2024-12').map(m => [m, 100]));
  const r = BT.simulate({ ...base, monthly: 500000, initial: 0, assets: [{ key: 'X', name: 'X', ccy: 'KRW', px, dv: new Map(), w: 100 }], start: '2020-01', end: '2024-12' });
  check('가격 고정 적립식 IRR', r.irr, 0, 0.0001);
  check('가격 고정 적립식 최종금액=원금', r.finalValue / r.invested - 1, 0, 1e-9);
}

// 6) 현금 수령 — 최종(보유+현금)은 재투자보다 작거나 같고, 받은 배당 합 = 현금
{
  const a = asset('U:JEPI');
  const c = BT.simulate({ ...base, reinvest: false, assets: [a], start: '2020-06', end: '2026-09' });
  const r = BT.simulate({ ...base, reinvest: true, assets: [a], start: '2020-06', end: '2026-09' });
  console.log(`  JEPI 현금수령 최종 ${Math.round(c.finalValue).toLocaleString()} (현금 ${Math.round(c.cash).toLocaleString()}) · 재투자 ${Math.round(r.finalValue).toLocaleString()}`);
  if (Math.abs(c.cash - c.totalDiv) > 1) { fail++; console.log('✗ 현금 ≠ 배당 합'); }
  check('JEPI 시간가중 수익률은 재투자 여부와 무관', c.cagr, r.cagr, 1e-9);
}

// 6-2) 내 돈 기준(IRR) — 재투자 거치식이면 시간가중과 같고, 현금 수령이면 쌓인 현금이 놀아서 더 낮다
{
  const a = asset('U:SCHD');
  const opt = { ...base, fxMode: 'krw', assets: [a], start: '2014-12', end: '2024-12' };
  const r = BT.simulate({ ...opt, reinvest: true }), c = BT.simulate({ ...opt, reinvest: false });
  const rd = BT.simulate({ ...opt, reinvest: true, initial: 0, monthly: 500000 }), cd = BT.simulate({ ...opt, reinvest: false, initial: 0, monthly: 500000 });
  console.log(`  SCHD 거치식(원화) 재투자: 시간가중 ${(r.cagr * 100).toFixed(2)}% · 내 돈 ${(r.irr * 100).toFixed(2)}%`);
  console.log(`  SCHD 거치식(원화) 현금수령: 시간가중 ${(c.cagr * 100).toFixed(2)}% · 내 돈 ${(c.irr * 100).toFixed(2)}%`);
  console.log(`  SCHD 적립식(원화) 재투자: 시간가중 ${(rd.cagr * 100).toFixed(2)}% · 내 돈 ${(rd.irr * 100).toFixed(2)}%`);
  console.log(`  SCHD 적립식(원화) 현금수령: 시간가중 ${(cd.cagr * 100).toFixed(2)}% · 내 돈 ${(cd.irr * 100).toFixed(2)}%`);
  check('재투자 거치식 내 돈 기준 = 시간가중', r.irr, r.cagr, 1e-6);
  check('현금수령 거치식 내 돈 기준 = (최종/원금)^(1/년)−1', c.irr, Math.pow(c.finalValue / c.invested, 1 / c.years) - 1, 1e-6);
  if (!(c.irr < c.cagr)) { fail++; console.log('✗ 현금수령 내 돈 기준이 시간가중보다 낮아야 함'); }
}

// 6-3) 종목별 가격 변동 — 원화 환산이면 환율 포함, 환율 고정이면 달러 기준과 같다
{
  const a = asset('U:SCHD');
  const k = BT.simulate({ ...base, fxMode: 'krw', assets: [a], start: '2014-12', end: '2024-12' }).perAsset[0];
  const u = BT.simulate({ ...base, fxMode: 'fixed', assets: [a], start: '2014-12', end: '2024-12' }).perAsset[0];
  console.log(`  SCHD 가격 변동 원화 ${(k.priceRet * 100).toFixed(2)}% · 달러 ${(k.priceRetLocal * 100).toFixed(2)}%`);
  check('원화 가격 변동 = (1+달러 변동)×(1+환율 변동)−1', k.priceRet, (1 + k.priceRetLocal) * fx.get('2024-12') / fx.get('2014-12') - 1, 1e-9);
  check('환율 고정이면 가격 변동 = 달러 기준', u.priceRet, u.priceRetLocal, 1e-12);
}

// 7) 리밸런싱 — 50:50 두 종목, 매년 맞춘 뒤 비중이 다시 50:50
{
  const r = BT.simulate({ ...base, rebalance: 12, assets: [asset('U:SPY', 50), asset('U:TLT', 50)], start: '2014-12', end: '2024-12' });
  const v = r.perAsset.map(p => p.value), tot = v[0] + v[1];
  check('10년 뒤(120개월=리밸런싱 달) SPY 비중', v[0] / tot, 0.5, 1e-9);
  console.log(`  SPY50/TLT50 매년 리밸런싱 연 ${(r.cagr * 100).toFixed(2)}% · MDD ${(r.mdd * 100).toFixed(1)}% (${r.mddAt})`);
}

console.log(fail ? `\n실패 ${fail}건` : '\n모두 통과');
process.exit(fail ? 1 : 0);
