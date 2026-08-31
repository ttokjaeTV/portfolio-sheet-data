#!/usr/bin/env python3
"""
us_symbols.json + 네이버 시세 → data/us_prices.csv, data/fx.csv

- 종목코드/종목명 : data/us_symbols.json (build_us_symbols.py가 만든 캐시)
- 현재가          : 네이버 해외주식 폴링 API (쉼표 배치)
- 환율            : 네이버 하나은행 고시 USD/KRW

출력
  data/us_prices.csv : 티커,종목명,구분,거래소,현재가USD,전일대비,등락률,갱신시각
  data/fx.csv        : 통화,환율,등락률,갱신시각
"""

import csv
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

SYMBOLS_PATH = os.environ.get("US_SYMBOLS_PATH", "data/us_symbols.json")
OUT_PATH = os.environ.get("US_PRICES_PATH", "data/us_prices.csv")
FX_PATH = os.environ.get("FX_PATH", "data/fx.csv")

PRICE_URL = "https://polling.finance.naver.com/api/realtime/worldstock/stock/"
FX_URL = "https://api.stock.naver.com/marketindex/exchange/FX_USDKRW"

BATCH_SIZE = 50
RETRY = 3
SLEEP = 0.3
KST = timezone(timedelta(hours=9))
UA = {"User-Agent": "Mozilla/5.0", "Referer": "https://m.stock.naver.com/"}


def get_json(url, timeout=25):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def num(v):
    if v is None or v == "":
        return ""
    return str(v).replace(",", "")


def fetch_fx(now):
    """USD/KRW 고시환율."""
    try:
        info = get_json(FX_URL).get("exchangeInfo", {})
        rate = num(info.get("closePrice"))
        ratio = num(info.get("fluctuationsRatio"))
        if not rate:
            raise ValueError("환율 없음")
    except Exception as exc:
        print(f"  [warn] 환율 조회 실패: {exc}", file=sys.stderr)
        return False

    os.makedirs(os.path.dirname(FX_PATH) or ".", exist_ok=True)
    with open(FX_PATH, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["통화", "환율", "등락률", "갱신시각"])
        w.writerow(["USDKRW", rate, ratio, now])
    print(f"  USD/KRW {rate}", file=sys.stderr)
    return True


def fetch_prices(codes):
    prices = {}
    total = len(codes)
    for i in range(0, total, BATCH_SIZE):
        chunk = codes[i:i + BATCH_SIZE]
        url = PRICE_URL + ",".join(chunk)

        for attempt in range(RETRY):
            try:
                for d in get_json(url).get("datas", []):
                    rc = d.get("reutersCode")
                    if not rc:
                        continue
                    prices[rc] = {
                        "price": num(d.get("closePrice")),
                        "change": num(d.get("compareToPreviousClosePrice")),
                        "rate": num(d.get("fluctuationsRatio")),
                    }
                break
            except (urllib.error.URLError, json.JSONDecodeError, TimeoutError) as exc:
                if attempt == RETRY - 1:
                    print(f"  [warn] batch {i // BATCH_SIZE} 실패: {exc}", file=sys.stderr)
                else:
                    time.sleep(1.5 * (attempt + 1))

        if (i // BATCH_SIZE) % 20 == 0:
            print(f"  {min(i + BATCH_SIZE, total)}/{total}", file=sys.stderr)
        time.sleep(SLEEP)
    return prices


def main():
    now = datetime.now(KST).strftime("%Y-%m-%d %H:%M")

    print("환율 조회...", file=sys.stderr)
    fetch_fx(now)

    if not os.path.exists(SYMBOLS_PATH):
        print(f"{SYMBOLS_PATH} 가 없습니다. build_us_symbols.py 를 먼저 실행하세요.", file=sys.stderr)
        sys.exit(1)

    with open(SYMBOLS_PATH, encoding="utf-8") as f:
        symbols = json.load(f)

    items = [(t, v) for t, v in symbols.items() if v and v.get("rc")]
    items.sort(key=lambda x: x[0])
    print(f"미국 종목 {len(items)}개 시세 조회...", file=sys.stderr)

    prices = fetch_prices([v["rc"] for _, v in items])

    missing = 0
    os.makedirs(os.path.dirname(OUT_PATH) or ".", exist_ok=True)
    with open(OUT_PATH, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["티커", "종목명", "구분", "거래소", "현재가USD", "전일대비", "등락률", "갱신시각"])
        for ticker, v in items:
            p = prices.get(v["rc"]) or {}
            if not p.get("price"):
                missing += 1
                continue                    # 시세 없는 종목은 CSV에서 제외 (용량 절약)
            w.writerow([
                ticker, v.get("name", ""), "ETF" if v.get("etf") else "주식",
                v.get("ex", ""), p.get("price", ""), p.get("change", ""), p.get("rate", ""), now,
            ])

    kept = len(items) - missing
    print(f"완료: {OUT_PATH} ({kept}행, 시세 없어 제외 {missing}건)", file=sys.stderr)

    if kept < len(items) * 0.5:
        print("시세 확보가 절반 미만입니다. 배포 중단.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
