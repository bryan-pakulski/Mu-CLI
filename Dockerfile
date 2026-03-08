FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/agents

WORKDIR /app

COPY agents/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

COPY . /app

RUN mkdir -p /app/.mu_cli/sessions /app/.mu_cli/workspaces \
    && chmod +x /app/scripts/docker-entrypoint.sh

EXPOSE 5000

ENTRYPOINT ["/app/scripts/docker-entrypoint.sh"]
CMD ["web"]
