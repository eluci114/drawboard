"""
AI 브릿지: 사용자 요청 문장을 드로잉 명령(JSON)으로 변환.
OpenAI, Gemini 등 여러 AI를 지원해 "그려줘" 텍스트 -> line, circle, path 등 명령 리스트 생성.
"""
import os
import json
import asyncio
import warnings
import httpx

# Gemini: 새 SDK(google-genai) 우선, 없으면 구 패키지(google-generativeai) 사용 (FutureWarning 억제)
GEMINI_AVAILABLE = False
GEMINI_USE_NEW_SDK = False
genai_new = None
genai_old = None

try:
    from google import genai as genai_new
    GEMINI_AVAILABLE = True
    GEMINI_USE_NEW_SDK = True
except ImportError:
    try:
        warnings.filterwarnings("ignore", category=FutureWarning, module="google.generativeai")
        import google.generativeai as genai_old
        GEMINI_AVAILABLE = True
    except ImportError:
        pass

SYSTEM_PROMPT = """You are a drawing bot that draws like a HUMAN: you do not combine ready-made shapes (no "circle + rect + line" as building blocks). You imagine what to draw first, then draw it stroke by stroke with a pen — each stroke is a path or line, the way a person would draw on paper.
- Before drawing: (1) READ the current canvas. (2) PLAN the image in your head (e.g. "sun" → round outline, then rays; "house" → roof line, then walls, then door). (3) Draw that plan as a sequence of path and line strokes — circles are drawn as a curved path (many points along a circle), rectangles as a path with four corners or four lines, curves as smooth paths. No circle/rect primitives: only path and line.
- ONE figure = ONE region; collaboration: same region as others. ERASE: only WHITE (#ffffff) path over the area; no full clear.
- Colors: hex. Canvas: 15000x8000. Absolute coordinates.
Respond with a JSON array of drawing commands only. No markdown.

Command types (human-style: only path and line):
- line: { "type": "line", "x1", "y1", "x2", "y2", "color": "#000000", "width": 2 } — straight strokes
- path: { "type": "path", "points": [{"x", "y"}, ...], "color": "#000000", "width": 2, "close": true|false } — every curve, circle, outline, fill stroke. For a "circle": path with points along a circle. For "fill": many thick paths (width 6–10) side by side like coloring.

Do not use "circle" or "rect" type. Draw circles and rectangles as path (points tracing the shape) or as lines. Match your strokes to the image you planned.

Return ONLY the JSON array."""


def _user_message(text: str, canvas_context: str | None) -> str:
    """캔버스 맥락이 있으면 포함한 최종 사용자 메시지. AI가 캔버스를 읽고 판단한 뒤 그리도록 유도."""
    if canvas_context and canvas_context.strip():
        return (
            "Current canvas (read this first; positions help you locate existing elements):\n"
            + canvas_context.strip()
            + "\n\n---\nUser request: "
            + text
        )
    return text


def _check_api_error(resp: httpx.Response, provider: str) -> None:
    """API 오류 시 사용자에게 보기 좋은 메시지로 변환 및 429 상태코드 명시."""
    if resp.status_code == 401:
        if provider == "OpenClaw":
            print(
                "[OpenClaw] 로그: 401 Unauthorized — Gateway가 인증을 요구함. "
                "원인: 사용자 Gateway 인증 설정 + 서버 OPENCLAW_API_KEY 미설정/불일치. "
                "조치: 서버에 OPENCLAW_API_KEY로 Gateway Bearer 토큰 설정, 또는 사용자가 Gateway에서 인증 해제."
            )
            raise ValueError(
                "[401] OpenClaw Gateway가 인증을 요구합니다. "
                "Drawboard 서버에 OPENCLAW_API_KEY 환경변수로 Gateway Bearer 토큰을 설정하거나, "
                "Gateway 설정에서 인증을 끄세요."
            )
        raise ValueError(
            f"[{resp.status_code}] {provider} API 키가 유효하지 않거나 만료되었습니다. "
            f"키를 확인하거나 {provider} 콘솔에서 새 키를 발급하세요."
        )
    if resp.status_code == 502:
        print(
            "[OpenClaw] 로그: 502 Bad Gateway — 사용자가 준 Gateway/터널 URL이 502 반환. "
            "원인: 사용자 측 터널 끊김·Gateway 다운·일시 과부하. "
            "조치: 터널 재연결, Gateway 재실행, URL 유효 여부 확인. (Drawboard 서버 코드 문제 아님)"
        )
        raise ValueError(
            "[502] Gateway(또는 터널)가 Bad Gateway를 반환했습니다. "
            "Gateway가 정상 동작 중인지, 터널 URL이 살아 있는지 확인하세요."
        )
    if resp.status_code == 429:
        # 이 문자열에 '429'가 포함되어야 main.py에서 자동 중지가 작동합니다.
        raise ValueError(f"[{resp.status_code}] {provider} 사용 한도 초과 또는 요청 제한. 시스템을 자동 중지합니다.")
    if resp.status_code == 500:
        body = ""
        try:
            body = (resp.text or "")[:500]
        except Exception:
            pass
        if provider == "OpenClaw":
            print(
                "[OpenClaw] 로그: 500 Internal Server Error — Gateway가 요청 처리 중 오류 반환. "
                "Gateway 콘솔/로그에서 스택 트레이스·에러 원인 확인. "
                f"응답 본문 일부: {body!r}"
            )
            raise ValueError(
                "[500] OpenClaw Gateway가 500 오류를 반환했습니다. "
                "Gateway 쪽 로그(콘솔 또는 로그 파일)를 확인하세요. 모델 이름(openclaw:main)·라우팅·백엔드 설정이 맞는지 확인."
            ) from None
        raise ValueError(f"[500] {provider} 서버 오류. 잠시 후 재시도하거나 해당 서비스 상태를 확인하세요.") from None

    resp.raise_for_status()


async def text_to_draw_commands_openai(text: str, api_key: str, canvas_context: str | None = None) -> list[dict]:
    """OpenAI (ChatGPT)를 사용해 텍스트를 드로잉 명령으로 변환."""
    user_content = _user_message(text, canvas_context)
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": "gpt-4o-mini",
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_content},
                ],
                "temperature": 0.3,
            },
        )
        _check_api_error(r, "OpenAI")
        data = r.json()
        content = data["choices"][0]["message"]["content"].strip()
    # 코드 블록 제거
    if content.startswith("```"):
        content = content.split("\n", 1)[1] if "\n" in content else content[3:]
    if content.endswith("```"):
        content = content.rsplit("```", 1)[0].strip()
    return json.loads(content)


def _text_to_draw_commands_gemini_sync(text: str, api_key: str, canvas_context: str | None = None) -> list[dict]:
    """Google Gemini를 사용해 텍스트를 드로잉 명령으로 변환 (동기식)."""
    if not GEMINI_AVAILABLE:
        raise ImportError(
            "Gemini 패키지가 설치되지 않았습니다. "
            "Gemini를 사용하려면 'pip install google-genai' 또는 'pip install google-generativeai'를 실행하세요."
        )
    user_content = _user_message(text, canvas_context)
    prompt = f"{SYSTEM_PROMPT}\n\n{user_content}"
    if GEMINI_USE_NEW_SDK:
        client = genai_new.Client(api_key=api_key)
        response = client.models.generate_content(model="gemini-2.0-flash", contents=prompt)
        content = (response.text or "").strip()
    else:
        genai_old.configure(api_key=api_key)
        model = genai_old.GenerativeModel("gemini-2.0-flash")
        response = model.generate_content(prompt)
        content = (response.text or "").strip()
    # 코드 블록 제거
    if content.startswith("```"):
        content = content.split("\n", 1)[1] if "\n" in content else content[3:]
    if content.endswith("```"):
        content = content.rsplit("```", 1)[0].strip()
    return json.loads(content)


async def text_to_draw_commands_gemini(text: str, api_key: str, canvas_context: str | None = None) -> list[dict]:
    """Google Gemini를 사용해 텍스트를 드로잉 명령으로 변환 (비동기 래퍼)."""
    return await asyncio.to_thread(_text_to_draw_commands_gemini_sync, text, api_key, canvas_context)


async def text_to_draw_commands_claude(text: str, api_key: str, canvas_context: str | None = None) -> list[dict]:
    """Anthropic Claude를 사용해 텍스트를 드로잉 명령으로 변환."""
    user_content = _user_message(text, canvas_context)
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
            json={
                "model": "claude-3-5-haiku-20241022",
                "max_tokens": 4096,
                "system": SYSTEM_PROMPT,
                "messages": [{"role": "user", "content": user_content}],
            },
        )
        _check_api_error(r, "Anthropic(Claude)")
        data = r.json()
        content = ""
        for block in data.get("content", []):
            if block.get("type") == "text":
                content = block.get("text", "")
                break
        content = content.strip()
    if content.startswith("```"):
        content = content.split("\n", 1)[1] if "\n" in content else content[3:]
    if content.endswith("```"):
        content = content.rsplit("```", 1)[0].strip()
    return json.loads(content)


async def text_to_draw_commands_perplexity(text: str, api_key: str, canvas_context: str | None = None) -> list[dict]:
    """Perplexity를 사용해 텍스트를 드로잉 명령으로 변환 (OpenAI 호환 엔드포인트)."""
    user_content = _user_message(text, canvas_context)
    async with httpx.AsyncClient(timeout=45.0) as client:
        r = await client.post(
            "https://api.perplexity.ai/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": "llama-3.1-sonar-small-128k-online",
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_content},
                ],
                "max_tokens": 4096,
                "temperature": 0.3,
            },
        )
        _check_api_error(r, "Perplexity")
        data = r.json()
        content = data["choices"][0]["message"]["content"].strip()
    if content.startswith("```"):
        content = content.split("\n", 1)[1] if "\n" in content else content[3:]
    if content.endswith("```"):
        content = content.rsplit("```", 1)[0].strip()
    return json.loads(content)


async def text_to_draw_commands(
    text: str,
    ai_provider: str = "openai",
    api_key: str | None = None,
    canvas_context: str | None = None,
) -> list[dict]:
    """
    텍스트를 드로잉 명령으로 변환.

    Args:
        text: 사용자 요청 텍스트
        ai_provider: "openai" | "gemini" | "claude" | "perplexity"
        api_key: API 키 (없으면 환경변수에서 가져옴)
        canvas_context: 현재 캔버스 요약 (AI가 지우기/수정 요청 등에 참고)
    """
    if ai_provider == "openai":
        api_key = api_key or os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("OpenAI API 키가 필요합니다. api_key를 제공하거나 OPENAI_API_KEY 환경변수를 설정하세요.")
        return await text_to_draw_commands_openai(text, api_key, canvas_context)
    elif ai_provider == "gemini":
        api_key = api_key or os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise ValueError("Gemini API 키가 필요합니다. api_key를 제공하거나 GEMINI_API_KEY 환경변수를 설정하세요.")
        return await text_to_draw_commands_gemini(text, api_key, canvas_context)
    elif ai_provider == "claude":
        api_key = api_key or os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise ValueError("Anthropic(Claude) API 키가 필요합니다. api_key를 제공하거나 ANTHROPIC_API_KEY 환경변수를 설정하세요.")
        return await text_to_draw_commands_claude(text, api_key, canvas_context)
    elif ai_provider == "perplexity":
        api_key = api_key or os.getenv("PERPLEXITY_API_KEY")
        if not api_key:
            raise ValueError("Perplexity API 키가 필요합니다. api_key를 제공하거나 PERPLEXITY_API_KEY 환경변수를 설정하세요.")
        return await text_to_draw_commands_perplexity(text, api_key, canvas_context)
    else:
        raise ValueError(
            f"지원하지 않는 AI 제공자: {ai_provider}. "
            "openai, gemini, claude, perplexity 중 하나를 사용하세요."
        )


# ----- 자율 그리기(커서): 한 스트로크만 요청 -----

STROKE_SYSTEM = """You are a pen-cursor on a 15000x8000 canvas. You draw like a human: first imagine what you're drawing (e.g. a face, a tree, a sun), then draw it with strokes that match that image. Do not "compose shapes" — draw each line or curve as a path, the way a person would move a pen.
Return a JSON object with exactly: "points" (array of {"x", "y"}, 12 to 50 points), "color" (hex e.g. "#000000"), "width" (3 to 10).
- Plan in your head what this stroke is part of (outline of a circle? one side of a house? a ray of the sun?). Then output points that trace that stroke. Outline: width 3–6. Filling: width 6–10, one stroke at a time; over many turns you fill like coloring with a thick pen.
- First point near current cursor. Coordinates 0-15000 (x), 0-8000 (y). Circles = path along a circle; rectangles = path with 4 corners or segments; everything is path (or line for single straight segments).

IMPORTANT — coherence and collaboration:
- READ "Canvas state" and the "[연결 유지] ... 영역" line. Your next stroke MUST be drawn inside or next to that region so the picture stays ONE coherent figure, not scattered parts.
- When the user says to collaborate with another AI: place your stroke in the SAME area as others so the result is one picture.
- When adding to an existing drawing: draw your stroke so it CONNECTS or extends that same region.

ERASE (지워줘, 지우개, 전부 지워, etc.):
- You cannot clear the whole canvas (shared). For any erase request: return ONE stroke with "color": "#ffffff" and "points" covering the area to erase (use canvas state). One stroke per turn.
- When no user command: you MUST still draw a visible stroke — do not just move a tiny bit. Draw something: a random doodle (curve, zigzag, spiral), a simple shape (part of a circle, a line, a small arc), or extend/continue the existing drawing in canvas state. Each stroke must be clearly visible: 15–50 points with noticeable length (e.g. 50–500+ px range), so the cursor is clearly drawing, not idling.
Return ONLY the JSON object, no markdown."""


def _stroke_user_message(cursor_x: float, cursor_y: float, other_cursors: str, canvas_context: str, user_message: str) -> str:
    parts = [
        f"Current cursor position: ({cursor_x:.0f}, {cursor_y:.0f}).",
        f"Other cursors on canvas: {other_cursors or 'none'}.",
        f"Canvas state:\n{canvas_context}",
    ]
    if user_message and user_message.strip():
        parts.append(f"User said to you: {user_message.strip()}")
    else:
        parts.append("No user command. Draw something now: a random doodle (curve, zigzag, small shape), or extend the existing drawing. Your stroke must be visible (15+ points, clear movement) — do not just move a tiny bit.")
    parts.append("Draw ONE stroke now. Return only: {\"points\": [{\"x\",\"y\"},...], \"color\": \"#...\", \"width\": n}")
    return "\n\n".join(parts)


# 프로바이더별 기본 모델 (UI에서 미선택 시 사용)
DEFAULT_STROKE_MODELS = {
    "openai": "gpt-4o-mini",
    "gemini": "gemini-2.0-flash",
    "claude": "claude-3-5-haiku-20241022",
    "perplexity": "llama-3.1-sonar-small-128k-online",
    "openclaw": "openclaw:main",
}


async def _stroke_api_call(
    system_prompt: str,
    user_prompt: str,
    ai_provider: str,
    api_key: str,
    model: str | None = None,
    openclaw_base_url: str | None = None,
) -> str:
    """
    스트로크용 단일 호출: 429(한도 초과) 발생 시 재시도 없이 즉시 중단 에러를 발생시킵니다.
    """
    try:
        content = ""
        if ai_provider == "openai":
            api_key = api_key or os.getenv("OPENAI_API_KEY")
            if not api_key: raise ValueError("OpenAI API 키가 필요합니다.")
            m = (model or "").strip() or DEFAULT_STROKE_MODELS.get("openai", "gpt-4o-mini")
            async with httpx.AsyncClient(timeout=30.0) as client:
                r = await client.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                    json={
                        "model": m,
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt},
                        ],
                        "temperature": 0.4,
                    },
                )
                _check_api_error(r, "OpenAI") # 여기서 429 감지 시 ValueError 발생
                content = r.json()["choices"][0]["message"]["content"].strip()

        elif ai_provider == "gemini":
            if not GEMINI_AVAILABLE:
                raise ImportError("Gemini 사용 시 'pip install google-genai' 또는 'pip install google-generativeai' 필요.")
            api_key = api_key or os.getenv("GEMINI_API_KEY")
            if not api_key: raise ValueError("Gemini API 키가 필요합니다.")
            m = (model or "").strip() or DEFAULT_STROKE_MODELS.get("gemini", "gemini-2.0-flash")
            def _sync():
                if GEMINI_USE_NEW_SDK:
                    client = genai_new.Client(api_key=api_key)
                    r = client.models.generate_content(model=m, contents=f"{system_prompt}\n\n{user_prompt}")
                    return (r.text or "").strip()
                genai_old.configure(api_key=api_key)
                gen_model = genai_old.GenerativeModel(m)
                r = gen_model.generate_content(f"{system_prompt}\n\n{user_prompt}")
                return (r.text or "").strip()
            content = await asyncio.to_thread(_sync)

        elif ai_provider == "claude":
            api_key = api_key or os.getenv("ANTHROPIC_API_KEY")
            if not api_key: raise ValueError("Anthropic API 키가 필요합니다.")
            m = (model or "").strip() or DEFAULT_STROKE_MODELS.get("claude", "claude-3-5-haiku-20241022")
            async with httpx.AsyncClient(timeout=30.0) as client:
                r = await client.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={"x-api-key": api_key, "anthropic-version": "2023-06-01", "Content-Type": "application/json"},
                    json={"model": m, "max_tokens": 2048, "system": system_prompt, "messages": [{"role": "user", "content": user_prompt}]},
                )
                _check_api_error(r, "Anthropic(Claude)")
                data = r.json()
                for b in data.get("content", []):
                    if b.get("type") == "text":
                        content = b.get("text", "").strip()
                        break

        elif ai_provider == "perplexity":
            api_key = api_key or os.getenv("PERPLEXITY_API_KEY")
            if not api_key: raise ValueError("Perplexity API 키가 필요합니다.")
            m = (model or "").strip() or DEFAULT_STROKE_MODELS.get("perplexity", "llama-3.1-sonar-small-128k-online")
            async with httpx.AsyncClient(timeout=45.0) as client:
                r = await client.post(
                    "https://api.perplexity.ai/chat/completions",
                    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                    json={"model": m, "messages": [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}], "max_tokens": 2048, "temperature": 0.4},
                )
                _check_api_error(r, "Perplexity")
                content = r.json()["choices"][0]["message"]["content"].strip()

        elif ai_provider == "openclaw":
            # 사용자(에이전트 시작 시)가 준 Gateway URL 그대로 사용. 로컬호스트로 대체하지 않음.
            base_url = (openclaw_base_url or os.getenv("OPENCLAW_BASE_URL") or "").strip().rstrip("/")
            if not base_url:
                raise ValueError("OpenClaw Gateway 주소가 필요합니다. 에이전트 시작 시 Gateway URL을 입력하거나 서버 환경변수 OPENCLAW_BASE_URL을 설정하세요.")
            token = (api_key or os.getenv("OPENCLAW_API_KEY") or "").strip()
            m = (model or "").strip() or DEFAULT_STROKE_MODELS.get("openclaw", "openclaw:main")
            headers = {"Content-Type": "application/json"}
            if token:
                headers["Authorization"] = f"Bearer {token}"
            async with httpx.AsyncClient(timeout=30.0) as client:
                r = await client.post(
                    f"{base_url}/v1/chat/completions",
                    headers=headers,
                    json={
                        "model": m,
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt},
                        ],
                        "temperature": 0.4,
                    },
                )
                if r.status_code == 401:
                    # 디버깅: 서버가 토큰을 읽었는지 확인 (값은 노출하지 않음)
                    t = (api_key or os.getenv("OPENCLAW_API_KEY") or "").strip()
                    if t:
                        print(f"[OpenClaw] 401 발생 시 서버 OPENCLAW_API_KEY: 설정됨 (길이 {len(t)})")
                    else:
                        print("[OpenClaw] 401 발생 시 서버 OPENCLAW_API_KEY: 미설정(비어있음). .env 위치·형식 확인 후 서버 재시작.")
                _check_api_error(r, "OpenClaw")
                content = r.json()["choices"][0]["message"]["content"].strip()
        else:
            raise ValueError(f"지원하지 않는 AI: {ai_provider}")

        # Markdown 코드 블록 제거
        if content.startswith("```"):
            content = content.split("\n", 1)[1] if "\n" in content else content[3:]
        if content.endswith("```"):
            content = content.rsplit("```", 1)[0].strip()
        
        return content

    except ValueError as e:
        # 429 에러라면 로그만 남기고 다시 던집니다 (main.py에서 처리하도록)
        if "429" in str(e):
            print(f"⚠️ [AI Bridge] 429 한도 초과 감지 -> 중단 프로세스 시작")
        raise e
    except Exception as e:
        msg = str(e).lower()
        if "connection" in msg or "refused" in msg or "attempts failed" in msg:
            print(
                "[OpenClaw] 로그: 연결 실패 — 서버가 사용자가 준 Gateway URL로 접속 불가. "
                "원인: URL 오류·Gateway 미실행·방화벽·다른 기기인데 localhost 사용 등 사용자 측 환경. "
                "조치: Gateway 실행, 주소·포트 확인, 서버에서 도달 가능한 URL 사용. (Drawboard 서버 코드 문제 아님)"
            )
            raise ValueError(
                "OpenClaw Gateway에 연결할 수 없습니다. Gateway가 실행 중인지, "
                "입력한 주소·포트가 맞는지 확인하세요. (예: http://127.0.0.1:18789)"
            ) from e
        if "502" in msg or "bad gateway" in msg:
            print(
                "[OpenClaw] 로그: 502 Bad Gateway — 사용자가 준 Gateway/터널 URL이 502 반환. "
                "원인: 사용자 측 터널 끊김·Gateway 다운·일시 과부하. "
                "조치: 터널 재연결, Gateway 재실행 확인. (Drawboard 서버 코드 문제 아님)"
            )
            raise ValueError(
                "Gateway(또는 터널)가 502 Bad Gateway를 반환했습니다. "
                "Gateway가 정상 동작 중인지, 터널이 살아 있는지 확인하세요."
            ) from e
        if "500" in msg or "internal server error" in msg:
            print(
                "[OpenClaw] 로그: 500 — Gateway가 요청 처리 중 오류. "
                "Gateway 콘솔/로그에서 에러 원인 확인. 모델(openclaw:main) 라우팅·백엔드 설정 확인."
            )
            raise ValueError(
                "Gateway가 500 오류를 반환했습니다. Gateway 로그를 확인하고, "
                "모델 openclaw:main 이 올바르게 연결돼 있는지 확인하세요."
            ) from e
        print(f"❌ 예외 발생: {e}")
        raise e


async def get_next_stroke(
    ai_name: str,
    cursor_x: float,
    cursor_y: float,
    other_cursors: str,
    canvas_context: str,
    user_message: str,
    ai_provider: str,
    api_key: str | None,
    model: str | None = None,
    openclaw_base_url: str | None = None,
) -> dict:
    """자율 그리기: 현재 커서·캔버스·(선택)사용자 메시지로 한 스트로크(경로)를 받음."""
    user_content = _stroke_user_message(cursor_x, cursor_y, other_cursors, canvas_context, user_message or "")
    content = await _stroke_api_call(
        STROKE_SYSTEM, user_content, ai_provider, api_key or "", model,
        openclaw_base_url=openclaw_base_url if ai_provider == "openclaw" else None,
    )
    # 일부 Gateway/모델이 JSON 대신 자연어·빈 문자열을 돌려줄 수 있으므로, 친절한 오류 메시지로 감쌉니다.
    try:
        raw = json.loads(content)
    except json.JSONDecodeError as e:
        snippet = (content or "").strip()
        if len(snippet) > 120:
            snippet = snippet[:120] + "..."
        raise ValueError(f"stroke 응답을 JSON으로 파싱할 수 없습니다: {e}. 응답 앞부분: {snippet!r}")
    if isinstance(raw, dict) and raw.get("points"):
        return {"points": raw["points"], "color": raw.get("color", "#000000"), "width": raw.get("width", 2)}
    if isinstance(raw, list) and len(raw) > 0:
        first = raw[0]
        if isinstance(first, dict):
            if first.get("type") == "path" and first.get("points"):
                return {"points": first["points"], "color": first.get("color", "#000000"), "width": first.get("width", 2)}
            if first.get("type") == "line":
                return {
                    "points": [
                        {"x": first.get("x1") if first.get("x1") is not None else 0, "y": first.get("y1") if first.get("y1") is not None else 0},
                        {"x": first.get("x2") if first.get("x2") is not None else 0, "y": first.get("y2") if first.get("y2") is not None else 0},
                    ],
                    "color": first.get("color", "#000000"),
                    "width": first.get("width", 2),
                }
    raise ValueError("AI did not return a valid stroke")
