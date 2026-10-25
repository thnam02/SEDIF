FROM python:3.12-slim
WORKDIR /app
COPY engine/requirements.txt /tmp/requirements.txt
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir -r /tmp/requirements.txt
COPY engine /app/engine
ENV PYTHONPATH=/app
ENV SERVICE_NAME=engine
EXPOSE 8080
CMD ["python", "-m", "engine.app"]
