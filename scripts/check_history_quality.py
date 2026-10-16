#!/usr/bin/env python3
"""
data/history/ 품질 점검 → data/history/quality_flags.csv + 요약 출력

계산기가 이 표를 보고 의심 구간을 경고하거나 잘라낼 수 있게 하려는 것이다.
값을 고치지는 않는다(원자료는 원자료로 둔다).

  유형
    jump        월간 종가가 5배 이상 오르거나 80% 이상 빠짐
    flicker     튀었다가 다음 달 되돌아옴 — 옛 데이터 오류일 가능성이 높다
    split_raw   분할·병합 달에 가격이 분할 비율만큼 움직임 — 네이버 보정 누락 의심 (국내)
    gap         중간에 빠진 달이 5% 이상
    dist_big    ETF 분배금이 그 달 종가의 20% 이상 — 단위 불일치·분할 의심

출력 열: 구분,종목코드,월,유형,값
"""

import csv
import glob
import math
import os
from collections import Counter

from winsafe import from_filename

ROOT = os.environ.get("HISTORY_DIR", "data/history")


def load(p):
    with open(p, encoding="utf-8") as f:
        r = csv.reader(f)
        next(r, None)
        return [(row[0], float(row[1])) for row in r if len(row) >= 2 and row[1]]


def mnum(m):
    return int(m[:4]) * 12 + int(m[5:7])


def scan_prices(kind, flags):
    series = {}
    for p in glob.glob(f"{ROOT}/{kind}/*.csv"):
        code = from_filename(os.path.basename(p)[:-4])
        s = load(p)
        series[code] = dict(s)
        if not s:
            continue
        exp = mnum(s[-1][0]) - mnum(s[0][0]) + 1
        if len(s) < exp * 0.95:
            flags.append((kind, code, s[0][0], "gap", f"{len(s)}/{exp}"))
        for i in range(1, len(s)):
            r = s[i][1] / s[i - 1][1]
            if r > 5 or r < 0.2:
                kind_ = "jump"
                if i + 1 < len(s):
                    r2 = s[i + 1][1] / s[i][1]
                    if (r > 5 and r2 < 0.3) or (r < 0.2 and r2 > 3.3):
                        kind_ = "flicker"
                flags.append((kind, code, s[i][0], kind_, f"{r:.3f}"))
    return series


def scan_kr_splits(series, flags):
    path = f"{ROOT}/kr_splits.csv"
    if not os.path.exists(path):
        return
    for r in csv.DictReader(open(path, encoding="utf-8")):
        try:
            a, b = map(float, r["비율"].split(":"))
            f = a / b
        except (ValueError, ZeroDivisionError):
            continue
        if 0.6 < f < 1.67:
            continue
        s = series.get(r["종목코드"])
        if not s or r["월"] not in s:
            continue
        ms = sorted(s)
        i = ms.index(r["월"])
        if i == 0:
            continue
        lr = math.log(s[ms[i]] / s[ms[i - 1]])
        if abs(lr - math.log(1 / f)) < abs(lr):
            flags.append(("kr_stock", r["종목코드"], r["월"], "split_raw",
                          f"{r['비율']} 가격{math.exp(lr):.3f}배"))


def scan_etf_dist(series, flags):
    path = f"{ROOT}/kr_etf_distributions.csv"
    if not os.path.exists(path):
        return
    for r in csv.DictReader(open(path, encoding="utf-8")):
        s = series.get(r["종목코드"])
        m = f"{r['기준일'][:4]}-{r['기준일'][4:6]}"
        if not s or m not in s:
            continue
        amt = float(r["주당금액"] or 0)
        if amt > 0 and amt / s[m] > 0.2:
            flags.append(("kr_etf", r["종목코드"], m, "dist_big", f"{amt:g}/{s[m]:g}"))


def etf_basis(etf):
    """네이버 ETF 가격이 분배금 보정값인지 종목별로 판정 → kr_etf_basis.csv

    KRX 실제 종가(kr_etf_krx/)와 겹치는 달을 비교한다. 네이버가 분배금을 소급 보정하면
    과거로 갈수록 네이버가 KRX 보다 낮다. 분배금이 없던 종목은 둘이 같아 '구분없음'.
    (야후 close 는 일부 달에 보정값이 섞여 기준으로 쓰지 않는다 — 2026-10 실측 17% 불일치)"""
    out = []
    for code, s in sorted(etf.items()):
        p = f"{ROOT}/kr_etf_krx/{code}.csv"
        if not os.path.exists(p):
            out.append((code, "KRX없음", ""))
            continue
        diffs = []
        for row in list(csv.reader(open(p, encoding="utf-8")))[1:]:
            if row[0] in s and row[2]:
                diffs.append(s[row[0]] / float(row[2]) - 1)
        if len(diffs) < 3:
            out.append((code, "판정불가", ""))
            continue
        worst = min(diffs)                      # 가장 크게 낮은 달
        tag = "분배금보정" if worst < -0.003 else "구분없음"
        out.append((code, tag, f"{worst:.4f}"))
    with open(f"{ROOT}/kr_etf_basis.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["종목코드", "네이버가격", "KRX대비_최대차이"])
        w.writerows(out)
    return Counter(t for _, t, _ in out)


def main():
    flags = []
    etf = scan_prices("kr_etf", flags)
    print("네이버 ETF 가격 성격:", dict(etf_basis(etf)))
    stock = scan_prices("kr_stock", flags)
    scan_prices("us", flags)
    scan_kr_splits(stock, flags)
    scan_etf_dist(etf, flags)
    flags.sort()
    with open(f"{ROOT}/quality_flags.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["구분", "종목코드", "월", "유형", "값"])
        w.writerows(flags)
    c = Counter((k, t) for k, _, _, t, _ in flags)
    codes = Counter((k, t) for k, t, _ in {(k, t, cd) for k, cd, _, t, _ in flags})
    print("점검 결과 (건수 / 종목수)")
    for key in sorted(c):
        print(f"  {key[0]:<9} {key[1]:<10} {c[key]:>5}건 / {codes[key]:>4}종목")


if __name__ == "__main__":
    main()
