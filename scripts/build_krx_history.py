#!/usr/bin/env python3
"""
KRX 월말 전종목 시세 → data/history/   (ETF · 개별주식)

  etf    MDCSTAT04301 ETF 전종목 시세
           krx_etf_month/{YYYY-MM}.csv  기준일,종목코드,종목명,종가,NAV,순자산총액,상장주식수,기초지수명,기초지수
           kr_etf_krx/{코드}.csv        월,기준일,종가,NAV,기초지수,순자산총액
           kr_etf_krx_list.csv          종목코드,종목명,첫월,마지막월,현재상장
  stock  MDCSTAT01501 주식 전종목 시세 (코스피·코스닥·코넥스, mktId=ALL)
           krx_stock_month/{YYYY-MM}.csv 기준일,종목코드,종목명,시장,종가,시가총액,상장주식수
           kr_stock_krx/{코드}.csv       월,기준일,종가,상장주식수,시가총액
           kr_stock_krx_list.csv         종목코드,종목명,첫월,마지막월,현재상장

왜 KRX 인가
  거래소 원본 실제 종가다. 그날 상장돼 있던 종목을 전부 주므로 지금은 사라진 종목까지
  남는다(생존편향 제거). ETF 는 NAV·기초지수·순자산이 같은 표에 붙어 있다.
  ★ 네이버 ETF 가격은 분배금까지 소급 보정된 값이다(docs/가격이력_수집.md).
  ★ KRX 주식 종가는 **분할·병합 보정이 없는 그날 실제 가격**이다. 백테스트에는 상장주식수 변화나
    kr_splits.csv 로 보정해야 한다. 분할 보정된 연속 가격이 필요하면 네이버 kr_stock/ 을 쓴다.

받는 방식
  매월 마지막 거래일 하루씩 부른다. 말일이 휴일이면 하루씩 당겨 최대 10일까지 찾는다.
  지난달 이전 스냅숏 파일이 이미 있으면 다시 받지 않는다(이어받기).

로그인
  scripts/krx_auth.py 가 KRX_ID/KRX_PW 를 준비한다. Actions 에서는 레포 Secrets 를 쓴다.
  ★ 계정 값은 어디에도 출력하지 않는다. pykrx 가 stdout 에 아이디를 찍으므로 import 부터 막는다.
  세션이 약 30분 뒤 끊기므로 20분마다, 그리고 데이터가 나오던 중 빈 달이 이어지면 다시 로그인한다.

사용: python scripts/build_krx_history.py etf|stock
  FROM=YYYYMM (기본 etf 200210 / stock 199501), FULL=1 (있는 스냅숏도 다시 받기)
"""

import contextlib
import csv
import glob
import io
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(__file__))
import krx_auth  # noqa: E402

KST = timezone(timedelta(hours=9))
ROOT = os.environ.get("HISTORY_DIR", "data/history")
FULL = os.environ.get("FULL") == "1"
RELOGIN_SEC = 20 * 60


def num(v):
    v = str(v or "").replace(",", "").strip()
    if v in ("", "-", "0", "0.00"):
        return ""
    return v


def txt(v):
    return str(v or "").strip()


CFG = {
    "etf": {
        "from": "200210",
        "snap": "krx_etf_month", "per": "kr_etf_krx", "list": "kr_etf_krx_list.csv",
        "live": ("data/etf_prices.csv", "종목코드"),
        "cols": ["기준일", "종목코드", "종목명", "종가", "NAV", "순자산총액", "상장주식수", "기초지수명", "기초지수"],
        "row": lambda d, r: [d, txt(r.get("ISU_SRT_CD")).upper(), txt(r.get("ISU_ABBRV")),
                             num(r.get("TDD_CLSPRC")), num(r.get("NAV")),
                             num(r.get("INVSTASST_NETASST_TOTAMT")), num(r.get("LIST_SHRS")),
                             txt(r.get("IDX_IND_NM")), num(r.get("OBJ_STKPRC_IDX"))],
        "per_cols": ["월", "기준일", "종가", "NAV", "기초지수", "순자산총액"],
        "per_row": lambda m, r: [m, r["기준일"], r["종가"], r["NAV"], r["기초지수"], r["순자산총액"]],
    },
    "stock": {
        "from": "199501",
        "snap": "krx_stock_month", "per": "kr_stock_krx", "list": "kr_stock_krx_list.csv",
        "live": ("data/kr_stocks.csv", "종목코드"),
        "cols": ["기준일", "종목코드", "종목명", "시장", "종가", "시가총액", "상장주식수"],
        "row": lambda d, r: [d, txt(r.get("ISU_SRT_CD")).upper(), txt(r.get("ISU_ABBRV")),
                             txt(r.get("MKT_NM")), num(r.get("TDD_CLSPRC")),
                             num(r.get("MKTCAP")), num(r.get("LIST_SHRS"))],
        "per_cols": ["월", "기준일", "종가", "상장주식수", "시가총액"],
        "per_row": lambda m, r: [m, r["기준일"], r["종가"], r["상장주식수"], r["시가총액"]],
    },
}


def quiet():
    return contextlib.redirect_stdout(io.StringIO())


_last_login = 0.0


def login():
    global _last_login
    ok = krx_auth.login(verbose=True)
    _last_login = time.time()
    return ok


def make_fetch(kind):
    with quiet():
        if kind == "etf":
            from pykrx.website.krx.etx.core import 전종목시세_ETF
            f = 전종목시세_ETF()
            return lambda d: f.fetch(d)
        from pykrx.website.krx.market.core import 전종목시세
        f = 전종목시세()
        return lambda d: f.fetch(d, "ALL")


def fetch(kind, raw, d):
    """하루치 전종목 → 행 리스트. 휴장일·빈 응답이면 []."""
    if time.time() - _last_login > RELOGIN_SEC:
        login()
    for attempt in range(3):
        try:
            with quiet():
                df = raw(d)
            break
        except Exception as e:
            print(f"  {d} 요청 오류 {type(e).__name__} — 재시도", file=sys.stderr)
            time.sleep(3 * (attempt + 1))
    else:
        return []
    if df is None or len(df) == 0 or "ISU_SRT_CD" not in df.columns:
        return []
    mk = CFG[kind]["row"]
    rows = [mk(d, r) for _, r in df.iterrows()]
    ci = CFG[kind]["cols"].index("종가")
    return [r for r in rows if r[ci]]          # 종가가 없는 행(휴장일·거래정지 표시)은 버린다


def month_iter(start, today):
    y, m = int(start[:4]), int(start[4:6])
    while (y, m) <= (today.year, today.month):
        yield y, m
        m += 1
        if m == 13:
            y, m = y + 1, 1


def last_day(y, m, today):
    nxt = date(y + (m == 12), m % 12 + 1, 1)
    return min(nxt - timedelta(days=1), today)


def write_csv(path, header, rows):
    tmp = path + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)
    os.replace(tmp, path)


def pivot(kind):
    c = CFG[kind]
    per, names = {}, {}
    for p in sorted(glob.glob(f"{ROOT}/{c['snap']}/*.csv")):
        month = os.path.basename(p)[:-4]
        with open(p, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                code = r["종목코드"]
                per.setdefault(code, []).append(c["per_row"](month, r))
                names[code] = r["종목명"]
    os.makedirs(f"{ROOT}/{c['per']}", exist_ok=True)
    for code, rows in per.items():
        write_csv(f"{ROOT}/{c['per']}/{code}.csv", c["per_cols"], rows)
    live = set()
    lp, lk = c["live"]
    if os.path.exists(lp):
        with open(lp, encoding="utf-8") as f:
            live = {r[lk] for r in csv.DictReader(f)}
    lst = [[code, names[code], rows[0][0], rows[-1][0], "Y" if code in live else "N"]
           for code, rows in sorted(per.items())]
    write_csv(f"{ROOT}/{c['list']}", ["종목코드", "종목명", "첫월", "마지막월", "현재상장"], lst)
    return len(per), sum(1 for x in lst if x[4] == "N")


def run(kind):
    c = CFG[kind]
    raw = make_fetch(kind)
    today = datetime.now(KST).date()
    snap = f"{ROOT}/{c['snap']}"
    os.makedirs(snap, exist_ok=True)
    cur = f"{today:%Y-%m}"
    prev = f"{(today.replace(day=1) - timedelta(days=1)):%Y-%m}"
    start = os.environ.get("FROM", c["from"])
    t0, done, fails, streak, seen = time.time(), 0, [], 0, False

    for y, m in month_iter(start, today):
        key = f"{y:04d}-{m:02d}"
        path = f"{snap}/{key}.csv"
        if os.path.exists(path) and not FULL and key not in (cur, prev):
            seen = True
            continue
        d = last_day(y, m, today)
        rows = []
        for _ in range(10):
            if d.month != m:
                break
            rows = fetch(kind, raw, d.strftime("%Y%m%d"))
            time.sleep(0.8)
            if rows:
                break
            d -= timedelta(days=1)
        if not rows:
            if seen:                       # 데이터가 나오기 시작한 뒤의 빈 달만 문제로 본다
                streak += 1
                fails.append(key)
                print(f"  {key} 데이터 없음", file=sys.stderr)
                if streak >= 3:
                    print("  빈 달이 이어져 다시 로그인합니다.", file=sys.stderr)
                    login()
                    streak = 0
            continue
        seen, streak = True, 0
        write_csv(path, c["cols"], rows)
        done += 1
        print(f"  {kind} {key} {rows[0][0]} {len(rows)}종목 ({time.time()-t0:.0f}s)", file=sys.stderr)

    n, gone = pivot(kind)
    first = sorted(glob.glob(f"{snap}/*.csv"))
    print(f"완료 {kind}: 새로 받은 달 {done} · 종목 {n} (상장폐지·이름변경 {gone}) · "
          f"첫 달 {os.path.basename(first[0])[:-4] if first else '-'} · 빈 달 {len(fails)} "
          f"{time.time()-t0:.0f}s", file=sys.stderr)
    if fails:
        print("  빈 달: " + " ".join(fails), file=sys.stderr)

    # 검산 기준점
    probes = {"etf": ("069500", ("2014-12", "2020-12")),       # KODEX 200 2014-12 종가 24,670
              "stock": ("005930", ("2014-12", "2017-12"))}     # 삼성전자 분할 전 실제가 1,327,000 / 2,548,000 근처
    code, months = probes[kind]
    chk = f"{ROOT}/{c['per']}/{code}.csv"
    if os.path.exists(chk):
        for r in csv.reader(open(chk, encoding="utf-8")):
            if r[0] in months:
                print(f"  검산 {code} {r[0]} " + " · ".join(f"{h} {v}" for h, v in zip(c['per_cols'][1:], r[1:])),
                      file=sys.stderr)
    return os.path.exists(f"{snap}/{prev}.csv")


def main():
    kinds = sys.argv[1:] or ["etf"]
    for k in kinds:
        if k not in CFG:
            raise SystemExit(__doc__)
    if not login():
        print("KRX 로그인에 실패해 중단합니다.", file=sys.stderr)
        sys.exit(1)
    ok = all([run(k) for k in kinds])
    if not ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
