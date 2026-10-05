FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app
COPY pyproject.toml ./
COPY app ./app
COPY third_party ./third_party
# INSTALL_AI=false gives a small image without PyTorch (no AI segmentation / AI 3D).
ARG INSTALL_AI=true
RUN pip install --upgrade pip \
 && if [ "$INSTALL_AI" = "true" ]; then \
      pip install --extra-index-url https://download.pytorch.org/whl/cpu ".[ai]"; \
    else pip install .; fi
ENV HF_HOME=/data/hf-cache U2NET_HOME=/data/rembg

RUN useradd --create-home --uid 1000 puzzlecut && mkdir -p /data && chown puzzlecut /data
USER puzzlecut
ENV STORAGE_DIR=/data
EXPOSE 8000
CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
