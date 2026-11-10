FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY alembic.ini .
COPY alembic ./alembic
COPY app ./app
RUN useradd --create-home --uid 1001 autopilot \
    && mkdir -p /data/uploads \
    && chown -R autopilot:autopilot /data /app
USER autopilot
EXPOSE 8000
