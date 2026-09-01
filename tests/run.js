/* OCR 회귀 시험.  실행:  node tests/run.js   (레포 루트에서) */
globalThis.GRAM=4;
require('./harness.js');
globalThis.FX=1370.2;
globalThis.state={accounts:['연금저축펀드1','IRP','ISA','일반계좌'],holdings:[],cash:{}};

let P=0,T=0,A=0,C=0;
for(const [nm,wantA,tx,real] of require('./ocr-cases.js')){
  ocrRows.length=0; parseShot(tx,0); C++;
  const gotA=(ocrRows[0]&&ocrRows[0].acct)||detectAcct(tx);
  if(gotA===wantA) A++;
  let bad=0; const n=Math.max(ocrRows.length,real.length); const out=[];
  for(let i=0;i<n;i++){
    const r=ocrRows[i]||{}, t=real[i]||[]; T++;
    const ok=r.code===t[0]&&String(r.qty)===t[1]&&String(r.avg)===t[2];
    if(ok) P++; else { bad++;
      out.push('   '+((r.code||'-')+' '+(r.name||'')).slice(0,26).padEnd(28)
        +String(r.qty??'').padStart(9)+String(r.avg??'').padStart(12)+'  기대 '+t.join(' ')); }
  }
  console.log((bad||gotA!==wantA?'■ ':'□ ')+nm.padEnd(28)+(n-bad)+'/'+n
    +'  계좌 '+(gotA===wantA?'OK':'FAIL '+gotA));
  if(bad) console.log(out.join('\n'));
}
console.log('\n종목 '+P+'/'+T+'   계좌 '+A+'/'+C);
process.exit(P===T&&A===C?0:1);
