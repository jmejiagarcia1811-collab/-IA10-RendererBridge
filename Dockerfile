FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=10000
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt && useradd --create-home --uid 10001 appuser
COPY . .
RUN mkdir -p /tmp/ia10-media && chown -R appuser:appuser /app /tmp/ia10-media
USER appuser
EXPOSE 10000
CMD ["python", "app.py"]
