"""
main.py
───────
온체인 찐상승 스캐너 — 진입점 및 스케줄러

실행 방법:
    python main.py

스케줄:
    매일 09:00 KST / 21:00 KST 자동 실행

파이프라인 (ARCHITECTURE.md 6단계):
    STEP 1: CoinGecko → 시총 200~350위 코인 목록 수집
    STEP 2: Binance   → 선물 미상장 종목 필터링
    STEP 3: Coinalyze → 온체인 데이터 수집 (Rate Limit 처리)
    STEP 4: Binance   → OHLCV + 기술적 레벨 계산
    STEP 5: Scorer    → 100점 스코어링 (60점 미만 제외)
    STEP 6: Planner   → 트레이딩 플랜 생성
    STEP 7: Notifier  → 텔레그램 전송
"""

import logging
import logging.handlers
import os
import sys
import time

import pytz
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

import config
from modules import coingecko, coinalyze, binance, scorer, planner, notifier

# ─── 로깅 설정 ────────────────────────────────────────────────────────────────

def setup_logging() -> None:
    """로그 파일 + 콘솔 동시 출력 설정."""
    os.makedirs(os.path.dirname(config.LOG_FILE), exist_ok=True)

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s — %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # 파일 핸들러 (일별 롤오버, 최대 7일 보관)
    file_handler = logging.handlers.TimedRotatingFileHandler(
        config.LOG_FILE,
        when="midnight",
        backupCount=7,
        encoding="utf-8",
    )
    file_handler.setFormatter(fmt)

    # 콘솔 핸들러
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(fmt)

    root = logging.getLogger()
    root.setLevel(getattr(logging, config.LOG_LEVEL, logging.INFO))
    root.addHandler(file_handler)
    root.addHandler(console_handler)


logger = logging.getLogger(__name__)


# ─── 메인 파이프라인 ──────────────────────────────────────────────────────────

def run_scan() -> None:
    """
    전체 스캔 파이프라인을 실행합니다.
    APScheduler 또는 직접 호출 모두 지원합니다.
    """
    logger.info("=" * 60)
    logger.info("온체인 찐상승 스캐너 스캔 시작")
    logger.info("=" * 60)
    start_time = time.monotonic()

    # ── STEP 1: 대상 코인 수집 (CoinGecko) ──────────────────────────────────
    logger.info("[STEP 1] CoinGecko 코인 목록 수집...")
    try:
        coins = coingecko.get_target_coins()
    except Exception as e:
        logger.error(f"[STEP 1] CoinGecko 수집 실패: {e}")
        notifier.send_message(f"⚠️ 스캐너 오류 (STEP 1): {e}", parse_mode="")
        return

    if not coins:
        logger.warning("[STEP 1] 수집된 코인 없음 — 스캔 중단")
        return

    logger.info(f"[STEP 1] {len(coins)}개 코인 수집 완료")

    # 거래량 하위 50% 제거 (API_GUIDE 방안 B)
    coins = coingecko.filter_by_volume(coins, percentile=0.5)
    logger.info(f"[STEP 1] 거래량 필터 후 {len(coins)}개 코인")

    # ── STEP 2: Binance 선물 미상장 종목 필터링 ──────────────────────────────
    logger.info("[STEP 2] Binance 선물 상장 여부 필터링...")
    try:
        listed_tickers = [
            c for c in coins
            if binance.is_listed(c["symbol"].upper())
        ]
    except Exception as e:
        logger.error(f"[STEP 2] Binance 필터링 실패: {e}")
        listed_tickers = coins  # 실패 시 전체 진행

    logger.info(
        f"[STEP 2] Binance 선물 상장 코인: {len(listed_tickers)}개"
    )

    if not listed_tickers:
        logger.warning("[STEP 2] 상장 코인 없음 — 스캔 중단")
        return

    # 심볼 목록 준비
    tickers = [c["symbol"].upper() for c in listed_tickers]
    coinalyze_symbols = [coinalyze.build_symbol(t) for t in tickers]

    # ── STEP 3: 온체인 데이터 수집 (Coinalyze, Rate Limit 처리) ─────────────
    logger.info(
        f"[STEP 3] Coinalyze 온체인 데이터 수집 ({len(coinalyze_symbols)}개 심볼)..."
    )
    try:
        metrics = coinalyze.get_all_metrics(coinalyze_symbols)
    except Exception as e:
        logger.error(f"[STEP 3] Coinalyze 수집 실패: {e}")
        notifier.send_message(f"⚠️ 스캐너 오류 (STEP 3): {e}", parse_mode="")
        return

    # ── STEP 4: 기술적 레벨 계산 (Binance OHLCV) ────────────────────────────
    logger.info("[STEP 4] Binance OHLCV + 기술적 레벨 계산...")
    levels_map: dict[str, dict] = {}
    for ticker in tickers:
        lvl = binance.get_levels(ticker)
        if lvl:
            levels_map[ticker] = lvl

    logger.info(f"[STEP 4] 기술적 레벨 계산 완료: {len(levels_map)}개")

    # ── STEP 5: 스코어링 ─────────────────────────────────────────────────────
    logger.info("[STEP 5] 스코어링 엔진 실행...")
    scored_coins: list[dict] = []

    for ticker in tickers:
        sym = coinalyze.build_symbol(ticker)
        lvl = levels_map.get(ticker)

        if lvl is None:
            continue  # 기술적 데이터 없으면 스킵

        oi_hist  = metrics.get(coinalyze.ENDPOINT_OI,          {}).get(sym, [])
        fr_hist  = metrics.get(coinalyze.ENDPOINT_FUNDING,      {}).get(sym, [])
        ls_hist  = metrics.get(coinalyze.ENDPOINT_LS_RATIO,     {}).get(sym, [])
        liq_hist = metrics.get(coinalyze.ENDPOINT_LIQUIDATION,  {}).get(sym, [])

        result = scorer.score_coin(
            ticker=ticker,
            oi_hist=oi_hist,
            fr_hist=fr_hist,
            ls_hist=ls_hist,
            liq_hist=liq_hist,
            levels=lvl,
        )

        if result is not None:
            scored_coins.append(result)

    # 점수 내림차순 정렬
    scored_coins.sort(key=lambda x: x["score"], reverse=True)
    logger.info(
        f"[STEP 5] 스코어링 완료: {len(scored_coins)}개 코인 통과 "
        f"(최소 {config.MIN_SCORE}점)"
    )

    # ── STEP 6: 트레이딩 플랜 생성 ───────────────────────────────────────────
    logger.info("[STEP 6] 트레이딩 플랜 생성...")
    for coin in scored_coins:
        coin["plan"] = planner.generate_plan(coin["levels"], coin["score"])

    # ── STEP 7: 텔레그램 전송 ────────────────────────────────────────────────
    logger.info("[STEP 7] 텔레그램 리포트 전송...")
    notifier.send_report(scored_coins)

    elapsed = time.monotonic() - start_time
    logger.info(f"스캔 완료 — 소요 시간: {elapsed:.1f}초")
    logger.info("=" * 60)


# ─── 스케줄러 설정 ────────────────────────────────────────────────────────────

def start_scheduler() -> None:
    """
    APScheduler를 사용하여 KST 09:00 / 21:00 스케줄을 등록합니다.
    """
    kst = pytz.timezone("Asia/Seoul")
    scheduler = BlockingScheduler(timezone=kst)

    for hour in config.SCHEDULE_HOURS_KST:
        scheduler.add_job(
            run_scan,
            trigger=CronTrigger(hour=hour, minute=0, timezone=kst),
            id=f"scan_{hour:02d}00_kst",
            name=f"온체인 스캔 {hour:02d}:00 KST",
            misfire_grace_time=300,   # 5분 이내 지연 허용
            coalesce=True,
        )
        logger.info(f"[Scheduler] {hour:02d}:00 KST 스캔 등록 완료")

    logger.info("스케줄러 시작 — Ctrl+C로 종료")
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("스케줄러 종료")


# ─── 진입점 ───────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    setup_logging()

    # 커맨드라인 인자 처리
    if len(sys.argv) > 1 and sys.argv[1] == "--run-now":
        # 즉시 1회 실행 (테스트용)
        logger.info("--run-now 플래그 감지 → 즉시 1회 실행")
        run_scan()
    else:
        # 스케줄러 모드 (기본)
        logger.info("스케줄러 모드로 시작합니다.")
        logger.info(
            f"실행 시각: {config.SCHEDULE_HOURS_KST} KST"
        )
        start_scheduler()
