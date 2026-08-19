"""
nodes/email_finder.py — Find professor email addresses (professor track only).
"""

import re
import logging
import requests
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)
EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")

UNI_PATTERNS = {
    "cambridge":  "{first}.{last}@cam.ac.uk",
    "oxford":     "{last}@cs.ox.ac.uk",
    "imperial":   "{first}.{last}@imperial.ac.uk",
    "eth":        "{first}.{last}@inf.ethz.ch",
    "tum":        "{first}.{last}@tum.de",
    "delft":      "{first}.{last}@tudelft.nl",
    "edinburgh":  "{first}.{last}@ed.ac.uk",
    "ucl":        "{first}.{last}@ucl.ac.uk",
    "epfl":       "{first}.{last}@epfl.ch",
}


def _scrape_email(url: str) -> str | None:
    if not url:
        return None
    try:
        r = requests.get(url, timeout=8, headers={"User-Agent": "Mozilla/5.0"})
        matches = EMAIL_RE.findall(r.text)
        matches = [m for m in matches if not any(s in m for s in
                   ["example", "sentry", "noreply", "webmaster", ".png", ".jpg"])]
        return matches[0] if matches else None
    except Exception:
        return None


def _infer_email(name: str, university: str) -> str | None:
    parts = name.lower().split()
    if len(parts) < 2:
        return None
    first, last = parts[0], parts[-1]
    uni_lower = university.lower()
    for key, pattern in UNI_PATTERNS.items():
        if key in uni_lower:
            return pattern.format(first=first, last=last)
    return None


def find_email(name: str, university: str, profile_url: str = "") -> tuple[str, str]:
    email = _scrape_email(profile_url)
    if email:
        return email, "verified"
    email = _infer_email(name, university)
    if email:
        return email, "inferred"
    return "", "missing"
