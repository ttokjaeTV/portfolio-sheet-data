#!/usr/bin/env python3
"""
원/달러 환율 월말 이력 → data/history/usdkrw_month.csv

  열: 월,기준일,매매기준율,서울종가

미국 종목 가격(data/history/us/)을 원화로 바꿔 백테스트하기 위한 자료다.

  매매기준율  서울외국환중개 고시 기준환율. 1990-03~ (시장평균환율제 시행 이후).
              세법상 외화 소득의 원화 환산 기준(국세청 원천세과-222)이기도 하다.
              GET http://www.smbs.biz/ExRate/StdExRate_xml.jsp?arr_value=USD_{시작}_{끝}
              → EUC-KR XML, <set label='YY.MM.DD' value='1380.6' />. 1년씩 끊어 받는다.
              (build_fx_std.py 와 같은 소스. 그쪽은 일별 최근분, 이쪽은 월말 전 기간)
  서울종가    네이버 FX_USDKRW 일별 종가 — 서울 외환시장 마감 시세. 2004-04~.
              indices_history.csv 의 '원/달러' 와 같은 소스. 참고용으로 나란히 둔다.

★ 1990-03 이전은 없다. 미국 종목 중 그 이전부터 있는 것(KO·AAPL 1985~ 등)은
  원화 백테스트를 1990-03 부터 하도록 계산기에서 자른다.
★ FRED(DEXKOUS, 미 연준 뉴욕 정오율)는 Claude 샌드박스·GitHub Actions 러너 모두에서
  연결이 끊겨(2026-10 실측) 쓰지 않는다.
★ 매매기준율과 서울종가는 같은 날도 0.2% 안팎 다르다(build_fx_std.py 문서 참고).

사용: python scripts/build_fx_history.py
"""

import csv
import json
import os
import re
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
OUT = os.environ.get("FX_HISTORY_PATH", "data/history/usdkrw_month.csv")
SMBS = "http://www.smbs.biz/ExRate/StdExRate_xml.jsp?arr_value=USD_{}_{}"
NAVER = "https://api.stock.naver.com/marketindex/exchange/FX_USDKRW/prices?page={}&pageSize=60"
UA = {"User-Agent": "Mozilla/5.0", "Referer": "https://m.stock.naver.com/"}
START_YEAR = 1990


def get(url, enc="utf-8", timeout=40):
    last = None
    for i in range(4):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode(enc, "replace")
        except Exception as e:
            last = e
            time.sleep(2 * (i + 1))
    raise last


def smbs():
    daily, today = {}, datetime.now(KST).date()
    for y in range(START_YEAR, today.year + 1):
        try:
            xml = get(SMBS.format(f"{y}-01-01", f"{y}-12-31"), enc="euc-kr")
        except Exception as e:
            print(f"  매매기준율 {y} 실패: {e}", file=sys.stderr)
            continue
        for yy, mm, dd, val in re.findall(r"label='(\d\d)\.(\d\d)\.(\d\d)' value='([\d.]+)'", xml):
            full = (1900 if int(yy) >= 80 else 2000) + int(yy)
            daily[f"{full}-{mm}-{dd}"] = float(val)
        time.sleep(0.3)
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


def month_last(daily):
    out = {}
    for d in sorted(daily):
        out[d[:7]] = (d, daily[d])
    return out


def read_old():
    old = {}
    if os.path.exists(OUT):
        with open(OUT, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                if "매매기준율" in r:
                    old[r["월"]] = r
    return old


def main():
    old = read_old()
    sd = smbs()
    if not sd:
        print("매매기준율을 한 건도 받지 못했습니다. 중단합니다.", file=sys.stderr)
        sys.exit(1)
    print(f"매매기준율 {len(sd)}일 {min(sd)} ~ {max(sd)}", file=sys.stderr)
    nd = naver()
    if nd:
        print(f"서울종가 {len(nd)}일 {min(nd)} ~ {max(nd)}", file=sys.stderr)
    sm, nm = month_last(sd), month_last(nd)

    rows, gaps = [], []
    cur = datetime.now(KST).strftime("%Y-%m")
    for m in sorted(set(sm) | set(old)):
        if m >= cur:          # ★ 끝난 달만 — 이번 달 환율은 계산기가 fx_std.csv(매일 갱신)로 붙인다
            continue
        if m in sm:
            day, std = sm[m][0], f"{sm[m][1]:.2f}"
        else:
            day, std = old[m]["기준일"], old[m]["매매기준율"]
        close = f"{nm[m][1]:.2f}" if m in nm else (old.get(m, {}).get("서울종가", ""))
        rows.append([m, day, std, close])
        if close:
            gaps.append(float(close) / float(std) - 1)

    if len(rows) < len([m for m in old if m < cur]) * 0.9:
        print(f"행 수가 {len(old)} → {len(rows)} 로 줄어 저장하지 않습니다.", file=sys.stderr)
        sys.exit(1)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    tmp = OUT + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["월", "기준일", "매매기준율", "서울종가"])
        w.writerows(rows)
    os.replace(tmp, OUT)

    # 빠진 달 점검
    ms = [int(r[0][:4]) * 12 + int(r[0][5:7]) for r in rows]
    holes = (ms[-1] - ms[0] + 1) - len(ms)
    print(f"완료: {OUT} {len(rows)}개월 {rows[0][0]} ~ {rows[-1][0]} · 빠진 달 {holes}", file=sys.stderr)
    if gaps:
        g = sorted(gaps)
        print(f"  서울종가/매매기준율 차이 {len(g)}개월: 중앙값 {g[len(g)//2]*100:+.2f}% · "
              f"최소 {g[0]*100:+.2f}% · 최대 {g[-1]*100:+.2f}%", file=sys.stderr)
    for probe in ("1990-03", "1997-12", "2008-10", "2014-12", "2024-12"):
        r = next((x for x in rows if x[0] == probe), None)
        if r:
            print(f"  검산 {probe} 매매기준율 {r[2]} (기준일 {r[1]}) 서울종가 {r[3] or '-'}", file=sys.stderr)


if __name__ == "__main__":
    main()
