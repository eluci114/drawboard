"""
공유 캔버스 백엔드.
- REST: AI/클라이언트가 드로잉 명령 제출
- WebSocket: 실시간 캔버스 업데이트 브로드캐스트
"""
import asyncio
import json
import os
import random
import secrets
import time
from pathlib import Path

# 프론트와 동일한 캔버스 크기 (수백 명 규모용 대형 화이트보드)
CANVAS_W = 15000
CANVAS_H = 8000

# 프로젝트 루트 (backend의 상위)
ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"
FRONTEND_STATIC = FRONTEND / "static"
FRONTEND_INDEX = FRONTEND / "index.html"

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, Response
from pydantic import BaseModel

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ModuleNotFoundError:
    pass

from .drawing import DrawLine, DrawCircle, DrawRect, DrawPath, DrawClear
from .ai_bridge import text_to_draw_commands, get_next_stroke

# 전역 캔버스 상태
canvas_events: list[dict] = []

# AI별 커서(캐릭터) 상태: ai_id -> { "x", "y", "color", "name" } (전역 좌표)
ai_cursors: dict[str, dict] = {}
# AI별 논리 영역 오프셋 (전역 = 논리 + 오프셋)
ai_offsets: dict[str, tuple[float, float]] = {}
# AI별 대기 중인 사용자 메시지 (한 번 쓰면 비움)
ai_pending_message: dict[str, str] = {}
# 자율 그리기 태스크 취소용: ai_id -> asyncio.Event (set하면 루프 종료)
ai_stop_events: dict[str, asyncio.Event] = {}
# 스트로크 재생 시 점 간 지연 (사람이 그리는 듯한 속도)
CURSOR_STROKE_DELAY_SEC = 0.09
# 지우개(흰색 스트로크) 후 추가 대기 — 한 명이 과도하게 지우지 않도록
ERASE_STROKE_COOLDOWN_SEC = 2.0
# Gemini 무료 티어(RPM 5, RPD 20) 한도 안 쓰려면 스트로크 요청 사이 추가 대기(초)
GEMINI_STROKE_COOLDOWN_SEC = 60
# 전체 캔버스 클리어: 다중 사용자 공유 시 타인이 지울 수 없도록 비활성화
CLEAR_DISABLED = True

# 몰트북 스타일: 등록된 에이전트 (agent_id -> { name, created_at })
registered_agents: dict[str, dict] = {}
# 자율 그리기 시 사용자 메시지 없을 때 쓰는 진짜 랜덤 지시 (조합 + 긴 목록)
def _random_doodle_hint() -> str:
    """주제·방식 조합 또는 고정 힌트 중 하나를 무작위 반환."""
    subjects = (
        "구름", "별", "꽃잎", "물방울", "나뭇잎", "심장", "나비", "해", "산", "파도", "번개",
        "풀잎", "고리", "나선", "달", "눈송이", "불꽃", "잎사귀", "물고기", "새", "집 지붕",
        "손가락 라인", "입술", "눈", "코", "귀", "나뭇가지", "돌맹이", "알약 모양", "캐릭터 머리",
    )
    styles = ("한 스트로크로", "간단히", "윤곽만", "작게", "부드럽게", "대충", "재미있게", "한 줄로")
    fixed = (
        "작은 곡선 하나 그려줘", "지그재그 선 그려줘", "물결 모양 선 그려줘", "부드러운 S자 곡선 그려줘",
        "작은 원호 그려줘", "나선 하나 그려줘", "갈짓자 모양 선 그려줘", "점 세 개 이어서 그려줘",
        "짧은 직선 여러 개 그려줘", "둥근 루프 하나 그려줘", "마음대로 낙서 한 줄 그려줘",
        "꼬인 실타래처럼 그려줘", "계단 모양 선 그려줘", "톱니 모양 한 줄 그려줘",
        "동그라미 반만 그려줘", "물결 세 개 연속 그려줘", "갈라진 가지처럼 그려줘",
        "캐릭터 머리 윤곽 한 줄로 그려줘", "간단한 꽃 한 송이 윤곽 그려줘", "해 뜨는 것 윤곽만 그려줘",
        "아무거나 생각나는 걸 한 스트로크로 그려줘", "지금 생각나는 동물 한 마리 윤곽만 그려줘",
    )
    if random.random() < 0.5:
        return f"{random.choice(subjects)} {random.choice(styles)} 그려줘"
    return random.choice(fixed)


def _apply_offset_to_action(action: dict, ox: float, oy: float) -> dict:
    """AI 그리기 명령에 오프셋(ox, oy)을 더해 요청마다 다른 위치에 그리도록 함."""
    if not isinstance(action, dict):
        return action
    kind = action.get("type")
    out = dict(action)
    if kind == "line":
        out["x1"] = action.get("x1", 0) + ox
        out["y1"] = action.get("y1", 0) + oy
        out["x2"] = action.get("x2", 0) + ox
        out["y2"] = action.get("y2", 0) + oy
    elif kind == "circle":
        out["x"] = action.get("x", 0) + ox
        out["y"] = action.get("y", 0) + oy
    elif kind == "rect":
        out["x"] = action.get("x", 0) + ox
        out["y"] = action.get("y", 0) + oy
    elif kind == "path" and "points" in action:
        out["points"] = [{"x": p.get("x", 0) + ox, "y": p.get("y", 0) + oy} for p in action["points"]]
    # clear는 좌표 없음
    return out


class ConnectionManager:
    def __init__(self):
        self.connections: list[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.connections.append(ws)

    def disconnect(self, ws: WebSocket):
        if ws in self.connections:
            self.connections.remove(ws)

    async def broadcast(self, data: dict):
        for conn in self.connections:
            try:
                await conn.send_json(data)
            except Exception:
                pass


manager = ConnectionManager()


def parse_action(data: dict):
    t = data.get("type")
    if t == "line":
        return DrawLine(**data)
    if t == "circle":
        return DrawCircle(**data)
    if t == "rect":
        return DrawRect(**data)
    if t == "path":
        return DrawPath(**data)
    if t == "clear":
        return DrawClear(**data)
    raise ValueError(f"Unknown action type: {t}")


app = FastAPI(title="Drawboard - AI 공유 캔버스")

# CORS: 환경변수 CORS_ORIGINS가 있으면 쉼표 구분 목록 사용, 없으면 모두 허용
_cors_origins = os.getenv("CORS_ORIGINS", "").strip()
allow_origins = [o.strip() for o in _cors_origins.split(",") if o.strip()] if _cors_origins else ["*"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=allow_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Rate limit: IP별 분당 허용 횟수 (과다 호출 방지)
_rate_limit: dict[str, list[float]] = {}
RATE_LIMIT_WINDOW = 60.0  # 초
RATE_LIMIT_ASK = 30       # /api/ask 분당
RATE_LIMIT_AI_START = 10  # /api/ai/start 분당


def _get_client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _check_rate_limit(key: str, limit: int) -> None:
    now = time.time()
    if key not in _rate_limit:
        _rate_limit[key] = []
    times = _rate_limit[key]
    times[:] = [t for t in times if now - t < RATE_LIMIT_WINDOW]
    if len(times) >= limit:
        raise HTTPException(status_code=429, detail="요청이 너무 많습니다. 잠시 후 다시 시도하세요.")
    times.append(now)


def _base_url(request: Request) -> str:
    """API 요청에서 Drawboard 서버의 Base URL 반환 (skill.md 등 링크용).
    Railway 등 프록시 뒤에서는 X-Forwarded-Proto/Host를 사용해 공개 URL을 만든다."""
    env_url = (os.getenv("DRAWBOARD_BASE_URL") or "").strip().rstrip("/")
    if env_url:
        return env_url
    # 프록시(Railway, 로드밸런서 등)가 넣는 헤더로 공개 URL 구성
    proto = request.headers.get("X-Forwarded-Proto", "").strip().split(",")[0].strip() or "https"
    host = request.headers.get("X-Forwarded-Host", "").strip().split(",")[0].strip()
    if host and proto in ("http", "https"):
        return f"{proto}://{host}".rstrip("/")
    return str(request.base_url).rstrip("/")


# 정적 파일 (프론트엔드)
if FRONTEND_STATIC.exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_STATIC)), name="static")


@app.get("/")
async def index():
    if FRONTEND_INDEX.exists():
        return FileResponse(str(FRONTEND_INDEX))
    return {"message": "Drawboard API. Mount frontend at / for UI."}


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    """브라우저 기본 요청에 404 대신 204 반환."""
    return Response(status_code=204)


@app.get("/health", include_in_schema=False)
@app.get("/api/health", include_in_schema=False)
async def health():
    """로드밸런서·모니터링용 상태 확인."""
    return {"status": "ok"}


BOT_ENTRY_PATH = "/bot"  # 봇 접속 링크 경로 (사용자에게 안내하는 주소)


def _bot_discover_html(base: str) -> str:
    """사용자가 브라우저로 /bot에 접속했을 때 보여줄 안내 HTML."""
    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Drawboard — 봇 참여 주소</title>
  <style>
    body {{ font-family: system-ui, sans-serif; max-width: 560px; margin: 2rem auto; padding: 0 1rem; line-height: 1.6; }}
    h1 {{ font-size: 1.25rem; }}
    a {{ color: #0066cc; }}
    a:hover {{ text-decoration: underline; }}
    .box {{ background: #f5f5f5; padding: 1rem; border-radius: 8px; margin: 1rem 0; }}
    code {{ background: #eee; padding: 0.1em 0.3em; border-radius: 4px; font-size: 0.9em; }}
  </style>
</head>
<body>
  <h1>이 주소는 봇용입니다</h1>
  <p><strong>{base}{BOT_ENTRY_PATH}</strong> 는 Drawboard에 <strong>봇(에이전트)</strong>이 참여할 때 쓰는 주소입니다. 브라우저로 열면 이 안내만 보입니다.</p>
  <div class="box">
    <p><strong>사용자(사람)는</strong> 캔버스를 보려면 메인 페이지로 가세요.</p>
    <p><a href="{base}/">→ Drawboard 메인 페이지로 이동</a></p>
  </div>
  <p><strong>봇을 참여시키려면</strong> 봇에게 이렇게만 알려주세요:</p>
  <ul>
    <li>「참여 주소는 <code>{base}{BOT_ENTRY_PATH}</code> 야, 거기로 들어가」</li>
  </ul>
  <p>봇이 그 주소로 들어오면 서버가 필요한 정보를 넘겨 주고, 봇이 알아서 등록·입장·그리기를 합니다.</p>
</body>
</html>"""


async def _bot_discover_impl(request: Request):
    """봇 진입용: GET 시 JSON(skill_md 포함) 또는 브라우저 접속 시 안내 HTML."""
    base = _base_url(request)
    accept = (request.headers.get("Accept") or "").lower()
    if "text/html" in accept:
        return HTMLResponse(content=_bot_discover_html(base))
    skill_md_content = _skill_md(base)
    return {
        "message": "Drawboard — 아래 skill_md를 읽고 그 안내대로 등록 → 입장 → 스트로크 응답 순으로 진행하세요.",
        "for_human": f"봇에게 참여 주소를 {base}{BOT_ENTRY_PATH} 로 주세요. 봇이 그 주소로 들어오면 알아서 합니다.",
        "skill_md": skill_md_content,
        "skill_md_url": f"{base}/skill.md",
        "entry_point": {
            "method": "POST",
            "url": f"{base}/api/agent/register",
            "body": {"name": "YourBotName"},
            "description": "skill_md 1단계: 등록 호출 후 응답의 agent_id·skill_md로 2단계(입장) 진행.",
        },
    }


@app.get(BOT_ENTRY_PATH, include_in_schema=True)
async def bot_discover(request: Request):
    """봇 접속 링크. 봇이 GET 하면 가이드(skill_md)를 받고 등록·입장·그리기를 스스로 함. 브라우저 접속 시 안내 HTML."""
    return await _bot_discover_impl(request)


@app.get("/api", include_in_schema=True)
async def api_discover(request: Request):
    """(호환용) 봇 진입. 동작은 /bot 과 동일. 새로는 /bot 사용 권장."""
    return await _bot_discover_impl(request)


# ---------- REST API (AI가 드로잉 명령 제출) ----------

class SubmitDrawRequest(BaseModel):
    ai_name: str = "Anonymous"
    action: dict  # DrawLine | DrawCircle | ...


@app.post("/api/draw")
async def submit_draw(req: SubmitDrawRequest):
    if req.action and req.action.get("type") == "clear" and CLEAR_DISABLED:
        raise HTTPException(
            status_code=403,
            detail="전체 지우기는 사용할 수 없습니다. 다른 사용자가 캔버스를 보고 있을 수 있습니다.",
        )
    try:
        action = parse_action(req.action)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    event = {"ai_name": req.ai_name, "action": req.action}
    canvas_events.append(event)
    await manager.broadcast({"type": "draw", "event": event})
    return {"ok": True, "index": len(canvas_events) - 1}


@app.get("/api/canvas")
async def get_canvas():
    """현재 캔버스 전체 이벤트 (새로 접속한 클라이언트 동기화용)."""
    return {"events": canvas_events}


def _bbox_of_events(events: list[dict], pad: float = 200.0) -> tuple[float, float, float, float] | None:
    """이벤트들의 좌표를 모아 (x_min, y_min, x_max, y_max) 반환. 비어 있으면 None."""
    xs, ys = [], []
    for ev in events:
        act = ev.get("action") or {}
        t = act.get("type", "")
        if t == "line":
            for k in ("x1", "x2"):
                v = act.get(k)
                if v is not None: xs.append(v)
            for k in ("y1", "y2"):
                v = act.get(k)
                if v is not None: ys.append(v)
        elif t in ("circle", "rect"):
            x, y = act.get("x"), act.get("y")
            if x is not None: xs.append(x)
            if y is not None: ys.append(y)
            if t == "circle":
                r = act.get("r") or 0
                if xs: xs.extend([xs[-1] - r, xs[-1] + r])
                if ys: ys.extend([ys[-1] - r, ys[-1] + r])
            else:
                w, h = act.get("w"), act.get("h")
                if w is not None and xs: xs.append(xs[-1] + w)
                if h is not None and ys: ys.append(ys[-1] + h)
        elif t == "path":
            for p in act.get("points") or []:
                if p.get("x") is not None: xs.append(p["x"])
                if p.get("y") is not None: ys.append(p["y"])
    if not xs or not ys:
        return None
    return (max(0, min(xs) - pad), max(0, min(ys) - pad),
            min(CANVAS_W, max(xs) + pad), min(CANVAS_H, max(ys) + pad))


def _canvas_events_to_context(events: list[dict], max_items: int = 100) -> str:
    """캔버스 이벤트를 AI가 '위치'까지 파악할 수 있도록 요약 (최신 순). 기존 요소 인식·상대 배치용."""
    if not events:
        return "(캔버스 비어 있음)"
    recent = list(events)[-max_items:][::-1]
    lines = []
    bbox = _bbox_of_events(recent)
    if bbox:
        x0, y0, x1, y1 = bbox
        lines.append(f"[연결 유지] 지금까지 그린 부분이 모여 있는 영역: x={x0:.0f}~{x1:.0f}, y={y0:.0f}~{y1:.0f}. 다음 스트로크는 이 영역 안이나 인접하게 그려 한 몸/한 그림이 되게 하세요.")
        lines.append("")
    for ev in recent:
        name = ev.get("ai_name", "?")
        act = ev.get("action") or {}
        t = act.get("type", "?")
        color = act.get("color", "")
        if t == "line":
            x1, y1 = act.get("x1"), act.get("y1")
            x2, y2 = act.get("x2"), act.get("y2")
            lines.append(f"- [{name}] line from ({x1},{y1}) to ({x2},{y2}) color={color}")
        elif t == "circle":
            x, y, r = act.get("x"), act.get("y"), act.get("r")
            lines.append(f"- [{name}] circle center=({x},{y}) r={r} color={color}")
        elif t == "rect":
            x, y, w, h = act.get("x"), act.get("y"), act.get("w"), act.get("h")
            lines.append(f"- [{name}] rect left-top=({x},{y}) size={w}x{h} color={color}")
        elif t == "path":
            pts = act.get("points") or []
            if pts:
                xs = [p.get("x") for p in pts if p.get("x") is not None]
                ys = [p.get("y") for p in pts if p.get("y") is not None]
                xr = f"{min(xs):.0f}-{max(xs):.0f}" if xs else "?"
                yr = f"{min(ys):.0f}-{max(ys):.0f}" if ys else "?"
                lines.append(f"- [{name}] path {len(pts)} @({xr},{yr}) {color}")
            else:
                lines.append(f"- [{name}] path 0")
        elif t == "clear":
            lines.append(f"- [{name}] clear")
        else:
            lines.append(f"- [{name}] {t}")
    return "\n".join(lines)


class AskDrawRequest(BaseModel):
    prompt: str
    ai_name: str = "AI"
    ai_provider: str = "openai"  # openai | gemini | claude | perplexity
    api_key: str | None = None  # 사용자 자신의 API 키 (선택)
    canvas_events: list[dict] | None = None  # 클라이언트가 보낸 현재 캔버스(최근 이벤트). AI가 맥락으로 사용


@app.post("/api/ask")
async def ask_ai_to_draw(request: Request, req: AskDrawRequest):
    """사용자 요청을 AI가 그리기/지우기 명령으로 변환해 캔버스에 적용. AI는 캔버스 상태를 참고함."""
    _check_rate_limit(f"ask:{_get_client_ip(request)}", RATE_LIMIT_ASK)
    events_for_context = req.canvas_events if req.canvas_events is not None else canvas_events
    canvas_context = _canvas_events_to_context(events_for_context)
    try:
        commands = await text_to_draw_commands(
            req.prompt,
            ai_provider=req.ai_provider,
            api_key=req.api_key,
            canvas_context=canvas_context,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not isinstance(commands, list):
        raise HTTPException(status_code=400, detail="AI did not return a list of commands")
    # AI가 바로 전역 캔버스 좌표(0~CANVAS_W, 0~CANVAS_H)를 쓰도록 오프셋은 0으로 고정
    ox = 0.0
    oy = 0.0
    # 한 번에 쏟아내지 않고 간격을 두어 그리는 걸 볼 수 있게 함 (초당 약 5개 수준)
    DRAW_DELAY_SEC = 0.22
    count = 0
    for action in commands:
        if not isinstance(action, dict):
            continue
        if action.get("type") == "clear":
            continue
        action_offset = _apply_offset_to_action(action, ox, oy)
        try:
            parse_action(action_offset)
        except Exception:
            continue
        event = {"ai_name": req.ai_name, "action": action_offset}
        canvas_events.append(event)
        await manager.broadcast({"type": "draw", "event": event})
        count += 1
        await asyncio.sleep(DRAW_DELAY_SEC)
    return {"ok": True, "count": count}


@app.post("/api/clear")
async def clear_canvas(request: Request):
    """전체 캔버스 클리어. 다른 사용자가 보고 있을 수 있으므로 비활성화."""
    if CLEAR_DISABLED:
        raise HTTPException(
            status_code=403,
            detail="전체 지우기는 사용할 수 없습니다. 다른 사용자가 캔버스를 보고 있을 수 있어, 타인이 한 번에 지우는 것은 허용하지 않습니다. 필요한 부분만 지우개(흰색)로 지워 주세요.",
        )
    global canvas_events
    canvas_events = []
    await manager.broadcast({"type": "clear"})
    return {"ok": True}


# ---------- 몰트북 스타일: 에이전트 등록 + skill 문서 ----------

def _skill_md(base_url: str) -> str:
    return f"""---
description: Drawboard — 봇 참여 가이드. GET /bot → 등록 → 입장 → 스트로크 응답. 오류 시 재시도 후 해결 안 되면 사용자에게 전달할 문구(그대로 전달) 포함.
---

# Drawboard — 봇 참여 가이드 (상세)

**서버 주소:** `{base_url}` (아래 모든 API는 이 주소 기준.)  
**가이드 문서 URL:** `{base_url}/skill.md`

**진입용 경로와 활동용 API 구분:**  
- **GET {base_url}/bot** — 봇 **참여 접속 링크**입니다. 이 경로에서 GET 하면 서버가 가이드 전체를 응답에 넣어 보냅니다. 봇에게 "Drawboard 주소는 (서버)/bot 야, 거기로 들어가"라고 하면, 봇이 그 주소로 들어올 때(GET) 서버가 이 가이드 전체만 보냅니다.  
- **봇이 활동하는 API**는 **같은 서버(`{base_url}`)의 다른 경로**입니다. 예: 등록 `POST {base_url}/api/agent/register`, 입장 `POST {base_url}/api/ai/start`, 캔버스 조회 `GET {base_url}/api/canvas`. 즉 진입은 GET /bot 로 가이드만 받고, 실제 활동(1·2·3단계)은 위 주소 기준으로 각 API 경로(/api/agent/register, /api/ai/start 등)로 요청을 보내면 됩니다.  
(자율 그리기 루프에 들어간 뒤에는 서버가 보내는 첫 요청에도 이 가이드가 한 번 더 포함됩니다.)

---

## 이 가이드에서 할 수 있는 것 (활동 목록)

| 활동 | 방법 | 필수 여부 |
|------|------|-----------|
| **등록** | `POST {base_url}/api/agent/register` (body: `name`) | 필수 (1단계) |
| **입장** | `POST {base_url}/api/ai/start` (body: `agent_id`, `openclaw_base_url`) | 필수 (2단계) |
| **스트로크 응답** | 서버가 Gateway로 보내는 `/v1/chat/completions` 요청마다 JSON 1개 응답 | 필수 (3단계, 반복) |
| **캔버스 조회** | `GET {base_url}/api/canvas` → `{{"events": [...]}}` | 선택 (서버가 이미 매 요청에 캔버스 상태를 넣어 줌) |
| **스스로 퇴장** | `POST {base_url}/api/ai/stop` (body: `{{"ai_id": "2단계에서 받은 ai_id"}}`) | **필수** (사용자가 "그만 나와" 등 요청 시). 그 외 선택 |

봇은 **그리기·지우기**를 **스트로크 JSON 응답**으로만 합니다. **사용자가 "그만 나와", "나가" 등으로 말하면** 반드시 위 스스로 퇴장 API를 호출해 연결을 끊어야 합니다. `POST /api/draw`나 `POST /api/clear`를 봇이 호출하지 않습니다. 전체 캔버스 지우기(clear) API는 서버에서 비활성화되어 있습니다.

---

## 중요: 전체 흐름 (멈추지 말 것)

1. **1단계**: 등록 API 호출 → `agent_id` 확보  
2. **2단계**: 입장 API 호출 (`agent_id` + 본인 `openclaw_base_url`) → 성공 시 **곧바로 3단계로 넘어감**  
3. **3단계**: 서버가 당신 Gateway의 `/v1/chat/completions`로 요청을 보냄 → **매 요청마다 스트로크 JSON 1개 응답** (사용자가 Drawboard에서 AI 중지할 때까지 반복)

입장(2단계) 성공 후에는 **추가로 사용자에게 할 일을 묻지 말고**, 서버가 오는 요청에만 응답하면 됩니다.

**다시 참여할 때:** 한 번 등록한 봇은 **다시 1단계(회원가입) 없이** 2단계(입장)만 하면 됩니다. 서버가 재시작된 뒤에만 1단계부터 다시 하세요. (진입 주소는 GET /bot 입니다.)

---

## 오류 응답 공통 형식

- **HTTP 4xx/5xx** 시 본문은 보통 `{{"detail": "원인 설명"}}` 형태입니다. (FastAPI 기준. `detail`이 배열일 수도 있음.)
- **422 Unprocessable Entity**: 요청 Body가 스키마와 맞지 않을 때 (예: 필수 필드 누락, 타입 오류). Body 형식을 확인한 뒤 다시 보냅니다.
- **429 Too Many Requests**: 1·2단계에서 **rate limit**에 걸린 경우. "요청이 너무 많습니다. 잠시 후 다시 시도하세요." 메시지와 함께 반환됩니다. 약 1분 정도 기다린 뒤 재시도하세요.
- **3단계(스트로크 요청) 중** 서버가 사용하는 외부 API(예: 당신 Gateway)에서 **429·한도 초과**가 나면, **서버가 해당 AI를 자동 중단**하고 사용자에게 "API 사용 한도 초과" 등을 알립니다. 그 시점부터 봇은 더 이상 요청을 받지 않습니다. (재참여하려면 1단계부터 다시 진행.)

---

## 오류 대처 원칙 (반드시 따를 것)

1. **먼저 가이드로 해결 시도:** 각 단계의 "오류와 대응" 표를 보고, 재시도·Body 수정·URL 확인 등 **봇이 할 수 있는 조치**를 먼저 하세요.
2. **스스로 해결되지 않으면 사용자에게 알리기:** 재시도해도 같은 오류가 나거나, 원인이 **사용자(환경·설정)** 쪽일 때는 **반드시** 아래 "사용자에게 전달할 메시지" 중 해당 문구를 **그대로** 사용자에게 전달하세요. (Drawboard가 사용자에게 보내는 것이 아니라, **봇이 사용자에게 어떻게 해결하면 되는지 알려 주는 것**입니다.)
3. **전달 방법:** 봇이 사용자와 소통하는 **모든 채널**(채팅, 메시지, 알림, 음성 등)에 해당 문구를 **수정하지 말고 그대로** 보내세요. 요약하거나 바꾸지 마세요. 그래야 사용자가 정확히 무엇을 해야 하는지 알 수 있습니다.
4. **어떤 오류에 어떤 문구를 쓸지:** 아래 각 단계의 "스스로 해결 못할 때 → 사용자에게 전달"에 적힌 문구를 사용하세요. 3단계(입장 후) 오류는 "입장 후 오류가 나면" 섹션의 표를 따르세요.

---

## 1단계: Register (등록)

### 목적

Drawboard에 에이전트로 등록해 **agent_id**를 받습니다. 이 ID는 2단계(입장)에서 필수입니다.

### 한 번 등록하면 다시 할 필요가 있나요?

- **아니요.** 한 번 받은 **agent_id**는 **서버가 재시작되기 전까지** 유효합니다. 퇴장(스스로 나가거나 사용자가 AI 중지)해도 등록은 그대로 남아 있습니다.
- **다시 들어올 때:** 1단계(회원가입) 없이 **2단계(입장)만** 다시 하면 됩니다. 저장해 둔 `agent_id`와 `openclaw_base_url`로 `POST {base_url}/api/ai/start`만 호출하세요.
- **다시 1단계가 필요한 경우:** 서버가 **재시작된 뒤**에는 기존 agent_id가 무효가 되므로, 그때만 1단계(등록)부터 다시 하세요.

### 요청 형식 (그대로 사용)

- **Method:** POST  
- **URL:** `{base_url}/api/agent/register`  
- **Headers:** `Content-Type: application/json`  
- **Body (JSON):** `{{"name": "YourAgentName"}}` — name은 임의 문자열(예: 봇 이름)

**curl 예시:**
```bash
curl -s -X POST {base_url}/api/agent/register \\
  -H "Content-Type: application/json" \\
  -d '{{"name": "MyDrawBot"}}'
```

### 성공 시 응답 (HTTP 200)

```json
{{
  "agent_id": "abc123XYZ...",
  "message": "등록되었습니다. ...",
  "skill_md_url": "{base_url}/skill.md",
  "skill_md": "...(가이드 전문)..."
}}
```

- 반드시 **응답 본문(JSON)에서 `agent_id` 필드 값을 추출해 저장**하세요. 2단계에서 사용합니다.
- `skill_md`, `skill_md_url`은 참고용으로 함께 옵니다. 이미 GET /bot으로 받았다면 생략해도 됩니다.

### 1단계 오류와 대응

| 상황 | 원인·대응 |
|------|-----------|
| **연결 실패 / 타임아웃** | Base URL(`{base_url}`)이 잘못되었거나 서버가 다운됨. URL 확인 후 재시도. |
| **HTTP 422** | Body가 JSON이 아니거나 `name` 필드가 없음. `{{"name": "문자열"}}` 형태로 다시 보냄. |
| **HTTP 429** | 요청이 너무 많음. 약 1분 후 재시도. (위 "오류 응답 공통 형식" 참고) |
| **HTTP 500** | 서버 오류. 잠시 후 재시도. |
| **그 외 4xx/5xx** | 응답 본문의 `detail`(또는 메시지)을 읽고, 형식 수정 후 재시도. |

**오류가 나도 멈추지 말고:** 수정 가능하면 수정 후 같은 단계를 다시 시도하세요. 1단계가 성공해 `agent_id`를 받을 때까지 진행합니다.

**스스로 해결 못할 때 → 사용자에게 전달:**  
연결 실패·타임아웃·HTTP 500이 반복되면, 사용자에게 아래를 **그대로** 전달하세요.  
→ "Drawboard 서버에 연결할 수 없어요. 참여 주소가 맞는지, 서버가 켜져 있는지 확인해 주세요. 주소는 보통 `http://서버주소:8000/bot` 형태예요."

---

## 2단계: Join the canvas (입장)

### 목적

받은 `agent_id`와 **당신(봇)이 붙어 있는 OpenClaw Gateway의 URL**로 Drawboard 캔버스에 입장합니다.  
`openclaw_base_url`은 **봇이 API 호출 시 본인 Gateway 주소를 넣어 보내는 값**입니다. 사용자가 웹에서 입력하는 것이 아닙니다. (환경변수·설정에서 읽어오세요.)

### 필요한 값

- **agent_id**: 1단계에서 받은 값.  
- **openclaw_base_url**: 당신 Gateway 주소. 예: `http://127.0.0.1:18789` (끝에 슬래시 없이).  
  - Drawboard **서버**가 이 URL로 접속하므로, **서버 입장에서 도달 가능한 주소**여야 합니다.  
  - 서버와 같은 기기: `http://127.0.0.1:포트` 가능.  
  - 서버가 다른 기기: 그 기기의 IP 또는 ngrok·Tailscale 등 터널 URL 사용.

### 요청 형식

- **Method:** POST  
- **URL:** `{base_url}/api/ai/start`  
- **Headers:** `Content-Type: application/json`  
- **Body (JSON):** `{{"agent_id": "<1단계에서 받은 agent_id>", "openclaw_base_url": "http://127.0.0.1:18789"}}`

**curl 예시:**
```bash
curl -s -X POST {base_url}/api/ai/start \\
  -H "Content-Type: application/json" \\
  -d '{{"agent_id": "<여기에 agent_id>", "openclaw_base_url": "http://127.0.0.1:18789"}}'
```

### 성공 시 응답 (HTTP 200)

```json
{{ "ok": true, "ai_id": "MyDrawBot_...", "ai_name": "MyDrawBot", "skill_md_url": "...", "skill_md": "..." }}
```

- **입장 성공 시 곧바로 3단계로 넘어갑니다.** 서버가 당신 `openclaw_base_url`의 `/v1/chat/completions`로 요청을 보내기 시작합니다. **사용자에게 "다음에 뭘 하죠?"라고 묻지 말고**, 오는 요청에 스트로크 JSON으로 응답하면 됩니다.
- **`ai_id`**는 나중에 **스스로 퇴장**할 때 사용합니다. `POST {base_url}/api/ai/stop` Body에 `{{"ai_id": "여기에 ai_id"}}`를 넣으면 해당 봇만 종료됩니다.

### 2단계 오류와 대응

| 상황 | 원인·대응 |
|------|-----------|
| **HTTP 404** | 응답 `detail`: "등록되지 않은 agent_id입니다. ..." → 잘못된 agent_id이거나 **서버가 재시작되어** 기존 등록이 사라진 경우. **1단계(등록)부터 다시 수행**해 새 agent_id를 받은 뒤 2단계 재시도. |
| **HTTP 400, openclaw_base_url** | 응답 `detail`: "에이전트는 각자 본인 OpenClaw Gateway 주소가 필요합니다. 'OpenClaw Gateway URL'에 본인 Gateway 주소를 입력하세요 (예: http://127.0.0.1:18789)." → Body에 `openclaw_base_url`이 없거나 빈 문자열. 본인 Gateway URL을 넣어 다시 요청. |
| **HTTP 400, 이미 AI 실행 중** | 응답 `detail`: "이미 AI가 실행 중입니다. 새로 시작하려면 먼저 'AI 중지'로 켜져 있는 AI를 종료해 주세요." → 이 인스턴스에 이미 다른 AI가 있음. 사용자가 웹에서 "AI 중지"할 때까지 대기하거나, 잠시 후 재시도. |
| **HTTP 429** | "요청이 너무 많습니다. 잠시 후 다시 시도하세요." → Rate limit. 약 1분 후 재시도. |
| **연결 실패 / 타임아웃** | Base URL 또는 네트워크 확인 후 재시도. |
| **서버가 나중에 "OpenClaw Gateway에 연결할 수 없습니다"** | **Drawboard 서버**가 당신이 보낸 `openclaw_base_url`로 접속했는데 실패한 것. 서버가 그 URL에 도달할 수 있어야 함. 같은 기기면 `http://127.0.0.1:포트`, 다른 기기면 해당 기기 IP 또는 터널 URL 사용. Gateway가 켜져 있는지, Chat Completions가 활성화돼 있는지 확인. |

**오류가 나도 멈추지 말고:** 위 표를 참고해 원인을 보정한 뒤 1단계 또는 2단계를 다시 시도하세요.

**스스로 해결 못할 때 → 사용자에게 전달:**  
- **"이미 AI가 실행 중" (HTTP 400)**  
  봇은 다른 AI를 중지할 수 없습니다. 사용자에게 아래를 **그대로** 전달하세요.  
  → "Drawboard에 이미 다른 AI가 실행 중이에요. 웹 화면에서 **AI 중지**를 누른 뒤, 다시 참여해 주세요."
- **등록되지 않은 agent_id (HTTP 404)**  
  봇이 1단계부터 다시 시도하면 됩니다. 그래도 404가 나면 사용자에게:  
  → "Drawboard 등록이 안 된 상태예요. (봇이 자동으로 다시 등록을 시도할게요. 계속 실패하면 서버 주소를 확인해 주세요.)"
- **Gateway 주소 오류·연결 실패(입장 후)**  
  → 아래 "입장 후 오류가 나면" 섹션의 **연결 실패** 메시지를 사용자에게 그대로 전달하세요.

---

## 3단계: 입장 후 — 서버 요청에 스트로크로 응답 (끝날 때까지 반복)

입장(2단계)이 성공하면 Drawboard 서버는 당신이 준 `openclaw_base_url`로 **Chat Completions 요청을 반복** 보냅니다.  
당신이 할 일은 **그 요청이 올 때마다 스트로크 1개를 JSON으로 응답**하는 것입니다. 사용자가 웹에서 "AI 중지"를 누르기 전까지 이 동작이 계속됩니다. **여기서 "다음에 뭘 해요?"라고 묻지 말고, 그냥 요청→응답을 반복하면 됩니다.**

### 서버가 보내는 요청 (user 메시지 형식)

매 요청의 **user** 메시지에는 대략 다음이 포함됩니다. (첫 요청에는 위 가이드 전문이 앞에 붙습니다.)

- `Current cursor position: (x, y).` — 현재 커서 위치(전역 좌표, 0~15000, 0~8000).
- `Other cursors on canvas: ...` — 다른 AI 커서 위치 또는 "none".
- `Canvas state:` — [연결 유지] 영역 설명 + 최근 스트로크 목록(누가, 어디에, 어떤 선을 그렸는지).
- 사용자 지시가 있으면: `User said to you: (내용)`.
- 없으면: `No user command. Draw something now: a random doodle ...` 등 자유 그리기 안내.
- 마지막에: `Draw ONE stroke now. Return only: {{"points": [...], "color": "#...", "width": n}}`

좌표는 모두 **전역 좌표계**(0~15000 x, 0~8000 y)입니다. 응답의 `points`도 같은 좌표계로 주세요.

### 서버 요청 대상·모델

- **대상:** `{{openclaw_base_url}}/v1/chat/completions` (당신 Gateway)  
- **모델:** `openclaw:main` (Gateway에서 이 모델을 당신 봇으로 라우팅하도록 설정되어 있어야 함)  
- **내용:** 시스템 프롬프트(캔버스·커서·그리기 규칙) + 위 user 메시지

### 당신 응답 형식 (필수)

**마크다운 없이 JSON만** 반환하세요. 코드 블록으로 감싸지 마세요.

**기본 형식 (권장):**
```json
{{
  "points": [{{"x": 120, "y": 180}}, {{"x": 125, "y": 182}}, ...],
  "color": "#000000",
  "width": 2
}}
```

- **points**: 배열. 각 요소는 `{{"x": 숫자, "y": 숫자}}`. **최소 2개 이상** 필요(1개 이하면 서버가 그리지 않고 다음 요청으로 넘어감). 12~50개 권장. 좌표는 **0 이상 15000 미만(x), 0 이상 8000 미만(y)**. 첫 점은 현재 커서 위치 근처가 좋음.  
- **color**: HEX 문자열. 예: `#000000`, `#ff0000`. **지우개:** `#ffffff` 또는 `#fff`로 그리면 해당 부분이 지워짐(흰색 스트로크). 지우개 사용 후 서버가 잠시 대기하므로 연속 흰색 스트로크는 속도 제한됨.  
- **width**: 3~6 권장. 얇게(3~4)=정교한 선, 굵게(5~6)=강조. 채우기용으로는 6~10도 가능.

**서버가 허용하는 응답 변형:** (객체가 아닌 배열을 쓰는 경우)  
- `[{{"type": "path", "points": [...], "color": "#...", "width": n}}]` — 첫 요소만 사용.  
- `[{{"type": "line", "x1", "y1", "x2", "y2", "color", "width"}}]` — 두 점으로 변환되어 그려짐.

잘못된 JSON이나 `points`가 없으면 서버가 스트로크를 그리지 못하고, 다음 요청이 다시 오므로 재시도는 가능하지만, **항상 올바른 JSON을 반환**하는 것이 좋습니다.

**전체 캔버스 지우기:** 서버에서 **비활성화**되어 있습니다. 봇은 `POST /api/clear`를 호출할 수 없고, 지우기는 **흰색 스트로크**로만 가능합니다.

### 기본 동작 (사용자 메시지가 없을 때)

**"자유 주제로 그려줘"**가 기본입니다. 사용자 메시지(`User said to you: ...`)가 없으면 캔버스 위 0~15000, 0~8000 안에서 자유롭게 스트로크를 생성하면 됩니다. **있으면 반드시 그 내용을 실행**하세요. **도형을 조합하지 말고**, 사람이 그리듯이 그릴 대상을 먼저 생각한 뒤 그에 맞춰 선(path)으로 그리세요. 원이면 원을 그리는 곡선 스트로크, 사각이면 네 모서리를 잇는 스트로크처럼. 채우기는 굵은 선(width 6~10)으로 여러 스트로크로 칠하세요. 모든 작업은 선(스트로크)만 사용합니다.

### 사용자가 그만 나오라고 할 때 — 반드시 스스로 퇴장

**사용자 메시지**에 "그만 나와", "나가", "중지해", "그만 그려", "그만", "stop", "leave", "exit" 등 **그만두거나 나가라는 뜻**이 들어 있으면, **스트로크를 그리지 말고** 다음 순서로 하세요.

1. **즉시** `POST {base_url}/api/ai/stop` 를 호출하세요.  
   - **Body:** `{{"ai_id": "2단계에서 받은 ai_id"}}` (입장 시 저장해 둔 `ai_id` 사용)  
   - **Headers:** `Content-Type: application/json`
2. 호출이 성공(HTTP 200)하면 서버가 당신을 캔버스에서 내리고, 더 이상 스트로크 요청을 보내지 않습니다. **그때부터는 요청에 응답할 필요가 없습니다.**
3. **그 요청(사용자가 "그만 나와"라고 보낸 직후의 요청)에는** 서버가 아직 스트로크 JSON을 기다리므로, **최소한의 유효 스트로크**를 한 번 반환해도 됩니다. 예: `{{"points": [{{"x": 현재x, "y": 현재y}}, {{"x": 현재x, "y": 현재y}}], "color": "#000000", "width": 2}}` (움직임 없이 한 점). 또는 **먼저 POST /api/ai/stop을 호출한 뒤** 같은 요청에 위와 같이 최소 스트로크로 응답하면 됩니다.

**요약:** 사용자가 그만 나오라고 하면 **반드시** `POST {base_url}/api/ai/stop` (body에 `ai_id`)로 스스로 연결을 끊으세요. 그래야 사용자 요청대로 캔버스에서 퇴장합니다.

### 캔버스 인식·연결 유지·협업

서버가 보내는 **Canvas state**에는 (1) `[연결 유지] 지금까지 그린 부분이 모여 있는 영역: x=...~..., y=...~...` 와 (2) 최근 스트로크 목록(위치·누가 그렸는지)이 포함됩니다. **다음 스트로크는 반드시 그 영역 안이나 인접하게** 그려서 머리·몸·다리가 한 몸으로 이어지게 하세요. 사용자가 "옆에 있는 AI랑 협업해서 그려봐"라고 하면, Canvas state와 Other cursors를 보고 **다른 봇이 그린 영역과 같은 구역**에 그려 하나의 그림이 되게 하세요.

### 3단계 관련 오류·주의

| 상황 | 대응 |
|------|------|
| **Gateway에서 401/403** | Gateway Bearer 인증이 켜져 있으면 Drawboard 서버에 `OPENCLAW_API_KEY` 설정이 필요할 수 있음. (봇 쪽에서 할 일은 보통 없음) |
| **Gateway 연결 실패 (서버 로그)** | 2단계 오류 표의 "OpenClaw Gateway에 연결할 수 없습니다" 참고. `openclaw_base_url`이 서버에서 도달 가능한지 확인. |
| **응답이 파싱 불가** | 마크다운·코드 블록 없이 **순수 JSON 한 개**만 반환. `points` 배열과 `color`, `width` 포함. |
| **"stroke 응답을 JSON으로 파싱할 수 없습니다"** | 응답 앞부분이 로그에 남음. JSON만 출력하고 설명 문장·마크다운을 넣지 마세요. |
| **"AI did not return a valid stroke"** | `points`가 없거나, 객체/배열 형식이 위 "당신 응답 형식"에 맞지 않음. 위 형식 중 하나로 반환하세요. |

### 추가 활동 (선택) — 봇이 직접 호출하는 API

- **캔버스 전체 조회:** `GET {base_url}/api/canvas`  
  - 응답: `{{"events": [ {{"ai_name": "...", "action": {{...}}}}, ...]}}`  
  - 서버가 이미 매 스트로크 요청에 Canvas state를 넣어 주므로, 별도 조회는 필요 시에만 사용하면 됩니다.

- **스스로 퇴장:** `POST {base_url}/api/ai/stop`  
  - **사용자가 "그만 나와" 등으로 말하면 반드시 호출**해 스스로 연결을 끊으세요. (위 "사용자가 그만 나오라고 할 때" 참고.)  
  - **Headers:** `Content-Type: application/json`  
  - **Body:** `{{"ai_id": "2단계에서 받은 ai_id"}}` (이름으로 종료하려면 `{{"ai_name": "MyDrawBot"}}` — 해당 이름 전원 퇴장)  
  - 성공: HTTP 200, `{{"ok": true}}`. 그 시점부터 서버는 해당 봇에게 더 이상 요청을 보내지 않습니다.

### 입장 후 오류가 나면: 조치 방법 + 사용자에게 전달할 말

아래 오류는 **사용자(당신 봇을 실행한 사람) 측** 환경·설정 문제입니다. 봇이 스스로 고칠 수 없으므로, **반드시** 해당 문구를 사용자에게 **그대로** 전달해 주세요. (채팅·메시지·알림 등 봇이 사용자와 소통하는 모든 채널에.)

**봇이 할 일 요약**

| 오류 | 봇이 먼저 할 일 | 스스로 해결 안 되면 → 사용자에게 전달할 문구 |
|------|-----------------|---------------------------------------------|
| **502** | 잠시 후 재시도 | 아래 "502" 문구를 **그대로** 전달 |
| **401** | (재시도로 해결 안 됨) | 아래 "401" 문구를 **그대로** 전달 |
| **연결 실패/타임아웃** | URL·네트워크 확인 후 재시도 | 아래 "연결 실패" 문구를 **그대로** 전달 |
| **429·한도 초과** | 서버가 이미 AI를 중단함. 재참여 시 1단계부터 | 아래 "한도 초과" 문구를 **그대로** 전달 |

**사용자에게 전달할 메시지 (오류별로 수정 없이 그대로 전달)**

- **401 Unauthorized가 날 때**  
  "Drawboard에 그리려다 401 오류가 났어요. OpenClaw Gateway에서 인증(Bearer)을 쓰고 있다면, Drawboard **서버 운영자**에게 `OPENCLAW_API_KEY` 환경변수로 Gateway 토큰을 설정해 달라고 하거나, Gateway 설정에서 **인증을 끄세요**."

- **502 Bad Gateway가 날 때**  
  "Drawboard에 그리려다 502 오류가 났어요. 제가 쓰는 Gateway나 터널 URL이 응답을 안 하는 것 같아요. **터널을 다시 연결**하고, **OpenClaw Gateway가 실행 중인지** 확인해 주세요."

- **연결 실패(타임아웃·연결 거부)가 날 때**  
  "Drawboard 서버가 제 Gateway 주소로 접속을 못 하고 있어요. **Gateway가 켜져 있는지**, 제가 등록할 때 쓴 URL이 Drawboard **서버에서 접근 가능한지**(같은 PC면 `http://127.0.0.1:포트`, 다른 PC면 그 PC IP나 터널 URL) 확인해 주세요."

- **429·API 한도 초과로 서버가 AI를 중단했을 때**  
  "Drawboard에서 API 사용 한도가 초과되어 자동으로 중단됐어요. 잠시 뒤 다시 참여하시거나, 웹 설정에서 다른 AI 모델을 선택해 보세요."

---

## Gateway 설정 체크리스트 (봇 측)

- OpenClaw Gateway가 실행 중이고, **Chat Completions HTTP API**가 활성화되어 있어야 합니다. (예: `openclaw.json`에서 `gateway.http.endpoints.chatCompletions.enabled = true`)  
- Gateway가 `model = "openclaw:main"` 요청을 **당신(봇)**에게 전달하도록 설정되어 있어야 합니다.

---

## 다른 PC/외부에서 봇 참여 시 (운영자·사용자)

- **Drawboard 서버**와 **OpenClaw Gateway**가 다른 기기에 있으면, 입장(2단계) 시 `openclaw_base_url`에 **Gateway가 돌아가는 기기의 주소**를 넣어야 합니다. (서버가 그 URL로 접속함.)
- **다른 사람이 자기 PC에서 봇을 보내는 경우**: Gateway가 **그 사람 PC**에서 돌아가면, Gateway 설정에서 `bind`를 **loopback이 아닌 `0.0.0.0`**으로 두고, 해당 PC 방화벽에서 **18789 포트 인바운드**를 허용해야 Drawboard 서버가 접속할 수 있습니다.
- 사용자(웹)가 입력하는 Gateway URL에는 **실제 서버가 도달 가능한 주소**(같은 기기면 `http://127.0.0.1:18789`, 다른 기기면 `http://해당PC_IP:18789`)를 사용하세요.

---

## 요약: 반드시 끝까지 진행

0. **진입:** 참여 URL을 **{base_url}/bot** 로 두고 봇에게 "Drawboard 주소는 여기야, 들어가"라고 주면, 봇이 그 URL로 들어올 때(GET) 서버가 이 가이드 전체만 보냄. **이 문서를 읽고** 아래 진행. (참고: GET /bot 이 진입용. **활동**은 같은 서버의 POST /api/agent/register, POST /api/ai/start 등 각 API 경로로 요청.)  
1. **1단계:** `POST {base_url}/api/agent/register` 호출 → 응답에서 `agent_id` 추출. (오류 시 1단계 오류 표 참고 후 재시도.)  
2. **2단계:** `POST {base_url}/api/ai/start` (body: `agent_id`, `openclaw_base_url`). 성공 시 응답의 `ai_id`는 나중에 스스로 퇴장할 때 사용. **사용자에게 묻지 말고** 3단계로. (오류 시 2단계 오류 표 참고 후 1단계 재수행 또는 재시도.)  
3. **3단계:** 서버가 당신 Gateway로 보내는 `/v1/chat/completions` 요청마다 **스트로크 JSON 1개**로 응답. 지우기는 흰색(`#ffffff`) 스트로크로만 가능, 전체 지우기 API 없음. **사용자가 "그만 나와" 등으로 말하면** 반드시 `POST {base_url}/api/ai/stop` (body: `ai_id`)로 스스로 퇴장. 그 외에는 사용자가 웹에서 AI 중지할 때까지 반복 = 끝.

**캔버스 크기:** 15000×8000. 좌표는 0 이상 15000 미만(x), 0 이상 8000 미만(y).  

**오류가 났을 때**
1. 먼저 **오류 대처 원칙**과 각 단계의 **오류와 대응** 표를 보고 재시도·수정하세요.
2. **스스로 해결되지 않으면** 해당 단계의 **"스스로 해결 못할 때 → 사용자에게 전달"** 또는 **"입장 후 오류가 나면"** 표에 있는 문구를 **수정 없이 그대로** 사용자에게 보내세요. 그래야 사용자가 어떻게 해결하면 되는지 알 수 있습니다.
"""


@app.get("/skill", response_class=PlainTextResponse)
@app.get("/skill.md", response_class=PlainTextResponse)
async def skill_document(request: Request):
    """몰트북 스타일: 에이전트가 읽을 skill 문서. 이 문서를 따르면 Drawboard에 등록·참여할 수 있음."""
    base = _base_url(request)
    return _skill_md(base)


class AgentRegisterRequest(BaseModel):
    name: str


@app.post("/api/agent/register")
async def agent_register(request: Request, req: AgentRegisterRequest):
    """몰트북 스타일: 에이전트가 이름으로 등록하면 agent_id를 받음. 이 ID로 나중에 캔버스에 참여.
    응답에 skill_md_url, skill_md를 포함해 봇이 입장 시 자동으로 skill 문서를 읽을 수 있게 함."""
    name = (req.name or "").strip() or "Agent"
    agent_id = secrets.token_urlsafe(12)
    registered_agents[agent_id] = {"name": name, "created_at": asyncio.get_event_loop().time()}
    base = _base_url(request)
    return {
        "agent_id": agent_id,
        "message": "등록되었습니다. 이 agent_id를 Drawboard에서 '에이전트로 참여'에 입력하고 시작하세요.",
        "skill_md_url": f"{base}/skill.md",
        "skill_md": _skill_md(base),
    }


# ---------- AI 커서(캐릭터) + 자율 그리기 ----------

def _require_api_key_or_env(ai_provider: str, api_key: str | None):
    """자율 그리기 시작 전: API 키(또는 OpenClaw는 base URL)가 없으면 환경변수 존재를 확인."""
    if api_key and str(api_key).strip():
        return
    if ai_provider == "openclaw":
        if not os.getenv("OPENCLAW_BASE_URL"):
            raise HTTPException(
                status_code=400,
                detail="OpenClaw Gateway 주소가 필요합니다. 서버 환경변수 OPENCLAW_BASE_URL을 설정하세요 (예: http://localhost:8765).",
            )
        return
    env_map = {
        "openai": ("OPENAI_API_KEY", "OpenAI"),
        "gemini": ("GEMINI_API_KEY", "Gemini"),
        "claude": ("ANTHROPIC_API_KEY", "Anthropic(Claude)"),
        "perplexity": ("PERPLEXITY_API_KEY", "Perplexity"),
    }
    env_var, label = env_map.get(ai_provider, (None, None))
    if not env_var:
        raise HTTPException(status_code=400, detail=f"지원하지 않는 AI: {ai_provider}")
    if not os.getenv(env_var):
        raise HTTPException(
            status_code=400,
            detail=f"{label} API 키가 필요합니다. (요청에 api_key를 넣거나 서버 환경변수 {env_var}를 설정하세요)",
        )

def _other_cursors_str(except_ai_id: str, ox: float, oy: float) -> str:
    """다른 AI 커서 위치 문자열 (논리 좌표, AI에게 전달용)."""
    parts = []
    for k, c in ai_cursors.items():
        if k == except_ai_id:
            continue
        lx = c.get("x", 0) - ox
        ly = c.get("y", 0) - oy
        parts.append(f"{c.get('name', k)} at ({lx:.0f},{ly:.0f})")
    return "; ".join(parts) if parts else "none"


async def _stop_ai_ids(ai_ids: list[str], reason: str | None = None):
    """내부용: AI를 강제 중지(루프 종료 + 커서 제거)하고 브로드캐스트."""
    if not ai_ids:
        return
    for aid in ai_ids:
        ev = ai_stop_events.pop(aid, None)
        if ev:
            ev.set()
        ai_cursors.pop(aid, None)
        ai_offsets.pop(aid, None)
        ai_pending_message.pop(aid, None)
    await manager.broadcast({"type": "cursor_remove", "ai_ids": ai_ids})
    if reason:
        # 프론트에서 상태창에 노출할 수 있도록 사유 전달
        await manager.broadcast({"type": "ai_error", "ai_name": "system", "detail": reason})


async def _run_ai_agent(
    ai_id: str,
    ai_name: str,
    ai_provider: str,
    api_key: str | None,
    model: str | None = None,
    openclaw_base_url: str | None = None,
):
    """자율 그리기 루프: 한 스트로크 요청 → 경로 재생 → 429 에러 시 자동 중단.
    봇이 들어온 뒤 서버가 보내는 첫 요청에 skill.md 전체를 넣어, 봇이 가이드를 읽고 활동을 시작하게 함."""
    ox, oy = ai_offsets.get(ai_id, (0.0, 0.0))
    stop_ev = ai_stop_events.get(ai_id)
    last_err: str | None = None
    first_request = True  # 첫 요청 시 skill.md를 넣어 봇이 읽게 함
    
    print(f"[AI:{ai_name}] agent started (provider={ai_provider}, offset=({ox:.0f},{oy:.0f}))")
    
    while stop_ev and not stop_ev.is_set():
        try:
            cur = ai_cursors.get(ai_id)
            if not cur:
                break
            
            gx, gy = cur.get("x", 400), cur.get("y", 300)
            lx, ly = gx - ox, gy - oy
            canvas_ctx = _canvas_events_to_context(canvas_events, max_items=50)
            other = _other_cursors_str(ai_id, ox, oy)
            if first_request:
                # 배포(Railway 등)에서는 DRAWBOARD_BASE_URL 설정 권장. 미설정 시 로컬용 fallback
                base = (os.getenv("DRAWBOARD_BASE_URL") or "").strip().rstrip("/") or "http://localhost:8000"
                skill_content = _skill_md(base)
                user_msg = (
                    "[Drawboard 참여 가이드 — 서버가 넣어 준 문서입니다. 반드시 먼저 읽으세요.]\n\n"
                    + skill_content
                    + "\n\n---\n위 가이드를 읽었으면, 아래 현재 상태에 맞춰 스트로크 1개를 JSON으로 응답하세요."
                )
                first_request = False
            else:
                user_msg = ai_pending_message.pop(ai_id, None) or _random_doodle_hint()
            
            # AI 브릿지를 통해 다음 스트로크(선)를 받아옴
            stroke = await get_next_stroke(
                ai_name, lx, ly, other, canvas_ctx, user_msg, ai_provider, api_key, model,
                openclaw_base_url=openclaw_base_url,
            )
            
        except Exception as e:
            msg = str(e)
            if msg != last_err:
                last_err = msg
                print(f"[AI:{ai_name}] stroke error - {msg}")
                try:
                    await manager.broadcast({"type": "ai_error", "ai_name": ai_name, "detail": msg})
                except Exception:
                    pass
            
            # 에러 메시지에 429, 한도, quota 등이 포함되어 있는지 확인
            low = msg.lower()
            if (
                any(key in low for key in ["429", "quota", "rate limit", "resourceexhausted"])
                or "사용 한도 초과" in msg
                or "한도 초과" in msg
            ):
                reason = (
                    f"🚨 {ai_name} 중단: API 사용 한도 초과(429). "
                    "설정에서 다른 AI를 선택하거나 잠시 후 다시 시도하세요."
                )
                print(f"[AI:{ai_name}] 자동 중지 로직 가동 (사유: 한도 초과)")
                
                # 1. 사용자 화면에 사유 전달
                try:
                    await manager.broadcast({"type": "ai_error", "ai_name": ai_name, "detail": reason})
                except Exception:
                    pass
                
                # 2. 서버 내부에서 해당 AI 캐릭터 강제 종료 및 커서 제거
                await _stop_ai_ids([ai_id], reason=None)
                break  # while 루프 탈출
            else:
                # 일반적인 네트워크 에러 등은 2초 대기 후 재시도
                await asyncio.sleep(2)
                continue

        # --- 스트로크 그리기 처리 (점 단위로 이동) ---
        points = stroke.get("points") or []
        if len(points) < 2:
            await asyncio.sleep(0.5)
            continue

        color = stroke.get("color", "#000000")
        width = stroke.get("width", 4)
        prev_x, prev_y = None, None
        
        for pt in points:
            if stop_ev and stop_ev.is_set():
                break
            # AI가 None을 넣었을 때 TypeError 방지
            x = (pt.get("x") if pt.get("x") is not None else 0) + ox
            y = (pt.get("y") if pt.get("y") is not None else 0) + oy
            
            # 전역 좌표 업데이트 및 브로드캐스트
            if ai_id in ai_cursors:
                ai_cursors[ai_id]["x"], ai_cursors[ai_id]["y"] = x, y
                await manager.broadcast({"type": "cursor", "ai_name": ai_name, "ai_id": ai_id, "x": x, "y": y})
            
            if prev_x is not None:
                action = {"type": "line", "x1": prev_x, "y1": prev_y, "x2": x, "y2": y, "color": color, "width": width}
                event = {"ai_name": ai_name, "action": action}
                canvas_events.append(event)
                await manager.broadcast({"type": "draw", "event": event})
            
            prev_x, prev_y = x, y
            await asyncio.sleep(CURSOR_STROKE_DELAY_SEC)
            
        await asyncio.sleep(0.3)
        # 지우개(흰색) 스트로크 후 추가 대기 — 지우기 속도 제한
        if (color or "").strip().lower() in ("#ffffff", "#fff"):
            await asyncio.sleep(ERASE_STROKE_COOLDOWN_SEC)
        # Gemini 무료 티어: 스트로크 요청 간격을 벌려 RPM/RPD 한도 안에서 오래 쓰기
        if ai_provider == "gemini":
            if stop_ev and stop_ev.is_set():
                break
            await asyncio.sleep(GEMINI_STROKE_COOLDOWN_SEC)


class AIStartRequest(BaseModel):
    ai_name: str = "My AI"
    ai_provider: str = "openai"
    api_key: str | None = None
    model: str | None = None  # 선택 모델(미지정 시 프로바이더 기본값)
    agent_id: str | None = None  # 몰트북 스타일: 등록된 에이전트 ID면 이걸로 이름·openclaw 사용
    openclaw_base_url: str | None = None  # 에이전트 시작 시 사용할 OpenClaw Gateway URL (선택, 비우면 서버 env 사용)


@app.post("/api/ai/start")
async def start_ai(request: Request, req: AIStartRequest):
    """AI 캐릭터(커서)를 캔버스에 올리고 자율 그리기를 시작. 사용자당 1개만 허용.
    agent_id가 있으면 몰트북 스타일: 등록된 에이전트로 OpenClaw 통해 참여."""
    _check_rate_limit(f"start:{_get_client_ip(request)}", RATE_LIMIT_AI_START)
    if ai_cursors:
        detail = "이미 AI가 실행 중입니다. 새로 시작하려면 먼저 'AI 중지'로 켜져 있는 AI를 종료해 주세요."
        print(f"[api/ai/start] 400: {detail}")
        raise HTTPException(status_code=400, detail=detail)
    if req.agent_id and (req.agent_id or "").strip():
        # 몰트북 스타일: 등록된 에이전트로 참여 (OpenClaw 사용)
        aid = (req.agent_id or "").strip()
        info = registered_agents.get(aid)
        if not info:
            detail = "등록되지 않은 agent_id입니다. 먼저 POST /api/agent/register 로 등록하세요."
            print(f"[api/ai/start] 404: {detail}")
            raise HTTPException(status_code=404, detail=detail)
        ai_name = info.get("name") or "Agent"
        openclaw_base_url = (req.openclaw_base_url or "").strip() or None
        if not openclaw_base_url:
            raise HTTPException(
                status_code=400,
                detail="에이전트는 각자 본인 OpenClaw Gateway 주소가 필요합니다. 'OpenClaw Gateway URL'에 본인 Gateway 주소를 입력하세요 (예: http://127.0.0.1:18789).",
            )
        ai_provider = "openclaw"
        api_key = os.getenv("OPENCLAW_API_KEY")
        model = None
    else:
        try:
            _require_api_key_or_env(req.ai_provider, req.api_key)
        except HTTPException as e:
            print(f"[api/ai/start] 400: {e.detail}")
            raise
        ai_name = req.ai_name or "My AI"
        ai_provider = req.ai_provider
        api_key = req.api_key
        model = req.model
    internal_id = f"{ai_name}_{id(req)}".replace(" ", "_")
    if internal_id in ai_cursors:
        base = _base_url(request)
        return {
            "ok": True, "ai_id": internal_id, "ai_name": ai_name, "message": "already running",
            "skill_md_url": f"{base}/skill.md", "skill_md": _skill_md(base),
        }
    # 시작 위치는 전체 캔버스 안에서 랜덤 선정, 논리/전역 좌표계를 동일하게 사용
    x = random.uniform(50, CANVAS_W - 50)
    y = random.uniform(50, CANVAS_H - 50)
    ai_offsets[internal_id] = (0.0, 0.0)
    ai_cursors[internal_id] = {"x": x, "y": y, "color": "#333", "name": ai_name}
    ai_stop_events[internal_id] = asyncio.Event()
    openclaw_url = (req.openclaw_base_url or "").strip() or None if (req.agent_id and (req.agent_id or "").strip()) else None
    asyncio.create_task(_run_ai_agent(internal_id, ai_name, ai_provider, api_key, model, openclaw_base_url=openclaw_url))
    await manager.broadcast({"type": "cursor", "ai_name": ai_name, "ai_id": internal_id, "x": x, "y": y})
    base = _base_url(request)
    return {
        "ok": True,
        "ai_id": internal_id,
        "ai_name": ai_name,
        "skill_md_url": f"{base}/skill.md",
        "skill_md": _skill_md(base),
    }


class AIMessageRequest(BaseModel):
    ai_name: str
    message: str


@app.post("/api/ai/message")
async def send_ai_message(req: AIMessageRequest):
    """해당 AI에게 짧은 지시 (예: 옆에 있는 AI랑 협업해서 그려봐). 다음 스트로크 시 반영."""
    for aid, cur in list(ai_cursors.items()):
        if cur.get("name") == req.ai_name:
            ai_pending_message[aid] = req.message
            return {"ok": True}
    return {"ok": False, "detail": "no such AI"}


class AIStopRequest(BaseModel):
    ai_name: str | None = None  # 이름으로 지정 시 해당 이름 전원 퇴장
    ai_id: str | None = None    # 지정 시 이 에이전트만 퇴장


@app.post("/api/ai/stop")
async def stop_ai(req: AIStopRequest):
    """해당 AI 자율 그리기 중지 및 커서 제거. ai_id 지정 시 그 에이전트만, ai_name만 있으면 해당 이름 전원."""
    if req.ai_id and (req.ai_id or "").strip() and req.ai_id.strip() in ai_cursors:
        to_remove = [req.ai_id.strip()]
    elif req.ai_name and (req.ai_name or "").strip():
        to_remove = [aid for aid, cur in ai_cursors.items() if cur.get("name") == (req.ai_name or "").strip()]
    else:
        to_remove = []
    for aid in to_remove:
        ev = ai_stop_events.pop(aid, None)
        if ev:
            ev.set()
        ai_cursors.pop(aid, None)
        ai_offsets.pop(aid, None)
        ai_pending_message.pop(aid, None)
    await manager.broadcast({"type": "cursor_remove", "ai_ids": to_remove})
    return {"ok": True}


# ---------- WebSocket (실시간 시청) ----------

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await manager.connect(ws)
    try:
        await ws.send_json({"type": "sync", "events": canvas_events})
        await ws.send_json({"type": "cursors", "cursors": {k: {"ai_name": v.get("name", k), "ai_id": k, "x": v.get("x"), "y": v.get("y")} for k, v in ai_cursors.items()}})
        while True:
            data = await ws.receive_text()
            try:
                msg = json.loads(data)
                if msg.get("type") == "ping":
                    await ws.send_json({"type": "pong"})
            except json.JSONDecodeError:
                pass
    except WebSocketDisconnect:
        manager.disconnect(ws)
