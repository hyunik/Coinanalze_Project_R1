"""
modules/planner.py
──────────────────
스코어 및 기술적 레벨 기반으로 진입/익절/손절 트레이딩 플랜을 생성합니다.

진입 전략 (현재가 위치 기준):
  상단 (>70%) → 피보 0.382 되돌림 대기 후 진입
  중간 (30~70%) → 현재가 근처 진입
  하단 (<30%) → 상방 돌파 확인 후 진입 (돌파 + 0.5%)

TP/SL 계산:
  TP1 = 20봉 고점 × 0.95  (저항 직전 익절)
  TP2 = 20봉 고점 × 1.02  (고점 돌파 시나리오)
  SL  = 20봉 저점 × 0.97  (저점 하단 3%)
"""

import logging

logger = logging.getLogger(__name__)


def recommend_leverage(score: int) -> str:
    """
    점수 기반 레버리지 권장값 반환.

    Parameters
    ----------
    score : int

    Returns
    -------
    str
    """
    if score >= 80:
        return "5~10x"
    if score >= 70:
        return "3~5x"
    return "2~3x (신중)"


def _entry_strategy(levels: dict) -> tuple[float, str]:
    """
    현재가 위치에 따른 진입가 및 진입 설명 반환.

    Returns
    -------
    tuple[float, str]
        (entry_price, entry_note)
    """
    pos = levels.get("position_pct", 0.5)
    current = levels["current_price"]
    fib_382 = levels["fib_382"]
    low_20  = levels["low_20"]

    if pos > 0.70:
        # 상단: 피보 0.382 되돌림 대기
        entry = fib_382
        note  = "피보 0.382 되돌림 대기 후 진입 권장"
    elif pos < 0.30:
        # 하단: 상방 돌파 + 0.5% 확인 후 진입
        entry = round(current * 1.005, 8)
        note  = "상방 돌파 확인 후 진입 (+0.5%)"
    else:
        # 중간: 현재가 근처 진입
        entry = current
        note  = "현재가 진입 가능 구간"

    return round(entry, 8), note


def generate_plan(levels: dict, score: int) -> dict:
    """
    트레이딩 플랜을 생성합니다.

    Parameters
    ----------
    levels : dict
        binance.calc_levels() 반환값
    score : int
        scorer.score_coin() 반환 총점

    Returns
    -------
    dict
        {
            "entry":      float,
            "entry_note": str,
            "tp1":        float,
            "tp2":        float,
            "sl":         float,
            "tp1_pct":    float,   # entry 대비 TP1 수익률 (%)
            "tp2_pct":    float,   # entry 대비 TP2 수익률 (%)
            "sl_pct":     float,   # entry 대비 SL 손실률 (%)
            "rr_ratio":   float,   # R:R (TP1 기준)
            "leverage":   str,
        }
    """
    high_20 = levels["high_20"]
    low_20  = levels["low_20"]

    entry, entry_note = _entry_strategy(levels)

    tp1 = round(high_20 * 0.95, 8)   # 저항 직전 익절
    tp2 = round(high_20 * 1.02, 8)   # 고점 돌파 시나리오
    sl  = round(low_20  * 0.97, 8)   # 저점 하단 3%

    # 수익률 / 손실률 계산
    if entry > 0:
        tp1_pct = round((tp1 - entry) / entry * 100, 1)
        tp2_pct = round((tp2 - entry) / entry * 100, 1)
        sl_pct  = round((sl  - entry) / entry * 100, 1)
    else:
        tp1_pct = tp2_pct = sl_pct = 0.0

    # R:R = TP1 수익 / SL 손실 (절댓값)
    rr_ratio = (
        round(abs(tp1_pct) / abs(sl_pct), 1)
        if sl_pct != 0 else 0.0
    )

    return {
        "entry":      entry,
        "entry_note": entry_note,
        "tp1":        tp1,
        "tp2":        tp2,
        "sl":         sl,
        "tp1_pct":    tp1_pct,
        "tp2_pct":    tp2_pct,
        "sl_pct":     sl_pct,
        "rr_ratio":   rr_ratio,
        "leverage":   recommend_leverage(score),
    }
