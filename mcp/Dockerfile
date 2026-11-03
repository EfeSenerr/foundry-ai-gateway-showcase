FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PORT=8000
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY server.py tools.py departments.py ./
COPY data ./data
RUN useradd --system --uid 10001 app && chown -R app /app
USER app
EXPOSE 8000
CMD ["python", "server.py"]
