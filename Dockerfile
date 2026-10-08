# CPU image; runs on Hugging Face Spaces (Docker SDK), Render, Fly, or any VM.
FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*

# Spaces runs the container as uid 1000
RUN useradd -m -u 1000 user
ENV HOME=/home/user HF_HOME=/home/user/.cache/huggingface PYTHONUNBUFFERED=1
WORKDIR /home/user/app

RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

ARG MODEL_ID=facebook/wav2vec2-xlsr-53-espeak-cv-ft
ENV MODEL_ID=${MODEL_ID}
USER user
# bake the model into the image so cold starts don't download 1.2 GB
RUN python -c "import os; from transformers import AutoFeatureExtractor, Wav2Vec2ForCTC; from huggingface_hub import hf_hub_download; m=os.environ['MODEL_ID']; AutoFeatureExtractor.from_pretrained(m); Wav2Vec2ForCTC.from_pretrained(m); hf_hub_download(m,'vocab.json')"

COPY --chown=user app ./app
COPY --chown=user static ./static

EXPOSE 7860
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-7860}"]
