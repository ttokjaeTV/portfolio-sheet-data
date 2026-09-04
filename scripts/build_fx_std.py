#!/usr/bin/env python3
"""
서울외국환중개 고시 매매기준율(기준환율) → data/fx_std.csv

세법상 외화 소득의 원화환산은 '수입시기 현재의 기준환율' 로 한다(국세청 원천세과-222).
그 기준환율을 고시하는 곳이 서울외국환중개다. 미국 배당을 원화로 환산해
금융소득종합과세 한도와 비교할 때 이 값을 쓴다.

시장 종가(네이버)와는 다르다. 실측 2026-08 넷째 주:
    8/25  고시 1,380.60  네이버 1,383.50  (-0.21%)
    8/26  고시 1,383.10  네이버 1,386.00  (-0.21%)
    8/27  고시 1,384.60  네이버 1,382.00  (+0.19%)
평균 0.2%p 차이라 무시할 만하지 않다.

★ 이 파일은 '덧붙이기' 다. indices_history.csv 처럼 매번 새로 그리지 않는다.
  기존 값을 지우지 않으므로 수집이 하루 실패해도 이력이 사라지지 않는다.

요청 방식 (2026-09-04 브라우저 네트워크 탭으로 확인)
    GET http://www.smbs.biz/ExRate/StdExRate_xml.jsp?arr_value=USD_{시작}_{끝}
    → EUC-KR XML(FusionCharts). <set label='26.08.25' value='1380.6' /> 형태.
      label 은 YY.MM.DD, value 가 매매기준율이다. 270영업일이 한 번에 온다.

★ 화면의 표(StdExRate.jsp)를 긁으려 하지 말 것. 그 표는 이 XML 을 받아
  자바스크립트가 그리는 것이라 HTML 응답에는 행이 하나도 없다.
  (POST 로 폼을 보내도 마찬가지다. 실제로 파싱 0행을 확인했다.)

출력: data/fx_std.csv
    날짜,매매기준율        (YYYY-MM-DD, 오름차순)
"""

import csv
import os
import re
import sys
import urllib.error

import urllib.request
from datetime import datetime, timedelta, timezone

URL = "http://www.smbs.biz/ExRate/StdExRate_xml.jsp?arr_value=USD_{}_{}"
REFERER = "http://www.smbs.biz/ExRate/StdExRate.jsp"
OUT_PATH = os.environ.get("FX_STD_PATH", "data/fx_std.csv")

KST = timezone(timedelta(hours=9))
BACKFILL_DAYS = 400        # 파일이 없을 때 채울 기간. 배당 12개월치를 덮고도 남는다
RECENT_DAYS = 15           # 평소 실행 시 확인할 최근 구간(누락분 자동 보정)
MIN_HOUR = 10              # 당일 기준환율은 아침에 고시된다. 그전에는 두드리지 않는다

UA = {"User-Agent": "Mozilla/5.0", "Referer": REFERER}

# <set color='c93749' label='26.08.25' value='1380.6' />
SET_RE = re.compile(
    r"<set\b[^>]*label='(\d{2})\.(\d{2})\.(\d{2})'[^>]*value='([\d.]+)'", re.I)


def fetch(start, end):
    """[(YYYY-MM-DD, 매매기준율), ...]"""
    req = urllib.request.Request(URL.format(start, end), headers=UA)
    with urllib.request.urlopen(req, timeout=40) as r:
        xml = r.read().decode("euc-kr", "replace")   # ★ EUC-KR. utf-8 로 읽으면 깨진다

    out = []
    for yy, mm, dd, v in SET_RE.findall(xml):
        try:
            rate = float(v)
        except ValueError:
            continue
        if rate > 0:
            out.append((f"20{yy}-{mm}-{dd}", rate))
    return out


def load_existing():
    got = {}
    if not os.path.exists(OUT_PATH):
        return got
    with open(OUT_PATH, encoding="utf-8") as f:
        for r in csv.reader(f):
            if len(r) >= 2 and r[0] != "날짜":
                try:
                    got[r[0]] = float(r[1])
                except ValueError:
                    pass
    return got


def main():
    now = datetime.now(KST)
    today = now.date()
    have = load_existing()
    latest = max(have) if have else None

    if latest == today.isoformat():
        print(f"오늘({today}) 기준환율이 이미 있습니다. 건너뜁니다.", file=sys.stderr)
        return
    if have and now.hour < MIN_HOUR:
        print(f"{now:%H:%M} KST — 당일 고시 전이라 건너뜁니다.", file=sys.stderr)
        return

    span = BACKFILL_DAYS if not have else RECENT_DAYS
    start = today - timedelta(days=span)
    print(f"{start} ~ {today} 조회 ({'최초 수집' if not have else '최근 보정'})",
          file=sys.stderr)

    try:
        rows = fetch(start, today)
    except (urllib.error.URLError, TimeoutError, UnicodeError) as exc:
        print(f"조회 실패: {exc}", file=sys.stderr)
        sys.exit(1)

    if not rows:
        # 표를 하나도 못 읽었다 = 응답 형식이 바뀌었거나 차단이다.
        # 빈 값을 쓰면 세금 계산이 오늘 환율로 되돌아가므로 파일을 건드리지 않는다.
        print("표를 하나도 읽지 못했습니다. 파일을 그대로 둡니다.", file=sys.stderr)
        sys.exit(1)

    added = sum(1 for d, _ in rows if d not in have)
    have.update(rows)                      # 같은 날짜는 새 값으로 갱신

    os.makedirs(os.path.dirname(OUT_PATH) or ".", exist_ok=True)
    with open(OUT_PATH, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["날짜", "매매기준율"])
        for d in sorted(have):
            w.writerow([d, f"{have[d]:.2f}"])

    days = sorted(have)
    print(f"완료: {OUT_PATH} ({len(days)}일, 새로 {added}일)  "
          f"{days[0]} ~ {days[-1]}", file=sys.stderr)


if __name__ == "__main__":
    main()
