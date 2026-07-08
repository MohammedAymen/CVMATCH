"""
fix_notion_schema.py
يضيف الـ columns على الـ database الموجود بدون ما نمسحه.
شغّله مرة واحدة بس.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from dotenv import load_dotenv
load_dotenv()

from notion_client import Client

token = os.getenv("NOTION_TOKEN")
db_id = os.getenv("NOTION_DATABASE_ID")
ds_id = os.getenv("NOTION_DATA_SOURCE_ID")

client = Client(auth=token)

SCHEMA = {
    "Job ID":          {"rich_text": {}},
    "Company":         {"rich_text": {}},
    "Location":        {"rich_text": {}},
    "Match Score":     {"number": {"format": "number"}},
    "Confidence":      {"select": {"options": [
                           {"name": "Low",    "color": "red"},
                           {"name": "Medium", "color": "yellow"},
                           {"name": "High",   "color": "green"},
                       ]}},
    "Strengths":       {"rich_text": {}},
    "Gaps":            {"rich_text": {}},
    "Recommendations": {"rich_text": {}},
    "Apply URL":       {"url": {}},
    "Status":          {"select": {"options": [
                           {"name": "Pending",      "color": "gray"},
                           {"name": "Applied",      "color": "blue"},
                           {"name": "Interviewing", "color": "yellow"},
                           {"name": "Rejected",     "color": "red"},
                           {"name": "Accepted",     "color": "green"},
                       ]}},
    "Posted Date":     {"date": {}},
    "Source":          {"rich_text": {}},
    "Last Updated":    {"date": {}},
}

print(f"📋 DB: {db_id}")
print(f"📋 DS: {ds_id}")

# تحقق من الـ properties الحالية
db = client.databases.retrieve(database_id=db_id)
existing = db.get("properties", {})
print(f"\n📊 Current properties ({len(existing)}): {list(existing.keys())}")

# أضف الـ schema بـ data_sources.update
print(f"\n➕ Adding {len(SCHEMA)} columns via data_sources.update...")
try:
    resp = client.data_sources.update(ds_id, properties=SCHEMA)
    new_props = resp.get("properties", {})
    print(f"✅ Done! Properties now ({len(new_props)}): {list(new_props.keys())}")
except Exception as e:
    print(f"❌ Failed: {e}")

# اختبر إضافة record
print("\n🧪 Testing page create with full properties...")
try:
    resp = client.pages.create(
        parent={"data_source_id": ds_id},
        properties={
            "Name":        {"title": [{"text": {"content": "TEST JOB"}}]},
            "Company":     {"rich_text": [{"text": {"content": "Test Co"}}]},
            "Match Score": {"number": 85},
            "Status":      {"select": {"name": "Pending"}},
        }
    )
    print(f"✅ Page created: {resp['id']}")
    # امسحه
    client.pages.update(page_id=resp["id"], archived=True)
    print("🗑️  Test page deleted")
except Exception as e:
    print(f"❌ Page create failed: {e}")