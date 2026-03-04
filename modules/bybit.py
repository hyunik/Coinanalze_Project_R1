"""
modules/bybit.py
────────────────
Bybit V5 REST API에서 OHLCV를 수집하고 기술적 레벨을 계산합니다.
(Binance 대체용 - GitHub Actions US IP 차단 회피 목적으로 작성)

인증 불필요 / Rate Limit 없음 (공개 엔드포인트)
"""

import logging

import requests
import pandas as pd
import numpy as np

import config

logger = logging.getLogger(__name__)

BYBIT_KLINES_URL    = "https://api.bybit.com/v5/market/kline"
BYBIT_EXCHANGE_URL  = "https://api.bybit.com/v5/market/instruments-info?category=linear"

# 캐시 — 프로세스 내 1회만 조회
_listed_futures_cache: set[str] | None = None


# ─── Binance 타임프레임 -> Bybit 타임프레임 변환 ─────────────────────────────

def _convert_interval(interval: str) -> str:
    """ '4h' -> '240', '1d' -> 'D' """
    mapping = {
        "1m": "1", "3m": "3", "5m": "5", "15m": "15", "30m": "30",
        "1h": "60", "2h": "120", "4h": "240", "6h": "360", "12h": "720",
        "1d": "D", "1w": "W", "1M": "M"
    }
    return mapping.get(interval, "240")


# ─── 상장 여부 확인 ───────────────────────────────────────────────────────────

def get_listed_futures() -> set[str]:
    """
    Bybit 선물(Linear) 거래 중인 심볼 전체를 반환합니다. (캐시 적용)

    Returns
    -------
    set[str]
        예: {"BTCUSDT", "ETHUSDT", "SOLUSDT", ...}
    """
    global _listed_futures_cache
    if _listed_futures_cache is not None:
        return _listed_futures_cache

    try:
        resp = requests.get(BYBIT_EXCHANGE_URL, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        _listed_futures_cache = {
            s["symbol"]
            for s in data.get("result", {}).get("list", [])
            if s.get("status") == "Trading" and s.get("symbol", "").endswith("USDT")
        }
        logger.info(
            f"[Bybit] 선물 상장 심볼 {len(_listed_futures_cache)}개 로드 완료"
        )
    except requests.RequestException as e:
        logger.error(f"[Bybit] exchangeInfo 조회 실패: {e}")
        _listed_futures_cache = set()

    return _listed_futures_cache


def is_listed(ticker: str) -> bool:
    """
    해당 ticker가 Bybit 선물에 상장되어 있는지 확인합니다.

    Parameters
    ----------
    ticker : str
        예: "SOL", "WIF"

    Returns
    -------
    bool
    """
    symbol = f"{ticker.upper()}USDT"
    return symbol in get_listed_futures()


# ─── OHLCV 수집 ───────────────────────────────────────────────────────────────

def get_ohlcv(
    ticker: str,
    interval: str | None = None,
    limit: int | None = None,
) -> pd.DataFrame | None:
    """
    Bybit 선물 OHLCV 캔들 데이터를 DataFrame으로 반환합니다.

    Parameters
    ----------
    ticker : str
        예: "SOL", "WIF"
    interval : str, optional
        기본값: config.BINANCE_INTERVAL ("4h")
    limit : int, optional
        기본값: config.BINANCE_LIMIT (100)

    Returns
    -------
    pd.DataFrame | None
        컬럼: open_time, open, high, low, close, volume, turnover
        실패 시 None 반환
    """
    # config의 Binance 변수명을 그대로 공유해서 사용 (기존 구조 호환성 유지)
    interval = interval or config.BINANCE_INTERVAL
    limit    = limit    or config.BINANCE_LIMIT
    symbol   = f"{ticker.upper()}USDT"

    params = {
        "category": "linear",
        "symbol":   symbol,
        "interval": _convert_interval(interval),
        "limit":    limit,
    }

    try:
        resp = requests.get(BYBIT_KLINES_URL, params=params, timeout=15)
        resp.raise_for_status()
        res_data = resp.json()
        
        if res_data.get("retCode") != 0:
            logger.warning(f"[Bybit] {symbol} 데이터 요청 오류: {res_data.get('retMsg')}")
            return None
            
        raw = res_data.get("result", {}).get("list", [])
    except requests.RequestException as e:
        logger.error(f"[Bybit] {symbol} OHLCV 수집 실패: {e}")
        return None

    if not raw:
        logger.warning(f"[Bybit] {symbol} OHLCV 데이터 없음")
        return None

    # Bybit 결과는 최신 순이므로 역순(기존부터 최신으로) 정렬
    raw = raw[::-1]

    columns = ["open_time", "open", "high", "low", "close", "volume", "turnover"]
    df = pd.DataFrame(raw, columns=columns)

    # 숫자형 변환
    numeric_cols = ["open", "high", "low", "close", "volume", "turnover"]
    df[numeric_cols] = df[numeric_cols].apply(pd.to_numeric)
    df["open_time"]  = pd.to_datetime(df["open_time"].astype(float), unit="ms")

    return df


# ─── 기술적 레벨 계산 ─────────────────────────────────────────────────────────

def calc_levels(df: pd.DataFrame) -> dict:
    """
    OHLCV DataFrame에서 기술적 레벨을 계산합니다.
    """
    recent = df.tail(20).copy()
    high   = recent["high"].max()
    low    = recent["low"].min()
    span   = high - low

    # Bybit kline에는 taker buy volume이 없으므로
    # 캔들의 형태(시가 대비 종가 위치)를 이용해 델타 추정 (매수우위=양수)
    # delta = 거래량 * ((close - open) / (high - low))
    df = df.copy()
    df["span"]  = df["high"] - df["low"]
    df["delta"] = np.where(
        df["span"] > 0,
        df["volume"] * ((df["close"] - df["open"]) / df["span"]),
        0
    )
    df["cvd"] = df["delta"].cumsum()

    current_price = float(df["close"].iloc[-1])
    cvd_slope     = float(df["cvd"].diff().tail(5).mean())
    vol_ratio     = float(
        df["volume"].iloc[-1] / recent["volume"].mean()
        if recent["volume"].mean() > 0 else 0
    )

    # 현재가 위치 (0=저점, 1=고점)
    position_pct = float((current_price - low) / span) if span > 0 else 0.5

    return {
        "current_price": round(current_price, 8),
        "high_20":       round(float(high), 8),
        "low_20":        round(float(low), 8),
        "fib_382":       round(float(high - span * 0.382), 8),
        "fib_618":       round(float(high - span * 0.618), 8),
        "cvd_slope":     round(cvd_slope, 2),
        "vol_ratio":     round(vol_ratio, 4),
        "position_pct":  round(position_pct, 4),
    }


def get_levels(ticker: str) -> dict | None:
    """
    ticker에 대한 OHLCV 수집 + 기술적 레벨 계산을 한 번에 수행합니다.
    """
    df = get_ohlcv(ticker)
    if df is None or len(df) < 20:
        logger.warning(f"[Bybit] {ticker} 데이터 부족 (< 20봉)")
        return None
    return calc_levels(df)
