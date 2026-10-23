FROM python:3.12-slim
WORKDIR /app
COPY engine/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
COPY engine /app/engine
ENV PYTHONPATH=/app
ENV SERVICE_NAME=engine
EXPOSE 8080
CMD ["python", "-m", "engine.app"]
