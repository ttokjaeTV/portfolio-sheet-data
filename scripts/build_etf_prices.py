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
# 마스터에 아직 없는 신규 상장 ETF 보충용 (EUC-KR 응답)
NAVER_ETF_LIST = "https://finance.naver.com/api/sise/etfItemList.nhn"
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
    """마스터에서 종목 메타를 뽑는다."""
    data = get_json(MASTER_URL, timeout=60)
    etfs = data.get("etfs", [])
    meta = data.get("meta", {})

    seen, out = set(), []
    for e in etfs:
        code = (e.get("ticker") or "").strip()
        name = (e.get("name") or "").strip()
        if not code or not name or code in seen:
            continue
        seen.add(code)

        expense = e.get("expenseRatio")
        out.append({
            "code": code,
            "name": name,
            "assetClass": (e.get("assetClass") or "").strip(),   # 주식/채권/혼합자산/원자재/부동산/통화/기타
            "market": (e.get("market") or "").strip(),           # 국내/해외/국내&해외
            "expense": "" if expense in (None, "") else str(expense),
            # 연금계좌 안전자산 30% 규칙 판정용
            "safe": "Y" if e.get("isPensionSafeAsset") else "N",
        })

    if not out:
        print("마스터에서 종목을 하나도 읽지 못했습니다.", file=sys.stderr)
        sys.exit(1)

    add_new_listings(out, seen)
    return out, meta


def add_new_listings(out, seen):
    """마스터에 아직 없는 신규 상장 ETF 를 네이버 목록에서 보충한다.

    마스터(krx_etf_master.json)는 etf-selector 레포에서 주 1회 수동 갱신되므로,
    그 사이 상장한 ETF 는 도구에서 아예 검색되지 않는다. 시세만이라도 나오게 채운다.
    분류(자산군·총보수·안전자산)는 마스터에만 있으므로 비워 두고,
    다음 마스터 갱신 때 정상 값으로 덮인다.

    ※ KIND 상장법인목록에는 ETF 가 0건이다. ETF 는 상장'법인'이 아니라
      집합투자증권이라 그 목록에 들어가지 않는다. 그래서 네이버 목록을 쓴다.
    """
    try:
        req = urllib.request.Request(NAVER_ETF_LIST, headers=UA)
        with urllib.request.urlopen(req, timeout=30) as r:
            body = r.read().decode("euc-kr")          # ★ EUC-KR. utf-8 로 읽으면 깨진다
        items = json.loads(body)["result"]["etfItemList"]
    except Exception as exc:
        print(f"  [warn] 신규상장 보충 실패(무시하고 진행): {exc}", file=sys.stderr)
        return

    added = 0
    for it in items:
        code = (it.get("itemcode") or "").strip()
        name = (it.get("itemname") or "").strip()
        if not code or not name or code in seen:
            continue
        seen.add(code)
        out.append({
            "code": code, "name": name,
            "assetClass": "", "market": "", "expense": "", "safe": "N",
        })
        added += 1
        print(f"  [신규] {code} {name} (분류 미정 — 마스터 갱신 필요)", file=sys.stderr)

    if added:
        print(f"  마스터에 없는 신규 상장 {added}종 보충", file=sys.stderr)


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
    prices = fetch_prices([m["code"] for m in master])

    now = datetime.now(KST).strftime("%Y-%m-%d %H:%M")
    missing = 0

    os.makedirs(os.path.dirname(OUT_PATH) or ".", exist_ok=True)
    with open(OUT_PATH, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["종목코드", "종목명", "자산군", "시장", "총보수",
                    "안전자산", "현재가", "전일대비", "등락률", "갱신시각"])
        for m in master:
            p = prices.get(m["code"]) or {}
            if not p.get("price"):
                missing += 1
            w.writerow([
                m["code"], m["name"], m["assetClass"], m["market"],
                m["expense"], m["safe"],
                p.get("price", ""), p.get("change", ""), p.get("rate", ""), now,
            ])

    print(f"완료: {OUT_PATH} ({len(master)}행, 시세 없음 {missing}건)", file=sys.stderr)

    # 시세 누락이 과반이면 실패 처리 — 깨진 CSV가 배포되는 걸 막는다
    if missing > len(master) * 0.5:
        print("시세 누락이 과반입니다. 배포 중단.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
