FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/opt/mucli TIKTOKEN_CACHE_DIR=/opt/mucli/.cache/tiktoken
RUN apt-get update && apt-get install -y --no-install-recommends git patch ripgrep ca-certificates \
    && rm -rf /var/lib/apt/lists/*
# Node.js and npm from the official release tarball, pinned by version and sha256. Debian's
# `npm` package is deliberately not used: it pulls in global dev tools (ESLint 6.4 as
# /usr/bin/eslint) that silently stand in for a project's own node_modules tools
# (documentation/plans/job_templates.md §5). Project tools come from the repository's
# node_modules, installed by a template's job setup.
ENV NODE_VERSION=22.20.0
RUN set -eu; \
    case "$(dpkg --print-architecture)" in \
        amd64) node_arch=x64; node_sha=eeaccb0378b79406f2208e8b37a62479c70595e20be6b659125eb77dd1ab2a29 ;; \
        arm64) node_arch=arm64; node_sha=4181609e03dcb9880e7e5bf956061ecc0503c77a480c6631d868cb1f65a2c7dd ;; \
        *) echo "unsupported architecture for the Node.js tarball" >&2; exit 1 ;; \
    esac; \
    python3 -c "import sys, urllib.request; urllib.request.urlretrieve(sys.argv[1], sys.argv[2])" \
        "https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-${node_arch}.tar.gz" /tmp/node.tar.gz; \
    echo "${node_sha}  /tmp/node.tar.gz" | sha256sum -c -; \
    tar -xzf /tmp/node.tar.gz -C /usr/local --strip-components=1 --no-same-owner \
        --exclude='*/include' --exclude='*/share' --exclude='*/CHANGELOG.md' --exclude='*/README.md' --exclude='*/LICENSE'; \
    rm -f /tmp/node.tar.gz; \
    node --version; npm --version
COPY requirements.txt /opt/mucli/requirements.txt
RUN pip install --no-cache-dir -r /opt/mucli/requirements.txt \
    && mkdir -p "$TIKTOKEN_CACHE_DIR" \
    && python3 -c "import tiktoken; tiktoken.get_encoding('cl100k_base')"
# Headless Chromium for job evidence (mu/jobs/evidence.py): agents screenshot the UI they changed,
# e.g. `playwright screenshot --full-page http://localhost:5173 /output/evidence/home.png`.
# Shared, world-readable browser path: stages run as the host uid, not root.
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright
RUN python3 -m playwright install --with-deps --only-shell chromium \
    && chmod -R a+rX /opt/ms-playwright \
    && rm -rf /var/lib/apt/lists/*
COPY . /opt/mucli
WORKDIR /workspace
ENTRYPOINT ["python3", "-m", "mu.jobs.sandbox.worker"]
