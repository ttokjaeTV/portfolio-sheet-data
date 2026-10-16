#!/usr/bin/env python3
"""
원/달러 환율 월말 이력 → data/history/usdkrw_month.csv

  열: 월,기준일,환율,출처,뉴욕정오,서울종가

미국 종목 가격(data/history/us/)을 원화로 바꿔 백테스트하기 위한 자료다.

출처 두 가지를 같은 달에 나란히 받는다.
  뉴욕정오  FRED DEXKOUS — 미 연준 H.10 '뉴욕 정오 매입률'. 일별, 1981-04~. 공식 통계.
            https://fred.stlouisfed.org/series/DEXKOUS
  서울종가  네이버 FX_USDKRW 일별 종가 — 서울 외환시장 기준. 2004-12~ (그 이전은 응답이 비어 있다).
            indices_history.csv 의 '원/달러' 와 같은 소스.

'환율' 열 = 서울종가가 있으면 서울종가, 없으면 뉴욕정오. '출처' 열에 어느 쪽인지 남긴다.
  ★ 두 값은 시각이 달라(서울 15:30 vs 뉴욕 12:00) 같은 날도 몇 원씩 다르다.
    스크립트 끝에 겹치는 구간의 차이를 찍어 둔다.
  ★ 세법상 원화 환산(배당소득 등)은 서울외국환중개 '매매기준율'을 쓴다(fx_std.csv).
    이 파일은 백테스트용 시장 환율이다.

※ FRED 는 Claude 샌드박스에서 연결이 끊긴다(2026-10 실측). GitHub Actions 에서 돌린다.
   FRED 를 못 받으면 서울종가만으로 쓰고, 기존 파일의 뉴욕정오 값은 지우지 않는다.

사용: python scripts/build_fx_history.py
"""

import csv
import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
OUT = os.environ.get("FX_HISTORY_PATH", "data/history/usdkrw_month.csv")
FRED = os.environ.get("FRED_URL", "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DEXKOUS")
NAVER = "https://api.stock.naver.com/marketindex/exchange/FX_USDKRW/prices?page={}&pageSize=60"
UA = {"User-Agent": "Mozilla/5.0", "Referer": "https://m.stock.naver.com/"}


def get(url, timeout=60):
    last = None
    for i in range(4):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8")
        except Exception as e:
            last = e
            time.sleep(2 * (i + 1))
    raise last


def month_last(daily):
    """{날짜: 값} → {월: (그달 마지막 관측일, 값)}"""
    out = {}
    for d in sorted(daily):
        out[d[:7]] = (d, daily[d])
    return out


def fred():
    daily = {}
    text = get(FRED)
    for row in list(csv.reader(text.splitlines()))[1:]:
        if len(row) >= 2 and row[1] not in ("", "."):
            try:
                daily[row[0]] = float(row[1])
            except ValueError:
                pass
    return daily


def naver():
    daily, page = {}, 1
    while page < 200:
        try:
            rows = json.loads(get(NAVER.format(page)))
        except Exception as e:
            print(f"  네이버 {page}페이지 실패: {e}", file=sys.stderr)
            break
        if not rows:
            break
        for r in rows:
            d = r.get("localTradedAt")
            v = str(r.get("closePrice", "")).replace(",", "")
            if d and v:
                daily[d] = float(v)
        page += 1
        time.sleep(0.3)
    return daily


def read_old():
    old = {}
    if os.path.exists(OUT):
        with open(OUT, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                old[r["월"]] = r
    return old


def main():
    old = read_old()
    try:
        fd = fred()
        print(f"FRED DEXKOUS {len(fd)}일 {min(fd)} ~ {max(fd)}", file=sys.stderr)
    except Exception as e:
        fd = {}
        print(f"FRED 실패({type(e).__name__}) — 기존 뉴욕정오 값을 유지합니다.", file=sys.stderr)
    nd = naver()
    if nd:
        print(f"네이버 원/달러 {len(nd)}일 {min(nd)} ~ {max(nd)}", file=sys.stderr)
    fm, nm = month_last(fd), month_last(nd)

    months = sorted(set(fm) | set(nm) | set(old))
    rows, gaps = [], []
    for m in months:
        ny = f"{fm[m][1]:.2f}" if m in fm else old.get(m, {}).get("뉴욕정오", "")
        se = f"{nm[m][1]:.2f}" if m in nm else old.get(m, {}).get("서울종가", "")
        if se:
            day = nm[m][0] if m in nm else old[m]["기준일"]
            rows.append([m, day, se, "서울종가", ny, se])
        elif ny:
            day = fm[m][0] if m in fm else old[m]["기준일"]
            rows.append([m, day, ny, "뉴욕정오", ny, se])
        if ny and se:
            gaps.append(float(se) / float(ny) - 1)

    if len(rows) < len(old) * 0.9:
        print(f"행 수가 {len(old)} → {len(rows)} 로 줄어 저장하지 않습니다.", file=sys.stderr)
        sys.exit(1)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    tmp = OUT + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["월", "기준일", "환율", "출처", "뉴욕정오", "서울종가"])
        w.writerows(rows)
    os.replace(tmp, OUT)

    src = {}
    for r in rows:
        src[r[3]] = src.get(r[3], 0) + 1
    print(f"완료: {OUT} {len(rows)}개월 {rows[0][0]} ~ {rows[-1][0]} {src}", file=sys.stderr)
    if gaps:
        gaps.sort()
        mid = gaps[len(gaps) // 2]
        print(f"  서울종가/뉴욕정오 차이 {len(gaps)}개월: 중앙값 {mid*100:+.2f}% · "
              f"최소 {gaps[0]*100:+.2f}% · 최대 {gaps[-1]*100:+.2f}%", file=sys.stderr)
    for probe in ("1997-12", "2008-10", "2014-12", "2024-12"):
        r = next((x for x in rows if x[0] == probe), None)
        if r:
            print(f"  검산 {probe} 환율 {r[2]} ({r[3]}) 뉴욕 {r[4] or '-'} 서울 {r[5] or '-'}", file=sys.stderr)


if __name__ == "__main__":
    main()
