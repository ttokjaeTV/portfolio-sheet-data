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
import re
import time
import sys
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta, timezone

OUT_PATH = os.environ.get("DIVIDENDS_PATH", "data/dividends.csv")
ETF_PATH = os.environ.get("OUT_PATH", "data/etf_prices.csv")
STOCK_PATH = os.environ.get("KR_STOCKS_PATH", "data/kr_stocks.csv")
HISTORY_PATH = os.environ.get("DIV_HISTORY_PATH", "data/dividend_history.csv")

# 월배당 트래커가 매월 갱신하는 분배금 이력 (분배금·과세표준액)
TRACKER = "https://ttokjaetv.github.io/montly-div/data/"
TRACKER_FILES = ["tax-base.json", "tax-base-extra.json"]

# 한국예탁결제원 세이브로 — 전 운용사 ETF 분배금 지급현황.
# 자세한 함정은 docs/과세기준가_수집.md 참고.
SEIBRO = "https://seibro.or.kr/websquare/engine/proworks/callServletService.jsp"
SEIBRO_REF = ("https://seibro.or.kr/websquare/control.jsp"
              "?w2xPath=/IPORTAL/user/etf/BIP_CNTS06030V.xml&menuNo=179")
SEIBRO_PAGE = 30          # 한 번에 30건 고정. START_PAGE 는 행 오프셋이다.

KST = timezone(timedelta(hours=9))
UA = {"User-Agent": "Mozilla/5.0"}

HEADER = ["종목코드", "구분", "주당배당금", "배당수익률", "지급주기", "연지급횟수",
          "연배당_실지급", "연배당_연환산", "과표_실지급", "지급월", "회차내역",
          "최근지급일", "출처", "갱신시각"]


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


def seibro(frm, to):
    """세이브로 분배금 지급현황 → [{code, date, amt, name, amc}, ...]

    ★ Content-Type 은 반드시 application/xml. form-urlencoded 로 보내면
      에러 코드 없이 '서버오류2' 만 돌아온다.
    ★ START_PAGE 는 페이지 번호가 아니라 행 오프셋(1, 31, 61 …)이다.
    ★ 필드명이 값과 어긋난다. ESTM_STDPRC 가 분배금, TAXSTD 가 과표기준가다."""
    hdr = {"User-Agent": UA["User-Agent"], "Referer": SEIBRO_REF,
           "Content-Type": "application/xml; charset=UTF-8"}
    out, seen, off = [], set(), 1
    while off < 6000:
        xml = ("<reqParam action='exerInfoDtramtPayStatPlist'"
               " task='ksd.safe.bip.cnts.etf.process.EtfExerInfoPTask'>"
               "<MENU_NO value='179'/><CMM_BTN_ABBR_NM value='조회'/>"
               "<W2XPATH value='/IPORTAL/user/etf/BIP_CNTS06030V.xml'/>"
               "<etf_sort_level_cd value=''/><etf_big_sort_cd value=''/>"
               "<etf_sort_cd value=''/><isin value=''/><mngco_custno value=''/>"
               "<RGT_RSN_DTAIL_SORT_CD value=''/>"
               f"<START_PAGE value='{off}'/><END_PAGE value='{off + SEIBRO_PAGE - 1}'/>"
               f"<fromRGT_STD_DT value='{frm}'/><toRGT_STD_DT value='{to}'/></reqParam>")
        try:
            req = urllib.request.Request(SEIBRO, data=xml.encode("utf-8"), headers=hdr)
            body = urllib.request.urlopen(req, timeout=40).read().decode("utf-8", "replace")
        except Exception as e:
            print(f"  세이브로 off={off}: {e}", file=sys.stderr)
            break
        rows = re.findall(r"<result>(.*?)</result>", body, re.S)
        if not rows:
            break
        fresh = 0
        for blk in rows:
            d = dict(re.findall(r'<(\w+)\s+value="([^"]*)"', blk))
            isin = d.get("ISIN", "")
            date = d.get("RGT_STD_DT", "")
            if len(isin) < 9 or len(date) != 8:
                continue
            key = (isin, date)
            if key in seen:
                continue
            seen.add(key)
            fresh += 1
            try:
                amt = float(d.get("ESTM_STDPRC") or 0)
            except ValueError:
                amt = 0.0
            out.append({"code": isin[3:9].upper(), "date": date, "amt": amt,
                        "name": html_unescape(d.get("KOR_SECN_NM", "")),
                        "amc": d.get("REP_SECN_NM", ""),
                        "kind": d.get("RGT_RSN_DTAIL_NM", ""),
                        "pay": d.get("TH1_PAY_TERM_BEGIN_DT", "")})
        if not fresh:
            break
        off += SEIBRO_PAGE
        time.sleep(0.25)
    return out


def html_unescape(s):
    for a, b in (("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"')):
        s = s.replace(a, b)
    return s


def tracker_tax(today):
    """트래커 JSON → {코드: {YYYY-MM: 과세표준액}}.

    분배금은 세이브로가 전 종목을 주므로 여기서는 과표만 쓴다.
    세이브로 지급기준일(8/31)과 ETF CHECK 배당락일(8/28)이 1영업일 어긋나므로
    날짜가 아니라 **월 단위**로 맞춘다. 한 달에 두 번 분배한 경우는 합산한다."""
    cutoff = (today - timedelta(days=400)).strftime("%Y-%m-%d")
    out, got = {}, 0
    for fn in TRACKER_FILES:
        try:
            doc = fetch_json(TRACKER + fn)
        except Exception as e:
            print(f"  트래커 {fn} 실패: {e}", file=sys.stderr)
            continue
        got += 1
        for code, recs in (doc.get("data") or {}).items():
            by = out.setdefault(code.upper(), {})
            for r in recs or []:
                if not r or len(r) < 3:
                    continue
                d = norm_date(r[0])
                if not d or d < cutoff:
                    continue
                try:
                    tax = float(r[2] or 0)
                except (TypeError, ValueError):
                    continue
                by[d[:7]] = by.get(d[:7], 0.0) + max(0.0, tax)
    if not got:
        print("  트래커 JSON 을 한 건도 못 받았습니다.", file=sys.stderr)
    return out


def seibro_stock(frm, to):
    """세이브로 주식 배당내역 → [{code, date, pay, amt, kind, name}, ...]

    ETF 와 같은 게이트웨이인데 task/action 이 다르다.
      주식 : divStatInfoPList / ksd.safe.bip.cnts.Company.process.EntrFnafInfoPTask
      ETF  : exerInfoDtramtPayStatPlist / ...etf.process.EtfExerInfoPTask

    KRX 의 DPS 보다 이쪽이 낫다. KRX 는 연간 누계만 주는데
    여기는 **회차별**이라 분기배당사도 구분되고 실지급일까지 나온다."""
    hdr = {"User-Agent": UA["User-Agent"],
           "Referer": ("https://seibro.or.kr/websquare/control.jsp"
                       "?w2xPath=/IPORTAL/user/company/BIP_CNTS01041V.xml&menuNo=285"),
           "Content-Type": "application/xml; charset=UTF-8"}
    out, seen, off = [], set(), 1
    while off < 20000:
        xml = ("<reqParam action='divStatInfoPList'"
               " task='ksd.safe.bip.cnts.Company.process.EntrFnafInfoPTask'>"
               "<MENU_NO value='285'/><CMM_BTN_ABBR_NM value='조회'/>"
               "<W2XPATH value='/IPORTAL/user/company/BIP_CNTS01041V.xml'/>"
               f"<RGT_STD_DT_FROM value='{frm}'/><RGT_STD_DT_TO value='{to}'/>"
               "<ISSUCO_CUSTNO value=''/><KOR_SECN_NM value=''/><SECN_KACD value=''/>"
               "<RGT_RSN_DTAIL_SORT_CD value=''/><LIST_TPCD value=''/>"
               f"<START_PAGE value='{off}'/><END_PAGE value='{off + SEIBRO_PAGE - 1}'/></reqParam>")
        try:
            req = urllib.request.Request(SEIBRO, data=xml.encode("utf-8"), headers=hdr)
            body = urllib.request.urlopen(req, timeout=40).read().decode("utf-8", "replace")
        except Exception as e:
            print(f"  세이브로(주식) off={off}: {e}", file=sys.stderr)
            break
        rows = re.findall(r"<result>(.*?)</result>", body, re.S)
        if not rows:
            break
        fresh = 0
        for blk in rows:
            d = dict(re.findall(r'<(\w+)\s+value="([^"]*)"', blk))
            code = (d.get("SHOTN_ISIN") or "").strip().upper()
            date = d.get("RGT_STD_DT", "")
            if len(code) != 6 or len(date) != 8:
                continue
            key = (code, date)
            if key in seen:
                continue
            seen.add(key)
            fresh += 1
            try:
                amt = float(d.get("CASH_ALOC_AMT") or 0)
            except ValueError:
                amt = 0.0
            out.append({"code": code, "date": date, "amt": amt,
                        "pay": d.get("TH1_PAY_TERM_BEGIN_DT", ""),
                        "kind": d.get("RGT_RSN_DTAIL_SORT_NM", ""),
                        "name": html_unescape(d.get("KOR_SECN_NM", "")),
                        "amc": d.get("LIST_TPNM", "")})
        if not fresh:
            break
        off += SEIBRO_PAGE
        time.sleep(0.2)
    return out


def build_stocks_seibro(today, krx):
    """세이브로 회차별 배당 → {코드: 행}. 배당수익률은 KRX 값이 있으면 쓴다."""
    frm = (today - timedelta(days=372)).strftime("%Y%m%d")
    recs = seibro_stock(frm, today.strftime("%Y%m%d"))
    print(f"  세이브로(주식) {len(recs)}건 · 종목 {len({r['code'] for r in recs})}개",
          file=sys.stderr)
    if not recs:
        return {}, False

    cut = (today - timedelta(days=365)).strftime("%Y%m%d")
    by = {}
    for r in recs:
        # 무배당·주식배당·현물배당은 현금이 0 이라 여기서 자연히 빠진다.
        # 반대로 '동시배당'(현금+주식 동시)은 현금 부분이 있으니 세야 한다.
        if r["date"] < cut or r["amt"] <= 0:
            continue
        by.setdefault(r["code"], []).append(r)

    out = {}
    for code, rows in by.items():
        rows.sort(key=lambda x: x["date"])
        amts = [x["amt"] for x in rows]
        n = len(amts)
        real = round(sum(amts), 4)                      # 실제로 받은 돈 전부
        annual = round(amts[-1] * n, 4) if even_enough(amts) else real
        last = rows[-1]
        months = sorted({int((x["pay"] or x["date"])[4:6]) for x in rows})
        k = krx.get(code)
        div = k[3] if k else ""
        out[code] = [code, "국내주식", last["amt"], div,
                     cycle_name(n), n, real, annual, "",
                     "|".join(str(m) for m in months),
                     detail_str(rows, lambda r: ymd(r["pay"] or r["date"])),
                     ymd(last["pay"] or last["date"]),
                     "seibro" + ("+KRX" if k else ""), ""]
    return out, bool(out)


def ymd(d):
    """'20260325' → '2026-03-25'. 빈 값이면 빈 문자열."""
    return f"{d[:4]}-{d[4:6]}-{d[6:8]}" if d and len(d) >= 8 else ""


def detail_str(rows, date_of):
    """회차 내역을 'YYYY-MM-DD:금액' 으로 잇는다.

    ★ 월(MM)만 적으면 연도를 알 수 없다. 최근 12개월 창에는 같은 달이 두 번
      들어올 수 있고(작년 9월 + 올해 9월), 그러면 화면에서 두 회차가 한 달로
      합쳐진다. 또 '가장 최근 회차' 를 집어 남은 달을 채우는 계산도 못 한다.
      미국(us_dividends.csv)과 같은 형식으로 맞춘다.

    특별배당인지 결산배당인지는 못 가린다(세이브로·DART 어디에도 구분이 없다).
    대신 회차를 그대로 보여줘서 보는 사람이 판단하게 한다."""
    return ";".join(f"{date_of(r)}:{r['amt']:g}" for r in rows)


def even_enough(amts):
    """회차 금액이 고른가? 연환산(최근 1회 × 횟수)을 쓸 수 있는지 판단한다.

    '최근 1회 × 횟수' 는 균등 지급일 때만 맞다. 불균등하면 엉터리가 된다.
      현대엘리베이터 1,000×4 + 결산 12,010 → 12,010×5 = 60,050원
      TIGER 배당성장 소액 여러 번 + 연 1회 큰 결산분배 → 같은 식으로 튄다

    특별배당인지 결산배당인지는 구분할 방법이 없다. 세이브로가 배당구분을
    '현금배당' 으로만 주기 때문이다. 그래서 '무엇인지' 를 맞히려 들지 않고
    '연환산을 믿을 수 있는가' 만 본다. 최대가 최소의 3배를 넘으면 못 믿는다."""
    a = [x for x in amts if x > 0]
    if len(a) < 2:
        return True
    return max(a) <= min(a) * 3


def append_history(recs):
    """받아온 회차를 이력 파일에 덧붙인다. 지우지 않는다.

    dividends.csv 는 '최근 1년' 롤링이라 오래된 회차가 밀려 사라진다.
    분배금 성장 추이 같은 걸 나중에 보려면 원본이 남아 있어야 한다.
    세이브로가 과거를 계속 갖고 있긴 하지만(KODEX 200 은 2003년부터),
    전 종목 10년치를 다시 긁으면 한 시간이 걸린다. 매주 몇 건씩 쌓아두는 편이 싸다.

    (종목코드, 지급기준일) 로 중복을 거른다."""
    old = {}
    if os.path.isfile(HISTORY_PATH):
        with open(HISTORY_PATH, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                key = (r.get("종목코드", ""), r.get("지급기준일", ""))
                if key[0] and key[1]:
                    old[key] = r

    before = len(old)
    for x in recs:
        key = (x["code"], x["date"])
        if key in old:
            continue
        old[key] = {"종목코드": x["code"], "지급기준일": x["date"],
                    "실지급일": x.get("pay", ""), "주당분배금": x["amt"],
                    "배당구분": x.get("kind", ""), "종목명": x.get("name", ""),
                    "운용사": x.get("amc", "")}

    cols = ["종목코드", "지급기준일", "실지급일", "주당분배금", "배당구분", "종목명", "운용사"]
    os.makedirs(os.path.dirname(HISTORY_PATH) or ".", exist_ok=True)
    with open(HISTORY_PATH, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for key in sorted(old, key=lambda k: (k[0], k[1])):
            w.writerow({c: old[key].get(c, "") for c in cols})
    print(f"  이력 누적: {len(old)}건 (신규 {len(old) - before}건) → {HISTORY_PATH}",
          file=sys.stderr)


def build_etf(today):
    """세이브로 분배금(전 종목) + 트래커 과표(189종) → {코드: 행}."""
    frm = (today - timedelta(days=372)).strftime("%Y%m%d")
    to = today.strftime("%Y%m%d")
    recs = seibro(frm, to)
    print(f"  세이브로 {len(recs)}건 · 종목 {len({r['code'] for r in recs})}개", file=sys.stderr)
    append_history(recs)
    taxmap = tracker_tax(today)
    print(f"  트래커 과표 {len(taxmap)}종", file=sys.stderr)

    # ★ 상장폐지 종목의 '청산분배' 를 반드시 뺀다.
    #   전액 상환이라 한 회차가 주가만큼 크다. 정상 분배와 섞으면 연배당이 수백 배로 뛴다.
    #   (예: TIME 미국배당다우존스액티브 0036D0 — 월 52원짜리가 청산 12,998원으로 잡혔다)
    cut = (today - timedelta(days=365)).strftime("%Y%m%d")
    by, dropped = {}, 0
    for r in recs:
        if r["date"] < cut or r["amt"] <= 0:
            continue
        if r.get("kind") and r["kind"] != "이익분배":
            dropped += 1
            continue
        by.setdefault(r["code"], []).append(r)
    if dropped:
        print(f"  이익분배가 아닌 회차 {dropped}건 제외 (청산분배 등)", file=sys.stderr)

    out = {}
    for code, rows in by.items():
        rows.sort(key=lambda x: x["date"])
        amts = [x["amt"] for x in rows]
        n = len(amts)
        real = round(sum(amts), 4)
        # 회차가 고르지 않으면 연환산은 못 믿는다. 실지급을 그대로 쓴다.
        annual = round(amts[-1] * n, 4) if even_enough(amts) else real
        last = rows[-1]
        # ★ 국내 ETF 는 배당락일(기준일)이 월말이고 실제 지급은 며칠 뒤 =
        #   대개 '다음 달' 이다. 배당락일로 월을 세면 달력이 한 달씩 앞당겨진다.
        #   (2026-09 실측: 977종 중 827종의 기준일이 30·31일)
        #   세이브로 TH1_PAY_TERM_BEGIN_DT 가 실지급일이므로 그것을 쓴다.
        months = sorted({int((x["pay"] or x["date"])[4:6]) for x in rows})
        # 과표는 아는 종목만. 모르면 빈칸으로 두고 화면에서 '미확인'으로 표시한다.
        # ★ 여기만 배당락일(date)로 찾는다. etf_tax_base 의 키가 배당락일이기 때문이다.
        #   지급일로 바꾸면 월이 어긋나 과표를 통째로 못 찾는다.
        tm = taxmap.get(code)
        if tm:
            tax = round(sum(tm.get(x["date"][:4] + "-" + x["date"][4:6], 0.0) for x in rows), 4)
            src = "seibro+etfcheck"
        else:
            tax, src = "", "seibro"
        out[code] = [code, "국내ETF", "", "", cycle_name(n), n,
                     real, annual, tax,
                     "|".join(str(m) for m in months),
                     detail_str(rows, lambda r: ymd(r["pay"] or r["date"])),
                     ymd(last["pay"] or last["date"]), src, ""]
    return out


# ---------------------------------------------------------------- 개별주
def build_stocks(today):
    """pykrx 시장 펀더멘털에서 DPS·DIV 를 뽑는다. 시장당 1회 호출."""
    # data.krx.co.kr 은 로그인해야 통계가 열린다. 자격증명이 없으면 빈 응답만 온다.
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import krx_auth
        krx_auth.ensure()
    except Exception as e:
        print(f"  krx_auth 로드 실패: {type(e).__name__}", file=sys.stderr)

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
                                          dps, dps, "", "", "", "", "KRX", ""]
        if got_any:
            ok = True
            print(f"  KRX 기준일 {d} · 배당 있는 종목 {len(out)}건", file=sys.stderr)
            break
    if not ok:
        print("  KRX 접속 실패 — 네이버로 넘어갑니다.", file=sys.stderr)
    return out, ok


DVR_RE = re.compile(r'id="_dvr"[^>]*>\s*([\d.,]+)')


def build_stocks_naver(today):
    """KRX 가 막혔을 때의 대안. 네이버 종목 페이지에서 배당수익률을 읽는다.

    ★ 네이버 종목 페이지는 이제 UTF-8 이다. EUC-KR 로 디코딩하면 전부 깨진다.
      (예전 문서에 EUC-KR 이라고 적혀 있는데 낡은 정보다.)

    한계
      - 종목당 1회 요청이라 2,700종에 15분쯤 걸린다. 주 1회면 감당된다.
      - 주당배당금을 직접 주지 않아 `현재가 × 배당수익률` 로 역산한다.
        시가배당률은 배당기준일 주가 기준이라 오차가 있다. 참고값으로만 쓴다.
    """
    if not os.path.isfile(STOCK_PATH):
        print(f"  {STOCK_PATH} 가 없습니다. build_kr_stocks.py 를 먼저 돌리세요.",
              file=sys.stderr)
        return {}, False

    rows = []
    with open(STOCK_PATH, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            code = (r.get("종목코드") or "").strip().upper()
            if code:
                rows.append((code, num(r.get("현재가"))))
    limit = int(os.environ.get("STOCK_LIMIT") or 0)
    if limit:
        rows = rows[:limit]

    out, got, fail = {}, 0, 0
    for i, (code, price) in enumerate(rows):
        url = f"https://finance.naver.com/item/main.naver?code={code}"
        try:
            req = urllib.request.Request(url, headers=UA)
            html = urllib.request.urlopen(req, timeout=12).read().decode("utf-8", "replace")
        except Exception:
            fail += 1
            continue
        m = DVR_RE.search(html)
        if not m:
            continue
        try:
            div = float(m.group(1).replace(",", ""))
        except ValueError:
            continue
        if div <= 0:
            continue
        dps = round(price * div / 100, 2) if price > 0 else 0
        got += 1
        out[code] = [code, "국내주식", dps, div, "연", 1, dps, dps, "", "", "", "", "네이버", ""]
        if i % 200 == 0 and i:
            print(f"    {i}/{len(rows)} · 배당 있는 종목 {got}건", file=sys.stderr)
        time.sleep(0.25)

    print(f"  네이버: {got}종 확보 (요청 {len(rows)}건, 실패 {fail}건)", file=sys.stderr)
    return out, got > 0


def num(v):
    try:
        return float(str(v).replace(",", "").strip() or 0)
    except ValueError:
        return 0.0


def main():
    today = datetime.now(KST).date()
    stamp = now_kst()

    print("[1/2] 국내 ETF 분배금 (세이브로 전 종목 + 트래커 과표)", file=sys.stderr)
    etf = build_etf(today)
    print(f"  ETF {len(etf)}종", file=sys.stderr)

    print("[2/2] 국내 개별주 배당", file=sys.stderr)
    # KRX 는 배당수익률(DIV)을 주고, 세이브로는 회차별 금액·지급월을 준다.
    # 둘을 합치는 게 가장 정확하다. 세이브로가 실패하면 KRX 만으로,
    # 그것도 안 되면 네이버로 내려간다.
    krx, krx_ok = build_stocks(today)
    stk, ok = build_stocks_seibro(today, krx)
    if not ok:
        stk, ok = krx, krx_ok
    if not ok:
        stk, ok = build_stocks_naver(today)
    print(f"  개별주 {len(stk)}종", file=sys.stderr)
    krx_ok = ok

    # ETF 코드가 개별주 쪽에 섞여 들어오면 ETF 를 우선한다
    merged = {}
    merged.update(stk)
    merged.update(etf)
    for r in merged.values():
        r[-1] = stamp

    # ── 쓰기 전에 검사한다 ──
    # 예전에는 파일을 먼저 쓰고 검사했다. 그래서 반쪽 결과가 디스크에 남았다.
    print(f"수집: {len(merged)}행 (KRX 접속 {'OK' if krx_ok else '실패'})", file=sys.stderr)

    if not merged:
        print("한 건도 못 모았습니다. 배포 중단.", file=sys.stderr)
        sys.exit(1)
    if not krx_ok:
        print("개별주 배당이 비었습니다. 배포 중단.", file=sys.stderr)
        sys.exit(1)

    # ★ 세이브로가 통째로 실패해도 pykrx 만으로 CSV 가 만들어진다.
    #   그러면 ETF 977종이 사라지고 지급월·회차내역이 전부 빈 반쪽 파일이 배포된다.
    #   (2026-09-04 실제 사고: 2,485행 → 1,333행, 출처가 전부 KRX)
    #   세이브로 몫이 비면 무조건 멈춘다.
    if not etf:
        print("세이브로 ETF 분배금이 0건입니다. 조회 실패로 보고 배포를 중단합니다.",
              file=sys.stderr)
        sys.exit(1)
    with_month = sum(1 for r in merged.values() if r[9])
    if with_month < len(merged) * 0.5:
        print(f"지급월이 있는 행이 {with_month}/{len(merged)} 뿐입니다. "
              f"세이브로 조회 실패로 보고 배포를 중단합니다.", file=sys.stderr)
        sys.exit(1)

    # 기존 파일보다 크게 줄면 멈춘다. 소스 한쪽이 조용히 빠지는 걸 잡는다.
    if os.path.exists(OUT_PATH):
        try:
            with open(OUT_PATH, encoding="utf-8") as f:
                before = sum(1 for _ in csv.reader(f)) - 1
        except OSError:
            before = 0
        if before > 0 and len(merged) < before * 0.9:
            print(f"{before}행 → {len(merged)}행 으로 줄었습니다. 배포 중단.",
                  file=sys.stderr)
            sys.exit(1)

    os.makedirs(os.path.dirname(OUT_PATH) or ".", exist_ok=True)
    with open(OUT_PATH, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(HEADER)
        for code in sorted(merged):
            w.writerow(merged[code])

    print(f"완료: {OUT_PATH} ({len(merged)}행)", file=sys.stderr)


if __name__ == "__main__":
    main()
