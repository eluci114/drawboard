# OpenClaw 2026.2 설정 오류 해결 (agents.main, gateway.bind)

OpenClaw 2026.2.x에서 아래 오류가 나면 **설정 스키마가 이전 버전과 달라진 것**입니다.

- `agents: Unrecognized key: "main"`
- `gateway.bind: Invalid input`

## 1. 자동 수정 (권장)

터미널에서 한 번 실행하면 **인식되지 않는 키를 제거**합니다.

```bash
openclaw doctor --fix
```

이후 Gateway를 다시 실행하세요.

---

## 2. 수동 수정

설정 파일: **`%USERPROFILE%\.openclaw\openclaw.json`** (예: `C:\Users\Admin\.openclaw\openclaw.json`)

### 2.1 `agents.main` 제거 후 `agents.list` 사용

**이전(미지원):**

```json
"agents": {
  "main": { ... }
}
```

**2026 형식:** `agents` 아래는 `defaults`와 `list`만 사용합니다. 에이전트는 `list` 배열에 `id`로 구분합니다.

```json
"agents": {
  "defaults": {
    "model": { "primary": "anthropic/claude-sonnet-4-20250514" }
  },
  "list": [
    { "id": "main", "identity": { "name": "Main", "emoji": "🦞" } }
  ]
}
```

기존 `agents.main` 내용이 있다면, 그 설정은 `agents.defaults` 또는 `list` 안의 해당 `id` 항목으로 옮기면 됩니다.

### 2.2 `gateway.bind` 값

**허용 값:** 문자열 `"loopback"` 또는 `"all"` 만 가능합니다.

- `"loopback"`: localhost만 접속 (기본, 로컬 Drawboard용 권장)
- `"all"`: 모든 인터페이스 (다른 기기에서 접속할 때)

숫자나 `0.0.0.0` 같은 값은 **Invalid input**이 됩니다. 반드시 위 두 문자열 중 하나로 바꾸세요.

```json
"gateway": {
  "port": 18789,
  "bind": "loopback",
  ...
}
```

---

## 3. 수정 후

설정 저장 후 Gateway를 다시 실행하세요.

```bash
openclaw gateway
```

Drawboard에서 봇 참여 시 사용하는 **Chat Completions**는 기존처럼 `gateway.http.endpoints.chatCompletions.enabled: true` 로 두면 됩니다. (`docs/OPENCLAW_CHAT_COMPLETIONS.md` 참고)
