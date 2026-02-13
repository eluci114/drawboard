# Railway 등에서 pydantic-core 휠 빌드 실패 시 Dockerfile 빌드 사용 (Python 3.11 = 사전 빌드 휠 사용)
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# 기동 스크립트로 PORT 로그 출력 후 uvicorn 실행
EXPOSE 8000
RUN chmod +x /app/scripts/start.sh
CMD ["/app/scripts/start.sh"]
