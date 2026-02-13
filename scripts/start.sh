#!/bin/sh
# Railway 등: PORT 환경변수로 uvicorn 기동 (기동 로그로 포트 확인 가능)
PORT="${PORT:-8000}"
echo "Starting uvicorn on 0.0.0.0:${PORT}"
exec uvicorn backend.main:app --host 0.0.0.0 --port "$PORT"
