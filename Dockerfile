FROM python:3.11-slim

# install system deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Install pip requirements
COPY requirements.txt /app/requirements.txt
RUN pip install --upgrade pip setuptools wheel && \
    pip install --no-cache-dir -r /app/requirements.txt

# Copy project
COPY . /app

# Collect static files (if used) and apply migrations optionally
RUN python manage.py collectstatic --noinput || true

EXPOSE 8008

CMD ["gunicorn", "futurapredict.wsgi:application", "--bind", "0.0.0.0:8008", "--workers", "3"]
