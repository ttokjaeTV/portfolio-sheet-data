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


def build_etf(today):
    """세이브로 분배금(전 종목) + 트래커 과표(189종) → {코드: 행}."""
    frm = (today - timedelta(days=372)).strftime("%Y%m%d")
    to = today.strftime("%Y%m%d")
    recs = seibro(frm, to)
    print(f"  세이브로 {len(recs)}건 · 종목 {len({r['code'] for r in recs})}개", file=sys.stderr)
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
        n = len(rows)
        real = round(sum(x["amt"] for x in rows), 4)
        last = rows[-1]
        annual = round(last["amt"] * n, 4)
        months = sorted({int(x["date"][4:6]) for x in rows})
        # 과표는 아는 종목만. 모르면 빈칸으로 두고 화면에서 '미확인'으로 표시한다.
        tm = taxmap.get(code)
        if tm:
            tax = round(sum(tm.get(x["date"][:4] + "-" + x["date"][4:6], 0.0) for x in rows), 4)
            src = "seibro+etfcheck"
        else:
            tax, src = "", "seibro"
        out[code] = [code, "국내ETF", "", "", cycle_name(n), n,
                     real, annual, tax,
                     "|".join(str(m) for m in months),
                     f"{last['date'][:4]}-{last['date'][4:6]}-{last['date'][6:]}", src, ""]
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
                                          dps, dps, "", "", "", "KRX", ""]
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
        out[code] = [code, "국내주식", dps, div, "연", 1, dps, dps, "", "", "", "네이버", ""]
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

    print("[1/2] 국내 ETF 분배금 (월배당 트래커)", file=sys.stderr)
    etf = build_etf(today)
    print(f"  ETF {len(etf)}종", file=sys.stderr)

    print("[2/2] 국내 개별주 배당", file=sys.stderr)
    # 1순위 KRX(시장당 1회로 끝나고 DPS 가 정확하다) → 막히면 네이버로 떨어진다
    stk, krx_ok = build_stocks(today)
    if not krx_ok:
        stk, krx_ok = build_stocks_naver(today)
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
