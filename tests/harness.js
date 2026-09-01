const fs=require("fs");
const js=fs.readFileSync("index.html","utf8").split("<script>").pop().split("</script>")[0];
(0,eval)(js.match(/function parseCSV[\s\S]*?\n}/)[0]+";globalThis.parseCSV=parseCSV;");
(0,eval)(js.match(/const num=[^\n]*/)[0].replace("const num=","globalThis.num="));
const P=f=>parseCSV(fs.readFileSync("data/"+f,"utf8"));
globalThis.KR={};globalThis.US={};
P("etf_prices.csv").slice(1).forEach(r=>{if(r[0])KR[r[0].toUpperCase()]={code:r[0],name:r[1],price:num(r[6]),cur:"KRW"}});
P("kr_stocks.csv").slice(1).forEach(r=>{if(r[0]&&!KR[r[0].toUpperCase()])KR[r[0].toUpperCase()]={code:r[0],name:r[1],price:num(r[4]),cur:"KRW"}});
P("us_prices.csv").slice(1).forEach(r=>{if(r[0])US[r[0].toUpperCase()]={code:r[0],name:r[1],price:num(r[4]),cur:"USD"}});
globalThis.FX=1370.2;globalThis.state={accounts:["연금저축펀드1"],holdings:[],cash:{}};
(0,eval)(js.match(/const DEFAULT_ACCOUNTS[^\n]*/)[0].replace("const ","globalThis."));
(0,eval)(js.match(/function sortAccts[\s\S]*?\n}/)[0].replace("function sortAccts","globalThis.sortAccts=function"));
(0,eval)(js.match(/const accts=[^\n]*/)[0].replace("const accts=","globalThis.accts="));
(0,eval)(js.match(/const NAME_NOISE[\s\S]*?\n}\n/)[0]);
(0,eval)(js.match(/const SKIP_LINE[^\n]*/)[0].replace("const SKIP_LINE=","globalThis.SKIP_LINE="));
(0,eval)(js.match(/let krFlat[\s\S]*?\n  return {m:KR\[b1\][\s\S]*?\n}/)[0]
  .replace("let krFlat={}, krAlias={}, krGram=new Map(), krGramCnt={}, usKor={};","globalThis.krFlat={};globalThis.krAlias={};globalThis.krGram=new Map();globalThis.krGramCnt={};globalThis.usKor={};")
  .replace("const HANGUL=","globalThis.HANGUL=").replace("const GRAM=","globalThis.GRAM=")
  .replace("const letters=","globalThis.letters=")
  .replace("function buildNameIndex","globalThis.buildNameIndex=function")
  .replace("function wordish","globalThis.wordish=function")
  .replace("function fuzzyKR","globalThis.fuzzyKR=function"));
(0,eval)(js.match(/function wordish[\s\S]*?\n  return false;\n}/)[0].replace("function wordish","globalThis.wordish=function"));
(0,eval)(js.match(/function nums\(line,hit\)[\s\S]*?\n  return out;\n}/)[0].replace("function nums","globalThis.nums=function"));
(0,eval)(js.match(/function inferQtyAvg[\s\S]*?\n  return null;\n}/)[0].replace("function inferQtyAvg","globalThis.inferQtyAvg=function"));
(0,eval)(js.match(/function fixThousands[\s\S]*?\n}/)[0].replace("function fixThousands","globalThis.fixThousands=function"));
(0,eval)(js.match(/function pickNums[\s\S]*?\n}/)[0].replace("function pickNums","globalThis.pickNums=function"));
(0,eval)(js.match(/function lcsLen[\s\S]*?\n  return best;\n}/)[0].replace("function lcsLen","globalThis.lcsLen=function"));
(0,eval)(js.match(/const BRANDS=[^\n]*/)[0].replace("const BRANDS=","globalThis.BRANDS="));
(0,eval)(js.match(/function arithMatch[\s\S]*?\n}/)[0].replace("function arithMatch","globalThis.arithMatch=function"));
(0,eval)(js.match(/function brandPriceMatch[\s\S]*?\n}/)[0].replace("function brandPriceMatch","globalThis.brandPriceMatch=function"));
(0,eval)(js.match(/function rawNums[\s\S]*?\n}/)[0].replace("function rawNums","globalThis.rawNums=function"));
(0,eval)(js.match(/function refineSibling[\s\S]*?\n}/)[0].replace("function refineSibling","globalThis.refineSibling=function"));
(0,eval)(js.match(/function matchStock[\s\S]*?\n  return null;\n}/)[0].replace("function matchStock","globalThis.matchStock=function"));
(0,eval)(js.match(/function detectMarket[\s\S]*?\n  return null;\n}/)[0].replace("function detectMarket","globalThis.detectMarket=function"));
(0,eval)(js.match(/function detectAcct[\s\S]*?\n  return null;\n}/)[0].replace("function detectAcct","globalThis.detectAcct=function"));
(0,eval)(js.match(/function detectCols[\s\S]*?\n}/)[0].replace("function detectCols","globalThis.detectCols=function"));
(0,eval)(js.match(/function avgOnly[\s\S]*?\n}/)[0].replace("function avgOnly","globalThis.avgOnly=function"));
(0,eval)(js.match(/function pick\(g,h,cols,k\)[\s\S]*?\n}/)[0].replace("function pick","globalThis.pick=function"));
(0,eval)(js.match(/const isIRP=[^\n]*/)[0].replace("const isIRP=","globalThis.isIRP="));
(0,eval)(js.match(/const isTaxAdv=[^\n]*/)[0].replace("const isTaxAdv=","globalThis.isTaxAdv="));
(0,eval)(js.match(/function parseShot[\s\S]*?\n}\n\nlet ocrShots/)[0].replace("function parseShot","globalThis.parseShot=function").replace(/\n\nlet ocrShots$/,""));
(0,eval)(js.match(/function sameTable[\s\S]*?\n}/)[0].replace("function sameTable","globalThis.sameTable=function"));
(0,eval)(js.match(/function mergeShotPairs[\s\S]*?\n}/)[0].replace("function mergeShotPairs","globalThis.mergeShotPairs=function"));
(0,eval)(js.match(/function spreadAccounts[\s\S]*?\n}/)[0].replace("function spreadAccounts","globalThis.spreadAccounts=function"));
(0,eval)(js.match(/function nextAcctName[\s\S]*?\n}/)[0].replace("function nextAcctName","globalThis.nextAcctName=function"));

/* 시세는 매일 바뀌므로 시험에서는 캡처 당시 값으로 고정한다.
   그러지 않으면 코드를 건드리지 않아도 결과가 흔들린다. */
try{
  const snap=JSON.parse(fs.readFileSync(__dirname+"/prices.json","utf8"));
  for(const c in snap.KR) if(KR[c]) KR[c].price=snap.KR[c];
  for(const t in snap.US) if(US[t]) US[t].price=snap.US[t];
}catch(e){ console.error("가격 고정본을 읽지 못했습니다:",e.message); }
globalThis.ocrRows=[]; buildNameIndex();
globalThis.shotAcct=[];globalThis.shotCols=[];globalThis.krStyleNums=false;globalThis.ocrShots=[];
