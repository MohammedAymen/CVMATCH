# scripts/upload_checkpoint.py
#
# لو رفع Notion فشل بعد ما الـ pipeline خلص، النتايج بتبقى محفوظة في
# data/checkpoints/jobs_<user_id>_<timestamp>.json
#
# السكريبت ده بيرفع ملف checkpoint محدد على Notion من غير ما يعيد
# الـ scraping أو الـ scoring تاني (يعني ثواني بدل ساعة).
#
# الاستخدام:
#   python scripts/upload_checkpoint.py data/checkpoints/jobs_xxx.json
#
# لو عايز تحدد database_id / data_source_id يدويًا بدل اللي متخزن في الملف:
#   python scripts/upload_checkpoint.py data/checkpoints/jobs_xxx.json --database-id XXX --data-source-id YYY

import sys
import json
import argparse
from pathlib import Path

# عشان يقدر يـ import من root المشروع لو السكريبت اتشغل من جوه scripts/
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.logger import logger
from notion.dashboard import push_to_notion_dashboard


def main():
    parser = argparse.ArgumentParser(description="Retry uploading a saved checkpoint to Notion.")
    parser.add_argument("checkpoint_path", help="Path to the checkpoint JSON file")
    parser.add_argument("--database-id", default=None, help="Override the database_id stored in the checkpoint")
    parser.add_argument("--data-source-id", default=None, help="Override the data_source_id stored in the checkpoint")
    parser.add_argument("--no-skip-duplicates", action="store_true", help="Disable duplicate-skipping (default: skip)")
    args = parser.parse_args()

    checkpoint_path = Path(args.checkpoint_path)
    if not checkpoint_path.exists():
        logger.error(f"❌ Checkpoint file not found: {checkpoint_path}")
        sys.exit(1)

    with open(checkpoint_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    jobs = data.get("jobs", [])
    if not jobs:
        logger.warning("⚠️ Checkpoint has no jobs to upload.")
        sys.exit(0)

    database_id = args.database_id or data.get("notion_database_id")
    data_source_id = args.data_source_id or data.get("notion_data_source_id")

    if not database_id or not data_source_id:
        logger.error(
            "❌ Missing database_id / data_source_id. "
            "They weren't saved in the checkpoint (or were null) — pass them explicitly with "
            "--database-id and --data-source-id."
        )
        sys.exit(1)

    logger.info(f"📂 Loaded checkpoint: {checkpoint_path} ({len(jobs)} job(s), saved_at={data.get('saved_at')})")
    logger.info(f"📤 Uploading to Notion (database_id={database_id}, data_source_id={data_source_id})...")

    result = push_to_notion_dashboard(
        jobs=jobs,
        database_id=database_id,
        data_source_id=data_source_id,
        skip_duplicates=not args.no_skip_duplicates,
    )

    logger.info(f"✅ Upload complete: {result}")


if __name__ == "__main__":
    main()