#!/usr/bin/env python3
"""
야후 파이낸스 월봉 이력 → data/history/

  us        미국 주식·ETF  월봉 종가(USD) + 배당 + 분할
              → data/history/us/{티커}.csv          (월,종가)
              → data/history/us_dividends.csv        (티커,월,주당배당USD)
              → data/history/us_splits.csv           (티커,월,비율)
  kr_split  국내 개별주식 액면분할·병합 이력
              → data/history/kr_splits.csv           (종목코드,월,비율)

왜 야후인가
  네이버 해외 차트는 2010-07 이전을 주지 않는다(SPY·KO·AAPL 모두 2010-07 시작, 2026-10 실측).
  야후는 상장 초기부터(SPY 1993, KO 1985) 준다. 2008 금융위기 구간이 백테스트에 꼭 필요하다.
  야후가 비어 있는 종목만 네이버 월봉(2010-07~)으로 메운다 — us_sources.csv 에 출처를 남긴다.

값의 성격
  close 는 분할만 보정된 값이다(배당 미반영, adjclose 는 쓰지 않는다).
  배당 금액도 분할 보정돼 있어 close 와 같은 단위다(SCHD 2011-12 0.040667 = 0.122÷3).
  월봉 간격이라 배당·분할 날짜는 '월' 단위로만 남긴다.

국내 분할 이력이 필요한 이유
  네이버 국내 가격은 분할 보정값인데 세이브로 배당금은 당시 1주 기준 금액이다.
  (삼성전자 2017 배당 42,500원 vs 보정 주가 39,460원) 이 표로 배당금을 같은 단위로 맞춘다.

이어받기: 종목별 원본은 .cache/yahoo/ 에 JSON 으로 남기고, 오늘 받은 것은 건너뛴다.
          집계 CSV 는 매 실행 끝에 캐시 전체로 다시 만든다.
  LIMIT=N, FETCH_WORKERS, FETCH_SLEEP, FULL=1 (캐시 무시)

사용: python scripts/build_yahoo_history.py us|kr_split
"""

import csv
import json
import os
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
UA = {"User-Agent": "Mozilla/5.0"}
YH = "https://query1.finance.yahoo.com/v8/finance/chart/{}?range=max&interval={}&events=div%7Csplit"
NAVER = ("https://api.stock.naver.com/chart/foreign/item/{}/month"
         "?startDateTime=199001010000&endDateTime={}0000")

ROOT = os.environ.get("HISTORY_DIR", "data/history")
CACHE = os.environ.get("CACHE_DIR", ".cache/yahoo")
WORKERS = int(os.environ.get("FETCH_WORKERS", "4"))
SLEEP = float(os.environ.get("FETCH_SLEEP", "0.25"))
LIMIT = int(os.environ.get("LIMIT", "0"))
FULL = os.environ.get("FULL") == "1"


def get_json(url, headers=UA, tries=4):
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            last = e
            if e.code == 429:          # 초당 요청 제한 — 길게 쉰다
                time.sleep(10 * (i + 1))
                continue
        except Exception as e:
            last = e
        time.sleep(1.5 * (i + 1))
    raise last


def month_of(ts, off):
    return datetime.fromtimestamp(ts + off, timezone.utc).strftime("%Y-%m")


def _result(url):
    data = get_json(url)
    try:
        return data["chart"]["result"][0]
    except (TypeError, KeyError, IndexError):
        return None


def yahoo(sym, interval):
    """→ {"m": [[월, 종가], ...], "div": [[월, 금액]], "split": [[월, "a:b"]]} 또는 None

    ★ range=max&interval=1mo 는 막대가 약 500개를 넘으면 야후가 말없이 3개월 간격으로
      줄여 보낸다(KO·AA·USB 등 1985년 이전 상장 776종, 2026-10 실측).
      간격이 벌어져 있으면 2000-01-01 을 경계로 두 번 나눠 다시 받는다."""
    res = _result(YH.format(sym, interval))
    if not res:
        return None
    parts = [res]
    ts = res.get("timestamp") or []
    if interval == "1mo" and len(ts) > 2:
        gaps = sorted(b - a for a, b in zip(ts, ts[1:]))
        if gaps[len(gaps) // 2] > 45 * 86400:
            base = ("https://query1.finance.yahoo.com/v8/finance/chart/{}"
                    "?period1={}&period2={}&interval=1mo&events=div%7Csplit")
            cut, far = 946684800, int(time.time()) + 86400 * 2   # 2000-01-01 UTC
            got = [_result(base.format(sym, 0, cut)), _result(base.format(sym, cut, far))]
            if all(got):
                parts = got

    month, divd, spl = {}, {}, {}
    for res in parts:
        off = res.get("meta", {}).get("gmtoffset", 0) or 0
        ts = res.get("timestamp") or []
        closes = (res.get("indicators", {}).get("quote") or [{}])[0].get("close") or []
        for t, c in zip(ts, closes):
            if c is not None and c > 0:
                month[month_of(t, off)] = round(c, 6)     # 같은 달 중복 막대는 뒤엣것(최신)
        ev = res.get("events") or {}
        for k, v in (ev.get("dividends") or {}).items():
            if v.get("amount"):
                m = month_of(int(v.get("date", k)), off)
                divd[(m, int(v.get("date", k)))] = round(v["amount"], 6)
        for k, v in (ev.get("splits") or {}).items():
            spl[month_of(int(v.get("date", k)), off)] = v.get("splitRatio", "")
    div = sorted([m, a] for (m, _), a in divd.items())
    return {"m": sorted(month.items()), "div": div, "split": sorted(map(list, spl.items()))}


def naver_month(rc):
    end = (datetime.now(KST) + timedelta(days=1)).strftime("%Y%m%d")
    data = get_json(NAVER.format(rc, end),
                    headers={"User-Agent": "Mozilla/5.0", "Referer": "https://m.stock.naver.com/"})
    infos = data.get("priceInfos") if isinstance(data, dict) else data
    out = {}
    for d in infos or []:
        day, c = d.get("localDate"), d.get("closePrice")
        if day and c:
            out[f"{day[:4]}-{day[4:6]}"] = float(c)
    return sorted(out.items())


def fmt(v):
    v = float(v)
    return str(int(v)) if v.is_integer() else f"{v:.6f}".rstrip("0").rstrip(".")


def cached_today(path):
    if FULL or not os.path.exists(path):
        return False
    return datetime.fromtimestamp(os.path.getmtime(path), KST).date() == datetime.now(KST).date()


def save_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, path)


def write_csv(path, header, rows):
    tmp = path + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)
    os.replace(tmp, path)


# ── 미국 ──────────────────────────────────────────────────

def us_universe():
    sym = json.load(open("data/us_symbols.json", encoding="utf-8"))
    with open("data/us_prices.csv", encoding="utf-8") as f:
        live = [r["티커"] for r in csv.DictReader(f)]
    return [(t, (sym.get(t) or {}).get("rc", "")) for t in live]


def us_job(t, rc):
    cpath = f"{CACHE}/us/{t.replace('/', '_')}.json"
    if cached_today(cpath):
        return t, "skip"
    time.sleep(SLEEP)
    y = yahoo(t.replace(".", "-"), "1mo")        # BRK.B → BRK-B
    src = "yahoo"
    if not y or not y["m"]:
        y = {"m": naver_month(rc) if rc else [], "div": [], "split": []}
        src = "naver" if y["m"] else "none"
    y["src"] = src
    save_json(cpath, y)
    if y["m"]:
        write_csv(f"{ROOT}/us/{t.replace('/', '_')}.csv", ["월", "종가"],
                  [(m, fmt(c)) for m, c in y["m"]])
    return t, src


def us_aggregate(items):
    divs, splits, srcs = [], [], []
    for t, _ in items:
        p = f"{CACHE}/us/{t.replace('/', '_')}.json"
        if not os.path.exists(p):
            continue
        y = json.load(open(p, encoding="utf-8"))
        srcs.append((t, y.get("src", ""), y["m"][0][0] if y["m"] else "", len(y["m"])))
        divs += [(t, m, fmt(a)) for m, a in y["div"]]
        splits += [(t, m, r) for m, r in y["split"]]
    write_csv(f"{ROOT}/us_dividends.csv", ["티커", "월", "주당배당USD"], divs)
    write_csv(f"{ROOT}/us_splits.csv", ["티커", "월", "비율"], splits)
    write_csv(f"{ROOT}/us_sources.csv", ["티커", "출처", "시작월", "개월수"], srcs)
    return len(divs), len(splits), len(srcs)


# ── 국내 분할 ──────────────────────────────────────────────

def kr_universe():
    suf = {"유가": ".KS", "코스닥": ".KQ"}     # 코넥스는 야후에 없다
    with open("data/kr_stocks.csv", encoding="utf-8") as f:
        return [(r["종목코드"], r["종목코드"] + suf[r["시장"]])
                for r in csv.DictReader(f) if r["시장"] in suf]


def kr_job(code, ysym):
    cpath = f"{CACHE}/kr/{code}.json"
    if cached_today(cpath):
        return code, "skip"
    time.sleep(SLEEP)
    y = yahoo(ysym, "3mo")
    out = {"split": y["split"] if y else [], "src": "yahoo" if y else "none"}
    save_json(cpath, out)
    return code, out["src"]


def kr_aggregate(items):
    rows, n = [], 0
    for code, _ in items:
        p = f"{CACHE}/kr/{code}.json"
        if not os.path.exists(p):
            continue
        n += 1
        rows += [(code, m, r) for m, r in json.load(open(p, encoding="utf-8"))["split"]]
    write_csv(f"{ROOT}/kr_splits.csv", ["종목코드", "월", "비율"], rows)
    return len(rows), n


# ── 국내 ETF 실제 종가 ──────────────────────────────────────
#
# ★ 네이버 국내 차트는 ETF 가격을 '분배금까지 소급 보정'해서 준다(개별주식은 분할만 보정).
#   KODEX 200 2014-12: 네이버 19,621 = 야후 수정종가 19,654 ≠ 실제 종가 24,670 (2026-10 실측)
#   삼성전자 2014-12: 네이버 26,540 = 야후 실제 종가 26,540
#   그래서 네이버 ETF 가격에 분배금을 다시 더하면 이중 계산이 된다.
#   분배금을 현금으로 받는 시뮬레이션(생활비)에는 실제 종가가 필요해 야후 close 를 따로 받는다.

def etf_universe():
    with open("data/etf_prices.csv", encoding="utf-8") as f:
        return [(r["종목코드"], r["종목코드"] + ".KS") for r in csv.DictReader(f)]


def etf_job(code, ysym):
    cpath = f"{CACHE}/kr_etf/{code}.json"
    if cached_today(cpath):
        return code, "skip"
    time.sleep(SLEEP)
    res = _result(YH.format(ysym, "1mo").replace("&events=div%7Csplit", ""))
    rows = []
    if res:
        off = res.get("meta", {}).get("gmtoffset", 0) or 0
        q = (res.get("indicators", {}).get("quote") or [{}])[0].get("close") or []
        a = (res.get("indicators", {}).get("adjclose") or [{}])[0].get("adjclose") or []
        month = {}
        for t, c, ac in zip(res.get("timestamp") or [], q, a or [None] * len(q)):
            if c:
                month[month_of(t, off)] = (round(c, 4), round(ac, 4) if ac else "")
        rows = [[m, c, ac] for m, (c, ac) in sorted(month.items())]
    save_json(cpath, {"m": rows})
    if rows:
        write_csv(f"{ROOT}/kr_etf_raw/{code}.csv", ["월", "종가", "수정종가"],
                  [(m, fmt(c), fmt(ac) if ac != "" else "") for m, c, ac in rows])
        return code, "yahoo"
    return code, "none"


def main():
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    kind = sys.argv[1]
    if kind == "us":
        items, job, sub = us_universe(), us_job, "us"
    elif kind == "kr_split":
        items, job, sub = kr_universe(), kr_job, "kr"
    elif kind == "kr_etf_raw":
        items, job, sub = etf_universe(), etf_job, "kr_etf"
        os.makedirs(f"{ROOT}/kr_etf_raw", exist_ok=True)
    else:
        raise SystemExit(f"알 수 없는 대상: {kind}")
    os.makedirs(f"{CACHE}/{sub}", exist_ok=True)
    os.makedirs(f"{ROOT}/us", exist_ok=True)

    todo = [it for it in items if not cached_today(f"{CACHE}/{sub}/{it[0].replace('/', '_')}.json")]
    if LIMIT:
        todo = todo[:LIMIT]
    print(f"{kind}: 전체 {len(items)} · 이번 {len(todo)}", file=sys.stderr)

    stat, fails, t0 = {}, [], time.time()
    with ThreadPoolExecutor(WORKERS) as ex:
        futs = {ex.submit(job, *it): it for it in todo}
        for i, fu in enumerate(as_completed(futs), 1):
            try:
                _, st = fu.result()
            except Exception as e:
                st = "fail"
                fails.append(f"{futs[fu][0]}:{type(e).__name__}")
            stat[st] = stat.get(st, 0) + 1
            if i % 300 == 0:
                print(f"  {i}/{len(todo)} {stat} {time.time()-t0:.0f}s", file=sys.stderr)
    print(f"수집 {kind}: {stat} {time.time()-t0:.0f}s", file=sys.stderr)
    if fails:
        print(f"  실패 {len(fails)}: {' '.join(fails[:30])}", file=sys.stderr)

    if kind == "us":
        print("  집계: 배당 %d건 · 분할 %d건 · 종목 %d" % us_aggregate(items), file=sys.stderr)
    elif kind == "kr_split":
        print("  집계: 분할 %d건 · 확인 종목 %d" % kr_aggregate(items), file=sys.stderr)

    done = sum(stat.values())
    if done and stat.get("fail", 0) > done * 0.5:
        sys.exit(1)


if __name__ == "__main__":
    main()
