#!/bin/bash
# 设置自动备份 cron（每天凌晨3点备份DB）
# 用法: bash setup_cron.sh

BACKUP_SCRIPT="/home/ubuntu/customer-service/backup_cloud.sh"

# 创建备份脚本
cat > "$BACKUP_SCRIPT" << 'EOF'
#!/bin/bash
BACKUP_DIR="/home/ubuntu/customer-service/backups"
mkdir -p "$BACKUP_DIR"
DATE=$(date +%Y%m%d_%H%M%S)

# 从容器里复制DB
sudo docker cp jinxiang-cs:/app/data/customer_service.db "$BACKUP_DIR/customer_service_${DATE}.db" 2>/dev/null
sudo docker cp jinxiang-cs:/app/customer_service.db "$BACKUP_DIR/customer_service_${DATE}.db" 2>/dev/null

# 清理30天前的备份
find "$BACKUP_DIR" -name "*.db" -mtime +30 -delete

echo "$(date): Backup done - customer_service_${DATE}.db"
EOF

chmod +x "$BACKUP_SCRIPT"

# 添加 cron（每天凌晨3点）
(crontab -l 2>/dev/null | grep -v backup_cloud; echo "0 3 * * * $BACKUP_SCRIPT >> /home/ubuntu/customer-service/backups/backup.log 2>&1") | crontab -

echo "Cron 设置完成："
crontab -l | grep backup
echo "备份目录: /home/ubuntu/customer-service/backups/"
