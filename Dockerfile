FROM python:3.12

WORKDIR /app

# Python 依赖（单独一层，改代码不用重装）
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# embedding 模型（如果存在则打进镜像，避免运行时下载）
COPY model_cache/ /root/.cache/chroma/onnx_models/all-MiniLM-L6-v2/

# 项目文件
COPY . .

# 数据目录
RUN mkdir -p /app/data

# 时区设置为北京时间
ENV TZ=Asia/Shanghai
RUN ln -snf /usr/share/zoneinfo/Asia/Shanghai /etc/localtime && echo Asia/Shanghai > /etc/timezone

ENV PYTHONUNBUFFERED=1
ENV PYTHONIOENCODING=utf-8
ENV CHROMA_PERSIST_DIR=/app/data/chroma_db

EXPOSE 8900

CMD ["python", "main.py"]
