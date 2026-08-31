#!/usr/bin/env python3
"""
KIND 상장법인목록 + 네이버 시세 → data/kr_stocks.csv

- 종목코드/회사명/시장구분 : KIND 상장법인목록 (kind.krx.co.kr, 공개 GET)
- 현재가                   : 네이버 폴링 API (쉼표 배치)

ETF 는 build_etf_prices.py 소관이라 여기서 제외한다.

출력: data/kr_stocks.csv
  종목코드,종목명,시장,업종,현재가,전일대비,등락률,갱신시각
"""

import csv
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

KIND_URL = ("https://kind.krx.co.kr/corpgeneral/corpList.do"
            "?method=download&searchType=13")
PRICE_URL = "https://polling.finance.naver.com/api/realtime/domestic/stock/"
OUT_PATH = os.environ.get("KR_STOCKS_PATH", "data/kr_stocks.csv")
ETF_PATH = os.environ.get("OUT_PATH", "data/etf_prices.csv")

BATCH_SIZE = 50
RETRY = 3
SLEEP = 0.35
KST = timezone(timedelta(hours=9))
UA = {"User-Agent": "Mozilla/5.0", "Referer": "https://kind.krx.co.kr/"}

CODE_RE = re.compile(r"^[0-9A-Z]{6}$")
TAG_RE = re.compile(r"<[^>]+>")
ROW_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S)
CELL_RE = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.S)


def get(url, timeout=40, raw=False):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read()
    return data if raw else json.loads(data.decode("utf-8"))


def load_kind():
    """KIND 상장법인목록에서 (코드, 회사명, 시장, 업종) 추출."""
    page = get(KIND_URL, raw=True).decode("euc-kr", "replace")
    rows = ROW_RE.findall(page)
    if not rows:
        print("KIND 목록을 읽지 못했습니다.", file=sys.stderr)
        sys.exit(1)

    def cells(r):
        return [html.unescape(TAG_RE.sub("", c)).strip() for c in CELL_RE.findall(r)]

    header = cells(rows[0])
    try:
        i_name = header.index("회사명")
        i_mkt = header.index("시장구분")
        i_code = header.index("종목코드")
        i_ind = header.index("업종")
    except ValueError:
        print(f"KIND 헤더가 예상과 다릅니다: {header}", file=sys.stderr)
        sys.exit(1)

    out, seen = [], set()
    for r in rows[1:]:
        c = cells(r)
        if len(c) <= max(i_name, i_mkt, i_code, i_ind):
            continue
        code = c[i_code].upper()
        if not CODE_RE.match(code) or code in seen:
            continue
        seen.add(code)
        out.append({"code": code, "name": c[i_name],
                    "market": c[i_mkt], "industry": c[i_ind]})
    return out


def load_etf_codes():
    """ETF CSV 에 이미 있는 코드는 제외한다."""
    codes = set()
    if os.path.exists(ETF_PATH):
        with open(ETF_PATH, encoding="utf-8") as f:
            for row in csv.reader(f):
                if row and row[0] != "종목코드":
                    codes.add(row[0].upper())
    return codes


def num(v):
    return "" if v in (None, "") else str(v).replace(",", "")


def fetch_prices(codes):
    prices = {}
    total = len(codes)
    for i in range(0, total, BATCH_SIZE):
        url = PRICE_URL + ",".join(codes[i:i + BATCH_SIZE])
        for attempt in range(RETRY):
            try:
                for d in get(url, timeout=25).get("datas", []):
                    c = d.get("itemCode")
                    if c:
                        prices[c] = {
                            "name": (d.get("stockName") or "").strip(),
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
        if (i // BATCH_SIZE) % 10 == 0:
            print(f"  {min(i + BATCH_SIZE, total)}/{total}", file=sys.stderr)
        time.sleep(SLEEP)
    return prices


def preferred_candidates(items):
    """우선주 코드를 규칙으로 만들어 낸다.

    KIND 상장법인목록에는 우선주가 없다(법인 단위 목록이라 삼성전자우·현대차2우B 가 빠진다).
    한국 우선주는 보통주 코드의 끝자리만 바뀌므로 후보를 만들어 네이버에 물어본다.
    없는 코드는 네이버가 조용히 빼고 돌려주므로 그대로 걸러진다.
        005930 삼성전자 → 005935 삼성전자우
        005380 현대차   → 005385 현대차우 / 005387 현대차2우B / 005389 현대차3우B
    """
    base = {x["code"] for x in items}
    out = []
    for x in items:
        c = x["code"]
        if not re.fullmatch(r"\d{5}0", c):
            continue
        for last in ("5", "7", "9"):
            cand = c[:5] + last
            if cand not in base:
                out.append({"code": cand, "name": "", "market": x["market"],
                            "industry": x["industry"]})
    return out


def main():
    now = datetime.now(KST).strftime("%Y-%m-%d %H:%M")

    print("KIND 상장법인목록 수집...", file=sys.stderr)
    items = load_kind()
    etf = load_etf_codes()
    items = [x for x in items if x["code"] not in etf]
    print(f"  보통주 {len(items)}종목 (ETF 제외)", file=sys.stderr)

    cands = preferred_candidates(items)
    print(f"  우선주 후보 {len(cands)}개 추가 조회", file=sys.stderr)
    allitems = items + cands

    print("시세 조회...", file=sys.stderr)
    prices = fetch_prices([x["code"] for x in allitems])

    rows = []
    for x in allitems:
        p = prices.get(x["code"]) or {}
        if not p.get("price"):
            continue                        # 없는 우선주 코드·거래정지 종목은 여기서 걸러진다
        # 종목명은 네이버 것을 쓴다. KIND 는 정식 법인명(현대자동차)이라
        # 증권사 화면 표기(현대차)와 달라 캡처 인식이 안 된다.
        name = p.get("name") or x["name"]
        if not name:
            continue
        rows.append([x["code"], name, x["market"], x["industry"],
                     p["price"], p["change"], p["rate"], now])

    os.makedirs(os.path.dirname(OUT_PATH) or ".", exist_ok=True)
    with open(OUT_PATH, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["종목코드", "종목명", "시장", "업종", "현재가", "전일대비", "등락률", "갱신시각"])
        for r in sorted(rows, key=lambda v: v[0]):
            w.writerow(r)

    pref = sum(1 for r in rows if re.fullmatch(r"\d{5}[579]", r[0]))
    print(f"완료: {OUT_PATH} ({len(rows)}행, 이 중 우선주 {pref}건)", file=sys.stderr)

    if len(rows) < len(items) * 0.5:
        print("시세 확보가 절반 미만입니다. 배포 중단.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
