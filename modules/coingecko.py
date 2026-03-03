"""
modules/coingecko.py
────────────────────
CoinGecko 무료 API에서 시총 순위 기준 코인 목록을 수집합니다.

무료 플랜 제한: 분당 10~30회 → 페이지 간 sleep(1.5) 필수
"""

import time
import logging

import requests

import config

logger = logging.getLogger(__name__)

COINGECKO_MARKETS_URL = "https://api.coingecko.com/api/v3/coins/markets"


def get_target_coins(
    rank_min: int | None = None,
    rank_max: int | None = None,
) -> list[dict]:
    """
    시총 순위 rank_min ~ rank_max 범위 코인 목록을 반환합니다.

    Parameters
    ----------
    rank_min : int, optional
        시총 순위 하한 (기본값: config.MARKET_CAP_RANK_MIN)
    rank_max : int, optional
        시총 순위 상한 (기본값: config.MARKET_CAP_RANK_MAX)

    Returns
    -------
    list[dict]
        [
            {
                "symbol": "wif",
                "name": "dogwifhat",
                "market_cap_rank": 210,
                "total_volume": 123456789,
                ...
            },
            ...
        ]
    """
    rank_min = rank_min if rank_min is not None else config.MARKET_CAP_RANK_MIN
    rank_max = rank_max if rank_max is not None else config.MARKET_CAP_RANK_MAX

    all_coins: list[dict] = []
    per_page = 250  # CoinGecko 최대값

    # rank_max를 커버하기 위해 필요한 페이지 수 계산
    total_pages = (rank_max // per_page) + 2

    for page in range(1, total_pages):
        logger.info(f"[CoinGecko] 페이지 {page}/{total_pages - 1} 수집 중...")
        params = {
            "vs_currency": "usd",
            "order": "market_cap_desc",
            "per_page": per_page,
            "page": page,
            "sparkline": "false",
        }
        try:
            resp = requests.get(
                COINGECKO_MARKETS_URL,
                params=params,
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as e:
            logger.error(f"[CoinGecko] 페이지 {page} 수집 실패: {e}")
            break

        if not data:
            break  # 더 이상 데이터 없음

        all_coins.extend(data)

        # 이미 rank_max를 초과한 코인까지 수집했으면 조기 종료
        last_rank = data[-1].get("market_cap_rank") or 9999
        if last_rank > rank_max:
            break

        # CoinGecko 무료 플랜 속도 제한 대응 (분당 10~30회)
        time.sleep(1.5)

    # rank_min ~ rank_max 필터링
    filtered = [
        c for c in all_coins
        if rank_min <= (c.get("market_cap_rank") or 9999) <= rank_max
    ]

    logger.info(
        f"[CoinGecko] 시총 {rank_min}~{rank_max}위 코인 {len(filtered)}개 수집 완료"
    )
    return filtered


def filter_by_volume(coins: list[dict], percentile: float = 0.5) -> list[dict]:
    """
    거래량 하위 percentile 비율 코인을 제거합니다. (API_GUIDE 방안 B)

    Parameters
    ----------
    coins : list[dict]
        get_target_coins() 반환값
    percentile : float
        제거할 하위 비율 (기본값: 0.5 → 하위 50% 제거)

    Returns
    -------
    list[dict]
        거래량 상위 (1 - percentile) 코인 목록
    """
    if not coins:
        return coins

    volumes = sorted(
        [c.get("total_volume") or 0 for c in coins]
    )
    threshold_idx = int(len(volumes) * percentile)
    threshold = volumes[threshold_idx]

    filtered = [c for c in coins if (c.get("total_volume") or 0) >= threshold]
    logger.info(
        f"[CoinGecko] 거래량 필터 적용: {len(coins)}개 → {len(filtered)}개"
    )
    return filtered
