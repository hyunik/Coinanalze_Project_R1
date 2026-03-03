# 📦 모듈 상세 설명

---

## modules/coingecko.py

### 역할
CoinGecko 무료 API에서 시총 순위 기준 코인 목록을 수집합니다.

### 주요 함수
| 함수 | 설명 |
|------|------|
| `get_target_coins()` | 시총 순위 범위 내 코인 반환 |

### 코드 예시
```python
def get_target_coins(rank_min: int, rank_max: int) -> list[dict]:
    """
    반환 형태:
    [
        {"symbol": "wif", "name": "dogwifhat", "market_cap_rank": 210, ...},
        ...
    ]
    """
    url = "https://api.coingecko.com/api/v3/coins/markets"
    all_coins = []

    # 250개씩 페이지네이션
    for page in range(1, (rank_max // 250) + 2):
        params = {
            "vs_currency": "usd",
            "order": "market_cap_desc",
            "per_page": 250,
            "page": page,
        }
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        all_coins.extend(resp.json())
        time.sleep(1.5)   # CoinGecko 무료 속도 제한 대응

    return [
        c for c in all_coins
        if rank_min <= c.get("market_cap_rank", 9999) <= rank_max
    ]
```

### 주의사항
- CoinGecko 무료 플랜: 분당 10~30회 제한 → 페이지 간 `sleep(1.5)` 필수
- 심볼이 거래소 티커와 다를 수 있음 (예: `iota` vs `IOTA`)
  → Binance 심볼 검증 단계 필요

---

## modules/coinalyze.py

### 역할
Coinalyze API에서 선물 온체인 데이터를 수집합니다.
Rate Limit(40회/분) 처리 로직이 이 모듈에 집중됩니다.

### 주요 함수
| 함수 | 설명 |
|------|------|
| `build_symbol(ticker)` | `SOLUSDT_PERP.A` 형식 집계 심볼 생성 |
| `fetch_metric(endpoint, symbols)` | Rate Limit 포함 API 호출 |
| `get_all_metrics(symbols)` | 4개 엔드포인트 일괄 수집 |

### 집계 심볼(`.A`) 설명
```
SOLUSDT_PERP.A  →  모든 거래소 SOL 무기한 선물 집계
SOLUSDT_PERP.BB →  Binance 단독
SOLUSDT_PERP.OK →  OKX 단독
```
> 반드시 `.A` 사용 → 거래소 편향 없는 전체 시장 데이터

### Rate Limiter 구현 예시
```python
import time
from collections import deque

class RateLimiter:
    """슬라이딩 윈도우 방식 Rate Limiter (40회/60초)"""

    def __init__(self, max_calls: int = 38, period: int = 60):
        # 여유 2회 확보 → 실제 38회로 운용
        self.max_calls = max_calls
        self.period    = period
        self.calls     = deque()   # 호출 타임스탬프 저장

    def wait_if_needed(self):
        now = time.monotonic()

        # 60초 지난 기록 제거
        while self.calls and now - self.calls[0] >= self.period:
            self.calls.popleft()

        if len(self.calls) >= self.max_calls:
            # 가장 오래된 호출이 60초를 채울 때까지 대기
            sleep_sec = self.period - (now - self.calls[0]) + 0.1
            print(f"[RateLimiter] {sleep_sec:.1f}초 대기 중...")
            time.sleep(sleep_sec)

        self.calls.append(time.monotonic())


rate_limiter = RateLimiter()   # 모듈 레벨 싱글톤


def fetch_metric(endpoint: str, symbols: list[str],
                 interval: str = "1day") -> dict:
    """
    endpoint 예시: "open-interest", "funding-rate",
                   "long-short-ratio", "liquidation-history"
    반환: {symbol: [history_list]}
    """
    BASE = "https://api.coinalyze.net/v1"
    HEADERS = {"api_key": config.COINALYZE_API_KEY}
    CHUNK = 20   # 1회 요청당 최대 심볼 수

    results = {}

    for i in range(0, len(symbols), CHUNK):
        chunk = symbols[i : i + CHUNK]

        rate_limiter.wait_if_needed()   # 호출 전 Rate Limit 체크

        try:
            resp = requests.get(
                f"{BASE}/{endpoint}",
                headers=HEADERS,
                params={"symbols": ",".join(chunk), "interval": interval},
                timeout=15,
            )

            if resp.status_code == 429:
                retry_after = int(resp.headers.get("Retry-After", 60))
                print(f"[429] {retry_after}초 후 재시도")
                time.sleep(retry_after)
                # tenacity 혹은 단순 재귀로 재시도 처리
                continue

            resp.raise_for_status()

            for item in resp.json():
                sym = item.get("symbol", "")
                results[sym] = item.get("history", [])

        except requests.RequestException as e:
            print(f"[ERROR] {endpoint} / {chunk[:3]}... : {e}")

        time.sleep(0.3)   # 청크 간 미세 딜레이

    return results
```

### tenacity를 활용한 재시도 데코레이터 (선택)
```python
from tenacity import retry, stop_after_attempt, wait_fixed

@retry(stop=stop_after_attempt(3), wait=wait_fixed(5))
def safe_api_call(url, headers, params):
    resp = requests.get(url, headers=headers, params=params, timeout=15)
    if resp.status_code == 429:
        raise Exception(f"Rate limit hit, Retry-After: {resp.headers.get('Retry-After')}")
    resp.raise_for_status()
    return resp.json()
```

---

## modules/binance.py

### 역할
Binance 선물 REST API에서 OHLCV를 가져와 기술적 레벨을 계산합니다.

### 주요 함수
| 함수 | 설명 |
|------|------|
| `get_ohlcv(ticker)` | 선물 4h OHLCV 반환 |
| `calc_levels(df)` | 지지/저항/CVD/피보나치 계산 |
| `is_listed(ticker)` | Binance 선물 상장 여부 확인 |

### 코드 예시
```python
def calc_levels(df: pd.DataFrame) -> dict:
    """
    반환:
    {
        "current_price": 0.31,
        "high_20": 0.35,
        "low_20": 0.28,
        "fib_382": 0.324,    # 고점 - 범위 * 0.382
        "fib_618": 0.307,    # 고점 - 범위 * 0.618
        "cvd_slope": 12500,  # 최근 5봉 CVD 기울기 (양수 = 매수우위)
        "vol_ratio": 1.8,    # 최근봉 거래량 / 20봉 평균
    }
    """
    recent = df.tail(20)
    high   = recent["high"].max()
    low    = recent["low"].min()
    span   = high - low

    # CVD = 누적 (매수거래량 - 매도거래량)
    df["delta"] = df["taker_buy_base"] - (df["volume"] - df["taker_buy_base"])
    df["cvd"]   = df["delta"].cumsum()

    return {
        "current_price": df["close"].iloc[-1],
        "high_20":  high,
        "low_20":   low,
        "fib_382":  round(high - span * 0.382, 8),
        "fib_618":  round(high - span * 0.618, 8),
        "cvd_slope": df["cvd"].diff().tail(5).mean(),
        "vol_ratio": df["volume"].iloc[-1] / recent["volume"].mean(),
    }
```

---

## modules/scorer.py

### 역할
온체인 데이터와 기술적 레벨을 종합하여 100점 스코어를 산출합니다.

### 스코어링 항목

| 항목 | 배점 | 만점 조건 |
|------|------|----------|
| OI 변화율 | 25점 | OI 15% 이상 급증 (신규 롱 유입 확인) |
| 펀딩비 | 25점 | -0.005% ~ +0.005% (과열 없는 중립) |
| CVD + 거래량 | 20점 | CVD 상승 + 거래량 1.2배 이상 |
| L/S 비율 | 15점 | L/S < 0.8 (숏 우위, 스퀴즈 잠재력) |
| 청산 패턴 | 15점 | 롱 청산 대량 소화 완료 (바닥 신호) |

### 등급 기준

| 점수 | 등급 | 의미 |
|------|------|------|
| 80~100 | 🔥 S급 | 강력 매수 시그널 |
| 70~79  | ⭐ A급 | 매수 우위 |
| 60~69  | 🔸 B급 | 조건부 관심 |
| 0~59   | ⚪ 제외 | 알림 미발송 |

### 코드 예시 (핵심 로직)
```python
def score_oi(oi_hist: list) -> tuple[int, str]:
    """OI 변화율 점수 (25점 만점)"""
    if len(oi_hist) < 2:
        return 0, "OI 데이터 없음"

    prev = oi_hist[-2].get("v", 0)
    curr = oi_hist[-1].get("v", 0)
    pct  = (curr - prev) / prev * 100 if prev else 0

    if pct >= 15:   return 25, f"✅ OI +{pct:.1f}% 급증 (신규 롱 공격 유입)"
    if pct >= 7:    return 18, f"✅ OI +{pct:.1f}% 증가"
    if pct >= 2:    return 10, f"🔸 OI +{pct:.1f}% 소폭 증가"
    if pct < -5:    return  3, f"❌ OI {pct:.1f}% 감소 (이탈)"
    return 7, f"OI {pct:.1f}% 보합"
```

---

## modules/planner.py

### 역할
스코어 및 기술적 레벨 기반으로 진입/익절/손절 전략을 생성합니다.

### 전략 산출 로직
```
현재가 위치 = (현재가 - 20봉저점) / (20봉고점 - 20봉저점)

상단 (>70%) → 피보 0.382 되돌림 대기 후 진입
중간 (30~70%) → 현재가 근처 진입
하단 (<30%) → 상방 돌파 확인 후 진입 (돌파 + 0.5%)

TP1 = 20봉 고점 * 0.95  (저항 직전 익절)
TP2 = 20봉 고점 * 1.02  (고점 돌파 시나리오)
SL  = 20봉 저점 * 0.97  (저점 하단 3%)
```

### 레버리지 권장 기준
```python
def recommend_leverage(score: int) -> str:
    if score >= 80: return "5~10x"
    if score >= 70: return "3~5x"
    return "2~3x (신중)"
```

---

## modules/notifier.py

### 역할
분석 결과를 텔레그램 메시지로 포맷하고 전송합니다.

### 전송 구조
```
[메시지 1] 헤더 (스캔 요약)
[메시지 2] 1위 코인 상세
[메시지 3] 2위 코인 상세
...
[최대 5개 코인]
```

> 텔레그램 메시지 1개당 4096자 제한 → 코인별 개별 메시지로 분리 전송