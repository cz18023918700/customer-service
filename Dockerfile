FROM python:3.12-slim

WORKDIR /app

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
