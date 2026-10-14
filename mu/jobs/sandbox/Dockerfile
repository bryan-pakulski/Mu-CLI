FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/opt/mucli TIKTOKEN_CACHE_DIR=/opt/mucli/.cache/tiktoken
RUN apt-get update && apt-get install -y --no-install-recommends git patch ripgrep ca-certificates nodejs npm \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt /opt/mucli/requirements.txt
RUN pip install --no-cache-dir -r /opt/mucli/requirements.txt \
    && mkdir -p "$TIKTOKEN_CACHE_DIR" \
    && python3 -c "import tiktoken; tiktoken.get_encoding('cl100k_base')"
COPY . /opt/mucli
WORKDIR /workspace
ENTRYPOINT ["python3", "-m", "mu.jobs.sandbox.worker"]
