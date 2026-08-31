#!/usr/bin/env python3
"""
krx_etf_master.json + 네이버 시세 → data/etf_prices.csv

포트폴리오 관리 시트가 IMPORTDATA로 읽어갈 CSV를 만든다.
- 종목코드/종목명 : krx_etf_master.json (etf-selector 레포에서 운영 중인 마스터)
- 현재가/등락      : 네이버 폴링 API (쉼표 배치 조회)

출력: data/etf_prices.csv
  종목코드,종목명,현재가,전일대비,등락률,갱신시각
"""

import csv
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

MASTER_URL = "https://ttokjaetv.github.io/etf-selector/data/krx_etf_master.json"
PRICE_URL = "https://polling.finance.naver.com/api/realtime/domestic/stock/"
OUT_PATH = os.environ.get("OUT_PATH", "data/etf_prices.csv")

BATCH_SIZE = 50        # 한 번에 조회할 종목 수
RETRY = 3
SLEEP = 0.4            # 배치 간 간격(초)
KST = timezone(timedelta(hours=9))

UA = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.naver.com/"}


def get_json(url, timeout=30):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def load_master():
    """마스터에서 (종목코드, 종목명) 목록을 뽑는다."""
    data = get_json(MASTER_URL, timeout=60)
    etfs = data.get("etfs", [])
    meta = data.get("meta", {})

    out = []
    for e in etfs:
        code = (e.get("ticker") or "").strip()
        name = (e.get("name") or "").strip()
        if code and name:
            out.append((code, name))

    # 중복 제거(코드 기준), 순서 유지
    seen, uniq = set(), []
    for code, name in out:
        if code not in seen:
            seen.add(code)
            uniq.append((code, name))

    if not uniq:
        print("마스터에서 종목을 하나도 읽지 못했습니다.", file=sys.stderr)
        sys.exit(1)

    return uniq, meta


def num(v):
    """'10,855' → '10855' / 빈값 → ''"""
    if v is None or v == "":
        return ""
    return str(v).replace(",", "")


def fetch_prices(codes):
    """종목코드 리스트 → {코드: {price, change, rate}}"""
    prices = {}
    total = len(codes)

    for i in range(0, total, BATCH_SIZE):
        chunk = codes[i:i + BATCH_SIZE]
        url = PRICE_URL + ",".join(chunk)

        for attempt in range(RETRY):
            try:
                data = get_json(url)
                for d in data.get("datas", []):
                    code = d.get("itemCode")
                    if not code:
                        continue
                    prices[code] = {
                        "price": num(d.get("closePriceRaw", d.get("closePrice"))),
                        "change": num(d.get("compareToPreviousClosePriceRaw",
                                            d.get("compareToPreviousClosePrice"))),
                        "rate": num(d.get("fluctuationsRatio")),
                    }
                break
            except (urllib.error.URLError, json.JSONDecodeError, TimeoutError) as exc:
                if attempt == RETRY - 1:
                    print(f"  [warn] batch {i // BATCH_SIZE} 실패: {exc}", file=sys.stderr)
                else:
                    time.sleep(1.5 * (attempt + 1))

        print(f"  {min(i + BATCH_SIZE, total)}/{total}", file=sys.stderr)
        time.sleep(SLEEP)

    return prices


def main():
    print("마스터 로드...", file=sys.stderr)
    master, meta = load_master()
    print(f"  ETF {len(master)}종목 (기준일 {meta.get('dataDate', '?')})", file=sys.stderr)

    print("시세 조회...", file=sys.stderr)
    prices = fetch_prices([c for c, _ in master])

    now = datetime.now(KST).strftime("%Y-%m-%d %H:%M")
    missing = 0

    os.makedirs(os.path.dirname(OUT_PATH) or ".", exist_ok=True)
    with open(OUT_PATH, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["종목코드", "종목명", "현재가", "전일대비", "등락률", "갱신시각"])
        for code, name in master:
            p = prices.get(code)
            if not p or p["price"] == "":
                missing += 1
                w.writerow([code, name, "", "", "", now])
            else:
                w.writerow([code, name, p["price"], p["change"], p["rate"], now])

    print(f"완료: {OUT_PATH} ({len(master)}행, 시세 없음 {missing}건)", file=sys.stderr)

    # 시세 누락이 과반이면 실패 처리 — 깨진 CSV가 배포되는 걸 막는다
    if missing > len(master) * 0.5:
        print("시세 누락이 과반입니다. 배포 중단.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
