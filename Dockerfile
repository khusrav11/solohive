FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
# Ключ передаётся при запуске:  docker run --env-file .env -v "$PWD/reports:/app/reports" solohive \
#   --input examples/requirements.pdf --output reports/report.md
ENTRYPOINT ["python", "-m", "solohive"]
