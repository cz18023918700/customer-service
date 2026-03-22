#!/bin/bash
# 一键部署/更新脚本
# 用法: bash deploy.sh

set -e

cd "$(dirname "$0")"

echo "=== 拉取最新代码 ==="
git pull

echo "=== 停止旧容器 ==="
sudo docker compose down 2>/dev/null || true

echo "=== 构建镜像 ==="
sudo docker compose build

echo "=== 启动容器 ==="
sudo docker compose up -d

echo "=== 等待启动 ==="
sleep 10

# 健康检查
HEALTH=$(curl -s http://localhost:8900/health 2>/dev/null)
if echo "$HEALTH" | grep -q "healthy"; then
    echo "=== 部署成功 ✅ ==="
    echo "$HEALTH"
else
    echo "=== 启动中，请稍等... ==="
    echo "用 'sudo docker logs jinxiang-cs --tail 20' 查看日志"
fi
