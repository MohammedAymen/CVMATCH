import os
from dotenv import load_dotenv
from pathlib import Path

load_dotenv()

class Settings:
    
    scrape_delay_min = float(os.getenv("SCRAPE_DELAY_MIN", "3"))
    scrape_delay_max = float(os.getenv("SCRAPE_DELAY_MAX", "7"))

    
    max_jobs_per_source = int(os.getenv("MAX_JOBS_PER_SOURCE", "50"))

    
    max_pages = int(os.getenv("MAX_PAGES", "3"))

    
    headless = os.getenv("HEADLESS", "False").lower() == "true"
    
    
    embedding_model = os.getenv("EMBEDDING_MODEL", "nomic-ai/nomic-embed-text-v1.5")

    embedder_batch_size = int(os.getenv("EMBEDDER_BATCH_SIZE", "32"))
    embedder_top_k = int(os.getenv("EMBEDDER_TOP_K", "5"))

    chroma_persist_directory = os.getenv("CHROMA_PERSIST_DIRECTORY", "data/chroma_db")

   
    github_token = os.getenv("GITHUB_TOKEN", None)

    
    notion_token = os.getenv("NOTION_TOKEN", None)
    notion_database_id = os.getenv("NOTION_DATABASE_ID", None)

    google_api_key = os.getenv("GEMINI_API_KEY", None)

    groq_api_key = os.getenv("GROQ_API_KEY", None)

settings = Settings()