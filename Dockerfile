# syntax=docker/dockerfile:1
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
# bake the model into the image so cold starts don't download it (1.2-1.6 GB). MODEL_ID is a Space variable:
# a wav2vec2 repo, or powsm-onnx:<repo> for the exported POWSM (app/powsm.py). A private repo needs a Space secret
# HF_TOKEN; it is mounted for this step only and never stored in the image.
COPY --chown=user app/__init__.py app/powsm.py app/prefetch.py ./app/
RUN --mount=type=secret,id=HF_TOKEN,mode=0444,required=false \
    if [ -s /run/secrets/HF_TOKEN ]; then export HF_TOKEN="$(cat /run/secrets/HF_TOKEN)"; fi; python -m app.prefetch

COPY --chown=user app ./app
COPY --chown=user static ./static

EXPOSE 7860
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-7860}"]
