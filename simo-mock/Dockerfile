FROM python:3.12-slim
WORKDIR /app
COPY simo-mock/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
COPY engine /app/engine
COPY simo-mock /app/simo-mock
COPY fides-sim/config.yaml /app/fides-sim/config.yaml
ENV PYTHONPATH=/app:/app/simo-mock
ENV SERVICE_NAME=simo-mock
EXPOSE 8080
CMD ["python", "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080", "--app-dir", "/app/simo-mock"]
