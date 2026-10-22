FROM python:3.12-alpine
WORKDIR /app
COPY app.py .
ENV SERVICE_NAME=engine
EXPOSE 8080
CMD ["python", "app.py"]
