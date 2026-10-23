FROM python:3.12-slim
WORKDIR /app
COPY engine /app/engine
COPY bank-core/app.py /app/app.py
ENV PYTHONPATH=/app
ENV SERVICE_NAME=bank-core
EXPOSE 8080
CMD ["python", "app.py"]
