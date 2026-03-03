FROM python:3.11-slim
WORKDIR /app
COPY mcp_api_server/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY mcp_api_server /app
COPY prompts /app/prompts
CMD ["bash", "-lc", "alembic upgrade head && python scripts_init.py && uvicorn app.main:app --host 0.0.0.0 --port 8001"]
