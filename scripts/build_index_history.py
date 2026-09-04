#!/usr/bin/env python3
"""
주요 지수 + 환율의 5년치 일별 종가 → data/indices_history.csv

상황판 타일을 눌렀을 때 흐름을 보여주기 위한 자료다.
브라우저에서 네이버를 직접 부르면 CORS 로 막히므로 여기서 받아 레포에 넣는다.

국내지수 : api.stock.naver.com/chart/domestic/index/{코드}/day?startDateTime=&endDateTime=
해외지수 : api.stock.naver.com/chart/foreign/index/{코드}/day?…
환율     : api.stock.naver.com/marketindex/exchange/FX_USDKRW/prices?page=&pageSize=

네이버 쪽 제약 두 가지를 우회한 형태다.
  - periodType=dayCandle&count=N 은 N 을 아무리 키워도 110일에서 끊긴다. 날짜 범위로 받는다.
  - 환율 pageSize 는 100 이면 400 을 뱉는다. 60 까지만 받는다.

출력: data/indices_history.csv
  이름,날짜,종가
"""

import csv
import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone

DOM_URL = ("https://api.stock.naver.com/chart/domestic/index/{}/day"
           "?startDateTime={}0000&endDateTime={}0000")
FOR_URL = ("https://api.stock.naver.com/chart/foreign/index/{}/day"
           "?startDateTime={}0000&endDateTime={}0000")
FX_URL = "https://api.stock.naver.com/marketindex/exchange/FX_USDKRW/prices?page={}&pageSize=60"

OUT_PATH = os.environ.get("INDEX_HISTORY_PATH", "data/indices_history.csv")
DAYS = 1830         # 달력일. 휴장일이 빠져 영업일 약 1,230일(5년)이 남는다
FX_PAGES = 22       # 60건씩 22페이지 = 약 5년

DOMESTIC = [("KOSPI", "코스피"), ("KOSDAQ", "코스닥")]
WORLD = [(".DJI", "다우"), (".IXIC", "나스닥"), (".INX", "S&P 500"), (".VIX", "VIX")]

KST = timezone(timedelta(hours=9))
UA = {"User-Agent": "Mozilla/5.0", "Referer": "https://m.stock.naver.com/"}


def get_json(url, timeout=25):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def candles(url, label):
    """일봉에서 (날짜, 종가) 만 뽑는다."""
    data = get_json(url)
    infos = data.get("priceInfos") if isinstance(data, dict) else data
    out = []
    for d in infos or []:
        day, close = d.get("localDate"), d.get("closePrice")
        if not day or close in (None, ""):
            continue
        out.append((f"{day[:4]}-{day[4:6]}-{day[6:]}", str(close)))
    if not out:
        print(f"  [warn] {label} 비어 있음", file=sys.stderr)
    return out


def fx_history():
    out = []
    for page in range(1, FX_PAGES + 1):
        try:
            for d in get_json(FX_URL.format(page)) or []:
                day = d.get("localTradedAt")
                close = str(d.get("closePrice", "")).replace(",", "")
                if day and close:
                    out.append((day, close))
        except Exception as exc:
            print(f"  [warn] 환율 {page}페이지 실패: {exc}", file=sys.stderr)
        time.sleep(0.3)
    return out


def main():
    today = datetime.now(KST)
    end = today.strftime("%Y%m%d")
    start = (today - timedelta(days=DAYS)).strftime("%Y%m%d")
    rows = []

    for group, base in ((DOMESTIC, DOM_URL), (WORLD, FOR_URL)):
        for code, label in group:
            try:
                for day, close in candles(base.format(code, start, end), label):
                    rows.append([label, day, close])
            except Exception as exc:
                print(f"  [warn] {label} 실패: {exc}", file=sys.stderr)
            time.sleep(0.3)

    for day, close in fx_history():
        rows.append(["원/달러", day, close])

    names = {r[0] for r in rows}
    if len(names) < 6:
        print(f"지수를 충분히 받지 못했습니다({len(names)}종). 배포 중단.", file=sys.stderr)
        sys.exit(1)

    # 이름별로 묶고 날짜 오름차순. 같은 날짜가 두 번 들어오면 뒤엣것을 버린다.
    rows.sort(key=lambda r: (r[0], r[1]))
    dedup, seen = [], set()
    for r in rows:
        k = (r[0], r[1])
        if k in seen:
            continue
        seen.add(k)
        dedup.append(r)

    # ★ 이 스크립트는 파일을 덧붙이지 않고 매번 새로 그린다.
    #   그래서 API 가 일부만 응답하면 있던 이력이 조용히 사라진다.
    #   '원/달러' 는 미국 배당의 원화 환산(세금 계산)에 쓰이므로 특히 위험하다.
    #   시리즈별로 기존 행 수의 90% 미만이면 배포를 중단한다.
    if os.path.exists(OUT_PATH):
        prev = {}
        try:
            with open(OUT_PATH, encoding="utf-8") as f:
                for r in csv.reader(f):
                    if len(r) >= 2 and r[0] != "이름":
                        prev[r[0]] = prev.get(r[0], 0) + 1
        except OSError as exc:
            print(f"  [warn] 기존 파일을 읽지 못했습니다: {exc}", file=sys.stderr)
        now = {}
        for r in dedup:
            now[r[0]] = now.get(r[0], 0) + 1
        for name, before in prev.items():
            after = now.get(name, 0)
            if after < before * 0.9:
                print(f"{name} 이력이 {before}일 → {after}일 로 줄었습니다. "
                      f"수집 실패로 보고 배포를 중단합니다.", file=sys.stderr)
                sys.exit(1)

    os.makedirs(os.path.dirname(OUT_PATH) or ".", exist_ok=True)
    with open(OUT_PATH, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["이름", "날짜", "종가"])
        w.writerows(dedup)

    print(f"완료: {OUT_PATH} ({len(dedup)}행)", file=sys.stderr)
    for n in sorted(names):
        sub = [r for r in dedup if r[0] == n]
        print(f"  {n:<8} {len(sub):>5}일  {sub[0][1]} ~ {sub[-1][1]}", file=sys.stderr)


if __name__ == "__main__":
    main()
