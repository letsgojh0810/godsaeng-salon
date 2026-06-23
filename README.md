# 삶은 갓생 🥔

> AI가 인증샷을 직접 보고 판단하는 갓생 습관 코치

카카오 PlayMCP 기반 개인 생산성 AI 서비스.  
목표를 설정하고, 사진으로 증명하고, AI가 달성 여부를 판별합니다.

---

## 서비스 컨셉

**갓생(God生)** — 신처럼 부지런하고 알차게 사는 삶. 요즘 MZ세대가 추구하는 자기계발 라이프스타일.

문제는 "했다"고 말하기는 쉽다는 것.  
삶은 갓생은 **인증샷을 AI가 직접 분석해서** 진짜 했는지 판별합니다.

- 헬스장 사진이 아닌 소파 사진을 올리면? ❌ 탈락
- 치킨을 찍어서 다이어트 인증이라고? ❌ 탈락
- 침대에 누워서 미라클모닝 인증? ❌ 탈락

**말이 아닌 사진으로 증명하는 갓생.**

---

## 서비스 개요

| 항목 | 내용 |
|------|------|
| 서비스명 | 삶은 갓생 |
| 플랫폼 | 카카오 PlayMCP (ChatGPT for Kakao) |
| AI 모델 | Google Gemini 2.5 Flash (Vision + Text) |
| 인프라 | Kakao Cloud KC — Git 소스 빌드 |
| 프로토콜 | MCP (Model Context Protocol) streamable-http |

---

## 핵심 기능

### 📸 AI 인증샷 검증
카테고리별로 다른 기준을 적용합니다.

| 카테고리 | 통과 기준 | 실패 예시 |
|----------|-----------|-----------|
| 💪 운동 | 헬스장 기구, 운동복, 땀, 야외 러닝 | 일상 사진, 음식 사진 |
| 🥗 다이어트 | 샐러드, 닭가슴살, 저칼로리 식단 | 피자, 치킨, 라면 |
| 📚 공부 | 책, 노트 필기, 강의 화면, 독서실 환경 | 스마트폰만 보이는 사진 |
| 🎯 취미 | 악기 연주, 그림, 요리 결과물 | 관련 없는 일상 사진 |
| 🌅 미라클모닝 | 아침 거울 셀카, 기상 직후 모습 | 침대에 누워있는 사진 |

- 목표 일치 + 60점 이상 → **달성 인정**
- 미달 시 감지된 활동 명시 + 재도전 유도

### 📊 진행도 추적
- 인증 완료 일수 / 목표 일수 프로그레스바
- 시도 횟수 vs 실제 달성 횟수 구분
- 30일 챌린지 기반 장기 습관 형성

### 📅 주간 리포트
- 요일별 달성 현황 (✅❌⬜)
- 주간 목표 달성 여부 판정
- 미달 목표 카테고리별 맞춤 격려
- 완벽 달성 주 축하 메시지

### 🗓️ 스마트 리마인더
- 목표 설정 시 7일 · 14일 · 완료일 마일스톤 알림 자동 생성
- 매일 반복 알림 설정 (미라클모닝 06:00, 운동 저녁 등)
- 대화 시작 시 오늘 알림 + 연속 달성 스트릭 자동 표시
- 미체크인 목표 당일 알림

### 🤖 AI 일일 플랜
- 설정된 목표를 반영한 시간대별 갓생 일정 생성
- Gemini가 현실적인 루틴으로 구성

---

## MCP Tools

| Tool | 기능 |
|------|------|
| `set_goal` | 목표 설정 (카테고리 / 기간 / 주간 목표 횟수) |
| `check_in` | 인증샷 AI 분석 및 달성 판정 |
| `get_progress` | 전체 목표 진행도 조회 |
| `get_weekly_report` | 주간 달성 리포트 |
| `generate_daily_plan` | 오늘 갓생 플랜 생성 |
| `set_reminder` | 단발 리마인더 설정 |
| `set_daily_reminder` | N일 반복 리마인더 설정 |
| `check_reminders` | 오늘 알림 + 스트릭 + 미체크인 목표 확인 |
| `setup_my_profile` | 프로필 및 활동 이력 불러오기 |

---

## 기술 스택

```
MCP Server   FastMCP (Python 3.11) — streamable-http transport
AI           Google Gemini 2.5 Flash (Vision + Text Generation)
Database     SQLite (godsaeng.db)
HTTP         httpx — 인증샷 URL 다운로드
Infra        Kakao Cloud KC — Docker / Git 소스 빌드
```

---

## 로컬 실행

```bash
# 의존성 설치
pip install -r requirements.txt

# 환경변수 설정
cp .env.example .env
# .env에 GOOGLE_API_KEY 입력

# 서버 실행
python server.py
```

MCP 엔드포인트: `http://localhost:8000/mcp`

---

## 배포 구조

```
GitHub (private) ──push──▶ Kakao Cloud KC
                            Git 소스 빌드 → Docker 이미지
                            ↓
                     PlayMCP 등록 (streamable-http)
                            ↓
                     ChatGPT for Kakao 채팅에서 툴 호출
```

---

## 서비스 방향

### 현재 (프로토타입)
- PlayMCP 단일 채팅 인터페이스
- SQLite 기반 단일 사용자 구조
- 9개 MCP 툴로 핵심 기능 구현

### 향후 계획
- **백엔드 분리** — FastAPI + PostgreSQL, 멀티 유저 지원
- **카카오 OAuth** — user_id 자동화 (현재는 기본값 사용)
- **카카오 채널 알림톡** — 실시간 푸시 알림
- **웹 대시보드** — 캘린더 히트맵, 사진 갤러리, 통계
- **AI 코치 고도화** — 패턴 분석, 목표 달성 예측

---

## 서비스명 유래

**삶은 갓생** = 삶은 감자 + 갓생

> 삶은 감자처럼 소박하지만, 꾸준히 갓생을 살아가는 사람들을 위한 서비스.  
> "삶다(boil)"와 "삶다(live)"의 중의적 표현.
