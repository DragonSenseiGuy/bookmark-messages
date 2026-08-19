"""Stage 1 + orchestration: fetch each pending link, then classify it.

For every Link with status='pending', fetch the page over plain HTTP, extract
title / meta description / visible body text, and hand the result to the LLM in
ai.py. Unreachable or non-HTML pages short-circuit to "Other" without spending
an AI request. Run directly:  python classify.py
"""
import time

import requests
from bs4 import BeautifulSoup

import ai
import config
from app import app
from models import db, Link, Tag


def fetch_page(url):
    """Fetch a URL and extract (title, description, body_text).

    Returns None if the page is unreachable or is not HTML, signalling the
    caller to short-circuit without calling the AI.
    """
    headers = {"User-Agent": config.USER_AGENT}
    try:
        resp = requests.get(
            url, headers=headers, timeout=config.FETCH_TIMEOUT, allow_redirects=True
        )
        resp.raise_for_status()
    except requests.RequestException:
        return None

    content_type = resp.headers.get("Content-Type", "")
    if "html" not in content_type.lower():
        return None

    soup = BeautifulSoup(resp.text, "html.parser")

    title = soup.title.string.strip() if soup.title and soup.title.string else ""

    description = ""
    meta = soup.find("meta", attrs={"name": "description"})
    if meta and meta.get("content"):
        description = meta["content"].strip()

    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    body_text = " ".join(soup.get_text(separator=" ").split())

    return title, description, body_text


def build_text(description, body_text):
    """Concatenate description + body and truncate to the char budget."""
    combined = (description + "\n" + body_text).strip()
    return combined[: config.MAX_TEXT_CHARS]


def classify_pending():
    """Classify every pending Link, updating rows in place."""
    with app.app_context():
        taxonomy = Tag.query.order_by(Tag.id).all()
        if not taxonomy:
            # No user-defined tags yet; fall back to the built-in defaults.
            taxonomy = config.CATEGORIES

        pending = Link.query.filter_by(status="pending").all()
        print(f"{len(pending)} pending link(s) to classify")

        for link in pending:
            page = fetch_page(link.url)

            if page is None:
                link.title = link.title or link.url
                link.category = "Other"
                link.summary = ""
                link.keep_as_bookmark = False
                link.reason = "unreachable"
                link.status = "classified"
                db.session.commit()
                print(f"  [skip] {link.url} -> Other (unreachable)")
                continue

            title, description, body_text = page
            text = build_text(description, body_text)
            result = ai.classify(link.url, text, taxonomy)

            link.title = result["title"] or title or link.url
            link.category = result["category"]
            link.summary = result["summary"]
            link.keep_as_bookmark = result["keep_as_bookmark"]
            link.reason = result["reason"]
            link.status = "classified"
            db.session.commit()
            print(f"  [done] {link.url} -> {link.category}")

            time.sleep(config.RATE_LIMIT_SLEEP)


if __name__ == "__main__":
    classify_pending()
