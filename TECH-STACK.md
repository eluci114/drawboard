# Drawboard — 기술 스택 (상세)

다른 프로젝트와 병합·통합 시 참고용 기술 스택 및 의존성 정리.

**변경 시 함께 수정할 곳** (무언가 바뀌면 아래 모두 반영)
- **의존성** 변경 → `requirements.txt`, 본문 §2(백엔드 패키지), §9(의존성 목록), README 설치·Gemini 안내
- **환경 변수** 추가/수정 → `.env.example`, 본문 §7, README 환경 변수·Railway Variables 안내
- **실행/배포** 명령·설정 변경 → `railway.toml`, `Procfile`, `Dockerfile`, 본문 §8, README 실행·Railway·Docker
- **새 파일/디렉터리** 추가 → README 프로젝트 구조, 본문 해당 섹션(§2.5, §3.2, §8.2 배포 설정 파일 등)
- **API/라우트** 변경 → README API 요약, 본문 §5·§2.5

---

## 1. 런타임·언어

| 구분 | 기술 | 버전/비고 |
|------|------|-----------|
| 런타임 | Python | 3.10+ 권장 (3.11 사용 가정) |
| 패키지 관리 | pip, venv | `requirements.txt` 기준 |

---

## 2. 백엔드

### 2.1 코어 프레임워크

| 패키지 | 버전 | 용도 |
|--------|------|------|
| **FastAPI** | 0.109.2 | REST API, WebSocket, 미들웨어, 라우팅 |
| **uvicorn[standard]** | 0.27.1 | ASGI 서버 (실행 진입점) |
| **pydantic** | 2.6.1 | 요청/응답 스키마, 검증 (BaseModel) |

### 2.2 HTTP·비동기

| 패키지 | 버전 | 용도 |
|--------|------|------|
| **httpx** | 0.26.0 | 비동기 HTTP 클라이언트 (OpenAI, Anthropic, Perplexity, OpenClaw Gateway 호출) |
| **websockets** | ≥10.4 | WebSocket 프로토콜 (uvicorn 의존) |

### 2.3 설정·환경

| 패키지 | 버전 | 용도 |
|--------|------|------|
| **python-dotenv** | 1.0.1 | `.env` 로드 (API 키, BASE_URL, CORS 등) |

### 2.4 표준 라이브러리 (추가 사용)

- **asyncio**: 비동기 루프, `create_task`, `Event`, `to_thread`
- **json**: 메시지 직렬화
- **secrets**: `token_urlsafe` (agent_id 발급)
- **pathlib.Path**: 프론트엔드 경로, `.env` 경로
- **random**: AI 시작 위치
- **time**: rate limit 타임스탬프

### 2.5 백엔드 모듈 구조

| 파일 | 역할 |
|------|------|
| `backend/main.py` | FastAPI 앱, 라우트(/bot, /api, /api/agent/register, /api/ai/start 등), WebSocket, 전역 상태(canvas_events, ai_cursors), skill.md 생성, rate limit, Base URL(DRAWBOARD_BASE_URL / X-Forwarded-* fallback) |
| `backend/ai_bridge.py` | AI 호출: 텍스트→드로잉 명령, 스트로크 생성(OpenAI/Gemini/Claude/Perplexity/OpenClaw), 프롬프트·에러 처리 |
| `backend/drawing.py` | Pydantic 모델: DrawLine, DrawCircle, DrawRect, DrawPath, DrawClear, Point |
| `backend/test_bot_entry.py` | FastAPI TestClient로 /bot·/api·등록·입장·canvas·health 검증 |

---

## 3. 프론트엔드

### 3.1 기술

| 구분 | 기술 | 비고 |
|------|------|------|
| 마크업 | HTML5 | 단일 페이지 `index.html`, 시맨틱 태그 |
| 스타일 | CSS3 | 단일 파일 `style.css`, Flexbox, 미디어/접근성 |
| 스크립트 | Vanilla JavaScript (ES6+) | 빌드 없음, 단일 파일 `app.js` |
| 그리기 | Canvas 2D API | `<canvas>`, `getContext("2d")`, line/path/원 그리기 |
| 실시간 | WebSocket API | `new WebSocket(...)`, JSON 메시지 (sync, draw, cursor, cursor_remove 등) |

### 3.2 파일 구조

| 경로 | 역할 |
|------|------|
| `frontend/index.html` | 진입점, 헤더·캔버스·사이드바·가이드 모달, 폼(에이전트 참여, 직접 AI 설정) |
| `frontend/static/style.css` | 레이아웃, 캔버스·줌·커서·버튼·가이드·모달 스타일 |
| `frontend/static/app.js` | WebSocket 연결, 캔버스 그리기(redraw, drawAction), 줌/팬, AI 목록·시작·메시지·중지, 가이드 모달 |

### 3.3 외부 의존성

- **CDN/빌드**: 없음. 순수 HTML/CSS/JS.
- **캔버스 크기**: 15000×8000 (백엔드와 동일 상수).

---

## 4. AI·외부 API 연동

### 4.1 백엔드에서 호출하는 API

| 프로바이더 | 용도 | 호출 방식 | 비고 |
|------------|------|------------|------|
| **OpenAI** | 드로잉 명령 변환, 스트로크 생성 | httpx → `api.openai.com/v1/chat/completions` | model: gpt-4o-mini 등 |
| **Google Gemini** | 동일 | `google-generativeai` 또는 `google-genai` (Client + generate_content) | gemini-2.0-flash, gemini-3-flash-preview 등 |
| **Anthropic (Claude)** | 동일 | httpx → `api.anthropic.com/v1/messages` | claude-3-5-haiku 등 |
| **Perplexity** | 동일 | httpx → `api.perplexity.ai/chat/completions` | |
| **OpenClaw Gateway** | 봇 진입 시 스트로크 | httpx → `{openclaw_base_url}/v1/chat/completions` | model: openclaw:main, 봇이 URL 제공 |

### 4.2 선택 패키지 (AI)

| 패키지 | 버전 | 용도 |
|--------|------|------|
| **google-generativeai** | ≥0.8.0 | Gemini (구 SDK, 선택) |
| **google-genai** | ≥1.0.0 | Gemini (새 SDK, 선택, 둘 중 하나만) |

---

## 5. 프로토콜·엔드포인트

| 구분 | 기술 |
|------|------|
| REST | FastAPI 라우트, JSON 요청/응답 |
| WebSocket | FastAPI `WebSocket`, JSON 프레임 (sync, draw, cursor, cursor_remove, ai_error, clear, ping/pong) |
| 정적 파일 | FastAPI `StaticFiles` (`/static`), `FileResponse` (`/`) |

---

## 6. 데이터·상태

| 구분 | 방식 |
|------|------|
| 서버 상태 | 인메모리: `canvas_events`, `ai_cursors`, `ai_offsets`, `ai_pending_message`, `ai_stop_events`, `registered_agents`, `_rate_limit` |
| DB | 없음 |
| 세션 | 없음 (stateless REST, WebSocket 연결만 유지) |

---

## 7. 환경 변수 (요약)

| 변수 | 용도 |
|------|------|
| `DRAWBOARD_BASE_URL` | skill.md·응답에 노출할 서버 주소 (Railway 배포 시 생성 도메인 URL 권장) |
| `CORS_ORIGINS` | 허용 출처 (쉼표 구분, 비우면 `*`) |
| `OPENAI_API_KEY` | OpenAI 호출 (선택) |
| `GEMINI_API_KEY` | Gemini 호출 (선택) |
| `ANTHROPIC_API_KEY` | Claude 호출 (선택) |
| `PERPLEXITY_API_KEY` | Perplexity 호출 (선택) |
| `OPENCLAW_BASE_URL` | 서버 측 OpenClaw Gateway 기본 주소 (선택) |
| `OPENCLAW_API_KEY` | Gateway Bearer 토큰 (선택) |

**Base URL 결정 순서** (코드): (1) `DRAWBOARD_BASE_URL` → (2) `X-Forwarded-Proto` + `X-Forwarded-Host` (프록시) → (3) `request.base_url`. Railway 등 프록시 뒤에서는 (2)로 공개 URL 자동 구성 가능.

---

## 8. 실행·배포

### 8.1 로컬

| 구분 | 명령/방식 |
|------|-----------|
| 로컬 실행 | `uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000` |
| 프로젝트 루트 | 반드시 프로젝트 루트에서 실행 (frontend 경로 상대 참조) |
| 테스트 | `python backend/test_bot_entry.py` (프로젝트 루트에서) |

### 8.2 Railway (클라우드 배포)

| 구분 | 내용 |
|------|------|
| 빌드 | **Dockerfile** 사용 (Python 3.11-slim). Railway가 Dockerfile 감지 시 이를 사용해 pydantic-core 등 휠 빌드 오류 방지 |
| 설정 파일 | `railway.toml` (Config as Code), `Procfile` |
| 시작 명령 | `uvicorn backend.main:app --host 0.0.0.0 --port $PORT` (Railway가 `PORT` 주입) |
| 헬스 체크 | `GET /health`, timeout 30초 |
| 배포 방식 | GitHub 연동 후 Deploy from GitHub repo → Generate Domain으로 공개 URL 발급 |
| 환경 변수 | Railway 대시보드 Variables에 `DRAWBOARD_BASE_URL`(생성 도메인) 등 설정 |

**배포 설정 파일**

| 파일 | 역할 |
|------|------|
| `Dockerfile` | Python 3.11-slim 기반 이미지, `requirements.txt` 설치, PORT 환경변수로 uvicorn 실행 (Railway 우선 사용) |
| `.dockerignore` | .venv, __pycache__, .git 등 제외해 이미지 경량화 |
| `railway.toml` | startCommand, healthcheckPath, healthcheckTimeout (Config as Code) |
| `Procfile` | `web: uvicorn backend.main:app --host 0.0.0.0 --port $PORT` (Procfile 사용 환경 대비) |

---

## 9. 의존성 목록 (requirements.txt 기준)

**기본 (배포·로컬 공통, Gemini 미포함 — 빌드 경량화)**

```
fastapi==0.109.2
uvicorn[standard]==0.27.1
websockets>=10.4
pydantic==2.6.1
httpx==0.26.0
python-dotenv==1.0.1
```

**선택 (Gemini 사용 시)**  
`pip install google-generativeai` 또는 `pip install google-genai`. requirements.txt에는 미포함(배포 시 소스 빌드 이슈 방지).

---

## 10. 병합 시 참고 사항

- **진입점**: `backend.main:app` (FastAPI 앱).
- **정적·SPA**: `app.mount("/static", ...)`, `FileResponse(FRONTEND_INDEX)` 로 `/` 제공. 다른 프로젝트에 통합 시 라우트 prefix 또는 별도 앱 마운트 고려.
- **전역 상태**: DB 없이 메모리만 사용. 재시작 시 초기화. 병합 시 세션/저장소 연동 필요하면 `canvas_events` 등 저장소 추상화 필요.
- **인증**: 현재 API 키·rate limit(IP) 수준. 계정·JWT 등 필요 시 FastAPI 미들웨어·의존성 추가.
- **AI 브릿지**: `ai_bridge.py`는 독립 모듈에 가깝게 구성. 다른 서비스에서 동일 스키마(드로잉 명령·스트로크 JSON)로 재사용 가능.
