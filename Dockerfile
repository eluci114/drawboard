# Railway 등에서 pydantic-core 휠 빌드 실패 시 Dockerfile 빌드 사용 (Python 3.11 = 사전 빌드 휠 사용)
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Railway가 PORT 환경변수 주입. 미설정 시 8000
EXPOSE 8000
CMD ["sh", "-c", "uvicorn backend.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
