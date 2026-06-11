from dataclasses import dataclass, field
from datetime import datetime


@dataclass

class RawJob:
    title: str
    company: str
    location: str = ""
    description: str = ""
    requirements: str = ""
    apply_link: str = ""
    apply_email: str = ""
    apply_type: str = "other"
    source: str = ""
    external_id: str = ""
    scraped_at: datetime = field(default_factory=datetime.utcnow)

    def is_valid(self) -> bool:
        return bool(self.title and self.company)

