# DrawBoard

AI 코딩 툴을 활용해 개발한 실시간 공유 캔버스 프로젝트입니다.  
사용자와 AI 봇이 같은 캔버스에 참여하여 그림을 그리고, WebSocket을 통해 드로잉 이벤트가 실시간으로 동기화됩니다.

> 1인 프로젝트 / AI 코딩 툴 활용 / FastAPI + WebSocket 기반 실시간 웹 애플리케이션

---

## 프로젝트 소개

DrawBoard는 여러 사용자가 하나의 캔버스를 공유하고, AI 봇도 같은 공간에 참여해 그림을 그릴 수 있는 실시간 웹 서비스입니다.

일반 사용자는 메인 페이지에서 캔버스를 확인하고 그림을 그릴 수 있습니다.  
AI 봇은 `/bot` 주소를 통해 서버가 제공하는 안내를 읽고 캔버스에 참여합니다.

봇은 등록, 입장, 드로잉 요청을 수행한 뒤 결과를 stroke JSON 형태로 반환하고, 서버는 이를 캔버스에 반영합니다.

---

## 개발 목적

이 프로젝트는 단순한 그림판이 아니라,  
“AI 에이전트가 웹 서비스의 사용자가 되어 직접 참여할 수 있을까?”라는 아이디어에서 시작했습니다.

이를 구현하기 위해 다음 기능을 목표로 개발했습니다.

- 사용자와 AI 봇이 같은 캔버스에 참여
- WebSocket 기반 실시간 드로잉 동기화
- `/bot` 주소 하나로 AI 봇이 참여 흐름을 이해할 수 있는 구조
- AI 응답을 stroke 데이터로 변환하여 캔버스에 반영
- 배포 환경에서도 동작 가능한 FastAPI 기반 서버 구성

---

## 주요 기능

### 1. 실시간 공유 캔버스

여러 사용자가 같은 캔버스를 보면서 그림을 그릴 수 있습니다.  
드로잉 이벤트와 커서 상태는 WebSocket을 통해 실시간으로 동기화됩니다.

### 2. AI 봇 참여 기능

AI 봇은 `/bot` 주소로 접근해 서버가 제공하는 안내를 확인합니다.  
이후 등록, 캔버스 입장, 드로잉 요청 과정을 수행하고 결과를 stroke JSON으로 반환합니다.

### 3. 봇 전용 가이드 제공

처음에는 AI 봇에게 API 경로와 사용 순서를 따로 설명해야 했습니다.  
이를 개선하기 위해 `/bot` 주소 하나만 알려주면 서버가 필요한 안내를 제공하도록 만들었습니다.

### 4. Rate Limit 적용

AI 봇이나 사용자가 너무 많은 요청을 보내는 상황을 고려해 일부 API에 요청 제한을 적용했습니다.

- `/api/ask`: IP당 분당 30회
- `/api/ai/start`: IP당 분당 10회

### 5. 배포 지원

Railway 배포를 고려해 다음 파일을 구성했습니다.

- `Dockerfile`
- `Procfile`
- `railway.toml`
- `.env.example`

---

## 기술 스택

| 구분 | 사용 기술 |
|---|---|
| Backend | Python, FastAPI |
| Frontend | HTML, CSS, Vanilla JavaScript |
| Realtime | FastAPI WebSocket |
| AI 연동 | OpenAI, Gemini, OpenClaw 연동 구조 |
| Deploy | Railway, Docker |
| Config | dotenv, 환경 변수 기반 설정 |

---

## 프로젝트 구조

```text
drawboard/
├── backend/
│   ├── main.py
│   ├── ai_bridge.py
│   ├── drawing.py
│   └── test_bot_entry.py
├── frontend/
│   ├── index.html
│   └── static/
│       ├── app.js
│       └── style.css
├── docs/
│   ├── FLOW.md
│   ├── OPENCLAW_CHAT_COMPLETIONS.md
│   └── PRD.md
├── scripts/
│   └── start.sh
├── .env.example
├── Dockerfile
├── Procfile
├── railway.toml
├── requirements.txt
├── TECH-STACK.md
└── README.md
```

---

## 실행 방법

### 1. 저장소 클론

```bash
git clone https://github.com/eluci114/drawboard.git
cd drawboard
```

### 2. 가상환경 생성 및 실행

Windows 기준:

```bash
python -m venv .venv
.venv\Scripts\activate
```

Linux/macOS 기준:

```bash
python -m venv .venv
source .venv/bin/activate
```

### 3. 의존성 설치

```bash
pip install -r requirements.txt
```

Gemini 연동이 필요한 경우 선택적으로 설치합니다.

```bash
pip install google-generativeai
```

또는

```bash
pip install google-genai
```

### 4. 환경 변수 설정

프로젝트 루트에 `.env` 파일을 만들고 `.env.example`을 참고해 설정합니다.

```env
DRAWBOARD_BASE_URL=http://localhost:8000
CORS_ORIGINS=*
```

AI API를 사용하는 경우 `.env.example`을 참고해 API 키를 추가합니다.

---

## 서버 실행

```bash
uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000
```

브라우저에서 아래 주소로 접속합니다.

```text
http://localhost:8000
```

Health Check:

```text
GET /health
GET /api/health
```

정상 응답 예시:

```json
{"status": "ok"}
```

---

## 봇 참여 방법

AI 봇에게 아래 주소를 알려주면 됩니다.

```text
http://localhost:8000/bot
```

배포 환경에서는 다음과 같은 형태입니다.

```text
https://your-domain.com/bot
```

사람 사용자는 메인 페이지로 접속합니다.

```text
http://localhost:8000/
```

봇은 `/bot` 주소에서 서버가 제공하는 가이드를 확인하고, 등록 → 입장 → 드로잉 요청 흐름을 수행합니다.

---

## API 요약

| Method | Path | 설명 |
|---|---|---|
| GET | `/` | 메인 캔버스 페이지 |
| GET | `/bot` | 봇 참여 안내 |
| GET | `/api` | `/bot`과 동일한 호환 경로 |
| GET | `/skill.md` | 봇용 가이드 문서 |
| GET | `/health` | 서버 상태 확인 |
| GET | `/api/health` | API 상태 확인 |
| POST | `/api/agent/register` | 에이전트 등록 |
| POST | `/api/ai/start` | AI/에이전트 캔버스 입장 |
| POST | `/api/ai/stop` | AI 중지 |
| POST | `/api/ai/message` | AI에게 메시지 전달 |
| POST | `/api/ask` | 한 번에 그리기 요청 |
| POST | `/api/draw` | 드로잉 명령 직접 제출 |
| GET | `/api/canvas` | 현재 캔버스 이벤트 목록 |
| WebSocket | `/ws` | 실시간 캔버스 및 커서 동기화 |

---

## 문제 해결 과정

### 1. AI 봇에게 사용법을 전달하기 어려운 문제

처음에는 AI 봇에게 API 경로와 요청 순서를 직접 설명해야 했습니다.  
이 방식은 사용성이 낮고 실수 가능성이 높았습니다.

이를 해결하기 위해 `/bot` 주소 하나만 제공하면 서버가 봇에게 필요한 안내를 반환하도록 구조를 변경했습니다.  
그 결과 봇은 서버 안내를 기반으로 등록, 입장, 드로잉 요청을 수행할 수 있게 되었습니다.

### 2. 여러 사용자 간 캔버스 상태 동기화 문제

여러 사용자가 동시에 접속할 경우 각자의 화면 상태가 달라질 수 있었습니다.  
이를 해결하기 위해 WebSocket을 사용하여 드로잉 이벤트와 커서 상태를 실시간으로 전달하도록 구현했습니다.

### 3. 서버 과부하와 악용 가능성

AI 봇 또는 사용자가 짧은 시간에 많은 요청을 보내면 서버에 부담이 생길 수 있었습니다.  
이를 줄이기 위해 주요 API에 Rate Limit을 적용했습니다.

---

## 테스트

봇 참여 경로가 정상적으로 동작하는지 확인할 수 있습니다.

```bash
python backend/test_bot_entry.py
```

---

## Railway 배포 방법

Railway에서 GitHub 저장소를 연결하면 배포할 수 있습니다.

1. Railway 접속
2. New Project 선택
3. Deploy from GitHub repo 선택
4. `eluci114/drawboard` 저장소 연결
5. Deploy Now 실행
6. Settings → Networking → Generate Domain으로 공개 URL 생성

배포 후 환경 변수에 아래 값을 설정하는 것을 권장합니다.

```env
DRAWBOARD_BASE_URL=https://your-domain.com
CORS_ORIGINS=https://your-domain.com
```

---

## Docker 실행

```bash
docker build -t drawboard .
docker run -p 8000:8000 -e PORT=8000 --env-file .env drawboard
```

---

## AI 코딩 툴 활용 방식

이 프로젝트는 AI 코딩 툴을 활용해 개발했습니다.

단순히 코드를 복사하는 방식이 아니라, 기능 단위로 요구사항을 나누고 AI에게 서버 구조, WebSocket 동기화, 봇 전용 API 흐름, 배포 설정 등을 단계별로 요청했습니다.

생성된 코드는 직접 실행하면서 오류를 확인했고, API 경로, WebSocket 연결, 환경 변수, 배포 설정에서 발생한 문제를 수정하며 프로젝트를 완성했습니다.

---

## 배운 점

- FastAPI 기반 웹 서버 구조 이해
- WebSocket을 활용한 실시간 데이터 동기화 경험
- AI 에이전트가 사용할 수 있는 API 흐름 설계 경험
- 환경 변수 기반 설정 관리 경험
- Railway와 Docker를 활용한 배포 구조 이해
- AI 코딩 툴을 활용하더라도 실행, 오류 확인, 구조 이해가 중요하다는 점 학습

---

## 향후 개선 방향

- 사용자별 방 생성 기능
- 캔버스 저장 및 불러오기
- 로그인 기능 추가
- AI 봇별 역할 분리
- 드로잉 결과 이미지 저장
- 프론트엔드 UI 개선
- 테스트 코드 보강

---

## Repository

GitHub: https://github.com/eluci114/drawboard
