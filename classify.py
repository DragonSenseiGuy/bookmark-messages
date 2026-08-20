"""Fetch, classify, and periodically check saved links.

For every Link with status='pending', fetch the page over plain HTTP, extract
title / meta description / visible body text, and hand the result to the LLM in
ai.py. Unreachable or non-HTML pages short-circuit to "Other" without spending
an AI request. Run directly:  python classify.py
"""
import time
from datetime import datetime, timedelta
import ipaddress
import socket
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

import ai
import config
from app import app
from models import db, Link, Tag


def _is_public_url(url):
    """Reject URLs that could make the server request a private network address."""
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    if parsed.username or parsed.password:
        return False
    try:
        addresses = {
            item[4][0]
            for item in socket.getaddrinfo(
                parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80)
            )
        }
    except (OSError, socket.gaierror):
        return False
    return bool(addresses) and all(ipaddress.ip_address(value).is_global for value in addresses)


def fetch_page(url):
    """Fetch a URL and extract (title, description, body_text).

    Returns None if the page is unreachable or is not HTML, signalling the
    caller to short-circuit without calling the AI.
    """
    headers = {"User-Agent": config.USER_AGENT}
    current_url = url
    for _ in range(6):
        if not _is_public_url(current_url):
            return None
        try:
            resp = requests.get(
                current_url,
                headers=headers,
                timeout=config.FETCH_TIMEOUT,
                allow_redirects=False,
            )
            if resp.is_redirect or resp.is_permanent_redirect:
                location = resp.headers.get("Location")
                if not location:
                    return None
                current_url = urljoin(current_url, location)
                continue
            resp.raise_for_status()
            break
        except requests.RequestException:
            return None
    else:
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
        pending = Link.query.filter_by(status="pending").all()
        print(f"{len(pending)} pending link(s) to classify")

        for link in pending:
            page = fetch_page(link.url)

            if page is None:
                link.title = link.title or link.url
                link.tags = [tag for tag in taxonomy if tag.name == "Other"]
                link.summary = ""
                link.keep_as_bookmark = False
                link.reason = "unreachable"
                link.health_status = "unreachable"
                link.last_checked_at = datetime.utcnow()
                link.status = "classified"
                db.session.commit()
                print(f"  [skip] {link.url} -> Other (unreachable)")
                continue

            title, description, body_text = page
            text = build_text(description, body_text)
            result = ai.classify(link.url, text, taxonomy)

            if result is None:
                link.title = title or link.url
                link.tags = []
                link.summary = ""
                link.keep_as_bookmark = None
                link.reason = "AI model not available at this time"
                link.health_status = "reachable"
                link.last_checked_at = datetime.utcnow()
                link.status = "failed"
                db.session.commit()
                print(f"  [failed] {link.url} -> AI model not available")
                continue

            link.title = result["title"] or title or link.url
            selected = set(result["tags"])
            link.tags = [tag for tag in taxonomy if tag.name in selected]
            link.summary = result["summary"]
            link.keep_as_bookmark = result["keep_as_bookmark"]
            link.reason = result["reason"]
            link.health_status = "reachable"
            link.last_checked_at = datetime.utcnow()
            link.status = "classified"
            db.session.commit()
            print(f"  [done] {link.url} -> {', '.join(result['tags']) or 'untagged'}")

            time.sleep(config.RATE_LIMIT_SLEEP)


def check_link_health():
    """Check stale links and reclassify legacy fetch failures that recover."""
    with app.app_context():
        cutoff = datetime.utcnow() - timedelta(
            hours=config.HEALTH_CHECK_INTERVAL_HOURS
        )
        links = (
            Link.query.filter_by(status="classified")
            .filter(
                db.or_(
                    Link.last_checked_at.is_(None),
                    Link.last_checked_at < cutoff,
                )
            )
            .order_by(Link.last_checked_at.asc(), Link.id.asc())
            .limit(config.HEALTH_CHECK_BATCH_SIZE)
            .all()
        )
        recovered = False

        for link in links:
            page = fetch_page(link.url)
            link.last_checked_at = datetime.utcnow()
            if page is None:
                link.health_status = "unreachable"
            else:
                link.health_status = "reachable"
                if link.reason == "unreachable":
                    link.status = "pending"
                    link.title = None
                    link.tags = []
                    link.summary = None
                    link.keep_as_bookmark = None
                    link.reason = None
                    recovered = True
            db.session.commit()

        return recovered


if __name__ == "__main__":
    classify_pending()
