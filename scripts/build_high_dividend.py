#!/usr/bin/env python3
"""
고배당기업(배당소득 분리과세 대상) → data/high_dividend.json

조세특례제한법 §104의27 (2025.12.23 개정, 2026.1.1 시행, 2028.12.31까지 3년 한시).
요건을 충족한 상장사가 스스로 거래소에 '기업가치 제고계획 공시'로 신고하고,
거래소가 그 목록을 KIND 에 모아 준다. 그 목록을 그대로 받아 온다.

  요건 : 배당성향 40% 이상  또는  배당성향 25% 이상 + 직전 사업연도 배당 10% 이상 증가
  세율 : 2천만원 이하 14% / ~3억 20% / ~50억 25% / 50억 초과 30% (지방세 별도)

★ 반드시 알아야 할 네 가지 — 화면 문구를 고칠 때 이 제약을 깨지 말 것.

  1) **자율공시라 이 목록은 하한선이다.** 요건은 충족하는데 공시를 빠뜨린 기업은
     목록에 없다. "목록에 있으면 대상"은 참이지만 "없으면 비대상"은 거짓이다.
  2) **연 1회 갱신이다.** 정기주총 이익배당 결의일 다음날까지 공시하므로 12월
     결산사는 3월에 몰린다. 판정 근거는 직전 사업연도 실적이다.
  3) **ETF·펀드·리츠는 제외다.** 코스피·코스닥 상장법인 현금배당 직접투자만이다.
     그래서 이 파일은 국내 개별주에만 붙인다.
  4) **자동 적용이 아니라 신청제다.** 투자자가 종합소득세 신고 때 분리과세
     신청서를 내야 적용된다. 도구가 "분리과세다"라고 단정하면 안 된다.

출처: https://kind.krx.co.kr/valueup/dividend.do?method=valueupHighDividendMain
      금융위 2026.2.24 보도자료 '배당소득 과세특례 대상기업의 기업가치 제고계획 공시'
"""

import csv
import html
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

OUT_PATH = os.environ.get("HIGH_DIV_PATH", "data/high_dividend.json")
STOCK_PATH = os.environ.get("KR_STOCKS_PATH", "data/kr_stocks.csv")

KIND = "https://kind.krx.co.kr/valueup/dividend.do"
KIND_REF = KIND + "?method=valueupHighDividendMain"
# 상장법인목록. 고배당 목록과 같은 KIND 라서 회사명 표기가 일치한다.
# (kr_stocks.csv 의 '종목명' 은 '롯데칠성'처럼 짧아 그것만으로는 안 붙는다.)
CORP_LIST = ("https://kind.krx.co.kr/corpgeneral/corpList.do"
             "?method=download&searchType=13")
PAGE = 100                      # 페이지당 100건. 635건이면 7번이면 끝난다.
KST = timezone(timedelta(hours=9))

# 우선주는 별도 종목코드지만 같은 법인의 배당이라 분리과세 대상이다.
# KIND 목록에는 보통주 회사명만 실리므로 접미사를 떼고 다시 맞춰 본다.
PREF_SUFFIX = re.compile(r"(\d?우[BC]?)$")


def fetch_page(page):
    body = urllib.parse.urlencode({
        "method": "valueupHighDividendSub",
        "currentPageSize": PAGE,
        "pageIndex": page,
        "orderMode": 0,
        "orderStat": "D",
        "forward": "valueupHighDividend_sub",
        "marketType": "",
        "selYear": "",
        "acntclsMm": "",
    }).encode()
    req = urllib.request.Request(KIND, data=body, headers={
        "User-Agent": "Mozilla/5.0",
        "Referer": KIND_REF,
        "Content-Type": "application/x-www-form-urlencoded",
    })
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "replace")


def strip_tags(s):
    # ★ html.unescape 를 빼면 'F&F' 가 'F&amp;F' 로 남아 회사명이 안 붙는다.
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", s))).strip()


def num(v):
    try:
        return float(str(v).replace(",", "").strip())
    except ValueError:
        return None


def parse(html):
    """<tr> → [{name, year, month, payout, growth, market}] """
    out = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S):
        tds = re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)
        if len(tds) < 7 or not strip_tags(tds[0]).isdigit():
            continue
        # 시장구분은 아이콘 alt 로만 들어온다
        alt = re.search(r"alt='(유가증권|코스닥)'", tds[1])
        out.append({
            "name": strip_tags(tds[1]),
            "market": alt.group(1) if alt else "",
            "year": strip_tags(tds[3]),
            "month": strip_tags(tds[4]),
            "payout": num(strip_tags(tds[5])),      # 배당성향 %
            "growth": num(strip_tags(tds[6])),      # 이익배당금액 증가율 %
        })
    return out


def collect():
    rows, seen = [], set()
    for page in range(1, 30):
        html = fetch_page(page)
        got = parse(html)
        if not got:
            break
        new = 0
        for r in got:
            key = (r["name"], r["year"])
            if key in seen:
                continue
            seen.add(key)
            rows.append(r)
            new += 1
        print(f"  {page}페이지: {len(got)}행 (신규 {new})", file=sys.stderr)
        # 마지막 페이지는 PAGE 보다 적게 온다. 새 게 하나도 없으면 같은 페이지가
        # 되돌아온 것이므로(파라미터 무시) 무한루프를 막기 위해 끊는다.
        if len(got) < PAGE or new == 0:
            break
        time.sleep(0.3)
    return rows


def load_corp_list():
    """KIND 상장법인목록 → {회사명: 종목코드}. EUC-KR 이다."""
    req = urllib.request.Request(CORP_LIST, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        page = r.read().decode("euc-kr", "replace")
    out = {}
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", page, re.S):
        tds = [strip_tags(t) for t in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]
        # [회사명, 시장, 종목코드, ...]
        if len(tds) >= 3 and re.fullmatch(r"[0-9A-Z]{6}", tds[2] or ""):
            out.setdefault(tds[0], tds[2])
    return out


def load_kr_stocks():
    """data/kr_stocks.csv → [(종목코드, 종목명)]. 우선주 상속에 쓴다.
    종목코드는 '0184E0' 처럼 문자가 섞이므로 문자열로 둔다."""
    if not os.path.exists(STOCK_PATH):
        print(f"  {STOCK_PATH} 없음 — 우선주 상속을 건너뜁니다.", file=sys.stderr)
        return []
    out = []
    with open(STOCK_PATH, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            code = (r.get("종목코드") or "").strip()
            name = (r.get("종목명") or "").strip()
            if code and name:
                out.append((code, name))
    return out


def main():
    print("[1/2] KIND 고배당기업 현황 수집", file=sys.stderr)
    rows = collect()
    print(f"  공시 {len(rows)}건", file=sys.stderr)
    if not rows:
        print("한 건도 못 받았습니다. 기존 파일을 남겨 두고 중단합니다.", file=sys.stderr)
        sys.exit(1)

    print("[2/2] 회사명 → 종목코드 매핑", file=sys.stderr)
    by_name = {r["name"]: r for r in rows}
    corp = load_corp_list()
    print(f"  상장법인목록 {len(corp)}사", file=sys.stderr)

    def rec(r, name):
        return {"name": name, "payout": r["payout"],
                "growth": r["growth"], "year": r["year"]}

    data, done = {}, set()
    # ① 상장법인목록의 회사명으로 정확히 맞춘다 (주 경로)
    for name, r in by_name.items():
        code = corp.get(name)
        if code:
            data[code] = rec(r, name)
            done.add(name)

    # ② 남은 건 kr_stocks 의 짧은 종목명으로 한 번 더 맞춘다
    kr = load_kr_stocks()
    for code, name in kr:
        if code not in data and name in by_name:
            data[code] = rec(by_name[name], name)
            done.add(name)

    # ③ 우선주는 같은 법인의 배당이라 대상이다. 보통주에서 상속시킨다.
    #    ('삼성전자우' → '삼성전자'). 보통주가 매칭된 경우에만 붙인다.
    common = {name: code for code, name in kr if code in data}
    pref = 0
    for code, name in kr:
        if code in data:
            continue
        base = PREF_SUFFIX.sub("", name)
        if base == name:
            continue
        src = common.get(base)
        if src:
            data[code] = dict(data[src], name=name, preferred=True)
            pref += 1

    unmatched = sorted(set(by_name) - done)
    print(f"  매핑 {len(data)}종목 (우선주 상속 {pref}종) / 공시 {len(by_name)}사 "
          f"· 미매칭 {len(unmatched)}사", file=sys.stderr)
    if unmatched:
        print(f"    미매칭: {unmatched[:10]}", file=sys.stderr)
    # 매칭률이 크게 떨어지면 회사명 표기 규칙이 바뀐 것이다. 조용히 넘기지 않는다.
    if len(done) < len(by_name) * 0.9:
        print(f"  ⚠ 매칭률 {len(done)}/{len(by_name)} — 표기 규칙 변경 의심",
              file=sys.stderr)

    doc = {
        "_meta": {
            "source": "한국거래소 KIND 고배당기업 현황 (기업가치 제고계획 공시)",
            "law": "조세특례제한법 §104의27 · 2026.1.1~2028.12.31 한시",
            "요건": "배당성향 40% 이상 또는 배당성향 25% 이상 + 직전 사업연도 배당 10% 이상 증가",
            "세율": "2천만원 이하 14% / ~3억 20% / ~50억 25% / 50억 초과 30% (지방세 별도)",
            "주의": "자율공시라 목록은 하한선이다. ETF·펀드·리츠는 제외. "
                    "종합소득세 신고 때 분리과세를 신청해야 적용된다. 건강보험료는 줄지 않는다.",
            "disclosures": len(by_name),
            "codes": len(data),
            "collected": datetime.now(KST).strftime("%Y-%m-%d %H:%M"),
        },
        "data": data,
    }

    # 크게 줄면 멈춘다. 공시가 조용히 빠지는 걸 잡는다.
    if os.path.exists(OUT_PATH):
        try:
            with open(OUT_PATH, encoding="utf-8") as f:
                before = len((json.load(f).get("data") or {}))
        except (OSError, ValueError):
            before = 0
        if before > 0 and len(data) < before * 0.8:
            print(f"{before}종목 → {len(data)}종목 으로 줄었습니다. 배포 중단.",
                  file=sys.stderr)
            sys.exit(1)

    os.makedirs(os.path.dirname(OUT_PATH) or ".", exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, separators=(",", ":"))
    print(f"완료: {OUT_PATH} ({len(data)}종목)", file=sys.stderr)


if __name__ == "__main__":
    main()
