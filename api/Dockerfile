FROM python:3.12-slim
WORKDIR /app
COPY server.py ./
COPY data ./data
EXPOSE 8000
CMD ["python", "server.py", "--host", "0.0.0.0", "--port", "8000"]
