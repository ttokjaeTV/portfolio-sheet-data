#!/usr/bin/env python3
"""
KRX 국내 ETF 월말 시세 (실제 종가·NAV·기초지수·순자산) → data/history/

  data/history/krx_etf_month/{YYYY-MM}.csv   그달 마지막 거래일 전종목 스냅숏 (상장폐지 종목 포함)
      기준일,종목코드,종목명,종가,NAV,순자산총액,상장주식수,기초지수명,기초지수
  data/history/kr_etf_krx/{코드}.csv          종목별로 다시 묶은 것
      월,기준일,종가,NAV,기초지수,순자산총액
  data/history/kr_etf_krx_list.csv           종목코드,종목명,첫월,마지막월,현재상장

왜 KRX 인가
  네이버 ETF 가격은 분배금까지 소급 보정된 값이고(docs/가격이력_수집.md),
  야후 실제 종가는 2007년 이후분만 있다. KRX 는 거래소 원본 실제 종가를 상장 초기부터 주고,
  NAV·기초지수·순자산이 같은 표에 붙어 있다. 그날 상장돼 있던 종목을 전부 주므로
  지금은 사라진 ETF 까지 남는다(생존편향 제거).

받는 방식
  전종목시세(MDCSTAT04301)를 매월 마지막 거래일 하루씩 부른다. 2002-10~ 약 290회.
  말일이 휴일이면 하루씩 당겨 최대 10일까지 찾는다.
  지난달 이전 스냅숏 파일이 이미 있으면 다시 받지 않는다(이어받기).

로그인
  data.krx.co.kr 은 로그인이 필요하다. scripts/krx_auth.py 가 KRX_ID/KRX_PW 를 준비한다.
  GitHub Actions 에서는 레포 Secrets 의 KRX_ID/KRX_PW 를 쓴다.
  ★ 계정 값은 어디에도 출력하지 않는다. pykrx 가 stdout 에 아이디를 찍으므로 import 부터 막는다.
  세션이 약 30분 뒤 끊기므로 20분마다, 그리고 빈 응답이 이어지면 다시 로그인한다.

사용: python scripts/build_krx_etf_history.py
  FROM=YYYYMM (기본 200210), FULL=1 (있는 스냅숏도 다시 받기)
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
SNAP = f"{ROOT}/krx_etf_month"
PER = f"{ROOT}/kr_etf_krx"
FULL = os.environ.get("FULL") == "1"
FROM = os.environ.get("FROM", "200210")
RELOGIN_SEC = 20 * 60

COLS = ["기준일", "종목코드", "종목명", "종가", "NAV", "순자산총액", "상장주식수", "기초지수명", "기초지수"]
SRC = ["ISU_SRT_CD", "ISU_ABBRV", "TDD_CLSPRC", "NAV", "INVSTASST_NETASST_TOTAMT",
       "LIST_SHRS", "IDX_IND_NM", "OBJ_STKPRC_IDX"]


def num(v):
    v = str(v or "").replace(",", "").strip()
    if v in ("", "-", "0", "0.00"):
        return ""
    return v


def quiet():
    return contextlib.redirect_stdout(io.StringIO())


_last_login = 0.0


def login():
    global _last_login
    ok = krx_auth.login(verbose=True)
    _last_login = time.time()
    return ok


def fetch(fetcher, d):
    """하루치 전종목 → 행 리스트. 휴장일·빈 응답이면 []."""
    if time.time() - _last_login > RELOGIN_SEC:
        login()
    for attempt in range(3):
        try:
            with quiet():
                df = fetcher.fetch(d)
            break
        except Exception as e:
            print(f"  {d} 요청 오류 {type(e).__name__} — 재시도", file=sys.stderr)
            time.sleep(3 * (attempt + 1))
    else:
        return []
    if df is None or len(df) == 0 or "ISU_SRT_CD" not in df.columns:
        return []
    rows = []
    for _, r in df.iterrows():
        close = num(r.get("TDD_CLSPRC"))
        if not close:
            continue
        rows.append([d, str(r["ISU_SRT_CD"]).strip().upper(), str(r.get("ISU_ABBRV", "")).strip(),
                     close, num(r.get("NAV")), num(r.get("INVSTASST_NETASST_TOTAMT")),
                     num(r.get("LIST_SHRS")), str(r.get("IDX_IND_NM", "")).strip(),
                     num(r.get("OBJ_STKPRC_IDX"))])
    return rows


def month_iter(today):
    y, m = int(FROM[:4]), int(FROM[4:6])
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


def pivot():
    """스냅숏 전체를 종목별 파일과 목록으로 다시 묶는다."""
    per, names = {}, {}
    for p in sorted(glob.glob(f"{SNAP}/*.csv")):
        month = os.path.basename(p)[:-4]
        with open(p, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                c = r["종목코드"]
                per.setdefault(c, []).append(
                    [month, r["기준일"], r["종가"], r["NAV"], r["기초지수"], r["순자산총액"]])
                names[c] = r["종목명"]
    os.makedirs(PER, exist_ok=True)
    for c, rows in per.items():
        write_csv(f"{PER}/{c}.csv", ["월", "기준일", "종가", "NAV", "기초지수", "순자산총액"], rows)
    live = set()
    if os.path.exists("data/etf_prices.csv"):
        with open("data/etf_prices.csv", encoding="utf-8") as f:
            live = {r["종목코드"] for r in csv.DictReader(f)}
    lst = [[c, names[c], rows[0][0], rows[-1][0], "Y" if c in live else "N"]
           for c, rows in sorted(per.items())]
    write_csv(f"{ROOT}/kr_etf_krx_list.csv", ["종목코드", "종목명", "첫월", "마지막월", "현재상장"], lst)
    return len(per), sum(1 for x in lst if x[4] == "N")


def main():
    if not login():
        print("KRX 로그인에 실패해 중단합니다.", file=sys.stderr)
        sys.exit(1)
    with quiet():
        from pykrx.website.krx.etx.core import 전종목시세_ETF
    fetcher = 전종목시세_ETF()

    today = datetime.now(KST).date()
    os.makedirs(SNAP, exist_ok=True)
    cur = f"{today:%Y-%m}"
    prev = f"{(today.replace(day=1) - timedelta(days=1)):%Y-%m}"
    t0, done, fails, empty_streak = time.time(), 0, [], 0

    for y, m in month_iter(today):
        key = f"{y:04d}-{m:02d}"
        path = f"{SNAP}/{key}.csv"
        if os.path.exists(path) and not FULL and key not in (cur, prev):
            continue
        d = last_day(y, m, today)
        rows = []
        for _ in range(10):
            if d.month != m:
                break
            rows = fetch(fetcher, d.strftime("%Y%m%d"))
            time.sleep(0.8)
            if rows:
                break
            d -= timedelta(days=1)
        if not rows:
            empty_streak += 1
            fails.append(key)
            print(f"  {key} 데이터 없음", file=sys.stderr)
            if empty_streak >= 3:          # 세션 만료 의심 — 다시 로그인하고 계속
                print("  빈 달이 이어져 다시 로그인합니다.", file=sys.stderr)
                login()
                empty_streak = 0
            continue
        empty_streak = 0
        write_csv(path, COLS, rows)
        done += 1
        print(f"  {key} {rows[0][0]} {len(rows)}종목 ({time.time()-t0:.0f}s)", file=sys.stderr)

    n, gone = pivot()
    print(f"완료: 새로 받은 달 {done} · 종목 {n} (상장폐지·이름변경 {gone}) · "
          f"빈 달 {len(fails)} {time.time()-t0:.0f}s", file=sys.stderr)
    if fails:
        print("  빈 달: " + " ".join(fails), file=sys.stderr)

    # 검산용 — 야후 실제 종가와 대조할 기준점 (KODEX 200 2014-12 = 24,670 근처여야 정상)
    chk = f"{PER}/069500.csv"
    if os.path.exists(chk):
        for r in csv.reader(open(chk, encoding="utf-8")):
            if r[0] in ("2014-12", "2020-12"):
                print(f"  검산 KODEX 200 {r[0]} 종가 {r[2]} NAV {r[3]} 기초지수 {r[4]}", file=sys.stderr)
    # 최근 달까지 받은 게 없으면 실패로 본다
    if not os.path.exists(f"{SNAP}/{prev}.csv"):
        sys.exit(1)


if __name__ == "__main__":
    main()
