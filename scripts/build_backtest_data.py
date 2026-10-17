#!/usr/bin/env python3
"""
백테스트 계산기용 데이터 정리 → data/bt/

  data/bt/index.json        검색용 종목 목록 (현재 상장 종목)
      {"updated": "...", "fx": "history/usdkrw_month.csv",
       "rows": [[키, 이름, "E"|"S", 시작월YYYYMM, 마지막월YYYYMM, 배당있음 0|1, 품질경고 수], ...]}
      키: "K:069500" (국내) / "U:SCHD" (미국). 가격·배당 파일 경로는 아래 규칙으로 만든다.
  data/bt/div/K/{코드}.csv  국내 배당·분배금  월,금액   ← 가격 파일과 같은 단위
  data/bt/div/U/{티커}.csv  미국 배당         월,금액   (USD)

가격 파일은 새로 만들지 않고 data/history/ 에 있는 것을 가리킨다.
  국내 ETF   history/kr_etf_krx/{코드}.csv   KRX 실제 종가(분배금 보정 없음), 2002-10~
             (KRX 에 없으면 history/kr_etf/{코드}.csv — 네이버는 분배금 보정값이라 배당 파일을 붙이지 않는다)
  국내 주식  history/kr_stock/{코드}.csv     네이버 분할 보정 종가, 1990~
  미국       history/us/{티커}.csv           야후 분할 보정 종가(USD)

배당을 가격과 같은 단위로 맞추는 방법
  국내 ETF  세이브로 분배금(기준일 월) 그대로 — 가격이 실제 종가라 단위가 같다.
  국내 주식 세이브로 배당금은 '당시 1주' 금액, 네이버 가격은 이후 분할·무상증자를 소급한 값이다.
            같은 달 KRX 실제 종가 ÷ 네이버 종가 = 그 시점의 보정 배수. 배당금을 이 배수로 나눈다.
            (kr_splits.csv 대신 이 방식을 쓰는 이유: 네이버가 실제로 적용한 보정을 그대로 따라가므로
             무상증자·권리락까지 자동으로 맞는다. 삼성전자 2017-12 배수 = 50.0)
            KRX 가 없는 1995-05 이전은 KRX 첫 달 배수를 쓴다.
  미국      야후 배당은 이미 분할 보정돼 있다. us_dividends_patch.csv(누락·오류 보정)를 덮는다.

사용: python scripts/build_backtest_data.py
"""

import csv
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
H = "data/history"
OUT = "data/bt"


def rows(path):
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def series(path, col="종가", key="월"):
    if not os.path.exists(path):
        return {}
    out = {}
    for r in rows(path):
        v = r.get(col, "")
        if v:
            try:
                out[r[key]] = float(v)
            except ValueError:
                pass
    return out


def fmt(v):
    return str(int(v)) if float(v).is_integer() else f"{v:.6f}".rstrip("0").rstrip(".")


def write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["월", "금액"])
        for m in sorted(data):
            if data[m] > 0:
                w.writerow([m, fmt(round(data[m], 6))])


def month_of(yyyymmdd):
    return f"{yyyymmdd[:4]}-{yyyymmdd[4:6]}"


def main():
    flags = defaultdict(int)
    if os.path.exists(f"{H}/quality_flags.csv"):
        for r in rows(f"{H}/quality_flags.csv"):
            flags[(r["구분"], r["종목코드"])] += 1

    index = []
    stat = defaultdict(int)

    # ── 국내 ETF ───────────────────────────────────────────
    edist = defaultdict(lambda: defaultdict(float))
    for r in rows(f"{H}/kr_etf_distributions.csv"):
        a = float(r["주당금액"] or 0)
        if a > 0:
            edist[r["종목코드"]][month_of(r["기준일"])] += a
    for r in rows("data/etf_prices.csv"):
        c, name = r["종목코드"], r["종목명"]
        krx = f"{H}/kr_etf_krx/{c}.csv"
        if os.path.exists(krx):
            px = series(krx)
            pfile, dfile = f"history/kr_etf_krx/{c}.csv", ""
            if edist.get(c):
                write(f"{OUT}/div/K/{c}.csv", edist[c])
                dfile = f"bt/div/K/{c}.csv"
            stat["ETF·KRX"] += 1
        else:
            px = series(f"{H}/kr_etf/{c}.csv")
            pfile, dfile = f"history/kr_etf/{c}.csv", ""
            stat["ETF·네이버(분배금 보정)"] += 1
        if not px:
            stat["ETF·가격없음"] += 1
            continue
        ms = sorted(px)
        index.append([f"K:{c}", name, "ETF", ms[0], ms[-1], pfile, dfile,
                      flags[("kr_etf", c)]])

    # ── 국내 주식 ──────────────────────────────────────────
    sdiv = defaultdict(list)
    for r in rows(f"{H}/kr_stock_dividends.csv"):
        a = float(r["주당금액"] or 0)
        if a > 0:
            sdiv[r["종목코드"]].append((month_of(r["기준일"]), a))
    probe = {}
    for r in rows("data/kr_stocks.csv"):
        c, name = r["종목코드"], r["종목명"]
        nv = series(f"{H}/kr_stock/{c}.csv")
        if not nv:
            stat["주식·가격없음"] += 1
            continue
        dfile = ""
        if sdiv.get(c):
            kx = series(f"{H}/kr_stock_krx/{c}.csv")
            common = sorted(m for m in kx if m in nv and nv[m] > 0)
            if common:
                fac = {m: kx[m] / nv[m] for m in common}
                first = fac[common[0]]
                out = defaultdict(float)
                for m, a in sdiv[c]:
                    if m < common[0]:
                        f = first
                    elif m in fac:
                        f = fac[m]
                    else:                       # 거래정지 등으로 그 달이 비면 직전 배수
                        prev = [x for x in common if x <= m]
                        f = fac[prev[-1]] if prev else first
                    out[m] += a / f
                write(f"{OUT}/div/K/{c}.csv", out)
                dfile = f"bt/div/K/{c}.csv"
                if c == "005930":
                    probe = {m: round(fac[m], 2) for m in ("2010-12", "2017-12", "2018-06") if m in fac}
            else:
                stat["주식·배당 단위 맞출 KRX 없음"] += 1
        stat["주식"] += 1
        ms = sorted(nv)
        index.append([f"K:{c}", name, "주식", ms[0], ms[-1], f"history/kr_stock/{c}.csv", dfile,
                      flags[("kr_stock", c)]])

    # ── 미국 ───────────────────────────────────────────────
    udiv = defaultdict(lambda: defaultdict(float))
    for r in rows(f"{H}/us_dividends.csv"):
        udiv[r["티커"]][r["월"]] += float(r["주당배당USD"])
    npatch = 0
    if os.path.exists(f"{H}/us_dividends_patch.csv"):
        for r in rows(f"{H}/us_dividends_patch.csv"):
            v = float(r["주당배당USD"])
            if r["조치"] == "교체":
                udiv[r["티커"]][r["월"]] = v
            else:
                udiv[r["티커"]][r["월"]] += v
            npatch += 1
    sym = json.load(open("data/us_symbols.json", encoding="utf-8"))
    for r in rows("data/us_prices.csv"):
        t = r["티커"]
        safe = t.replace("/", "_")
        px = series(f"{H}/us/{safe}.csv")
        if not px:
            stat["미국·가격없음"] += 1
            continue
        dfile = ""
        if udiv.get(t):
            write(f"{OUT}/div/U/{safe}.csv", udiv[t])
            dfile = f"bt/div/U/{safe}.csv"
        kind = "ETF" if (sym.get(t) or {}).get("etf") or r.get("구분") == "ETF" else "주식"
        name = r["종목명"] or t
        ms = sorted(px)
        index.append([f"U:{t}", name, kind, ms[0], ms[-1], f"history/us/{safe}.csv", dfile,
                      flags[("us", t)]])
        stat["미국"] += 1

    os.makedirs(OUT, exist_ok=True)
    # 모바일에서 가볍게: 경로는 규칙으로 만들 수 있으니 빼고, 월은 YYYYMM 정수로
    #   가격 경로  K+ETF  → history/kr_etf_krx/{코드}.csv (네이버 대체분은 'n' 표시)
    #              K+주식 → history/kr_stock/{코드}.csv
    #              U      → history/us/{티커}.csv
    #   배당 경로  bt/div/{K|U}/{코드}.csv  (배당 1 일 때만)
    compact = []
    for k, name, kind, s0, s1, pf, df, q in index:
        src = "n" if "/kr_etf/" in pf else ""
        compact.append([k, name, "E" if kind == "ETF" else "S", int(s0.replace("-", "")),
                        int(s1.replace("-", "")), 1 if df else 0, q] + ([src] if src else []))
    payload = {
        "updated": datetime.now(KST).strftime("%Y-%m-%d"),
        "fx": "history/usdkrw_month.csv",
        "cols": ["키", "이름", "구분(E=ETF,S=주식)", "시작월", "마지막월", "배당파일", "품질경고", "출처(n=네이버)"],
        "rows": compact,
    }
    with open(f"{OUT}/index.json", "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))

    print(f"완료: {len(index)}종목 · {dict(stat)} · 미국 배당 보정 {npatch}건", file=sys.stderr)
    print(f"  삼성전자 보정 배수 {probe}", file=sys.stderr)
    print(f"  index.json {os.path.getsize(f'{OUT}/index.json')/1e6:.2f}MB", file=sys.stderr)


if __name__ == "__main__":
    main()
