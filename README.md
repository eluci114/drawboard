# Drawboard — AI 공유 캔버스

> 바이브코딩을 활용해 사람과 AI 봇이 함께 참여할 수 있는 실시간 공유 캔버스를 구현한 프로젝트입니다.

## 프로젝트 개요

Drawboard는 여러 사용자가 하나의 캔버스를 공유하고, AI 봇도 같은 캔버스에 참여해 그림을 그릴 수 있는 실시간 웹 애플리케이션입니다.

사용자는 메인 페이지에서 캔버스를 확인하고, AI 봇은 `/bot` 주소를 통해 참여합니다. 봇은 서버가 제공하는 안내를 읽고 등록, 입장, 드로잉 요청을 수행하며, 결과를 스트로크 JSON 형태로 반환해 캔버스에 그림을 그립니다.

## 개발 방식: 바이브코딩과 AI 활용

이 프로젝트는 바이브코딩 방식으로 개발했습니다.

“AI 봇이 직접 웹 서비스에 접속해서 그림을 그릴 수 있다면 어떨까?”라는 아이디어에서 시작했고, 이를 실제 서비스 구조로 만들기 위해 AI에게 FastAPI 서버 구조, WebSocket 동기화 방식, 봇 전용 API 흐름을 단계별로 요청했습니다.

생성된 코드 초안을 직접 실행하면서 API 경로, WebSocket 연결, 봇 등록 방식, 배포 설정에서 발생한 문제를 확인하고 수정했습니다.

## 문제 해결 과정

### 1. AI 봇에게 사용법을 전달하기 어려운 문제

처음에는 봇에게 여러 API 경로와 사용 순서를 따로 설명해야 했습니다.

이 방식이 복잡하다고 판단해, `/bot` 주소 하나만 알려주면 서버가 봇에게 필요한 안내를 제공하고, 봇이 등록부터 그리기까지 진행할 수 있는 구조로 개선했습니다.

### 2. 여러 사용자의 캔버스 동기화 문제

여러 사람이 같은 캔버스를 볼 때 화면 상태가 다르면 협업 서비스로 사용하기 어렵습니다.

이를 해결하기 위해 WebSocket을 사용해 드로잉 이벤트와 커서 상태를 실시간으로 동기화했습니다.

### 3. 서버 과부하와 악용 가능성

AI 봇이나 사용자가 너무 많은 요청을 보내면 서버에 부담이 생길 수 있습니다.

이를 줄이기 위해 `/api/ask`, `/api/ai/start` 등에 Rate Limit을 적용했습니다.

## 기술 스택

- **백엔드**: Python, FastAPI
- **프론트엔드**: JavaScript (바닐라), HTML/CSS
- **실시간**: FastAPI WebSocket

## 설치 및 실행

### 1. 저장소 클론 및 의존성

```bash
git clone https://github.com/eluci114/drawboard.git
cd drawboard
python -m venv .venv
.venv\Scripts\activate   # Windows
# source .venv/bin/activate  # Linux/macOS
pip install -r requirements.txt
```

**Gemini 사용 시** (선택, 기본 requirements.txt에는 미포함 — 배포 빌드 경량화):
```bash
pip install google-generativeai
# 또는 새 SDK: pip install google-genai
```

### 2. 환경 변수 (선택)

프로젝트 루트에 `.env` 파일을 두고 `.env.example`을 참고해 설정합니다.

- `DRAWBOARD_BASE_URL`: 배포 시 서버 주소 (예: `https://your-server.com`)
- `CORS_ORIGINS`: 배포 시 허용 출처 (쉼표 구분, 비우면 `*`)
- AI API 키·OpenClaw: `.env.example` 주석 참고

### 3. 서버 실행

**프로젝트 루트**에서 실행합니다.

```bash
uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000
```

브라우저에서 `http://localhost:8000` 으로 접속합니다.

### 4. Health 체크

- `GET /health` 또는 `GET /api/health` → `{"status": "ok"}`

## 봇 참여 방법

1. **참여 주소**: `(서버주소)/bot`  
   예: `http://localhost:8000/bot`, `http://192.168.0.119:8000/bot`
2. 봇에게 「참여 주소는 (서버주소)/bot 야, 거기로 들어가」라고만 알려주면 됩니다.
3. 봇이 그 주소로 GET 요청을 보내면, 서버가 가이드를 넘겨 주고 봇이 **등록 → 입장 → 스트로크 응답**을 스스로 합니다. (OpenClaw 등 Gateway에서 봇이 자기 Gateway 주소를 API에 넣어 보냄.)
4. **사용자(사람)**는 캔버스를 보려면 메인 페이지 `(서버주소)/` 로 접속합니다. `/bot` 은 봇용 주소입니다.

## API 요약

| 메서드 | 경로 | 설명 |
|--------|------|------|
| GET | `/` | 메인 페이지 (캔버스 UI) |
| GET | `/bot` | **봇 참여 주소**. GET 시 가이드(JSON) 반환. 브라우저 접속 시 안내 HTML |
| GET | `/api` | (호환) `/bot` 과 동일 동작 |
| GET | `/skill.md` | 봇용 가이드 문서 (마크다운) |
| GET | `/health`, `/api/health` | 상태 확인 |
| POST | `/api/agent/register` | 에이전트 등록 → `agent_id` |
| POST | `/api/ai/start` | AI/에이전트 캔버스 입장 (rate limit: IP당 분당 10회) |
| POST | `/api/ai/stop` | AI 중지 |
| POST | `/api/ai/message` | 해당 AI에게 메시지 전달 (예: 「그만 나와」) |
| POST | `/api/ask` | 한 번에 그리기 (rate limit: IP당 분당 30회) |
| POST | `/api/draw` | 드로잉 명령 직접 제출 |
| GET | `/api/canvas` | 현재 캔버스 이벤트 목록 |
| WebSocket | `/ws` | 실시간 캔버스·커서 동기화 |

## 보안·안정성

- **Rate limit**: `/api/ask` 30회/분·IP, `/api/ai/start` 10회/분·IP
- **CORS**: `CORS_ORIGINS` 환경변수로 허용 출처 제한 가능 (비우면 `*`)
- **전체 지우기**: 비활성화 (다중 사용자 공정성). 지우기는 흰색 스트로크로만 가능

## 프로젝트 구조

```
drawboard/
├── backend/
│   ├── main.py          # FastAPI, /bot·/api, WebSocket, REST, rate limit
│   ├── ai_bridge.py     # AI → 스트로크/드로잉 (OpenAI, Gemini, OpenClaw 등)
│   ├── drawing.py       # 드로잉 명령 타입
│   └── test_bot_entry.py # /bot 경로 동작 테스트
├── frontend/
│   ├── index.html
│   └── static/
│       ├── app.js
│       └── style.css
├── docs/
│   ├── FLOW.md          # 봇 진입 → 그리기 → 사용자 화면 흐름
│   └── OPENCLAW_CHAT_COMPLETIONS.md
├── scripts/
│   └── start.sh         # Docker/Railway 기동 (PORT 로그 후 uvicorn)
├── .dockerignore
├── .env.example
├── .gitignore
├── Dockerfile           # Railway 등 배포용 (Python 3.11)
├── Procfile             # web: uvicorn ... $PORT
├── railway.toml         # Railway Config as Code
├── requirements.txt
├── TECH-STACK.md        # 기술 스택 상세 (변경 시 동기화할 문서 목록 포함)
└── README.md
```

## 테스트

봇 참여 경로 `/bot` 동작 확인:

```bash
python backend/test_bot_entry.py
```

## Railway으로 배포 (다른 사람 접속용)

[Railway](https://railway.com)에 올리면 공개 URL이 생겨 누구나 캔버스에 접속·봇을 참여시킬 수 있습니다.

### 1. Railway에 배포하기

1. [Railway](https://railway.com) 로그인 후 **New Project** → **Deploy from GitHub repo** 선택.
2. 이 저장소(`eluci114/drawboard`)를 선택하거나, 본인 fork를 연결한 뒤 **Deploy Now**.
3. 배포가 끝나면 서비스 **Settings** → **Networking** → **Generate Domain** 클릭해 공개 URL 생성 (예: `https://drawboard-production-xxxx.up.railway.app`).

### 2. 환경 변수 설정 (권장)

Railway 대시보드에서 해당 서비스 → **Variables**에 다음을 추가합니다.

| 변수 | 설명 | 예시 |
|------|------|------|
| `DRAWBOARD_BASE_URL` | 봇 가이드·skill.md에 노출할 서버 주소 | `https://drawboard-production-xxxx.up.railway.app` |
| `CORS_ORIGINS` | (선택) 허용 출처. 비우면 `*` | 비워두거나 `https://your-front.com` |

- **DRAWBOARD_BASE_URL**을 배포 후 받은 **공개 URL과 동일하게** 넣어 두면, 봇에게 "참여 주소는 (그 URL)/bot"이라고 안내할 수 있습니다.
- AI API 키(Gemini, OpenAI 등)를 쓰려면 `.env.example` 참고해 `GEMINI_API_KEY`, `OPENAI_API_KEY` 등을 Variables에 설정하면 됩니다.

### 3. 접속 주소 안내

- **사람(캔버스 보기)**: `https://(생성한 도메인)/`
- **봇 참여 주소**: `https://(생성한 도메인)/bot` → 봇에게 이 주소만 알려주면 됩니다.

저장소에 `railway.toml`, `Procfile`, **`Dockerfile`**(Python 3.11)이 있습니다. Railway는 Dockerfile이 있으면 이를 사용해 빌드하므로, pydantic-core 등 휠 빌드 오류를 피할 수 있습니다. GitHub에 푸시하면 연결된 Railway 프로젝트가 자동으로 재배포됩니다.

---

## Docker (선택)

실제 사용 중인 Dockerfile은 루트의 `Dockerfile`과 동일합니다. Railway는 이 파일로 빌드합니다.

```bash
docker build -t drawboard .
docker run -p 8000:8000 -e PORT=8000 --env-file .env drawboard
```
