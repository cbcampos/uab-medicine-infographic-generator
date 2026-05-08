FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH="/app"

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
        libmagic1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY llm_utils ./llm_utils
WORKDIR /app/llm_utils
RUN pip install --no-cache-dir -e .
WORKDIR /app

COPY . .

EXPOSE 8501

CMD ["streamlit", "run", "infographic_app.py", "--server.port=8501", "--server.address=0.0.0.0"]