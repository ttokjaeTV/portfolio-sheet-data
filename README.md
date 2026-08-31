# 포트폴리오 관리 도구 — 똑재TV

연금저축·IRP·ISA·일반계좌에 흩어진 국내 ETF와 미국 직투를 한 화면에 모아
평가손익, 자산 비중, IRP 안전자산 한도를 계산하는 도구.

도구: https://ttokjaetv.github.io/portfolio-sheet-data/

## 왜 만들었나

기존 구글 스프레드시트는 국내 ETF 현재가를 행마다 `IMPORTXML`로 긁어왔다.
종목이 20개만 넘어도 호출 제한에 걸려 `#N/A`가 무작위로 떴고,
종목명은 `국내상장ETF 종목코드(수기등록필요)` 탭에 손으로 채워야 했다.
그 탭은 2025-05-29 기준에서 멈춰 있었다.

이 레포는 시세 수집을 한 곳으로 모은다. 신규 상장 ETF도 자동으로 따라온다.

## 구조

```
index.html                            포트폴리오 관리 도구 (단일 HTML)
scripts/build_etf_prices.py           국내 ETF 시세 → etf_prices.csv
scripts/build_index_history.py        지수·환율 5년치 일별 종가 → indices_history.csv
scripts/build_us_symbols.py           미국 티커 → 네이버 코드 매핑 (증분)
scripts/build_us_prices.py            미국 시세 + 환율 → us_prices.csv, fx.csv
.github/workflows/update-prices.yml   장중 10분마다 시세 갱신
.github/workflows/update-us-symbols.yml  주 1회 신규 상장분 코드 해석
data/                                 산출물 (자동 커밋)
docs/시트_적용가이드.md                 기존 스프레드시트를 CSV로 전환하는 법
```

## 데이터

| 파일 | 내용 | 규모 |
|---|---|---|
| `data/etf_prices.csv` | 국내 ETF 코드·종목명·자산군·총보수·안전자산·시세 | 1,163행 |
| `data/us_prices.csv` | 미국 주식·ETF 티커·종목명·거래소·USD 시세 | 12,299행 |
| `data/fx.csv` | USD/KRW 고시환율 | 1행 |
| `data/us_symbols.json` | 티커 → 네이버 reutersCode 매핑 캐시 | 13,141건 |
| `data/indices_history.csv` | 지수·환율 5년치 일별 종가 (상황판 차트용) | 8,823행 |

출처

- 국내 ETF 종목·자산군·안전자산 여부: `ttokjaeTV/etf-selector` 의 `krx_etf_master.json`
- 미국 티커 유니버스: 나스닥 공식 심볼 디렉터리 (`nasdaqtrader.com`)
- 시세·환율: 네이버 금융

## 미국 종목코드에 규칙이 없다는 점

네이버는 미국 종목을 `reutersCode`로 조회하는데, 이 코드는 거래소만으로 못 만든다.

```
NASDAQ   AAPL.O   QQQ.O
NYSE     JPM      KO       (접미사 없음)
AMEX     SCHD.K   인데 같은 AMEX 인 SPY 는 접미사 없음
버크셔B   BRKb
```

그래서 자동완성 API로 한 번씩 조회해 `us_symbols.json`에 캐싱한다.
캐시에 있으면 건너뛰므로, 두 번째 실행부터는 신규 상장분만 조회한다.

## 로컬 실행

```bash
python scripts/build_etf_prices.py
python scripts/build_us_symbols.py     # 최초 1회는 20분가량 걸린다
python scripts/build_us_prices.py
```

`index.html`은 CSV를 절대 주소로 읽으므로 더블클릭만 해도 동작한다.

## 운영 주의사항

- 네이버 비공식 API라 구조가 바뀌면 워크플로가 실패한다. Actions 실패 알림을 켜둘 것.
- 시세 확보가 절반 미만이면 스크립트가 종료 코드 1로 죽는다. 깨진 CSV 배포 방지용.
- 마지막 성공분 CSV가 레포에 남으므로 한 번 실패해도 도구가 즉시 깨지지는 않는다.
- 미국 시세는 한국 장중에는 전 거래일 종가다.
- 지수 이력은 네이버 쪽 제약 두 가지를 우회한다.
  `count=N` 은 아무리 키워도 110일에서 끊기므로 날짜 범위로 받고,
  환율 `pageSize` 는 100 이면 400 을 뱉으므로 60 씩 5페이지로 받는다.
- Actions Workflow permissions 가 `Read and write` 여야 커밋이 푸시된다.
