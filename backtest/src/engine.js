/* 똑재TV 백테스트 계산기 — 계산 엔진 (화면과 분리, node 로 테스트)
 *
 * 월 단위. 모든 값은 원화(KRW). 미국 종목은 그달 말 매매기준율로 환산한다.
 *   fxMode 'krw'  : 환율 반영 (실제 원화 투자 결과)
 *   fxMode 'fixed': 환율 효과 제외 — 시작 달 환율로 고정 (= 달러 기준 수익률)
 *
 * 흐름 (t = 0 … n, 각 달 말 종가 기준)
 *   t=0      초기 투자금 + (적립식이면) 첫 달 월 적립액을 목표 비중대로 매수
 *   t≥1      ① 그달 배당락된 배당을 받는다 (보유 주식 수 × 주당 배당 × 환율)
 *            ② 재투자면 그 종목을 다시 산다 / 현금 수령이면 현금으로 쌓는다(재투자하지 않음)
 *            ③ 월 적립액을 목표 비중대로 매수
 *            ④ 리밸런싱 달이면 보유 평가액을 목표 비중으로 다시 맞춘다 (현금은 건드리지 않음)
 *   소수 주 매수 허용, 매매 수수료·세금 없음(세전).
 *
 * 성과 지표
 *   연평균 수익률(시간가중) : 돈을 넣고 뺀 시점과 무관한 '상품 자체' 성과. 배당은 받은 것으로 계산.
 *   내 돈 기준 연 수익률    : 실제로 넣은 돈의 흐름(초기·적립)으로 구한 IRR. 적립식 비교용.
 *   최대 낙폭(MDD)          : 시간가중 지수 기준 고점 대비 최대 하락.
 */
(function (root) {
  'use strict';

  function monthsBetween(a, b) {               // 'YYYY-MM' 포함 범위
    const out = [];
    let y = +a.slice(0, 4), m = +a.slice(5, 7);
    const ey = +b.slice(0, 4), em = +b.slice(5, 7);
    while (y < ey || (y === ey && m <= em)) {
      out.push(`${y}-${String(m).padStart(2, '0')}`);
      m++; if (m === 13) { m = 1; y++; }
    }
    return out;
  }

  // 빈 달은 직전 값으로 채운다 (거래정지·휴장 등). 첫 값 이전은 없음(null).
  function fill(map, months) {
    const out = []; let last = null;
    for (const m of months) {
      if (map.has(m)) last = map.get(m);
      out.push(last);
    }
    return out;
  }

  function irrMonthly(flows) {                  // flows[t]: 그달 현금흐름(투자 = 음수)
    const npv = r => flows.reduce((s, f, t) => s + f / Math.pow(1 + r, t), 0);
    let lo = -0.99, hi = 1.0;
    if (npv(lo) * npv(hi) > 0) return null;
    for (let i = 0; i < 200; i++) {
      const mid = (lo + hi) / 2;
      if (npv(lo) * npv(mid) <= 0) hi = mid; else lo = mid;
    }
    return (lo + hi) / 2;
  }

  /**
   * @param {object} o
   *   assets   [{key, name, ccy:'KRW'|'USD', px: Map(month→price), dv: Map(month→주당배당), w: 비중%}]
   *   fx       Map(month→원/달러)
   *   start, end  'YYYY-MM'
   *   initial  초기 투자금(원)
   *   monthly  월 적립액(원) — 0 이면 거치식
   *   reinvest true=배당 재투자 / false=현금 수령
   *   rebalance 0=안 함, 12=매년
   *   fxMode   'krw' | 'fixed'
   */
  function simulate(o) {
    const months = monthsBetween(o.start, o.end);
    const n = months.length - 1;
    if (n < 1) throw new Error('기간이 너무 짧습니다 (최소 2개월).');
    const wsum = o.assets.reduce((s, a) => s + a.w, 0);
    if (!(wsum > 0)) throw new Error('비중 합계가 0입니다.');
    const W = o.assets.map(a => a.w / wsum);

    const fxArr = fill(o.fx || new Map(), months);
    const needFx = o.assets.some(a => a.ccy === 'USD');
    if (needFx && fxArr.some(v => v == null)) throw new Error('환율 자료가 없는 기간이 포함돼 있습니다 (1990-03 이후만 가능).');
    const fx0 = needFx ? fxArr[0] : 1;
    const rate = (a, t) => a.ccy === 'USD' ? (o.fxMode === 'fixed' ? fx0 : fxArr[t]) : 1;

    const P = o.assets.map(a => fill(a.px, months));
    P.forEach((p, i) => { if (p[0] == null) throw new Error(`${o.assets[i].name}: 시작 달 가격이 없습니다.`); });
    const D = o.assets.map(a => months.map(m => (a.dv && a.dv.get(m)) || 0));
    const krwPrice = (i, t) => P[i][t] * rate(o.assets[i], t);

    const sh = o.assets.map(() => 0);
    const buy = (amt) => { o.assets.forEach((a, i) => { sh[i] += amt * W[i] / krwPrice(i, 0 + buy.t); }); };
    let cash = 0, invested = 0;
    const flows = new Array(n + 1).fill(0);
    const rows = [];                           // 월별 기록
    const divByAsset = o.assets.map(() => 0);
    const idx = [1];                           // 시간가중 지수

    // t = 0
    buy.t = 0;
    const first = o.initial + o.monthly;
    if (first > 0) { buy(first); invested += first; flows[0] -= first; }
    const holdVal = t => sh.reduce((s, q, i) => s + q * krwPrice(i, t), 0);
    rows.push({ m: months[0], invested, value: holdVal(0), cash, div: 0 });

    for (let t = 1; t <= n; t++) {
      buy.t = t;
      const prev = holdVal(t - 1);
      // ① 배당
      let divT = 0;
      const divI = o.assets.map((a, i) => sh[i] * D[i][t] * rate(a, t));
      divI.forEach((d, i) => { divT += d; divByAsset[i] += d; });
      const grossNow = holdVal(t) + divT;      // 배당을 포함한 이번 달 성과 (시간가중)
      idx.push(idx[t - 1] * (prev > 0 ? grossNow / prev : 1));
      // ② 재투자 / 현금
      if (o.reinvest) divI.forEach((d, i) => { if (d > 0) sh[i] += d / krwPrice(i, t); });
      else cash += divT;
      // ③ 적립
      if (o.monthly > 0) { buy(o.monthly); invested += o.monthly; flows[t] -= o.monthly; }
      // ④ 리밸런싱
      if (o.rebalance && t % o.rebalance === 0) {
        const tot = holdVal(t);
        o.assets.forEach((a, i) => { sh[i] = tot * W[i] / krwPrice(i, t); });
      }
      rows.push({ m: months[t], invested, value: holdVal(t), cash, div: divT });
    }

    const finalValue = holdVal(n) + cash;
    flows[n] += finalValue;
    const years = n / 12;
    const twr = idx[n];
    const cagr = Math.pow(twr, 1 / years) - 1;
    const irrM = (o.monthly > 0 || cash > 0) ? irrMonthly(flows) : null;
    const irr = irrM == null ? null : Math.pow(1 + irrM, 12) - 1;

    // 최대 낙폭
    let peak = idx[0], mdd = 0, mddAt = months[0], dd = [];
    idx.forEach((v, t) => {
      if (v > peak) peak = v;
      const d = v / peak - 1;
      dd.push(d);
      if (d < mdd) { mdd = d; mddAt = months[t]; }
    });

    // 연도별 (달력 기준, 첫·마지막 해는 일부 기간)
    const years_ = [];
    let y0 = months[0].slice(0, 4), base = idx[0], divY = 0;
    for (let t = 1; t <= n; t++) {
      divY += rows[t].div;
      const y = months[t].slice(0, 4);
      const last = t === n || months[t + 1].slice(0, 4) !== y;
      if (last) {
        years_.push({
          year: y, ret: idx[t] / base - 1,
          partial: (y === months[0].slice(0, 4) && months[0].slice(5) !== '12') || (t === n && months[n].slice(5) !== '12'),
          invested: rows[t].invested, value: rows[t].value + rows[t].cash, div: divY,
        });
        base = idx[t]; divY = 0;
      }
    }
    const fullYears = years_.filter(y => !y.partial);
    const worst = fullYears.length ? fullYears.reduce((a, b) => (b.ret < a.ret ? b : a)) : null;
    const best = fullYears.length ? fullYears.reduce((a, b) => (b.ret > a.ret ? b : a)) : null;

    const last12 = rows.slice(Math.max(1, n - 11)).reduce((s, r) => s + r.div, 0);
    const totalDiv = divByAsset.reduce((s, v) => s + v, 0);

    return {
      months, n, years, rows, idx, dd,
      invested, finalValue, holdings: holdVal(n), cash,
      cagr, irr, mdd, mddAt, twr,
      yearly: years_, worst, best,
      totalDiv, last12Div: last12, monthlyDivAvg: last12 / Math.min(12, n),
      perAsset: o.assets.map((a, i) => ({
        key: a.key, name: a.name, w: W[i], value: sh[i] * krwPrice(i, n), div: divByAsset[i],
        priceRet: P[i][n] / P[i][0] - 1,
      })),
      fx0, fxN: needFx ? fxArr[n] : null,
    };
  }

  const api = { simulate, monthsBetween, irrMonthly };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.BT = api;
})(typeof window !== 'undefined' ? window : globalThis);
