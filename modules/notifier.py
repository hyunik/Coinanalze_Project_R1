"""
modules/notifier.py
───────────────────
분석 결과를 텔레그램 메시지로 포맷하고 전송합니다.

전송 구조:
  [메시지 1] 헤더 (스캔 요약)
  [메시지 2~6] 코인별 상세 (점수 높은 순 최대 5개)

텔레그램 메시지 1개당 4096자 제한 → 코인별 개별 메시지 분리 전송
"""

import logging
from datetime import datetime

import requests
import pytz

import config

logger = logging.getLogger(__name__)

KST = pytz.timezone("Asia/Seoul")
TELEGRAM_API_URL = (
    f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage"
)


# ─── 메시지 포맷 함수 ─────────────────────────────────────────────────────────

def _now_kst() -> str:
    """현재 KST 시각을 포맷 문자열로 반환."""
    return datetime.now(KST).strftime("%Y-%m-%d %H:%M KST")


def format_header(results: list[dict]) -> str:
    """
    스캔 요약 헤더 메시지를 생성합니다.

    Parameters
    ----------
    results : list[dict]
        scorer.score_coin() 반환값 리스트 (60점 이상만 포함)

    Returns
    -------
    str
    """
    if not results:
        return (
            "📭 온체인 찐상승 스캐너 리포트\n"
            f"📅 {_now_kst()}\n"
            "──────────────────────────\n"
            "현재 기준 충족 코인 없음\n"
            f"(시총 {config.MARKET_CAP_RANK_MIN}~{config.MARKET_CAP_RANK_MAX}위 "
            f"/ 최소 {config.MIN_SCORE}점 이상 없음)\n\n"
            "시장 전반적으로 과열 또는 모멘텀 부재 상태입니다.\n"
            "다음 스캔: 내일 오전 09:00 KST"
        )

    s_count = sum(1 for r in results if r["score"] >= 80)
    a_count = sum(1 for r in results if 70 <= r["score"] < 80)
    b_count = sum(1 for r in results if 60 <= r["score"] < 70)

    lines = [
        "📊 온체인 찐상승 스캐너 리포트",
        f"📅 {_now_kst()}",
        "──────────────────────────",
        f"🔍 스캔 범위: 시총 {config.MARKET_CAP_RANK_MIN}~{config.MARKET_CAP_RANK_MAX}위",
        f"✅ 발굴 코인: {len(results)}개 (최소 {config.MIN_SCORE}점 이상)",
    ]
    if s_count:
        lines.append(f"🔥 S급(80+): {s_count}개")
    if a_count:
        lines.append(f"⭐ A급(70~79): {a_count}개")
    if b_count:
        lines.append(f"🔸 B급(60~69): {b_count}개")
    lines.append("──────────────────────────")
    lines.append("아래 상세 분석을 확인하세요 👇")

    return "\n".join(lines)


def format_detail(rank: int, coin: dict) -> str:
    """
    코인 1개의 상세 분석 메시지를 생성합니다.

    Parameters
    ----------
    rank : int
        순위 (1부터 시작)
    coin : dict
        scorer.score_coin() 반환값에 planner.generate_plan() 결과가 추가된 dict

    Returns
    -------
    str
    """
    ticker  = coin["ticker"]
    score   = coin["score"]
    grade   = coin["grade"]
    details = coin["details"]
    plan    = coin.get("plan", {})

    def _price(v: float) -> str:
        """가격 포맷 (소수점 자릿수 자동 조정)."""
        if v >= 1000:
            return f"${v:,.2f}"
        if v >= 1:
            return f"${v:.4f}"
        return f"${v:.6f}"

    lines = [
        "━━━━━━━━━━━━━━━━━━━━",
        f"🚀 [{rank}위] {ticker} 찐상승 시그널",
        "━━━━━━━━━━━━━━━━━━━━",
        f"📅 {_now_kst()}",
        f"📊 온체인 스코어: {score}점 / 100  {grade}",
        "",
        "【온체인 분석】",
        details["oi_msg"],
        details["fr_msg"],
        details["cvd_msg"],
        details["ls_msg"],
        details["liq_msg"],
        "",
        "【핵심 지표】",
        f" - OI 변화:     {details['oi_pct']:+.1f}%",
        f" - 펀딩비:      {details['fr_pct']:+.4f}%",
        f" - L/S 비율:    {details['ls_ratio']:.2f}",
        f" - CVD 기울기:  {details['cvd_slope']:+,.0f}",
        f" - 거래량 배율: {details['vol_ratio']:.1f}x",
    ]

    if plan:
        lines += [
            "",
            "【트레이딩 플랜】",
            f" 📍 Entry : {_price(plan['entry'])}",
            f"    ↳ {plan['entry_note']}",
            f" 🎯 TP1   : {_price(plan['tp1'])}  ({plan['tp1_pct']:+.1f}%)",
            f" 🎯 TP2   : {_price(plan['tp2'])}  ({plan['tp2_pct']:+.1f}%)  ← 고점 돌파 시나리오",
            f" 🛑 SL    : {_price(plan['sl'])}  ({plan['sl_pct']:+.1f}%)",
            f" ⚖️  R:R   = 1 : {plan['rr_ratio']}",
            f" ⚡ 레버리지: {plan['leverage']}",
        ]

    lines += [
        "",
        "⚠️ 본 분석은 참고용입니다.",
        "   반드시 본인 추가 분석 후 진입하세요.",
        "━━━━━━━━━━━━━━━━━━━━",
    ]

    return "\n".join(lines)


# ─── 전송 함수 ────────────────────────────────────────────────────────────────

def send_message(text: str, parse_mode: str = "HTML") -> bool:
    """
    텔레그램 메시지를 전송합니다.

    Parameters
    ----------
    text : str
        전송할 메시지 (최대 4096자)
    parse_mode : str
        "HTML" | "Markdown" | "" (기본값: "HTML")

    Returns
    -------
    bool
        전송 성공 여부
    """
    payload: dict = {
        "chat_id": config.TELEGRAM_CHAT_ID,
        "text":    text,
    }
    if parse_mode:
        payload["parse_mode"] = parse_mode

    try:
        resp = requests.post(
            TELEGRAM_API_URL,
            json=payload,
            timeout=10,
        )
        resp.raise_for_status()
        logger.debug(f"[Telegram] 메시지 전송 성공 ({len(text)}자)")
        return True
    except requests.RequestException as e:
        logger.error(f"[Telegram] 메시지 전송 실패: {e}")
        return False


def send_report(results: list[dict]) -> None:
    """
    전체 리포트를 텔레그램으로 전송합니다.

    Parameters
    ----------
    results : list[dict]
        scorer + planner 결과가 합쳐진 코인 데이터 리스트
        (점수 내림차순 정렬 상태)
    """
    # 1. 헤더 메시지 전송
    header = format_header(results)
    send_message(header, parse_mode="")

    if not results:
        return

    # 2. 상세 메시지 전송 (최대 MAX_ALERTS개)
    top_coins = results[: config.MAX_ALERTS]
    for rank, coin in enumerate(top_coins, start=1):
        detail = format_detail(rank, coin)
        send_message(detail, parse_mode="")
        logger.info(f"[Telegram] [{rank}위] {coin['ticker']} 상세 전송 완료")
