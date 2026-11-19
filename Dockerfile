FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
      ffmpeg fonts-noto-core fonts-noto-extra fonts-dejavu-core fontconfig ca-certificates \
    && rm -rf /var/lib/apt/lists/* && fc-cache -f
WORKDIR /srv
# Musik-Filter (Demucs) mit CPU-PyTorch – eigene Schicht, damit sie im Cache bleibt
RUN pip install --no-cache-dir torch==2.5.1 torchaudio==2.5.1 --index-url https://download.pytorch.org/whl/cpu \
    && pip install --no-cache-dir demucs==4.0.1 soundfile \
    && python -c "from demucs.pretrained import get_model; get_model('htdemucs')"
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV DATA_DIR=/srv/data PYTHONUNBUFFERED=1
