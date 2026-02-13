# OpenClaw Gateway에서 405 해결 (Chat Completions 활성화)

Drawboard가 `POST /v1/chat/completions`를 호출할 때 **405 Method Not Allowed**가 나오면,  
OpenClaw Gateway에서 **Chat Completions HTTP API가 꺼져 있기 때문**입니다. 기본값이 비활성화입니다.

## 1. 설정 파일 열기

- 경로: **`%USERPROFILE%\.openclaw\openclaw.json`** (예: `C:\Users\Admin\.openclaw\openclaw.json`)

## 2. `gateway` 안에 `http` 추가

`gateway` 객체 **안에** 아래 `http` 블록을 **추가**합니다. (이미 `port`, `auth` 등이 있는 그 `gateway` 안입니다.)

```json
"gateway": {
  "mode": "local",
  "auth": { ... },
  "port": 18789,
  "bind": "loopback",
  "tailscale": { "mode": "off", "resetOnExit": false },
  "http": {
    "endpoints": {
      "chatCompletions": {
        "enabled": true
      }
    }
  }
}
```

즉, `"tailscale": { ... }` 다음에 **쉼표(,)를 붙이고** 아래를 넣으면 됩니다.

```json
,"http":{"endpoints":{"chatCompletions":{"enabled":true}}}
```

## 3. Gateway 재시작

설정 저장 후 **OpenClaw Gateway를 한 번 종료했다가 다시 실행**하세요.

```bash
# 기존 gateway 종료 후
openclaw gateway
```

## 4. 인증 (Bearer 토큰)

문서 기준으로 Chat Completions는 **Bearer 토큰**을 사용합니다.  
`gateway.auth.mode`가 `token`이면 `gateway.auth.token` 값을 사용합니다.

Drawboard 서버 `.env`에 다음을 넣으면, OpenClaw 호출 시 해당 토큰을 씁니다.

```env
OPENCLAW_API_KEY=623b66f0e582ca254a4c2a2028b712c1a099b55a38175c1a
```

(위 값은 현재 `openclaw.json`의 `gateway.auth.token` 예시입니다. 본인 설정에 맞게 넣으세요.)

## 참고

- [OpenClaw – OpenAI Chat Completions (HTTP)](https://docs.clawd.bot/gateway/openai-http-api): 엔드포인트 비활성화가 기본이며, `gateway.http.endpoints.chatCompletions.enabled: true` 로 켜야 함.
