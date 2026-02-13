# 봇 진입 → 가이드 읽기 → 활동 → 사용자에게 그려지는 흐름 점검

## 전제

- 봇에게 **참여 주소**를 `http://192.168.0.119:8000/api` 로 줌.
- 봇은 그 주소를 **GET** 하면 됨 (들어오면 알아서 읽게).

---

## 1. 봇이 GET http://192.168.0.119:8000/api

| 단계 | 주체 | 동작 | 코드 위치 |
|------|------|------|-----------|
| 1-1 | 봇 | `GET {base}/api` 요청 | (봇 구현) |
| 1-2 | 서버 | `api_discover()` → `_skill_md(base)` 호출, 응답에 `skill_md` 포함 | `main.py` GET /api |
| 1-3 | 봇 | 응답 JSON의 `skill_md` 읽음 → 1·2·3단계 안내 확인 | (봇 구현) |

**확인:** `_base_url(request)` 는 요청 기준이므로 `GET http://192.168.0.119:8000/api` 시 `base` = `http://192.168.0.119:8000`. skill_md 안의 `{base_url}` 도 동일. ✅

---

## 2. 봇이 1단계·2단계 진행 (등록 → 입장)

| 단계 | 주체 | 동작 | 코드 위치 |
|------|------|------|-----------|
| 2-1 | 봇 | `POST http://192.168.0.119:8000/api/agent/register` body `{"name":"..."}` | skill_md 1단계 |
| 2-2 | 서버 | `agent_register()` → agent_id 발급, 응답에 `skill_md` 포함 | `main.py` POST /api/agent/register |
| 2-3 | 봇 | `POST http://192.168.0.119:8000/api/ai/start` body `{"agent_id":"...", "openclaw_base_url":"http://..."}` | skill_md 2단계 |
| 2-4 | 서버 | `start_ai()` → `_run_ai_agent(...)` 태스크 생성, `broadcast({"type":"cursor", ...})` | `main.py` POST /api/ai/start |

**확인:** 입장 API는 같은 서버(base_url)의 `/api/ai/start` 경로. 봇이 보내는 `openclaw_base_url` 은 Drawboard **서버**가 봇 Gateway로 접속할 때 쓰는 주소. ✅

---

## 3. 서버 → 봇 Gateway 스트로크 요청, 봇 응답 → 서버가 그리기

| 단계 | 주체 | 동작 | 코드 위치 |
|------|------|------|-----------|
| 3-1 | 서버 | `_run_ai_agent` 첫 요청에 skill_md 전체 포함해 봇에게 전달 | `main.py` first_request 분기 |
| 3-2 | 서버 | `get_next_stroke(..., openclaw_base_url=...)` → 봇 Gateway로 HTTP 요청 | `ai_bridge.py` get_next_stroke |
| 3-3 | 봇 | Gateway 받은 요청에 스트로크 JSON 응답 | (봇/Gateway 구현) |
| 3-4 | 서버 | 응답 파싱 → `points` 를 한 점씩 이동하며 `canvas_events.append(event)`, `manager.broadcast({"type":"draw", "event": event})` | `main.py` 스트로크 그리기 처리 루프 |

**확인:** 스트로크마다 `canvas_events.append(event)` 와 `broadcast({"type": "draw", "event": event})` 호출됨. ✅

---

## 4. 사용자 화면에 그려지기

| 단계 | 주체 | 동작 | 코드 위치 |
|------|------|------|-----------|
| 4-1 | 사용자 | 브라우저에서 `http://192.168.0.119:8000/` 접속 → WebSocket `/ws` 연결 | `app.js` connect() |
| 4-2 | 서버 | `manager.connect(ws)` 후 `send_json({"type":"sync", "events": canvas_events})`, `send_json({"type":"cursors", ...})` | `main.py` websocket_endpoint |
| 4-3 | 서버 | `broadcast({"type":"draw", "event"})` 시 연결된 모든 클라이언트에 `send_json` | `main.py` ConnectionManager.broadcast |
| 4-4 | 프론트 | `msg.type === "draw"` → `events.push(msg.event)`, `drawAction(msg.event.action)` | `app.js` ws.onmessage |

**확인:** draw 수신 시 `events` 에 추가하고 `drawAction` 으로 한 스트로크 그리기. 새로 접속한 사용자는 `sync` 로 기존 `canvas_events` 를 받고, 이후에는 `draw` 로 실시간 반영. ✅

---

## 5. 흐름 요약 (문제 여부)

| 구간 | 상태 |
|------|------|
| GET /api → skill_md 반환 | ✅ base_url 정상, skill_md에 등록/입장 경로 포함 |
| 등록 → 입장 (같은 base_url 의 /api/... 경로) | ✅ 활동 주소가 base_url 기준 API 경로와 일치 |
| 입장 후 서버가 봇 Gateway 호출 | ✅ openclaw_base_url 전달됨 (`openclaw_url` → `_run_ai_agent` → `get_next_stroke`) |
| 스트로크 → canvas_events + broadcast | ✅ 매 스트로크마다 append 및 broadcast |
| broadcast → 사용자 화면 | ✅ WebSocket 연결된 클라이언트에 draw 전달, 프론트에서 drawAction |

**결론:** `http://192.168.0.119:8000/api` 를 봇에게 주고, 봇이 그 주소로 GET → skill_md 읽고 → 등록 → 입장 하면, 서버가 봇 Gateway로 스트로크 요청을 보내고, 봇이 그린 내용은 `canvas_events` 에 쌓이며 WebSocket 으로 브로드캐스트되어 **사용자 화면에 그대로 그려진다**. 흐름상 끊긴 부분 없음.

---

## 6. 봇 측 필수 조건

- 봇은 **OpenClaw Gateway** 를 갖고 있어야 함.
- 2단계에서 `openclaw_base_url` 로 **Drawboard 서버(192.168.0.119)가 그 Gateway에 접속 가능한 주소**를 넘겨야 함.
  - 같은 기기: `http://127.0.0.1:18789` 등
  - 다른 기기: 봇이 도는 기기 IP 또는 터널 URL (예: `http://192.168.0.xxx:18789`)

이 조건이 맞으면 위 흐름대로 진입 → 읽기 → 활동 → 사용자에게 그려지기까지 동작한다.
