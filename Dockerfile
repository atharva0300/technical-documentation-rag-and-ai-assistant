FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
# torch is a transitive dependency of sentence-transformers. Installed via plain
# `pip install -r requirements.txt`, pip pulls the default CUDA-enabled build, which
# drags in ~2GB+ of nvidia-*/triton wheels this project never uses (see
# docs/ENVIRONMENT.md: CPU-only execution, no GPU) -- that's what was blowing up the
# image size and build time. Installing the CPU-only wheel first means the later
# `-r requirements.txt` pass is satisfied by what's already installed and skips it.
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
