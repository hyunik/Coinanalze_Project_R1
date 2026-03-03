"""
modules/binance.py
──────────────────
Binance 선물 REST API에서 OHLCV를 수집하고 기술적 레벨을 계산합니다.

인증 불필요 / Rate Limit 없음 (공개 엔드포인트)
"""

import logging

import requests
import pandas as pd
import numpy as np

import config

logger = logging.getLogger(__name__)

BINANCE_KLINES_URL    = "https://fapi.binance.com/fapi/v1/klines"
BINANCE_EXCHANGE_URL  = "https://fapi.binance.com/fapi/v1/exchangeInfo"

# 캐시 — 프로세스 내 1회만 조회
_listed_futures_cache: set[str] | None = None


# ─── 상장 여부 확인 ───────────────────────────────────────────────────────────

def get_listed_futures() -> set[str]:
    """
    Binance 선물 거래 중인 심볼 전체를 반환합니다. (캐시 적용)

    Returns
    -------
    set[str]
        예: {"BTCUSDT", "ETHUSDT", "SOLUSDT", ...}
    """
    global _listed_futures_cache
    if _listed_futures_cache is not None:
        return _listed_futures_cache

    try:
        resp = requests.get(BINANCE_EXCHANGE_URL, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        _listed_futures_cache = {
            s["symbol"]
            for s in data.get("symbols", [])
            if s.get("status") == "TRADING"
        }
        logger.info(
            f"[Binance] 선물 상장 심볼 {len(_listed_futures_cache)}개 로드 완료"
        )
    except requests.RequestException as e:
        logger.error(f"[Binance] exchangeInfo 조회 실패: {e}")
        _listed_futures_cache = set()

    return _listed_futures_cache


def is_listed(ticker: str) -> bool:
    """
    해당 ticker가 Binance 선물에 상장되어 있는지 확인합니다.

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
    Binance 선물 OHLCV 캔들 데이터를 DataFrame으로 반환합니다.

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
        컬럼: open_time, open, high, low, close, volume,
               close_time, taker_buy_base, taker_buy_quote
        실패 시 None 반환
    """
    interval = interval or config.BINANCE_INTERVAL
    limit    = limit    or config.BINANCE_LIMIT
    symbol   = f"{ticker.upper()}USDT"

    params = {
        "symbol":   symbol,
        "interval": interval,
        "limit":    limit,
    }

    try:
        resp = requests.get(BINANCE_KLINES_URL, params=params, timeout=15)
        resp.raise_for_status()
        raw = resp.json()
    except requests.RequestException as e:
        logger.error(f"[Binance] {symbol} OHLCV 수집 실패: {e}")
        return None

    if not raw:
        logger.warning(f"[Binance] {symbol} OHLCV 데이터 없음")
        return None

    columns = [
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_asset_volume", "num_trades",
        "taker_buy_base", "taker_buy_quote", "ignore",
    ]
    df = pd.DataFrame(raw, columns=columns)

    # 숫자형 변환
    numeric_cols = ["open", "high", "low", "close", "volume",
                    "taker_buy_base", "taker_buy_quote"]
    df[numeric_cols] = df[numeric_cols].apply(pd.to_numeric)
    df["open_time"]  = pd.to_datetime(df["open_time"], unit="ms")
    df["close_time"] = pd.to_datetime(df["close_time"], unit="ms")

    return df


# ─── 기술적 레벨 계산 ─────────────────────────────────────────────────────────

def calc_levels(df: pd.DataFrame) -> dict:
    """
    OHLCV DataFrame에서 기술적 레벨을 계산합니다.

    Parameters
    ----------
    df : pd.DataFrame
        get_ohlcv() 반환값

    Returns
    -------
    dict
        {
            "current_price": float,   # 현재가 (마지막 종가)
            "high_20":       float,   # 최근 20봉 고점
            "low_20":        float,   # 최근 20봉 저점
            "fib_382":       float,   # 피보나치 0.382 되돌림
            "fib_618":       float,   # 피보나치 0.618 되돌림
            "cvd_slope":     float,   # 최근 5봉 CVD 기울기 (양수=매수우위)
            "vol_ratio":     float,   # 최근봉 거래량 / 20봉 평균
            "position_pct":  float,   # 현재가 위치 (0~1, 0=저점, 1=고점)
        }
    """
    recent = df.tail(20).copy()
    high   = recent["high"].max()
    low    = recent["low"].min()
    span   = high - low

    # CVD (Cumulative Volume Delta) 계산
    # delta = 매수 체결량 - 매도 체결량
    df = df.copy()
    df["delta"] = df["taker_buy_base"] - (df["volume"] - df["taker_buy_base"])
    df["cvd"]   = df["delta"].cumsum()

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

    Returns
    -------
    dict | None
        calc_levels() 반환값, 실패 시 None
    """
    df = get_ohlcv(ticker)
    if df is None or len(df) < 20:
        logger.warning(f"[Binance] {ticker} 데이터 부족 (< 20봉)")
        return None
    return calc_levels(df)
