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

import bisect
import csv
import json
import os
import sys
import time
import urllib.error
import urllib.request
from winsafe import to_filename
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
CUR = datetime.now(KST).strftime("%Y-%m")        # 이번 달 — 가격 이력에 넣지 않는다
UA = {"User-Agent": "Mozilla/5.0"}
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


def _crowded(res):
    """월봉·분기봉 events 에서 회차가 빠졌을 수 있으면 True.

    ★★ 야후는 배당·분할을 막대 timestamp 를 키로 붙인다 (2026-10-24 실측).
       한 막대에 두 회차가 들면(12월 정기분+연말분·특별배당·주간 분배) 남는 회차를 다음 막대 키로
       밀어 넣고, 밀다가 막대가 모자라면 최근 회차를 말없이 버린다.
         MINT 2026-06~09 · JPST 2026-08·09 · TBIL 2026-07·08 누락, 주간 분배 ULTY 98회 중 33회만 옴.
       그래서 키가 배당락일이 속한 막대와 다른 회차가 하나라도 있으면 일봉 events 로 다시 받는다.
       일봉은 막대가 회차보다 훨씬 많아 빠지지 않는다(전 기간 일봉이라 응답이 수백 KB — 이때만 받는다)."""
    ts = res.get("timestamp") or []
    ev = res.get("events") or {}
    for kind in ("dividends", "splits"):
        for k, v in (ev.get(kind) or {}).items():
            i = bisect.bisect_right(ts, int(v.get("date", k))) - 1
            if i < 0 or ts[i] != int(k):
                return True
    return False


def yahoo(sym, interval):
    """→ {"m": [[월, 종가], ...], "div": [[월, 금액]], "split": [[월, "a:b"]]} 또는 None

    ★★ range=max 를 쓰지 말 것 (2026-10-16 실측).
       range=max&interval=1mo 는 야후가 막대 수에 맞춰 간격을 멋대로 바꾼다.
         - 상장 오래된 종목(KO·AAPL 등) → 3개월봉
         - 상장 짧은 종목(JEPI·QQQM 등)  → **주봉**. 그달 마지막 주봉의 종가가 다음 달 초까지 걸쳐
           월말 종가가 1~2% 틀어진다(JEPI 2020-08: 52.95 ← 실제 54.19).
       period1=0&period2=지금 으로 요청하면 모든 종목이 1mo 로 온다.
       응답의 meta.dataGranularity 가 요청과 다르면 버리고 오류로 처리한다."""
    far = int(time.time()) + 86400 * 2
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
           f"?period1=0&period2={far}&interval={interval}&events=div%7Csplit")
    res = _result(url)
    if not res:
        return None
    got = (res.get("meta") or {}).get("dataGranularity")
    if got and got != interval:
        raise ValueError(f"{sym} 간격 {got} ≠ 요청 {interval}")
    if interval != "1d" and _crowded(res):
        day = _result(url.replace(f"interval={interval}", "interval=1d"))
        if day:
            res["events"] = day.get("events") or {}     # 가격은 그대로 월봉, 배당·분할만 일봉 것
    parts = [res]

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
    cpath = f"{CACHE}/us/{to_filename(t)}.json"
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
        # ★ 끝난 달만 둔다 — 이번 달은 계산기가 us_prices.csv(30분마다 갱신)로 붙인다.
        #   내용이 같으면 파일을 건드리지 않는다(매주 실행해도 1만 2천 개 파일이 바뀌지 않게).
        rows = [(m, fmt(c)) for m, c in y["m"] if m < CUR]
        path = f"{ROOT}/us/{to_filename(t)}.csv"
        old = None
        if os.path.exists(path):
            old = [l for l in open(path, encoding="utf-8").read().replace("\r", "").split("\n")[1:] if l]
        if old != [f"{m},{c}" for m, c in rows]:
            write_csv(path, ["월", "종가"], rows)
    return t, src


def us_aggregate(items):
    divs, splits, srcs = [], [], []
    for t, _ in items:
        p = f"{CACHE}/us/{to_filename(t)}.json"
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


def main():
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    kind = sys.argv[1]
    if kind == "us":
        items, job, sub = us_universe(), us_job, "us"
    elif kind == "kr_split":
        items, job, sub = kr_universe(), kr_job, "kr"
    else:
        raise SystemExit(f"알 수 없는 대상: {kind}")
    os.makedirs(f"{CACHE}/{sub}", exist_ok=True)
    os.makedirs(f"{ROOT}/us", exist_ok=True)

    todo = [it for it in items if not cached_today(f"{CACHE}/{sub}/{to_filename(it[0])}.json")]
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
