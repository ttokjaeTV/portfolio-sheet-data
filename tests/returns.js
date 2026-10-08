const fs=require('fs');
/* 수익률 흐름 계산 시험.  실행:  node tests/returns.js   (레포 루트에서) */
const html=fs.readFileSync(process.argv[2]||'index.html','utf8');
const js=html.split('<script>').pop().split('</script>')[0];
const grab=re=>{const m=js.match(re); if(!m) throw new Error('missing '+re); return m[0];};
globalThis.num=v=>{const n=parseFloat(String(v??'').replace(/,/g,'')); return isFinite(n)?n:0;};
globalThis.HIST={}; globalThis.state={hist:[]}; globalThis.FX=1300; globalThis.BOARD={};
globalThis.PRICES={}; globalThis.lookup=c=>PRICES[c]||null;
for(const f of ['priceNowKRW','dayReturn','asofV','legacyReturn','buildReturns','sliceReturns'])
  (0,eval)(grab(new RegExp('function '+f+'\\([\\s\\S]*?\\n}')).replace('function '+f,'globalThis.'+f+'=function'));
let ok=0,bad=0; const eq=(n,a,b,t=1e-6)=>{ if(Math.abs(a-b)<=t){ok++;console.log('□',n,a.toFixed(4));} else {bad++;console.log('■',n,'got',a,'want',b);} };

// 1) 입금 후 추가매수: 가격 그대로면 0%, 다음날 10% 오르면 +10%
let d1={d:'2026-10-01',val:10000,buy:10000,px:{A:[10,1000]}};
let d2={d:'2026-10-02',val:20000,buy:20000,px:{A:[20,1000]}};
eq('입금+추가매수 당일',dayReturn(d1,d2.px),0);
let d3px={A:[20,1100]}; eq('다음날 10% 상승',dayReturn(d2,d3px),10);
// 2) 수익 난 종목 전량 매도·출금: 그날 가격 상승분만 수익
PRICES.B={price:1100,cur:'KRW'};
eq('전량 매도한 날(오늘 시세 사용)',dayReturn({val:10000,px:{B:[10,1000]}},{}),10);
// 3) 미국 종목: 달러 가격 그대로, 환율 1300→1400 이면 원화 +7.69%
eq('환율 상승분 포함',dayReturn({val:1300000,px:{SPY:[10,130000]}},{SPY:[10,140000]}),(1400/1300-1)*100);
// 4) 예수금은 분모에만: 주식 5000 + 현금 5000, 주식 10% → 5%
eq('예수금 희석',dayReturn({val:10000,px:{A:[5,1000]}},{A:[5,1100]}),5);
// 5) 추정 구간(px 없는 옛 기록): 원금 +10000 입금, 평가 +11000 → 1000/10000 = 10%
eq('옛 기록: 입금 걸러 내기',legacyReturn({d:'2026-09-01',val:10000,buy:10000},{d:'2026-09-02',val:21000,buy:20000}),10);
// 6) 누적 사슬: +10%, -10% → -1%
state.hist=[{d:'2026-10-01',val:100},{d:'2026-10-02',val:110,r:10},{d:'2026-10-03',val:99,r:-10}];
const all=buildReturns(); eq('누적 사슬 +10%→-10%',all[2].mine,-1);
// 7) 지수 날짜 맞추기: 코스피 그날(포함), S&P 전날까지(미포함)
HIST={'코스피':[{d:'2026-10-01',v:100},{d:'2026-10-02',v:110}],'S&P 500':[{d:'2026-10-01',v:200},{d:'2026-10-02',v:220}]};
const a2=buildReturns(); eq('코스피 같은 날 종가',a2[1].kL,110); eq('S&P 전 거래일 종가',a2[1].sL,200);
// 8) 엑셀코스피 공유 데이터로 기간 재기준(30일·7일)이 그쪽 공식과 같은지
if(process.argv[3]){ // 엑셀코스피 공유 스냅샷 JSON 을 주면 기간 재기준을 그쪽 공식과 대조한다
const snap=JSON.parse(fs.readFileSync(process.argv[3],'utf8')).snapshot.performance;
const ek=snap.map(r=>({d:r.d,mine:r.mine,est:false,day:0,kL:6912.95*(1+r.kospi/100),sL:7674.37*(1+r.snp/100)}));
function theirs(e){ const n=snap.slice(-90),a=n[n.length-1],u=e?Date.parse(a.d)-(e-1)*864e5:-1/0,r=n.filter(i=>Date.parse(i.d)>=u),o=r[0];
  return r.map(i=>{const l={d:i.d}; for(const f of ['mine','kospi','snp']) l[f]=((1+i[f]/100)/(1+o[f]/100)-1)*100; return l;}); }
for(const e of [0,30,7]){
  const A=sliceReturns(ek,e), B=theirs(e); let mx=0;
  if(A.length!==B.length){bad++;console.log('■ 기간',e,'길이',A.length,B.length);continue;}
  A.forEach((x,i)=>{ for(const f of ['mine','kospi','snp']) mx=Math.max(mx,Math.abs(x[f]-B[i][f])); });
  eq(`기간 ${e||'전체'}일 재기준 최대오차 (끝값 내 ${A[A.length-1].mine.toFixed(3)}%, 코스피 ${A[A.length-1].kospi.toFixed(3)}%, S&P ${A[A.length-1].snp.toFixed(3)}%)`,mx,0,1e-9);
}
}
console.log(`\n${ok} 통과 / ${bad} 실패`); process.exit(bad?1:0);
