FROM python:3.12-slim
WORKDIR /app
COPY bank-core/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
COPY engine /app/engine
COPY bank-core /app/bank-core
COPY fides-sim/config.yaml /app/fides-sim/config.yaml
COPY docs/personas.json /app/docs/personas.json
ENV PYTHONPATH=/app:/app/bank-core
ENV SERVICE_NAME=bank-core
EXPOSE 8080
CMD ["python", "/app/bank-core/app.py"]
