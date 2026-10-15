#!/usr/bin/env python3
"""
종목별 과거 가격 이력 → data/history/

백테스트 계산기용 원자료다. 오늘 시세만 있는 etf_prices.csv·kr_stocks.csv·us_prices.csv 와
같은 유니버스를 쓴다.

  kr_etf    국내 ETF      일봉 → data/history/kr_etf_daily/{코드}.csv  (날짜,종가)
                          월봉 → data/history/kr_etf/{코드}.csv        (월,종가)  ← 일봉에서 만든다
  kr_stock  국내 개별주식  월봉 → data/history/kr_stock/{코드}.csv      (월,종가)
  us        미국 주식·ETF 월봉 → data/history/us/{티커}.csv           (월,종가)  USD

출처: 네이버 차트 API (build_index_history.py 와 같은 계열)
  국내 api.stock.naver.com/chart/domestic/item/{코드}/{day|month}?startDateTime=&endDateTime=
  미국 api.stock.naver.com/chart/foreign/item/{reutersCode}/{day|month}?…

★ 가격은 액면분할·병합만 보정된 값이다(삼성전자 2018 50:1, SCHD 2024 3:1 실측 확인).
  배당·분배금은 반영되지 않으므로 총수익률은 분배금 이력(build_dividend_history.py)과
  합쳐 계산해야 한다.
★ 월봉의 '월' 은 그 달 마지막 거래일 종가다. 이번 달 행은 장중 값이라 다음 실행 때 덮인다.

이어받기
  기본(증분): 파일이 있으면 마지막 2개월부터 다시 받아 합친다. 없으면 전체 기간.
  FULL=1   : 있어도 전체 기간을 다시 받는다.
  LIMIT=N  : 이번 실행에서 처리할 종목 수 상한 (샌드박스 시간 제한 대비)
  SKIP_FRESH=1 : 오늘 이미 갱신한 파일은 건너뛴다(백필 이어 돌리기용, 기본 켜짐)

사용: python scripts/build_price_history.py kr_etf|kr_stock|us
"""

import csv
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
UA = {"User-Agent": "Mozilla/5.0", "Referer": "https://m.stock.naver.com/"}
DOM = "https://api.stock.naver.com/chart/domestic/item/{}/{}?startDateTime={}0000&endDateTime={}0000"
FOR = "https://api.stock.naver.com/chart/foreign/item/{}/{}?startDateTime={}0000&endDateTime={}0000"

ROOT = os.environ.get("HISTORY_DIR", "data/history")
WORKERS = int(os.environ.get("FETCH_WORKERS", "3"))
SLEEP = float(os.environ.get("FETCH_SLEEP", "0.25"))
LIMIT = int(os.environ.get("LIMIT", "0"))
FULL = os.environ.get("FULL") == "1"
SKIP_FRESH = os.environ.get("SKIP_FRESH", "1") == "1"
EARLIEST = "19900101"

_lock = threading.Lock()


def get_json(url, tries=4):
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 404:          # 네이버에 없는 종목
                return []
            last = e
        except Exception as e:         # 연결 끊김·타임아웃
            last = e
        time.sleep(1.5 * (i + 1))
    raise last


def candles(url):
    data = get_json(url)
    infos = data.get("priceInfos") if isinstance(data, dict) else data
    out = []
    for d in infos or []:
        day, close = d.get("localDate"), d.get("closePrice")
        if not day or close in (None, "", 0):
            continue
        out.append((day, float(close)))
    return out


def fmt(v):
    """정수면 소수점 없이, 아니면 유효숫자만."""
    return str(int(v)) if float(v).is_integer() else f"{v:.6f}".rstrip("0").rstrip(".")


def read_rows(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        r = csv.reader(f)
        next(r, None)
        return [row for row in r if len(row) >= 2]


def write_rows(path, header, rows):
    tmp = path + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)
    os.replace(tmp, path)


def merge(old, new):
    """키(날짜/월) 기준으로 새 값이 덮는다."""
    m = {r[0]: r[1] for r in old}
    for k, v in new:
        m[k] = v
    return sorted(m.items())


def fresh_today(path):
    if not os.path.exists(path):
        return False
    mt = datetime.fromtimestamp(os.path.getmtime(path), KST).date()
    return mt == datetime.now(KST).date()


def start_for(path, months_back):
    """증분이면 파일 마지막 시점에서 months_back 달 전부터, 아니면 처음부터."""
    if FULL:
        return EARLIEST
    rows = read_rows(path)
    if not rows:
        return EARLIEST
    last = rows[-1][0].replace("-", "")[:6]       # YYYYMM
    y, m = int(last[:4]), int(last[4:6])
    m -= months_back
    while m <= 0:
        m += 12
        y -= 1
    return f"{y:04d}{m:02d}01"


# ── 작업 단위 ──────────────────────────────────────────────

def job_kr_etf(code, end):
    dpath = f"{ROOT}/kr_etf_daily/{code}.csv"
    mpath = f"{ROOT}/kr_etf/{code}.csv"
    if SKIP_FRESH and fresh_today(dpath) and not FULL:
        return code, "skip", 0
    start = start_for(dpath, 2)
    rows = candles(DOM.format(code, "day", start, end))
    if not rows:
        return code, "empty", 0
    daily = merge(read_rows(dpath),
                  [(f"{d[:4]}-{d[4:6]}-{d[6:]}", fmt(c)) for d, c in rows])
    write_rows(dpath, ["날짜", "종가"], daily)
    # 월봉은 일봉 전체에서 다시 만든다 — 그 달 마지막 거래일 종가
    month = {}
    for d, c in daily:
        month[d[:7]] = c
    write_rows(mpath, ["월", "종가"], sorted(month.items()))
    return code, "ok", len(daily)


def job_monthly(kind, code, rc, end):
    path = f"{ROOT}/{kind}/{safe(code)}.csv"
    if SKIP_FRESH and fresh_today(path) and not FULL:
        return code, "skip", 0
    start = start_for(path, 2)
    base = DOM if kind == "kr_stock" else FOR
    rows = candles(base.format(rc, "month", start, end))
    if not rows:
        return code, "empty", 0
    merged = merge(read_rows(path), [(f"{d[:4]}-{d[4:6]}", fmt(c)) for d, c in rows])
    write_rows(path, ["월", "종가"], merged)
    return code, "ok", len(merged)


def safe(t):
    """파일명용. BRK.B → BRK.B 그대로 두되 경로 구분자만 막는다."""
    return t.replace("/", "_")


# ── 유니버스 ──────────────────────────────────────────────

def universe(kind):
    if kind == "kr_etf":
        with open("data/etf_prices.csv", encoding="utf-8") as f:
            return [(r["종목코드"], r["종목코드"]) for r in csv.DictReader(f)]
    if kind == "kr_stock":
        with open("data/kr_stocks.csv", encoding="utf-8") as f:
            return [(r["종목코드"], r["종목코드"]) for r in csv.DictReader(f)]
    if kind == "us":
        sym = json.load(open("data/us_symbols.json", encoding="utf-8"))
        with open("data/us_prices.csv", encoding="utf-8") as f:
            live = [r["티커"] for r in csv.DictReader(f)]
        return [(t, sym[t]["rc"]) for t in live if t in sym and sym[t].get("rc")]
    raise SystemExit(f"알 수 없는 대상: {kind}")


def main():
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    kind = sys.argv[1]
    end = (datetime.now(KST) + timedelta(days=1)).strftime("%Y%m%d")
    items = universe(kind)
    sub = "kr_etf_daily" if kind == "kr_etf" else kind
    os.makedirs(f"{ROOT}/{sub}", exist_ok=True)
    os.makedirs(f"{ROOT}/{kind}", exist_ok=True)

    if SKIP_FRESH and not FULL:
        todo = [it for it in items if not fresh_today(f"{ROOT}/{sub}/{safe(it[0])}.csv")]
    else:
        todo = items
    if LIMIT:
        todo = todo[:LIMIT]
    print(f"{kind}: 전체 {len(items)} · 이번 {len(todo)}", file=sys.stderr)

    stat = {"ok": 0, "empty": 0, "fail": 0, "skip": 0}
    empties, fails = [], []
    t0 = time.time()

    def run(it):
        code, rc = it
        time.sleep(SLEEP)
        if kind == "kr_etf":
            return job_kr_etf(code, end)
        return job_monthly(kind, code, rc, end)

    with ThreadPoolExecutor(WORKERS) as ex:
        futs = {ex.submit(run, it): it for it in todo}
        for i, fu in enumerate(as_completed(futs), 1):
            code = futs[fu][0]
            try:
                _, st, n = fu.result()
            except Exception as e:
                st = "fail"
                fails.append(f"{code}:{type(e).__name__}")
            stat[st] += 1
            if st == "empty":
                empties.append(code)
            if i % 200 == 0:
                print(f"  {i}/{len(todo)} {stat} {time.time()-t0:.0f}s", file=sys.stderr)

    print(f"완료 {kind}: {stat} {time.time()-t0:.0f}s", file=sys.stderr)
    if empties:
        print(f"  빈 응답 {len(empties)}: {' '.join(empties[:30])}", file=sys.stderr)
    if fails:
        print(f"  실패 {len(fails)}: {' '.join(fails[:30])}", file=sys.stderr)
    # 절반 넘게 실패하면 네이버 쪽 이상으로 보고 실패 처리 (깨진 이력 배포 방지)
    done = stat["ok"] + stat["empty"] + stat["fail"]
    if done and stat["fail"] > done * 0.5:
        sys.exit(1)


if __name__ == "__main__":
    main()
