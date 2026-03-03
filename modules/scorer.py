"""
modules/scorer.py
─────────────────
온체인 데이터와 기술적 레벨을 종합하여 100점 스코어를 산출합니다.

스코어링 항목:
  ① OI 변화율      25점  — 신규 롱 유입 확인
  ② 펀딩비         25점  — 과열 없는 중립 구간
  ③ CVD + 거래량   20점  — 매수 압력 강도
  ④ L/S 비율       15점  — 숏 우위 (스퀴즈 잠재력)
  ⑤ 청산 패턴      15점  — 롱 청산 대량 소화 (바닥 신호)

등급:
  80~100 → 🔥 S급 (강력 매수 시그널)
  70~79  → ⭐ A급 (매수 우위)
  60~69  → 🔸 B급 (조건부 관심)
  0~59   → ⚪ 제외 (알림 미발송)
"""

import logging

logger = logging.getLogger(__name__)


# ─── 개별 항목 채점 함수 ──────────────────────────────────────────────────────

def score_oi(oi_hist: list[dict]) -> tuple[int, str]:
    """
    OI 변화율 점수 (25점 만점).

    Parameters
    ----------
    oi_hist : list[dict]
        [{"t": timestamp, "v": oi_usd}, ...]

    Returns
    -------
    tuple[int, str]
        (점수, 설명 문자열)
    """
    if len(oi_hist) < 2:
        return 0, "OI 데이터 없음"

    prev = oi_hist[-2].get("v", 0) or 0
    curr = oi_hist[-1].get("v", 0) or 0

    if prev == 0:
        return 0, "OI 이전 데이터 없음"

    pct = (curr - prev) / prev * 100

    if pct >= 15:
        return 25, f"✅ OI +{pct:.1f}% 급증 (신규 롱 공격 유입)"
    if pct >= 7:
        return 18, f"✅ OI +{pct:.1f}% 증가 (매수 관심 상승)"
    if pct >= 2:
        return 10, f"🔸 OI +{pct:.1f}% 소폭 증가"
    if pct < -5:
        return 3,  f"❌ OI {pct:.1f}% 감소 (포지션 이탈)"
    return 7, f"OI {pct:.1f}% 보합"


def score_funding(fr_hist: list[dict]) -> tuple[int, str]:
    """
    펀딩비 점수 (25점 만점).

    Parameters
    ----------
    fr_hist : list[dict]
        [{"t": timestamp, "v": funding_rate}, ...]
        v 단위: 소수 (0.01% = 0.0001)

    Returns
    -------
    tuple[int, str]
    """
    if not fr_hist:
        return 0, "펀딩비 데이터 없음"

    fr = fr_hist[-1].get("v", 0) or 0
    fr_pct = fr * 100  # 퍼센트 변환

    # 중립 구간: -0.005% ~ +0.005% (소수로 -0.00005 ~ +0.00005)
    if -0.00005 <= fr <= 0.00005:
        return 25, f"✅ 펀딩비 {fr_pct:+.4f}% (과열 없음, 최적 구간)"
    if -0.0002 <= fr < -0.00005:
        return 20, f"✅ 펀딩비 {fr_pct:+.4f}% (음수 → 역발상 매수 구간)"
    if 0.00005 < fr <= 0.0002:
        return 15, f"🔸 펀딩비 {fr_pct:+.4f}% (약 과열)"
    if fr > 0.0002:
        return 5,  f"❌ 펀딩비 {fr_pct:+.4f}% (과열 — 롱 청산 위험)"
    if fr < -0.0002:
        return 10, f"🔸 펀딩비 {fr_pct:+.4f}% (극단적 음수)"
    return 10, f"펀딩비 {fr_pct:+.4f}%"


def score_cvd_volume(cvd_slope: float, vol_ratio: float) -> tuple[int, str]:
    """
    CVD + 거래량 점수 (20점 만점).

    Parameters
    ----------
    cvd_slope : float
        최근 5봉 CVD 기울기 (양수 = 매수 우위)
    vol_ratio : float
        최근봉 거래량 / 20봉 평균

    Returns
    -------
    tuple[int, str]
    """
    cvd_up  = cvd_slope > 0
    vol_ok  = vol_ratio >= 1.2

    if cvd_up and vol_ratio >= 1.5:
        return 20, f"✅ CVD 상승 + 거래량 {vol_ratio:.1f}배 (매수 압력 강함)"
    if cvd_up and vol_ok:
        return 15, f"✅ CVD 상승 + 거래량 {vol_ratio:.1f}배"
    if cvd_up:
        return 10, f"🔸 CVD 상승 추세 확인 (거래량 {vol_ratio:.1f}배 — 보통)"
    if vol_ok:
        return 8,  f"🔸 거래량 {vol_ratio:.1f}배 (CVD 하락 주의)"
    return 3, f"❌ CVD 하락 + 거래량 {vol_ratio:.1f}배 (매수 압력 약함)"


def score_ls_ratio(ls_hist: list[dict]) -> tuple[int, str]:
    """
    L/S 비율 점수 (15점 만점).

    Parameters
    ----------
    ls_hist : list[dict]
        [{"t": timestamp, "v": ls_ratio}, ...]
        v: 1.0 = 50:50, <1.0 = 숏 우위

    Returns
    -------
    tuple[int, str]
    """
    if not ls_hist:
        return 0, "L/S 데이터 없음"

    ls = ls_hist[-1].get("v", 1.0) or 1.0

    if ls < 0.80:
        return 15, f"✅ L/S {ls:.2f} (숏 우위 → 숏스퀴즈 잠재력 높음)"
    if ls < 0.90:
        return 10, f"🔸 L/S {ls:.2f} (약 숏 우위)"
    if ls < 1.00:
        return 7,  f"🔸 L/S {ls:.2f} (균형에 가까움)"
    if ls < 1.20:
        return 5,  f"L/S {ls:.2f} (롱 우위 — 중립)"
    return 2, f"❌ L/S {ls:.2f} (롱 과열 — 스퀴즈 위험)"


def score_liquidation(liq_hist: list[dict]) -> tuple[int, str]:
    """
    청산 패턴 점수 (15점 만점).

    롱 청산 대량 소화 완료 = 바닥 신호 (매수 기회)

    Parameters
    ----------
    liq_hist : list[dict]
        [{"t": timestamp, "l": long_liq_usd, "s": short_liq_usd}, ...]

    Returns
    -------
    tuple[int, str]
    """
    if not liq_hist:
        return 0, "청산 데이터 없음"

    # 최근 3봉 합산
    recent = liq_hist[-3:]
    long_liq  = sum(item.get("l", 0) or 0 for item in recent)
    short_liq = sum(item.get("s", 0) or 0 for item in recent)

    def fmt_usd(v: float) -> str:
        if v >= 1_000_000:
            return f"${v / 1_000_000:.1f}M"
        if v >= 1_000:
            return f"${v / 1_000:.0f}K"
        return f"${v:.0f}"

    # 롱 청산이 숏 청산의 2배 이상 → 바닥 확인
    if long_liq > 0 and long_liq >= short_liq * 2:
        return 15, (
            f"✅ 롱 청산 {fmt_usd(long_liq)} 대량 소화 완료 (바닥 확인)"
        )
    if long_liq > 0 and long_liq >= short_liq * 1.3:
        return 10, f"🔸 롱 청산 {fmt_usd(long_liq)} 우세 (청산 패턴 보통)"
    if short_liq > long_liq * 2:
        return 3,  f"❌ 숏 청산 {fmt_usd(short_liq)} 우세 (추가 하락 주의)"
    return 7, f"청산 패턴 보통 (롱 {fmt_usd(long_liq)} / 숏 {fmt_usd(short_liq)})"


# ─── 등급 판정 ────────────────────────────────────────────────────────────────

def get_grade(score: int) -> str:
    """점수 → 등급 문자열 반환."""
    if score >= 80:
        return "🔥 S급 (강력)"
    if score >= 70:
        return "⭐ A급 (우세)"
    if score >= 60:
        return "🔸 B급 (조건부)"
    return "⚪ 제외"


# ─── 종합 채점 ────────────────────────────────────────────────────────────────

def score_coin(
    ticker: str,
    oi_hist:  list[dict],
    fr_hist:  list[dict],
    ls_hist:  list[dict],
    liq_hist: list[dict],
    levels:   dict,
) -> dict | None:
    """
    코인 1개에 대한 종합 스코어를 산출합니다.

    Parameters
    ----------
    ticker : str
    oi_hist : list[dict]    — open-interest history
    fr_hist : list[dict]    — funding-rate history
    ls_hist : list[dict]    — long-short-ratio history
    liq_hist : list[dict]   — liquidation-history history
    levels : dict           — binance.calc_levels() 반환값

    Returns
    -------
    dict | None
        60점 미만이면 None 반환 (알림 제외)
        {
            "ticker":       str,
            "score":        int,
            "grade":        str,
            "details":      dict,   # 항목별 점수 + 설명
            "levels":       dict,   # 기술적 레벨
        }
    """
    oi_score,  oi_msg  = score_oi(oi_hist)
    fr_score,  fr_msg  = score_funding(fr_hist)
    cvd_score, cvd_msg = score_cvd_volume(
        levels.get("cvd_slope", 0),
        levels.get("vol_ratio", 0),
    )
    ls_score,  ls_msg  = score_ls_ratio(ls_hist)
    liq_score, liq_msg = score_liquidation(liq_hist)

    total = oi_score + fr_score + cvd_score + ls_score + liq_score

    logger.debug(
        f"[Scorer] {ticker}: OI={oi_score} FR={fr_score} "
        f"CVD={cvd_score} LS={ls_score} LIQ={liq_score} → {total}점"
    )

    import config as _cfg
    if total < _cfg.MIN_SCORE:
        return None

    # 펀딩비 퍼센트 표시용
    fr_val = fr_hist[-1].get("v", 0) if fr_hist else 0
    ls_val = ls_hist[-1].get("v", 1.0) if ls_hist else 1.0

    return {
        "ticker": ticker,
        "score":  total,
        "grade":  get_grade(total),
        "details": {
            "oi_score":   oi_score,   "oi_msg":   oi_msg,
            "fr_score":   fr_score,   "fr_msg":   fr_msg,
            "cvd_score":  cvd_score,  "cvd_msg":  cvd_msg,
            "ls_score":   ls_score,   "ls_msg":   ls_msg,
            "liq_score":  liq_score,  "liq_msg":  liq_msg,
            # 핵심 지표 원값
            "oi_pct":     round(
                ((oi_hist[-1].get("v", 0) - oi_hist[-2].get("v", 0))
                 / oi_hist[-2].get("v", 1) * 100)
                if len(oi_hist) >= 2 and oi_hist[-2].get("v") else 0, 1
            ),
            "fr_pct":     round(fr_val * 100, 4),
            "ls_ratio":   round(ls_val, 2),
            "cvd_slope":  levels.get("cvd_slope", 0),
            "vol_ratio":  levels.get("vol_ratio", 0),
        },
        "levels": levels,
    }
