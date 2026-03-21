"""数据库备份脚本

用法: python backup.py
- 备份到 backups/ 目录，文件名含日期时间
- 自动清理超过 30 天的旧备份
"""

import shutil
import sys
from datetime import datetime, timedelta
from pathlib import Path

DB_PATH = Path(__file__).parent / "customer_service.db"
BACKUP_DIR = Path(__file__).parent / "backups"
KEEP_DAYS = 30


def backup():
    if not DB_PATH.exists():
        print("数据库文件不存在，跳过备份")
        return

    BACKUP_DIR.mkdir(exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = BACKUP_DIR / f"customer_service_{timestamp}.db"

    shutil.copy2(str(DB_PATH), str(backup_path))
    size_kb = backup_path.stat().st_size / 1024
    print(f"备份完成: {backup_path.name} ({size_kb:.1f} KB)")

    # 清理旧备份
    cutoff = datetime.now() - timedelta(days=KEEP_DAYS)
    removed = 0
    for f in BACKUP_DIR.glob("customer_service_*.db"):
        try:
            file_date = datetime.strptime(f.stem.split("_", 2)[2], "%Y%m%d_%H%M%S")
            if file_date < cutoff:
                f.unlink()
                removed += 1
        except (ValueError, IndexError):
            pass

    if removed:
        print(f"清理 {removed} 个超过 {KEEP_DAYS} 天的旧备份")

    # 统计
    backups = list(BACKUP_DIR.glob("customer_service_*.db"))
    total_mb = sum(f.stat().st_size for f in backups) / 1024 / 1024
    print(f"当前共 {len(backups)} 个备份，占用 {total_mb:.1f} MB")


if __name__ == "__main__":
    backup()
