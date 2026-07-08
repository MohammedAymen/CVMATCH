"""
debug_notion.py — بيشوف إيه اللي موجود في الـ Notion database فعلاً
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from dotenv import load_dotenv
load_dotenv()

from notion_client import Client

token = os.getenv("NOTION_TOKEN")
db_id = os.getenv("NOTION_DATABASE_ID")
ds_id = os.getenv("NOTION_DATA_SOURCE_ID")

print(f"Token: {token[:15]}..." if token else "❌ No token")
print(f"DB ID: {db_id}")
print(f"DS ID: {ds_id}")

client = Client(auth=token)

# شوف الـ database structure
print("\n📋 Retrieving database...")
db = client.databases.retrieve(database_id=db_id)
print(f"Title: {db.get('title', [{}])[0].get('plain_text', 'N/A')}")
props = db.get("properties", {})
print(f"Properties ({len(props)}):")
for name, prop in props.items():
    print(f"   - {name}: {prop.get('type')}")

# شوف الـ data_sources
data_sources = db.get("data_sources", [])
print(f"\nData sources: {data_sources}")

# جرب تضيف record بسيط جداً
print("\n🧪 Testing simple page create...")
try:
    # أبسط حاجة ممكنة — title بس
    resp = client.pages.create(
        parent={"database_id": db_id},
        properties={
            "title": {"title": [{"text": {"content": "TEST"}}]}
        }
    )
    print(f"✅ Simple create worked! Page ID: {resp['id']}")
    # امسح الـ test page
    client.pages.update(page_id=resp["id"], archived=True)
    print("🗑️ Test page deleted")
except Exception as e:
    print(f"❌ Simple create failed: {e}")

# جرب data_source_id
print("\n🧪 Testing with data_source_id...")
try:
    resp = client.pages.create(
        parent={"data_source_id": ds_id},
        properties={
            "title": {"title": [{"text": {"content": "TEST2"}}]}
        }
    )
    print(f"✅ data_source_id create worked! Page ID: {resp['id']}")
    client.pages.update(page_id=resp["id"], archived=True)
    print("🗑️ Test page deleted")
except Exception as e:
    print(f"❌ data_source_id create failed: {e}")