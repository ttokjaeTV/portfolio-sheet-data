#!/usr/bin/env python3
"""
국내 배당·분배금 → data/dividends.csv

두 갈래에서 모은다.

  1) 국내 개별주식 : pykrx `get_market_fundamental` (KOSPI·KOSDAQ 각 1회 호출)
       DPS = 주당배당금(최근 결산), DIV = 배당수익률(%)
       ※ 시장 전체를 한 번에 받으므로 종목별 반복 조회가 없다.

  2) 국내 ETF     : 월배당 트래커(ttokjaeTV/montly-div)의 분배금 이력 JSON
       실제 지급된 분배금과 과세표준액이 들어 있어 세후 계산까지 된다.

두 값을 나란히 낸다.
  연배당_실지급  = 최근 12개월 실제 지급액 합계        (보수적)
  연배당_연환산  = 가장 최근 1회 지급액 × 연 지급횟수   (최근 흐름 반영)

출력: data/dividends.csv
  종목코드,구분,주당배당금,배당수익률,지급주기,연지급횟수,
  연배당_실지급,연배당_연환산,과표_실지급,최근지급일,출처,갱신시각

※ pykrx 는 KRX 서버를 직접 부른다. 일부 네트워크(사내망·일부 클라우드)에서는
   빈 응답이 오며 `Expecting value: line 1 column 1` 로 터진다.
   그 경우 ETF 분만 쓰고 종료 코드 1 로 죽는다 — 반쪽 CSV 가 배포되면
   구독자 화면에서 배당이 통째로 사라지기 때문이다.
"""

import csv
import json
import os
import sys
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta, timezone

OUT_PATH = os.environ.get("DIVIDENDS_PATH", "data/dividends.csv")
ETF_PATH = os.environ.get("OUT_PATH", "data/etf_prices.csv")

# 월배당 트래커가 매월 갱신하는 분배금 이력 (분배금·과세표준액)
TRACKER = "https://ttokjaetv.github.io/montly-div/data/"
TRACKER_FILES = ["tax-base.json", "tax-base-extra.json"]

KST = timezone(timedelta(hours=9))
UA = {"User-Agent": "Mozilla/5.0"}

HEADER = ["종목코드", "구분", "주당배당금", "배당수익률", "지급주기", "연지급횟수",
          "연배당_실지급", "연배당_연환산", "과표_실지급", "지급월", "최근지급일", "출처", "갱신시각"]


def now_kst():
    return datetime.now(KST).strftime("%Y-%m-%d %H:%M")


def fetch_json(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))


# ---------------------------------------------------------------- ETF
def cycle_name(n):
    """연 지급횟수 → 사람이 읽는 주기 이름."""
    if n >= 11:
        return "월"
    if n >= 5:
        return "격월"
    if n >= 3:
        return "분기"
    if n == 2:
        return "반기"
    if n == 1:
        return "연"
    return "부정기"


def norm_date(v):
    """트래커는 2026/08/28, 다른 소스는 2026-08-28 로 준다.
    구분자가 섞이면 문자열 비교가 조용히 틀린다 ('/' > '-' 라서
    같은 해 1월 날짜가 9월 기준선을 통과해 버린다). 하나로 맞춘다."""
    s = str(v or "")[:10].replace("/", "-").replace(".", "-")
    p = s.split("-")
    if len(p) != 3 or not all(x.isdigit() for x in p):
        return ""
    return f"{int(p[0]):04d}-{int(p[1]):02d}-{int(p[2]):02d}"


def build_etf(today):
    """트래커 JSON → {코드: 행}. 최근 12개월 지급분만 센다."""
    cutoff = (today - timedelta(days=365)).strftime("%Y-%m-%d")
    out = {}
    got = 0
    for fn in TRACKER_FILES:
        try:
            doc = fetch_json(TRACKER + fn)
        except Exception as e:
            print(f"  트래커 {fn} 실패: {e}", file=sys.stderr)
            continue
        data = doc.get("data") or {}
        got += 1
        for code, recs in data.items():
            # recs = [[날짜, 분배금, 과세표준액], ...]
            rows = []
            for r in recs:
                if not r or len(r) < 2:
                    continue
                d = norm_date(r[0])
                if not d:
                    continue
                try:
                    amt = float(r[1] or 0)
                except (TypeError, ValueError):
                    continue
                try:
                    tax = float(r[2] or 0) if len(r) > 2 else 0.0
                except (TypeError, ValueError):
                    tax = 0.0
                rows.append((d, amt, tax))
            if not rows:
                continue
            rows.sort(key=lambda x: x[0])
            recent = [x for x in rows if x[0] >= cutoff]
            # 분배금 0원(미지급) 회차는 지급 횟수에서 뺀다
            paid = [x for x in recent if x[1] > 0]
            n = len(paid)
            real = round(sum(x[1] for x in paid), 4)
            tax_sum = round(sum(x[2] for x in paid), 4)
            last_d, last_amt = (paid[-1][0], paid[-1][1]) if paid else (rows[-1][0], 0.0)
            annual = round(last_amt * n, 4) if n else 0.0
            # 실제로 지급이 있었던 달. 캘린더는 추정이 아니라 이 값으로 그린다.
            months = sorted({int(x[0][5:7]) for x in paid})
            out[code.upper()] = [code.upper(), "국내ETF", "", "", cycle_name(n), n,
                                 real, annual, tax_sum,
                                 "|".join(str(m) for m in months), last_d, "montly-div", ""]
    if not got:
        print("  트래커 JSON 을 한 건도 못 받았습니다.", file=sys.stderr)
    return out


# ---------------------------------------------------------------- 개별주
def build_stocks(today):
    """pykrx 시장 펀더멘털에서 DPS·DIV 를 뽑는다. 시장당 1회 호출."""
    try:
        from pykrx import stock
    except ImportError:
        print("  pykrx 가 없습니다. pip install pykrx", file=sys.stderr)
        return {}, False

    # KRX 는 휴장일에 빈 응답을 준다. 최근 영업일을 며칠 거슬러 찾는다.
    out = {}
    ok = False
    for back in range(0, 8):
        d = (today - timedelta(days=back)).strftime("%Y%m%d")
        got_any = False
        for mkt in ("KOSPI", "KOSDAQ"):
            try:
                df = stock.get_market_fundamental(d, market=mkt)
            except Exception as e:
                print(f"  {d} {mkt}: {type(e).__name__} {e}", file=sys.stderr)
                continue
            if df is None or len(df) == 0 or "DPS" not in df.columns:
                continue
            got_any = True
            for code, row in df.iterrows():
                try:
                    dps = float(row.get("DPS") or 0)
                    div = float(row.get("DIV") or 0)
                except (TypeError, ValueError):
                    continue
                if dps <= 0 and div <= 0:
                    continue
                # 국내 상장사는 대부분 연 1회 결산배당이다.
                # 분기배당사도 DPS 는 연간 누계로 들어오므로 실지급=연환산으로 둔다.
                # KRX 펀더멘털은 지급월을 주지 않는다. 캘린더에서는
                # 추측해 넣지 않고 '지급월 미상'으로 따로 센다.
                out[str(code).upper()] = [str(code).upper(), "국내주식",
                                          dps, div, "연", 1,
                                          dps, dps, "", "", "", "KRX", ""]
        if got_any:
            ok = True
            print(f"  KRX 기준일 {d} · 배당 있는 종목 {len(out)}건", file=sys.stderr)
            break
    if not ok:
        print("  KRX 접속 실패 — 빈 응답만 돌아왔습니다.", file=sys.stderr)
    return out, ok


def main():
    today = datetime.now(KST).date()
    stamp = now_kst()

    print("[1/2] 국내 ETF 분배금 (월배당 트래커)", file=sys.stderr)
    etf = build_etf(today)
    print(f"  ETF {len(etf)}종", file=sys.stderr)

    print("[2/2] 국내 개별주 배당 (pykrx / KRX)", file=sys.stderr)
    stk, krx_ok = build_stocks(today)
    print(f"  개별주 {len(stk)}종", file=sys.stderr)

    # ETF 코드가 개별주 쪽에 섞여 들어오면 ETF 를 우선한다
    merged = {}
    merged.update(stk)
    merged.update(etf)
    for r in merged.values():
        r[-1] = stamp

    os.makedirs(os.path.dirname(OUT_PATH) or ".", exist_ok=True)
    with open(OUT_PATH, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(HEADER)
        for code in sorted(merged):
            w.writerow(merged[code])

    print(f"완료: {OUT_PATH} ({len(merged)}행)", file=sys.stderr)
    print(f"KRX 접속: {'OK' if krx_ok else '실패'}", file=sys.stderr)

    if not merged:
        print("한 건도 못 모았습니다. 배포 중단.", file=sys.stderr)
        sys.exit(1)
    if not krx_ok:
        print("개별주 배당이 비었습니다. ETF 분만 갱신됩니다.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
