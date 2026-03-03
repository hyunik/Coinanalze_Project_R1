# 🔌 API 사용 가이드

---

## Coinalyze API

### 기본 정보
```
Base URL : https://api.coinalyze.net/v1
인증 방식 : Request Header → api_key: YOUR_KEY
무료 플랜 : 40 calls/min
초과 응답 : HTTP 429 + Retry-After 헤더
```

### 사용 엔드포인트

#### 1. Open Interest
```
GET /open-interest
params:
  symbols  : BTCUSDT_PERP.A,ETHUSDT_PERP.A  (콤마 구분, 최대 20개)
  interval : 1min / 5min / 15min / 30min / 1hour / 2hour / 4hour / 1day

응답 예시:
[
  {
    "symbol": "BTCUSDT_PERP.A",
    "history": [
      {"t": 1704067200, "v": 18500000000},  // t=타임스탬프, v=OI(USD)
      {"t": 1704153600, "v": 19200000000}
    ]
  }
]
```

#### 2. Funding Rate
```
GET /funding-rate
params: symbols, interval (동일)

history 항목: {"t": ..., "v": 0.0001}  // v = 펀딩비 소수 (0.01% = 0.0001)
```

#### 3. Long/Short Ratio
```
GET /long-short-ratio
params: symbols, interval (동일)

history 항목: {"t": ..., "v": 0.85}  // v = 롱/숏 비율 (1.0 = 50:50)
```

#### 4. Liquidation History
```
GET /liquidation-history
params: symbols, interval (동일)

history 항목: {"t": ..., "l": 1500000, "s": 800000}
// l = 롱 청산 USD, s = 숏 청산 USD
```

---

## Rate Limit 운용 전략

### 호출 수 계산
```
대상 코인 수: 200개
청크 크기: 20개
청크 수: 200 / 20 = 10개

엔드포인트 4개 × 청크 10개 = 총 40회 호출
→ 무료 플랜 한도(40회/분)와 정확히 일치 → 여유 없음!
```

### 권장 최적화 방안

**방안 A: 청크 크기 줄이기 (안전, 느림)**
```python
CHUNK = 15   # 20 → 15로 축소
# 총 호출 = 4 × ceil(200/15) ≈ 56회 → 2분 분산
# 각 청크 사이 sleep(1.5초) → 56회 / 56.5초 ≈ 분당 38회
```

**방안 B: 분석 대상 줄이기 (권장)**
```python
# 1단계: Binance 선물 미상장 종목 먼저 제거
# 2단계: CoinGecko 거래량 하위 50% 제거
# → 실제 분석 대상 100~120개로 줄어듦 (충분히 여유)
```

**방안 C: 슬라이딩 윈도우 + sleep 조합 (구현 예시)**
```python
class RateLimiter:
    def __init__(self, max_calls=38, period=60):
        self.max_calls = max_calls
        self.period = period
        self.calls = deque()

    def wait_if_needed(self):
        now = time.monotonic()
        # 만료된 타임스탬프 제거
        while self.calls and now - self.calls[0] >= self.period:
            self.calls.popleft()
        # 한도 초과 시 슬립
        if len(self.calls) >= self.max_calls:
            wait = self.period - (now - self.calls[0]) + 0.5
            time.sleep(wait)
        self.calls.append(time.monotonic())
```

### 429 에러 처리
```python
if response.status_code == 429:
    retry_after = int(response.headers.get("Retry-After", 60))
    print(f"Rate limit 도달. {retry_after}초 대기 후 재시도")
    time.sleep(retry_after)
    # → 재시도 (tenacity 데코레이터 또는 while 루프)
```

---

## Binance Futures API

### 사용 엔드포인트
```
GET https://fapi.binance.com/fapi/v1/klines
params:
  symbol   : SOLUSDT
  interval : 1m / 3m / 5m / 15m / 30m / 1h / 2h / 4h / 1d
  limit    : 최대 1500 (기본 500)

Rate Limit: 무료 / 인증 불필요
```

### 심볼 검증 (Binance 상장 여부 확인)
```python
def get_listed_futures() -> set[str]:
    """Binance 선물 상장 심볼 전체 반환"""
    url = "https://fapi.binance.com/fapi/v1/exchangeInfo"
    resp = requests.get(url, timeout=10)
    data = resp.json()
    return {s["symbol"] for s in data["symbols"] if s["status"] == "TRADING"}
```

---

## CoinGecko API
```
GET https://api.coingecko.com/api/v3/coins/markets
params:
  vs_currency : usd
  order       : market_cap_desc
  per_page    : 250 (최대)
  page        : 1, 2, 3 ...

무료 플랜 제한 : 분당 10~30회 → 페이지 간 sleep(1.5) 필수
```