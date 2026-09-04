#!/usr/bin/env python3
"""
us_symbols.json + 배당 이력 → data/us_dividends.csv

미국은 국내 세이브로 같은 '전 종목 한 방' 소스가 없어 종목별로 받아야 한다.
후보를 실측해 stockanalysis 를 주 소스로 정했다 (2026-09-03).

  stockanalysis  배당락일 + **지급일** + 주기, 12,300종 약 10분   ← 주 소스
  야후(chart)     배당락일만. 지급일 없음, 약 14분                 ← 폴백
  Nasdaq         커버리지 14%(NYSE·AMEX 0%), 94분                 ← 탈락
  네이버 /basic   연배당·수익률만, 지급월 없음                      ← 탈락

★ 기준일은 **지급일**이다.
  소득세법 시행령 §46 7호 — 집합투자기구로부터의 이익의 수입시기는 '지급받은 날'.
  국내(세이브로 실지급일)와 같은 기준이라 한·미가 맞아떨어진다.
  지급일을 못 받은 회차만 배당락일로 대체하고 출처 열에 표시한다.
  (실측: 배당락→지급 중앙값 7일, 72%가 같은 달 안에 끝난다)

출력: data/us_dividends.csv
  티커,종목명,구분,연배당USD,배당수익률,지급주기,연지급횟수,지급월,회차내역,
  최근배당락일,최근지급일,출처,갱신시각
"""

import csv
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone

SYMBOLS_PATH = os.environ.get("US_SYMBOLS_PATH", "data/us_symbols.json")
PRICES_PATH = os.environ.get("US_PRICES_PATH", "data/us_prices.csv")
OUT_PATH = os.environ.get("US_DIV_PATH", "data/us_dividends.csv")

SA_URL = "https://stockanalysis.com/api/symbol/{kind}/{sym}/dividend"
YH_URL = ("https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
          "?range=2y&interval=1mo&events=div")

WORKERS = int(os.environ.get("FETCH_WORKERS", "6"))   # 올리면 막힐 수 있다
RETRY = 3
KST = timezone(timedelta(hours=9))
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120",
      "Accept": "application/json"}

HEADER = ["티커", "종목명", "구분", "연배당USD", "배당수익률", "지급주기", "연지급횟수",
          "지급월", "회차내역", "최근배당락일", "최근지급일", "출처", "갱신시각"]

MONEY_RE = re.compile(r"[-+]?\d[\d,]*\.?\d*")


def _get(url, timeout=20):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _money(v):
    """'$0.910' → 0.91 / 'n/a' → None"""
    if not v:
        return None
    m = MONEY_RE.search(str(v).replace("$", ""))
    if not m:
        return None
    try:
        return float(m.group().replace(",", ""))
    except ValueError:
        return None


def _day(v):
    """'2026-09-10' → date / 'n/a'·빈값 → None"""
    if not v or str(v).lower() in ("n/a", "-", ""):
        return None
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def fetch_sa(ticker, is_etf):
    """stockanalysis. [(배당락일, 지급일|None, 금액), ...] 오름차순.
    404 는 '배당 없음'이 대부분이라 재시도하지 않는다."""
    url = SA_URL.format(kind="e" if is_etf else "s", sym=ticker)
    for attempt in range(RETRY):
        try:
            d = (_get(url) or {}).get("data") or {}
            out = []
            for h in (d.get("history") or []):
                ex, amt = _day(h.get("dt")), _money(h.get("amt"))
                if ex is None or amt is None:
                    continue
                out.append((ex, _day(h.get("pay")), amt))
            out.sort()
            return out
        except urllib.error.HTTPError as e:
            if e.code in (404, 403):
                return []
            if attempt == RETRY - 1:
                return None            # None = 실패(폴백 대상), [] = 배당 없음
            time.sleep(1.5 * (attempt + 1))
        except Exception:
            if attempt == RETRY - 1:
                return None
            time.sleep(1.5 * (attempt + 1))
    return None


def fetch_yh(ticker):
    """야후 폴백. 지급일이 없어 (배당락일, None, 금액) 으로만 돌려준다."""
    try:
        j = _get(YH_URL.format(sym=ticker))
        res = (j.get("chart") or {}).get("result") or []
        if not res:
            return []
        divs = (res[0].get("events") or {}).get("dividends") or {}
        out = []
        for v in divs.values():
            ts, amt = v.get("date"), v.get("amount")
            if ts is None or amt in (None, ""):
                continue
            out.append((datetime.fromtimestamp(ts, timezone.utc).date(), None, float(amt)))
        out.sort()
        return out
    except Exception:
        return []


def cycle_of(n):
    if n >= 11:
        return "월"
    if n in (4, 5):
        return "분기"
    if n in (2, 3):
        return "반기"
    if n == 1:
        return "연"
    return "불규칙"


def load_prices():
    px = {}
    if not os.path.exists(PRICES_PATH):
        return px
    with open(PRICES_PATH, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            try:
                v = float((r.get("현재가USD") or "").replace(",", ""))
                if v > 0:
                    px[r["티커"]] = v
            except (ValueError, KeyError):
                pass
    return px


def main():
    now = datetime.now(KST).strftime("%Y-%m-%d %H:%M")
    if not os.path.exists(SYMBOLS_PATH):
        print(f"{SYMBOLS_PATH} 가 없습니다.", file=sys.stderr)
        sys.exit(1)
    with open(SYMBOLS_PATH, encoding="utf-8") as f:
        symbols = json.load(f)

    items = sorted(((t, v) for t, v in symbols.items() if v and v.get("rc")),
                   key=lambda x: x[0])
    px = load_prices()
    print(f"미국 {len(items)}종 배당 이력 조회 (워커 {WORKERS})...", file=sys.stderr)

    # stockanalysis 는 '이미 공시된 미래 회차'까지 준다. 그대로 합치면 12개월에 13~21회가
    # 잡혀 연배당이 부풀어 오른다(실측: MSFT 3.64 → 4.47). 오늘까지로 끊는다.
    today = datetime.now(timezone.utc).date()
    cutoff = today - timedelta(days=365)
    rows, done, withdiv, fellback = [], 0, 0, 0

    def work(it):
        t, meta = it
        divs = fetch_sa(t, bool(meta.get("etf")))
        if divs is None:                       # 조회 실패 → 야후로 한 번 더
            return it, fetch_yh(t), "yahoo"
        return it, divs, "stockanalysis"

    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        for (ticker, meta), divs, src in ex.map(work, items):
            done += 1
            if src == "yahoo":
                fellback += 1
            if done % 1000 == 0:
                print(f"  {done}/{len(items)}  (배당 {withdiv}종, 폴백 {fellback})", file=sys.stderr)
            if not divs:
                continue

            # 세법상 수입시기는 '지급받은 날'. 지급일이 있으면 그걸 쓰고 없으면 배당락일로 대체한다.
            eff = [(pay or ex, ex, pay, amt) for ex, pay, amt in divs]
            # ★ 시작점은 포함하지 않는다 — (오늘-365일, 오늘].
            #   월배당은 12회 간격이 364일이라 양 끝이 다 걸려 13회로 세어진다.
            #   (실측 2026-09: 13회로 잡힌 208종 중 202종이 이 경계 현상이었다)
            recent = [x for x in eff if cutoff < x[0] <= today]
            if not recent:
                continue
            withdiv += 1

            annual = round(sum(a for _, _, _, a in recent), 4)
            months = sorted({d.month for d, _, _, _ in recent})
            hist = ";".join(f"{d:%Y-%m}:{a:g}" for d, _, _, a in recent)
            p = px.get(ticker)
            yld = round(annual / p * 100, 2) if p else ""
            last_ex = max(x[1] for x in recent)
            pays = [x[2] for x in recent if x[2]]
            # 회차의 절반 이상에 지급일이 있어야 '지급일 기준'이라고 말할 수 있다
            basis = "지급일" if len(pays) >= len(recent) / 2 else "배당락일"

            rows.append([
                ticker, meta.get("name", ""), "ETF" if meta.get("etf") else "주식",
                f"{annual:g}", yld, cycle_of(len(recent)), len(recent),
                "|".join(str(m) for m in months), hist,
                last_ex.isoformat(), max(pays).isoformat() if pays else "",
                basis, now,
            ])

    if len(items) >= 2000 and withdiv < len(items) * 0.2:
        print(f"배당 종목이 {withdiv}/{len(items)}종뿐입니다. "
              f"차단 가능성이 있어 배포를 중단합니다.", file=sys.stderr)
        sys.exit(1)

    rows.sort(key=lambda r: r[0])
    os.makedirs(os.path.dirname(OUT_PATH) or ".", exist_ok=True)
    with open(OUT_PATH, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(HEADER)
        w.writerows(rows)

    print(f"완료: {OUT_PATH} ({len(rows)}행 / 조회 {len(items)}종, 야후 폴백 {fellback}종)",
          file=sys.stderr)
    print(f"  주기: {dict(Counter(r[5] for r in rows))}", file=sys.stderr)
    print(f"  기준: {dict(Counter(r[11] for r in rows))}", file=sys.stderr)


if __name__ == "__main__":
    main()
