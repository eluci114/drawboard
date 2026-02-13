"""
봇 접속 경로 /bot 으로 변경 후 전체 동작 검증.
실행: 프로젝트 루트(c:\\drawboard)에서 python backend/test_bot_entry.py
"""
import sys
from pathlib import Path

# 프로젝트 루트를 path에 추가 후 backend.main 임포트 (relative import 해결)
_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_root))

from fastapi.testclient import TestClient
from backend.main import app

client = TestClient(app)
BASE = "http://testserver"


def test_get_bot_returns_json():
    """GET /bot (기본 Accept) -> JSON, skill_md·for_human 포함, for_human에 /bot"""
    r = client.get("/bot")
    assert r.status_code == 200, f"GET /bot -> {r.status_code}"
    data = r.json()
    assert "skill_md" in data, "skill_md 필드 없음"
    assert "for_human" in data, "for_human 필드 없음"
    assert "/bot" in data["for_human"], f"for_human에 /bot 없음: {data['for_human']}"
    assert "등록" in data["skill_md"] and "입장" in data["skill_md"], "skill_md에 진입 안내 없음"
    assert BASE + "/bot" in data["for_human"] or "/bot" in data["for_human"]
    print("  OK GET /bot -> JSON, for_human에 /bot 포함")


def test_get_bot_accept_html():
    """GET /bot (Accept: text/html) -> HTML, /bot 안내"""
    r = client.get("/bot", headers={"Accept": "text/html"})
    assert r.status_code == 200
    html = r.text
    assert "봇용" in html or "봇" in html
    assert "/bot" in html
    assert "메인 페이지" in html
    print("  OK GET /bot (Accept: text/html) -> HTML, /bot 안내")


def test_get_api_same_as_bot():
    """GET /api -> /bot 과 동일 응답 (호환)"""
    r = client.get("/api")
    assert r.status_code == 200
    data = r.json()
    assert "skill_md" in data and "for_human" in data
    assert "/bot" in data["for_human"]
    print("  OK GET /api -> 동일 JSON, for_human에 /bot")


def test_register_then_start_flow():
    """1단계 등록 -> agent_id 추출 -> 2단계 입장(openclaw_base_url 필수) -> 200 -> 퇴장하여 다음 테스트에 영향 없게"""
    r1 = client.post("/api/agent/register", json={"name": "TestBot"})
    assert r1.status_code == 200, f"register -> {r1.status_code}"
    data1 = r1.json()
    agent_id = data1.get("agent_id")
    assert agent_id, "agent_id 없음"

    # 2단계: openclaw_base_url 없으면 400
    r2 = client.post("/api/ai/start", json={"agent_id": agent_id})
    assert r2.status_code == 400, f"start without url -> {r2.status_code} (expected 400)"
    assert "openclaw" in r2.json().get("detail", "").lower() or "Gateway" in r2.json().get("detail", "")

    # 2단계: openclaw_base_url 있으면 200
    r3 = client.post(
        "/api/ai/start",
        json={"agent_id": agent_id, "openclaw_base_url": "http://127.0.0.1:19999"},
    )
    assert r3.status_code == 200, f"start with url -> {r3.status_code} {r3.text}"
    ai_id = r3.json().get("ai_id")
    assert ai_id

    # 퇴장해서 다음 테스트(잘못된 agent_id -> 404)가 400(이미 실행 중)이 아닌 404를 받을 수 있게
    client.post("/api/ai/stop", json={"ai_id": ai_id})
    print("  OK 등록 -> 입장(openclaw_base_url) -> 200, ai_id 반환; 퇴장 후 다음 테스트 준비")


def test_invalid_agent_id_404():
    """잘못된 agent_id로 입장 -> 404 (AI가 한 명도 없을 때)"""
    r = client.post(
        "/api/ai/start",
        json={"agent_id": "invalid_id_xxx", "openclaw_base_url": "http://127.0.0.1:18789"},
    )
    assert r.status_code == 404, f"expected 404, got {r.status_code} {r.json()}"
    assert "등록되지 않은" in r.json().get("detail", "")
    print("  OK 잘못된 agent_id -> 404")


def test_skill_md_contains_bot():
    """skill_md 내용에 진입 주소가 /bot 으로 되어 있음"""
    r = client.get("/bot")
    data = r.json()
    sm = data["skill_md"]
    assert "GET " in sm and "/bot" in sm, "skill_md에 GET .../bot 없음"
    assert "진입" in sm or "참여" in sm
    # 활동용은 /api 유지
    assert "/api/agent/register" in sm and "/api/ai/start" in sm
    print("  OK skill_md 내 진입=/bot, 활동=/api/... 유지")


def test_canvas_and_health():
    """GET /api/canvas, GET /api/health 정상"""
    r = client.get("/api/canvas")
    assert r.status_code == 200
    assert "events" in r.json()
    r2 = client.get("/api/health")
    assert r2.status_code == 200
    print("  OK /api/canvas, /api/health 200")


def run_all():
    print("Bot entry path /bot - full test")
    print("-" * 50)
    try:
        test_get_bot_returns_json()
        test_get_bot_accept_html()
        test_get_api_same_as_bot()
        test_skill_md_contains_bot()
        test_register_then_start_flow()
        test_invalid_agent_id_404()
        test_canvas_and_health()
        print("-" * 50)
        print("All tests passed.")
        return 0
    except AssertionError as e:
        print(f"  FAIL: {e}")
        return 1
    except Exception as e:
        print(f"  ERROR: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    exit(run_all())
