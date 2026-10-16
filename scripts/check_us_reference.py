#!/usr/bin/env python3
"""
미국 주요 종목 야후 이력 교차 검증 → data/history/us_verify.csv + us_verify_detail.csv

야후는 공식 자료가 아니다. 무료로 전 종목·전 기간을 주는 공식 출처가 없어서
회원들이 많이 볼 주요 종목만 독립된 다른 출처와 맞춰 본다.

  가격   야후 월말 종가  vs  네이버 해외 월봉(로이터 계열, 2010-07~)        — 독립 데이터 업체끼리 대조
  배당   야후 배당      vs  나스닥 거래소 공식 배당 이력 (나스닥 상장 종목만)  — 공식 출처
         야후 배당      vs  stockanalysis (data/us_dividends.csv, 최근 2년)  — 독립 데이터 업체

판정
  가격: 겹치는 달 중 차이 0.5% 이상인 달의 비율. 이번 달(장중)은 뺀다.
  배당: 같은 배당락 월끼리 금액 비교, 차이 1% 이상 또는 한쪽에만 있는 회차를 센다.
        나스닥 금액은 분할 전 원래 금액이라 us_splits.csv 로 보정해 맞춘다.
        stockanalysis 는 지급일 기준이라 야후 배당락 월이 지급 월 -2 ~ 0 안에 있으면 같은 회차로 본다.

사용: python scripts/check_us_reference.py
"""

import csv
import json
import os
import sys
import time
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
ROOT = os.environ.get("HISTORY_DIR", "data/history")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
NAVER = ("https://api.stock.naver.com/chart/foreign/item/{}/month"
         "?startDateTime=199001010000&endDateTime={}0000")
NASDAQ = "https://api.nasdaq.com/api/quote/{}/dividends?assetclass={}"

# 회원들이 많이 볼 종목 — 지수·배당·커버드콜·채권·리츠·원자재·레버리지 ETF + 대표 개별주
TICKERS = {
    "지수": "SPY VOO IVV SPLG VTI QQQ QQQM DIA IWM RSP VT VEA VWO",
    "성장·섹터": "SCHG VUG VGT XLK SMH SOXX XLE XLF XLV",
    "배당": "SCHD VIG VYM DGRO HDV DVY SDY NOBL SPYD DGRW FDVV VIGI",
    "커버드콜": "JEPI JEPQ QYLD XYLD RYLD DIVO SPYI QQQI GPIX GPIQ",
    "채권": "TLT IEF SHY BND AGG BIL SGOV LQD HYG TLTW TMF",
    "리츠·원자재": "VNQ SCHH GLD IAU SLV",
    "레버리지": "TQQQ SOXL UPRO",
    "개별주": "AAPL MSFT NVDA AMZN GOOGL META TSLA AVGO BRK.B KO PEP JNJ PG O MO ABBV T VZ",
}


def get_json(url, headers, timeout=30):
    last = None
    for i in range(3):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:
            last = e
            time.sleep(2 * (i + 1))
    raise last


def load_csv(path):
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def mnum(m):
    return int(m[:4]) * 12 + int(m[5:7])


def ratio_of(s):
    a, b = s.split(":")
    return float(a) / float(b)


def main():
    sym = json.load(open("data/us_symbols.json", encoding="utf-8"))
    splits = defaultdict(list)
    for r in load_csv(f"{ROOT}/us_splits.csv"):
        splits[r["티커"]].append((r["월"], ratio_of(r["비율"])))
    ydiv = defaultdict(lambda: defaultdict(float))
    for r in load_csv(f"{ROOT}/us_dividends.csv"):
        ydiv[r["티커"]][r["월"]] += float(r["주당배당USD"])
    sa = {r["티커"]: r for r in load_csv("data/us_dividends.csv")}
    cur = datetime.now(KST).strftime("%Y-%m")
    end = (datetime.now(KST) + timedelta(days=1)).strftime("%Y%m%d")

    summary, detail = [], []
    for group, names in TICKERS.items():
        for t in names.split():
            row = {"분류": group, "티커": t, "거래소": (sym.get(t) or {}).get("ex", "")}
            ypath = f"{ROOT}/us/{t}.csv"
            if not os.path.exists(ypath):
                row["판정"] = "야후 이력 없음"
                summary.append(row)
                continue
            yp = {r["월"]: float(r["종가"]) for r in load_csv(ypath)}
            row["야후시작"] = min(yp)

            # ── 가격: 야후 vs 네이버 ─────────────────────────
            rc = (sym.get(t) or {}).get("rc")
            n_ok = n_bad = 0
            if rc:
                try:
                    data = get_json(NAVER.format(rc, end), {"User-Agent": UA, "Referer": "https://m.stock.naver.com/"})
                    infos = data.get("priceInfos") if isinstance(data, dict) else data
                    for d in infos or []:
                        m = f"{d['localDate'][:4]}-{d['localDate'][4:6]}"
                        if m == cur or m not in yp or not d.get("closePrice"):
                            continue
                        diff = yp[m] / float(d["closePrice"]) - 1
                        if abs(diff) < 0.005:
                            n_ok += 1
                        else:
                            n_bad += 1
                            detail.append([t, "가격", m, f"{yp[m]:g}", f"{float(d['closePrice']):g}", f"{diff*100:+.2f}%"])
                except Exception as e:
                    row["가격비고"] = f"네이버 실패 {type(e).__name__}"
                time.sleep(0.3)
            row["가격_대조월"] = n_ok + n_bad
            row["가격_불일치월"] = n_bad

            # ── 배당: 야후 vs 나스닥 공식 ─────────────────────
            # 같은 달 두 번(12월 정기+특별)은 양쪽 다 월 합계로 비교한다.
            # 나스닥 이력은 옛 회차가 군데군데 비어 있다 → '나스닥에 없음'은 오류로 보지 않는다.
            # 분할이 배당락과 같은 달이면 보정 전·후 중 맞는 쪽을 인정한다.
            yd = ydiv.get(t, {})
            if row["거래소"] == "NASDAQ":
                kind = "etf" if (sym.get(t) or {}).get("etf") else "stocks"
                try:
                    data = get_json(NASDAQ.format(t.replace(".", "/"), kind),
                                    {"User-Agent": UA, "Accept": "application/json"})
                    rows = ((data.get("data") or {}).get("dividends") or {}).get("rows") or []
                    raw = defaultdict(float)
                    for r in rows:
                        if r.get("type") != "Cash":
                            continue
                        mm, dd, yy = r["exOrEffDate"].split("/")
                        if f"{yy}-{mm}" > cur:          # 아직 배당락 전인 예고분은 뺀다
                            continue
                        raw[f"{yy}-{mm}"] += float(r["amount"].replace("$", "").replace(",", ""))
                    ok = bad = ymiss = nmiss = 0
                    for m in sorted(raw):
                        after = before = raw[m]
                        for sm, f in splits.get(t, []):
                            if sm > m:
                                after /= f
                                before /= f
                            elif sm == m:
                                after /= f          # 같은 달 분할: 보정한 값도 후보
                        a = yd.get(m, 0.0)
                        if not a:
                            ymiss += 1
                            detail.append([t, "배당(나스닥)", m, "", f"{before:.6g}", "야후에 없음"])
                        elif min(abs(a / before - 1), abs(a / after - 1)) < 0.02:
                            ok += 1
                        else:
                            bad += 1
                            detail.append([t, "배당(나스닥)", m, f"{a:.6g}", f"{before:.6g}", f"{(a/before-1)*100:+.2f}%"])
                    first = min(raw) if raw else ""
                    nmiss = sum(1 for m in yd if first and m >= first and m not in raw)
                    row["나스닥시작"] = first
                    row["배당_나스닥_대조회차"] = ok + bad
                    row["배당_나스닥_불일치"] = bad
                    row["배당_야후누락"] = ymiss
                    row["배당_나스닥누락"] = nmiss
                except Exception as e:
                    row["배당비고"] = f"나스닥 실패 {type(e).__name__}"
                time.sleep(0.4)

            # ── 배당: 야후 vs stockanalysis (최근 2년) ─────────
            # SA 는 지급일 기준. 야후 배당락 월 m 의 합계를 지급월 m~m+2 인 SA 회차 1~2개 합과 맞춘다.
            s = sa.get(t)
            if s and s.get("회차내역"):
                items = []
                for it in s["회차내역"].split(";"):
                    if ":" in it:
                        pay, amt = it.split(":")
                        items.append((pay[:7], float(amt)))
                lo = min(mnum(p) for p, _ in items) - 2
                hi = max(mnum(p) for p, _ in items)
                months = [m for m in sorted(yd) if lo <= mnum(m) <= hi]
                used, ok, bad = set(), 0, 0
                for m in months:
                    cand = [i for i, (p, _) in enumerate(items)
                            if i not in used and 0 <= mnum(p) - mnum(m) <= 2]
                    hit = None
                    for i in cand:
                        if abs(items[i][1] / yd[m] - 1) < 0.01:
                            hit = (i,)
                            break
                    if not hit:
                        for x in cand:
                            for y in cand:
                                if x < y and abs((items[x][1] + items[y][1]) / yd[m] - 1) < 0.01:
                                    hit = (x, y)
                                    break
                            if hit:
                                break
                    if hit:
                        ok += 1
                        used.update(hit)
                    elif mnum(m) >= lo + 2:          # 창 맨 앞 달은 SA 쪽이 잘렸을 수 있다
                        bad += 1
                        detail.append([t, "배당(SA)", m, f"{yd[m]:.6g}", "", "SA에 맞는 회차 없음"])
                left = [items[i] for i in range(len(items)) if i not in used]
                # SA 에만 있는 회차 (야후 누락 의심) — 마지막 달은 야후 반영 전일 수 있어 뺀다
                sa_only = [p for p, _ in left if mnum(p) < hi]
                for p, v in left:
                    if mnum(p) < hi:
                        detail.append([t, "배당(SA)", p, "", f"{v:g}", "야후에 없음"])
                row["배당_SA_대조회차"] = ok + bad
                row["배당_SA_불일치"] = bad + len(sa_only)

            # ── 판정 ────────────────────────────────────────
            issues = []
            if row["가격_대조월"] and row["가격_불일치월"] / row["가격_대조월"] > 0.02:
                issues.append("가격")
            if row.get("배당_나스닥_불일치") or row.get("배당_야후누락", 0) > 1:
                issues.append("배당(나스닥)")
            if row.get("배당_SA_불일치", 0) > 0:
                issues.append("배당(SA)")
            if not row["가격_대조월"]:
                issues.append("가격 대조 불가")
            row["판정"] = "통과" if not issues else "확인 필요: " + ", ".join(issues)
            summary.append(row)
            print(f"  {t:<6} 가격 {row['가격_불일치월']}/{row['가격_대조월']} · "
                  f"나스닥배당 {row.get('배당_나스닥_불일치','-')}/{row.get('배당_나스닥_대조회차','-')}(야후누락 {row.get('배당_야후누락','-')}) · "
                  f"SA배당 {row.get('배당_SA_불일치','-')}/{row.get('배당_SA_대조회차','-')} → {row['판정']}",
                  file=sys.stderr)

    cols = ["분류", "티커", "거래소", "야후시작", "가격_대조월", "가격_불일치월",
            "나스닥시작", "배당_나스닥_대조회차", "배당_나스닥_불일치", "배당_야후누락", "배당_나스닥누락",
            "배당_SA_대조회차", "배당_SA_불일치", "판정", "가격비고", "배당비고"]
    with open(f"{ROOT}/us_verify.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(summary)
    with open(f"{ROOT}/us_verify_detail.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["티커", "항목", "월", "야후", "대조값", "차이"])
        w.writerows(detail)
    # ── 배당 보정표 ──────────────────────────────────────
    # 야후에 빠진 회차를 나스닥 거래소 공식 이력으로 채운다(분할 보정한 금액).
    # 금액이 다른 회차는 자동으로 고르지 않는다 — 12월 두 번 지급을 나스닥이 하나만 가진 경우가 많아서다.
    # 사람이 공식 자료로 확인한 회차만 MANUAL 에 적는다.
    MANUAL = [
        # 브로드컴 2018 분기배당 $1.75(분할 전) = 0.175 (2024-07 10:1 분할 보정). 야후는 2배로 기록.
        ("AVGO", "2018-03", "0.175", "교체", "나스닥 공식 1.75 ÷ 10 (야후 0.35 이중 기록)"),
        ("AVGO", "2018-06", "0.175", "교체", "나스닥 공식 1.75 ÷ 10 (야후 0.35 이중 기록)"),
    ]
    patch = [(t, m, nv, "추가", "나스닥 공식 이력에만 있음")
             for t, kind, m, yv, nv, why in detail if kind == "배당(나스닥)" and why == "야후에 없음"]
    patch += MANUAL
    with open(f"{ROOT}/us_dividends_patch.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["티커", "월", "주당배당USD", "조치", "근거"])
        w.writerows(sorted(patch))
    print(f"  배당 보정표: {len(patch)}건 → {ROOT}/us_dividends_patch.csv", file=sys.stderr)

    passed = sum(1 for r in summary if r["판정"] == "통과")
    print(f"완료: {len(summary)}종목 중 통과 {passed} · 확인 필요 {len(summary)-passed}", file=sys.stderr)


if __name__ == "__main__":
    main()
