"""
modules/coinalyze.py
────────────────────
Coinalyze API에서 선물 온체인 데이터를 수집합니다.
Rate Limit(40회/분) 처리 로직이 이 모듈에 집중됩니다.

Rate Limit 처리 전략 (ARCHITECTURE.md / API_GUIDE.md 준수):
  1. 슬라이딩 윈도우 카운터 (38회/60초) — 호출 전 자동 sleep
  2. HTTP 429 수신 시 Retry-After 헤더 파싱 후 대기
  3. tenacity @retry 데코레이터로 최대 3회 재시도
"""

import time
import logging
from collections import deque

import requests
from tenacity import (
    retry,
    stop_after_attempt,
    wait_fixed,
    retry_if_exception_type,
)

import config

logger = logging.getLogger(__name__)

BASE_URL = "https://api.coinalyze.net/v1"

# ─── 엔드포인트 상수 ──────────────────────────────────────────────────────────
ENDPOINT_OI          = "open-interest"
ENDPOINT_FUNDING     = "funding-rate"
ENDPOINT_LS_RATIO    = "long-short-ratio"
ENDPOINT_LIQUIDATION = "liquidation-history"

ALL_ENDPOINTS = [
    ENDPOINT_OI,
    ENDPOINT_FUNDING,
    ENDPOINT_LS_RATIO,
    ENDPOINT_LIQUIDATION,
]


# ─── Rate Limiter ─────────────────────────────────────────────────────────────

class RateLimiter:
    """
    슬라이딩 윈도우 방식 Rate Limiter (40회/분 → 여유 2회 확보하여 38회 운용)

    ARCHITECTURE.md 및 API_GUIDE.md 방안 C 구현:
      - 60초 내 호출 타임스탬프를 deque로 관리
      - 한도 초과 시 가장 오래된 호출이 만료될 때까지 자동 sleep
    """

    def __init__(
        self,
        max_calls: int = config.RATE_LIMIT_MAX_CALLS,
        period: int = config.RATE_LIMIT_PERIOD,
    ):
        self.max_calls = max_calls
        self.period    = period
        self.calls: deque[float] = deque()

    def wait_if_needed(self) -> None:
        """호출 전 Rate Limit 체크 — 필요 시 자동 sleep."""
        now = time.monotonic()

        # 만료된 타임스탬프 제거 (60초 초과)
        while self.calls and now - self.calls[0] >= self.period:
            self.calls.popleft()

        if len(self.calls) >= self.max_calls:
            # 가장 오래된 호출이 period를 채울 때까지 대기
            sleep_sec = self.period - (now - self.calls[0]) + 0.5
            logger.info(f"[RateLimiter] 한도 도달 → {sleep_sec:.1f}초 대기 중...")
            time.sleep(max(sleep_sec, 0))

        self.calls.append(time.monotonic())

    @property
    def current_count(self) -> int:
        """현재 슬라이딩 윈도우 내 호출 수."""
        now = time.monotonic()
        while self.calls and now - self.calls[0] >= self.period:
            self.calls.popleft()
        return len(self.calls)


# 모듈 레벨 싱글톤 — 모든 fetch 함수가 공유
_rate_limiter = RateLimiter()


# ─── 심볼 유틸 ────────────────────────────────────────────────────────────────

def build_symbol(ticker: str) -> str:
    """
    Coinalyze 집계 심볼 생성.

    Parameters
    ----------
    ticker : str
        예: "SOL", "WIF"

    Returns
    -------
    str
        예: "SOLUSDT_PERP.A"  (모든 거래소 집계)
    """
    return f"{ticker.upper()}USDT_PERP.A"


# ─── 429 전용 예외 ────────────────────────────────────────────────────────────

class RateLimitError(Exception):
    """HTTP 429 Rate Limit 초과 시 발생."""
    def __init__(self, retry_after: int = 60):
        self.retry_after = retry_after
        super().__init__(f"Rate limit hit. Retry-After: {retry_after}s")


# ─── 핵심 API 호출 함수 ───────────────────────────────────────────────────────

@retry(
    stop=stop_after_attempt(3),
    wait=wait_fixed(5),
    retry=retry_if_exception_type(RateLimitError),
    reraise=True,
)
def _call_api(url: str, headers: dict, params: dict) -> list:
    """
    단일 API 호출 (tenacity 재시도 래퍼).

    HTTP 429 수신 시 Retry-After 헤더를 파싱하여 대기 후 RateLimitError를 raise,
    tenacity가 최대 3회 재시도합니다.
    """
    resp = requests.get(url, headers=headers, params=params, timeout=15)

    if resp.status_code == 429:
        retry_after_str = resp.headers.get("Retry-After", "60")
        try:
            retry_after = int(float(retry_after_str)) + 1
        except ValueError:
            retry_after = 60
        logger.warning(f"[429] Rate limit 도달. {retry_after}초 대기 후 재시도...")
        time.sleep(retry_after)
        raise RateLimitError(retry_after)

    resp.raise_for_status()
    return resp.json()


def fetch_metric(
    endpoint: str,
    symbols: list[str],
    interval: str | None = None,
) -> dict[str, list]:
    """
    Coinalyze 단일 엔드포인트에서 여러 심볼 데이터를 수집합니다.

    Parameters
    ----------
    endpoint : str
        "open-interest" | "funding-rate" | "long-short-ratio" | "liquidation-history"
    symbols : list[str]
        Coinalyze 심볼 목록 (예: ["SOLUSDT_PERP.A", "WIFUSDT_PERP.A"])
    interval : str, optional
        기본값: config.COINALYZE_INTERVAL ("1day")

    Returns
    -------
    dict[str, list]
        {symbol: history_list}
        예: {"SOLUSDT_PERP.A": [{"t": 1704067200, "v": 18500000000}, ...]}
    """
    interval = interval or config.COINALYZE_INTERVAL
    headers  = {"api_key": config.COINALYZE_API_KEY}
    url      = f"{BASE_URL}/{endpoint}"
    chunk    = config.CHUNK_SIZE
    results: dict[str, list] = {}

    for i in range(0, len(symbols), chunk):
        sym_chunk = symbols[i : i + chunk]

        # ① 슬라이딩 윈도우 체크 — 한도 근접 시 자동 sleep
        _rate_limiter.wait_if_needed()

        params = {
            "symbols":  ",".join(sym_chunk),
            "interval": interval,
        }

        try:
            data = _call_api(url, headers, params)
            for item in data:
                sym = item.get("symbol", "")
                results[sym] = item.get("history", [])

            logger.debug(
                f"[Coinalyze] {endpoint} | 청크 {i // chunk + 1} | "
                f"{len(sym_chunk)}개 심볼 | "
                f"윈도우 내 {_rate_limiter.current_count}회"
            )

        except RateLimitError:
            logger.error(f"[Coinalyze] {endpoint} 재시도 3회 초과 — 청크 스킵")
        except requests.RequestException as e:
            logger.error(f"[Coinalyze] {endpoint} 요청 오류: {e}")

        # ② 청크 간 미세 딜레이 (ARCHITECTURE.md: 1.5초 권장)
        time.sleep(config.CHUNK_SLEEP)

    return results


def get_all_metrics(symbols: list[str]) -> dict[str, dict[str, list]]:
    """
    4개 엔드포인트 데이터를 일괄 수집합니다.

    Parameters
    ----------
    symbols : list[str]
        Coinalyze 심볼 목록

    Returns
    -------
    dict[str, dict[str, list]]
        {
            "open-interest":      {symbol: history},
            "funding-rate":       {symbol: history},
            "long-short-ratio":   {symbol: history},
            "liquidation-history":{symbol: history},
        }
    """
    logger.info(
        f"[Coinalyze] {len(symbols)}개 심볼 × 4 엔드포인트 수집 시작"
    )
    result: dict[str, dict[str, list]] = {}

    for ep in ALL_ENDPOINTS:
        logger.info(f"[Coinalyze] {ep} 수집 중...")
        result[ep] = fetch_metric(ep, symbols)

    logger.info("[Coinalyze] 전체 온체인 데이터 수집 완료")
    return result
