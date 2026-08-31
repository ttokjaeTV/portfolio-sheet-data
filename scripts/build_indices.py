#!/usr/bin/env python3
"""
주요 지수 + 환율 → data/indices.csv

국내지수는 폴링 API 배치, 해외지수는 종목별 index API 를 쓴다.
(worldstock/index 경로는 지수에 동작하지 않는다. api.stock.naver.com/index/{코드}/basic 를 쓸 것)

출력: data/indices.csv
  구분,이름,값,전일대비,등락률,기준시각
"""

import csv
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

DOM_URL = "https://polling.finance.naver.com/api/realtime/domestic/index/"
WORLD_URL = "https://api.stock.naver.com/index/{}/basic"
FX_URL = "https://api.stock.naver.com/marketindex/exchange/FX_USDKRW"
OUT_PATH = os.environ.get("INDICES_PATH", "data/indices.csv")

DOMESTIC = [("KOSPI", "코스피"), ("KOSDAQ", "코스닥")]
WORLD = [(".DJI", "다우"), (".IXIC", "나스닥"), (".INX", "S&P 500"), (".VIX", "VIX")]

KST = timezone(timedelta(hours=9))
UA = {"User-Agent": "Mozilla/5.0", "Referer": "https://m.stock.naver.com/"}


def get_json(url, timeout=20):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def num(v):
    return "" if v in (None, "") else str(v).replace(",", "")


def main():
    now = datetime.now(KST).strftime("%Y-%m-%d %H:%M")
    rows = []

    # 국내지수 — 한 번에
    try:
        data = get_json(DOM_URL + ",".join(c for c, _ in DOMESTIC))
        got = {d.get("itemCode"): d for d in data.get("datas", [])}
        for code, label in DOMESTIC:
            d = got.get(code)
            if not d:
                print(f"  [warn] {label} 없음", file=sys.stderr)
                continue
            rows.append(["국내", label, num(d.get("closePrice")),
                         num(d.get("compareToPreviousClosePrice")),
                         num(d.get("fluctuationsRatio"))])
    except Exception as exc:
        print(f"  [warn] 국내지수 실패: {exc}", file=sys.stderr)

    # 해외지수 — 종목별
    for code, label in WORLD:
        try:
            d = get_json(WORLD_URL.format(code))
            rows.append(["해외", label, num(d.get("closePrice")),
                         num(d.get("compareToPreviousClosePrice")),
                         num(d.get("fluctuationsRatio"))])
        except Exception as exc:
            print(f"  [warn] {label} 실패: {exc}", file=sys.stderr)
        time.sleep(0.3)

    # 환율
    try:
        info = get_json(FX_URL).get("exchangeInfo", {})
        rows.append(["환율", "원/달러", num(info.get("closePrice")), "",
                     num(info.get("fluctuationsRatio"))])
    except Exception as exc:
        print(f"  [warn] 환율 실패: {exc}", file=sys.stderr)

    if len(rows) < 4:
        print("지수를 충분히 받지 못했습니다. 배포 중단.", file=sys.stderr)
        sys.exit(1)

    os.makedirs(os.path.dirname(OUT_PATH) or ".", exist_ok=True)
    with open(OUT_PATH, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["구분", "이름", "값", "전일대비", "등락률", "기준시각"])
        for r in rows:
            w.writerow(r + [now])

    print(f"완료: {OUT_PATH} ({len(rows)}건)", file=sys.stderr)
    for r in rows:
        print(f"  {r[1]:<8} {r[2]:>12} {r[4]:>7}%", file=sys.stderr)


if __name__ == "__main__":
    main()
