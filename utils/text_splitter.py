
import re
from typing import Dict

_REQUIREMENT_HEADERS = [
    r"requirements", r"qualifications", r"what you.?ll need",
    r"what we.?re looking for", r"must have", r"skills? (required|needed)",
    r"المتطلبات", r"المؤهلات", r"الشروط",
]

_HEADER_PATTERN = re.compile(
    r"^\s*(?:[-*#>]|\d+[.)])?\s*(" + "|".join(_REQUIREMENT_HEADERS) + r")\s*[:：]?\s*$",
    re.IGNORECASE | re.MULTILINE,
)


def split_job_text(raw_text: str) -> Dict[str, str]:
    
    raw_text = (raw_text or "").strip()
    if not raw_text:
        return {"description": "", "requirements": ""}

    match = _HEADER_PATTERN.search(raw_text)
    if not match:
        return {"description": raw_text, "requirements": ""}

    description  = raw_text[:match.start()].strip()
    requirements = raw_text[match.end():].strip()

    
    if not description and not requirements:
        return {"description": raw_text, "requirements": ""}
    if not description:
        description = raw_text  

    return {"description": description, "requirements": requirements}