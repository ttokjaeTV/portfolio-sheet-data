#!/usr/bin/env python3
"""
세이브로 국내 분배금·배당 '전체' 이력 → data/history/

  etf    국내 ETF 분배금   → data/history/kr_etf_distributions.csv
  stock  국내 주식 배당     → data/history/kr_stock_dividends.csv
         열: 종목코드,기준일,지급일,주당금액,구분,종목명

build_dividends.py 는 최근 760일만 받아 연배당을 계산한다. 이 스크립트는 백테스트용으로
상장 초기까지 거슬러 받는다. 요청 형식은 build_dividends.py 의 seibro()/seibro_stock() 과 같다.

★ 금액은 '당시 1주' 기준이다. 가격 이력(네이버)은 분할 보정값이므로 주식은
  data/history/kr_splits.csv 로 단위를 맞춘 뒤 써야 한다. ETF 는 분할이 드물다.
★ 세이브로는 긴 페이징 도중 연결을 끊는다(Errno 104, 2026-10 실측).
  같은 오프셋을 재시도하고, 분기 단위로 잘라 받아 캐시(.cache/seibro/)에 남긴다.
  지난 분기는 캐시가 있으면 다시 받지 않는다. 최근 2개 분기는 매번 다시 받는다.

사용: python scripts/build_dividend_history.py etf|stock
  FROM_YEAR (기본 etf 2002 / stock 1990)
"""

import csv
import json
import os
import re
import sys
import time
import urllib.request
from datetime import date, datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
URL = "https://seibro.or.kr/websquare/engine/proworks/callServletService.jsp"
ROOT = os.environ.get("HISTORY_DIR", "data/history")
CACHE = os.environ.get("CACHE_DIR", ".cache/seibro")
PAGE = 30

CFG = {
    "etf": {
        "ref": "https://seibro.or.kr/websquare/control.jsp?w2xPath=/IPORTAL/user/etf/BIP_CNTS06030V.xml&menuNo=179",
        "body": ("<reqParam action='exerInfoDtramtPayStatPlist'"
                 " task='ksd.safe.bip.cnts.etf.process.EtfExerInfoPTask'>"
                 "<MENU_NO value='179'/><CMM_BTN_ABBR_NM value='조회'/>"
                 "<W2XPATH value='/IPORTAL/user/etf/BIP_CNTS06030V.xml'/>"
                 "<etf_sort_level_cd value=''/><etf_big_sort_cd value=''/>"
                 "<etf_sort_cd value=''/><isin value=''/><mngco_custno value=''/>"
                 "<RGT_RSN_DTAIL_SORT_CD value=''/>"
                 "<START_PAGE value='{s}'/><END_PAGE value='{e}'/>"
                 "<fromRGT_STD_DT value='{frm}'/><toRGT_STD_DT value='{to}'/></reqParam>"),
        "out": "kr_etf_distributions.csv",
        "from": 2002,
    },
    "stock": {
        "ref": "https://seibro.or.kr/websquare/control.jsp?w2xPath=/IPORTAL/user/company/BIP_CNTS01041V.xml&menuNo=285",
        "body": ("<reqParam action='divStatInfoPList'"
                 " task='ksd.safe.bip.cnts.Company.process.EntrFnafInfoPTask'>"
                 "<MENU_NO value='285'/><CMM_BTN_ABBR_NM value='조회'/>"
                 "<W2XPATH value='/IPORTAL/user/company/BIP_CNTS01041V.xml'/>"
                 "<RGT_STD_DT_FROM value='{frm}'/><RGT_STD_DT_TO value='{to}'/>"
                 "<ISSUCO_CUSTNO value=''/><KOR_SECN_NM value=''/><SECN_KACD value=''/>"
                 "<RGT_RSN_DTAIL_SORT_CD value=''/><LIST_TPCD value=''/>"
                 "<START_PAGE value='{s}'/><END_PAGE value='{e}'/></reqParam>"),
        "out": "kr_stock_dividends.csv",
        "from": 1990,
    },
}


def unescape(s):
    for a, b in (("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"'), ("&#39;", "'")):
        s = s.replace(a, b)
    return s


def post(kind, frm, to, off):
    c = CFG[kind]
    xml = c["body"].format(s=off, e=off + PAGE - 1, frm=frm, to=to)
    hdr = {"User-Agent": "Mozilla/5.0", "Referer": c["ref"],
           "Content-Type": "application/xml; charset=UTF-8"}
    last = None
    for i in range(6):
        try:
            req = urllib.request.Request(URL, data=xml.encode("utf-8"), headers=hdr)
            return urllib.request.urlopen(req, timeout=40).read().decode("utf-8", "replace")
        except Exception as e:
            last = e
            time.sleep(2 * (i + 1))
    raise last


def parse(kind, body):
    out = []
    for blk in re.findall(r"<result>(.*?)</result>", body, re.S):
        d = dict(re.findall(r'<(\w+)\s+value="([^"]*)"', blk))
        if kind == "etf":
            isin = d.get("ISIN", "")
            code = isin[3:9].upper() if len(isin) >= 9 else ""
            amt = d.get("ESTM_STDPRC")          # ★ 필드명과 달리 이게 분배금
            kindnm = d.get("RGT_RSN_DTAIL_NM", "")
        else:
            code = (d.get("SHOTN_ISIN") or "").strip().upper()
            amt = d.get("CASH_ALOC_AMT")
            kindnm = d.get("RGT_RSN_DTAIL_SORT_NM", "")
        day = d.get("RGT_STD_DT", "")
        if len(code) != 6 or len(day) != 8:
            continue
        try:
            amt = float(amt or 0)
        except ValueError:
            amt = 0.0
        out.append([code, day, d.get("TH1_PAY_TERM_BEGIN_DT", ""), amt, kindnm,
                    unescape(d.get("KOR_SECN_NM", ""))])
    return out


def fetch_range(kind, frm, to):
    rows, seen, off = [], set(), 1
    while True:
        part = parse(kind, post(kind, frm, to, off))
        if not part:
            break
        fresh = 0
        for r in part:
            k = (r[0], r[1], r[4])
            if k not in seen:
                seen.add(k)
                rows.append(r)
                fresh += 1
        if not fresh or len(part) < PAGE:
            break
        off += PAGE
        time.sleep(0.25)
    return rows


def quarters(y0, today):
    for y in range(y0, today.year + 1):
        for q in range(4):
            s = date(y, q * 3 + 1, 1)
            if s > today:
                return
            e = (date(y + (q == 3), (q * 3 + 4) % 12 or 12, 1) - timedelta(days=1))
            yield s, min(e, today)


def fmt(v):
    return str(int(v)) if float(v).is_integer() else f"{v:.6f}".rstrip("0").rstrip(".")


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in CFG:
        raise SystemExit(__doc__)
    kind = sys.argv[1]
    today = datetime.now(KST).date()
    y0 = int(os.environ.get("FROM_YEAR", CFG[kind]["from"]))
    os.makedirs(f"{CACHE}/{kind}", exist_ok=True)
    recent = today - timedelta(days=200)           # 최근 2개 분기는 정정 공시를 반영하려 다시 받는다
    t0, fails = time.time(), []

    qs = list(quarters(y0, today))
    for s, e in qs:
        path = f"{CACHE}/{kind}/{s:%Y%m}.json"
        if os.path.exists(path) and e < recent:
            continue
        try:
            rows = fetch_range(kind, s.strftime("%Y%m%d"), e.strftime("%Y%m%d"))
        except Exception as ex:
            fails.append(f"{s:%Y%m}:{type(ex).__name__}")
            print(f"  {s:%Y-%m} 실패 {ex}", file=sys.stderr)
            continue
        with open(path + ".tmp", "w", encoding="utf-8") as f:
            json.dump(rows, f, ensure_ascii=False)
        os.replace(path + ".tmp", path)
        print(f"  {s:%Y-%m}~{e:%m} {len(rows)}건 ({time.time()-t0:.0f}s)", file=sys.stderr)

    # 집계 — 캐시 전체를 모은다
    allrows, seen = [], set()
    for s, _ in qs:
        path = f"{CACHE}/{kind}/{s:%Y%m}.json"
        if not os.path.exists(path):
            continue
        for r in json.load(open(path, encoding="utf-8")):
            k = (r[0], r[1], r[4])
            if k in seen:
                continue
            seen.add(k)
            allrows.append(r)
    allrows.sort(key=lambda r: (r[0], r[1]))
    os.makedirs(ROOT, exist_ok=True)
    out = f"{ROOT}/{CFG[kind]['out']}"
    with open(out + ".tmp", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["종목코드", "기준일", "지급일", "주당금액", "구분", "종목명"])
        for r in allrows:
            w.writerow([r[0], r[1], r[2], fmt(r[3]), r[4], r[5]])
    os.replace(out + ".tmp", out)
    codes = len({r[0] for r in allrows})
    first = allrows and min(r[1] for r in allrows)
    print(f"완료 {kind}: {len(allrows)}건 · {codes}종목 · 최초 {first} · "
          f"실패 분기 {len(fails)} {time.time()-t0:.0f}s", file=sys.stderr)
    if fails:
        print("  다시 실행하면 실패 분기만 이어 받는다: " + " ".join(fails), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
