FROM python:3.12-slim

WORKDIR /app

# 系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential && \
    rm -rf /var/lib/apt/lists/*

# Python 依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 项目文件
COPY . .

# 数据目录
RUN mkdir -p /app/data

ENV PYTHONUNBUFFERED=1
ENV PYTHONIOENCODING=utf-8
ENV CHROMA_PERSIST_DIR=/app/data/chroma_db

EXPOSE 8900

CMD ["python", "main.py"]
