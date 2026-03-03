# 📡 온체인 찐상승 스캐너 (On-Chain Bull Signal Scanner)

시총 200~350위 코인을 대상으로 온체인 데이터를 분석하여,
구조적 상승 가능성이 높은 코인을 자동 탐지하고 텔레그램으로 알림을 전송하는 시스템입니다.

---

## 🎯 목적

- Coinalyze API 기반 온체인 데이터(OI, 펀딩비, 청산, L/S 비율) 자동 수집
- 100점 스코어링 엔진으로 "찐상승" 후보 필터링
- 진입가 / 익절 / 손절 레벨 자동 산출
- 매일 오전 9시, 오후 9시(KST) 텔레그램 자동 발송

---

## 🏗️ 기술 스택

| 역할 | 도구 |
|------|------|
| 언어 | Python 3.11+ |
| 시총 순위 | CoinGecko API (무료) |
| 온체인 데이터 | Coinalyze API |
| 가격/OHLCV | Binance Futures REST API |
| 스케줄링 | APScheduler |
| 알림 | Telegram Bot API |
| Rate Limit 처리 | tenacity (재시도) + 슬라이딩 윈도우 카운터 |

---

## ⚡ 퀵스타트

### 1. 환경 설정
```bash
pip install requests pandas numpy apscheduler pytz tenacity python-telegram-bot
```

### 2. config.py 작성
```python
COINALYZE_API_KEY  = "your_key"
TELEGRAM_BOT_TOKEN = "your_bot_token"
TELEGRAM_CHAT_ID   = "your_chat_id"

MARKET_CAP_RANK_MIN = 150   # 여유 범위로 150~400 권장
MARKET_CAP_RANK_MAX = 400
MIN_SCORE           = 60    # 이 점수 이상만 알림
TIMEFRAME           = "4h"  # Binance OHLCV 타임프레임
```

### 3. 실행
```bash
python main.py
```

---

## 📅 알림 스케줄

| 시각 (KST) | UTC | 동작 |
|-----------|-----|------|
| 오전 09:00 | 00:00 | 전체 스캔 + 텔레그램 발송 |
| 오후 09:00 | 12:00 | 전체 스캔 + 텔레그램 발송 |

---

## ⚠️ Coinalyze 무료 플랜 제한

- **Rate Limit**: 분당 40회 API 호출
- 초과 시 `HTTP 429` + `Retry-After` 헤더 반환
- 본 시스템은 슬라이딩 윈도우 + tenacity 재시도로 자동 처리
- 상세 내용 → [API_GUIDE.md](./API_GUIDE.md)

---

## 📂 프로젝트 구조
```
onchain-scanner/
├── main.py              # 진입점, 스케줄러 실행
├── config.py            # 설정값 모음
├── modules/
│   ├── coingecko.py     # 시총 순위 코인 목록 수집
│   ├── coinalyze.py     # 온체인 데이터 수집 + Rate Limit 처리
│   ├── binance.py       # OHLCV 수집 + 기술적 레벨 계산
│   ├── scorer.py        # 100점 스코어링 엔진
│   ├── planner.py       # 진입/익절/손절 전략 생성
│   └── notifier.py      # 텔레그램 메시지 포맷 + 전송
└── logs/
    └── scanner.log      # 실행 로그
```