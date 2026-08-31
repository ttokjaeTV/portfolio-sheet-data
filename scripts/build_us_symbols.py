#!/usr/bin/env python3
"""
미국 상장 종목의 네이버 reutersCode 매핑을 만든다.

왜 필요한가:
  네이버는 미국 종목을 reutersCode로 조회한다. 그런데 이 코드에 규칙이 없다.
    NASDAQ : AAPL.O, QQQ.O
    NYSE   : JPM, KO       (접미사 없음)
    AMEX   : SCHD.K        인데 같은 AMEX인 SPY 는 접미사 없음
    BRK.B  : BRKb
  거래소만 알아서는 못 만들기 때문에, 자동완성 API로 한 번 조회해 캐싱한다.

증분 동작:
  data/us_symbols.json 에 이미 있는 티커는 건너뛴다.
  신규 상장분만 조회하므로 두 번째 실행부터는 몇 초면 끝난다.

출력: data/us_symbols.json
  { "AAPL": {"rc":"AAPL.O","name":"애플","ex":"NASDAQ","etf":false}, ... }
"""

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

NASDAQ_LISTED = "https://www.nasdaqtrader.com/dynamic/symdir/nasdaqlisted.txt"
OTHER_LISTED = "https://www.nasdaqtrader.com/dynamic/symdir/otherlisted.txt"
AUTOCOMPLETE = "https://m.stock.naver.com/front-api/search/autoComplete?query={}&target=stock"

OUT_PATH = os.environ.get("US_SYMBOLS_PATH", "data/us_symbols.json")
WORKERS = int(os.environ.get("WORKERS", "6"))
LIMIT = int(os.environ.get("LIMIT", "0"))        # 0이면 전체
UA = {"User-Agent": "Mozilla/5.0"}


def fetch(url, timeout=20):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def universe():
    """나스닥 공식 심볼 디렉터리에서 미국 상장 티커를 모은다."""
    out = {}

    # nasdaqlisted.txt : Symbol|Security Name|Market Category|Test Issue|...|ETF|
    for line in fetch(NASDAQ_LISTED).splitlines()[1:]:
        p = line.split("|")
        if len(p) < 7 or p[0].startswith("File Creation"):
            continue
        if p[3] == "Y":          # 테스트 종목 제외
            continue
        out[p[0].strip()] = {"etf": p[6].strip() == "Y", "src": "NASDAQ"}

    # otherlisted.txt : ACT Symbol|Security Name|Exchange|CQS Symbol|ETF|...|Test Issue|
    for line in fetch(OTHER_LISTED).splitlines()[1:]:
        p = line.split("|")
        if len(p) < 7 or p[0].startswith("File Creation"):
            continue
        if p[6].strip() == "Y":  # 테스트 종목 제외
            continue
        sym = p[0].strip()
        if sym and sym not in out:
            out[sym] = {"etf": p[4].strip() == "Y", "src": p[2].strip()}

    return out


def resolve(sym):
    """티커 → 네이버 reutersCode. 못 찾으면 None."""
    q = urllib.parse.quote(sym)
    for attempt in range(3):
        try:
            data = json.loads(fetch(AUTOCOMPLETE.format(q), timeout=15))
            items = (data.get("result") or {}).get("items") or []
            for it in items:
                # 티커가 정확히 일치하고 미국 종목인 것만
                if (it.get("code") or "").upper() == sym.upper() \
                        and it.get("nationCode") == "USA":
                    return {
                        "rc": it.get("reutersCode"),
                        "name": it.get("name"),
                        "ex": it.get("typeCode"),
                        "etf": bool(it.get("isEtf")),
                    }
            return None
        except (urllib.error.URLError, json.JSONDecodeError, TimeoutError):
            time.sleep(1.0 * (attempt + 1))
    return None


def main():
    cache = {}
    if os.path.exists(OUT_PATH):
        try:
            with open(OUT_PATH, encoding="utf-8") as f:
                cache = json.load(f)
            print(f"기존 캐시 {len(cache)}건", file=sys.stderr)
        except json.JSONDecodeError:
            print("캐시 파일이 깨져 있어 새로 만듭니다.", file=sys.stderr)

    print("티커 목록 수집...", file=sys.stderr)
    uni = universe()
    print(f"  전체 {len(uni)}종목", file=sys.stderr)

    todo = [s for s in uni if s not in cache]
    if LIMIT:
        todo = todo[:LIMIT]
    print(f"  신규 조회 대상 {len(todo)}종목", file=sys.stderr)

    if not todo:
        print("갱신할 항목이 없습니다.", file=sys.stderr)
        return

    def flush():
        os.makedirs(os.path.dirname(OUT_PATH) or ".", exist_ok=True)
        tmp = OUT_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        os.replace(tmp, OUT_PATH)

    done = [0]
    step = max(1, len(todo) // 20)
    SAVE_EVERY = 500          # 중간 저장 — 중단돼도 진행분이 남는다

    def work(sym):
        r = resolve(sym)
        done[0] += 1
        if done[0] % step == 0:
            print(f"  {done[0]}/{len(todo)}", file=sys.stderr)
        return sym, r

    try:
        with ThreadPoolExecutor(max_workers=WORKERS) as ex:
            for n, (sym, r) in enumerate(ex.map(work, todo), 1):
                if r and r.get("rc"):
                    r["etf"] = r["etf"] or uni[sym]["etf"]
                    cache[sym] = r
                else:
                    cache[sym] = None      # 네이버 미지원 — 재조회 방지용으로 기록
                if n % SAVE_EVERY == 0:
                    flush()
    finally:
        flush()

    found = sum(1 for v in cache.values() if v)
    print(f"완료: {OUT_PATH} (전체 {len(cache)}건 / 조회 성공 {found}건)", file=sys.stderr)


if __name__ == "__main__":
    main()
