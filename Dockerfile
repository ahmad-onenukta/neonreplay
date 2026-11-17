FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
      ffmpeg fonts-noto-core fonts-noto-extra fonts-dejavu-core fontconfig ca-certificates \
    && rm -rf /var/lib/apt/lists/* && fc-cache -f
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV DATA_DIR=/srv/data PYTHONUNBUFFERED=1
