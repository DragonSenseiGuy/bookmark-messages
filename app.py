from flask import Flask, request, abort, render_template, redirect, url_for
from sqlalchemy.exc import IntegrityError
from urllib.parse import urlparse
from datetime import datetime, timedelta
import os
import re
import threading
import time
import config
from models import db, Submission, Link, Tag

app = Flask(__name__)

BASEDIR = os.path.abspath(os.path.dirname(__file__))
DATA_DIR = os.path.join(BASEDIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, "bookmarks.db")
app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{DB_PATH}"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db.init_app(app)

url_pattern = re.compile(r"(?:https?://|www\.)[^\s<>\")\]]+")
md_pattern = re.compile(r"\[[^\]]+\]\((https?://[^)\s]+)\)")
def seed_tags():
    """Populate the tags table from the built-in defaults on first run."""
    if Tag.query.count() == 0:
        for name in config.CATEGORIES:
            db.session.add(Tag(name=name, description=""))
        db.session.commit()


with app.app_context():
    db.create_all()
    seed_tags()


@app.route("/")
def main():
    return redirect(url_for("links"))


@app.route("/submit", methods=["POST"])
def submit():
    uploaded = request.files.get("file")
    if uploaded:
        if not uploaded.filename.lower().endswith(".txt"):
            abort(400, "expected a .txt file")
        text = uploaded.read().decode("utf-8")
    else:
        if request.content_type and request.content_type.startswith("text"):
            text = request.get_data(as_text=True)
        else:
            abort(400, "no file or text/plain body provided")

    sub = Submission(text=text)
    db.session.add(sub)
    db.session.commit()

    urls = []
    for m in md_pattern.findall(text):
        urls.append(m)
    for m in url_pattern.findall(text):
        urls.append(m)

    for u in set(urls):
        link = Link(url=u, submission_id=sub.id)
        db.session.add(link)
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()

    return redirect(url_for("links"))


def normalize_host(url):
    parsed = urlparse(url if "://" in url else f"https://{url}")
    host = parsed.netloc or parsed.path
    return host.lower().replace("www.", "")


def _serialize_link(row):
    hostname = normalize_host(row.url)
    return {
        "id": row.id,
        "url": row.url,
        "hostname": hostname,
        "favicon_url": f"https://www.google.com/s2/favicons?domain={hostname}&sz=64",
        "status": row.status,
        "title": row.title,
        "category": row.category,
        "summary": row.summary,
        "keep_as_bookmark": bool(row.keep_as_bookmark),
        "reason": row.reason,
    }


def _is_skipped(row):
    """A link that finished classification and was not worth keeping."""
    return row.status == "classified" and not row.keep_as_bookmark


@app.route("/links")
def links():
    # Search & category filtering happen client-side (instant, no reload), so the
    # full set is always rendered. ``q``/``category`` only seed the initial UI
    # state (from a shared/bookmarked URL); the browser applies them on load.
    q = (request.args.get("q") or "").strip()
    category = (request.args.get("category") or "").strip()

    rows = Link.query.order_by(Link.created_at.desc()).all()
    # Hide skipped links from the steady-state view; they only appear briefly
    # during live classification, then animate away.
    links_list = [_serialize_link(row) for row in rows if not _is_skipped(row)]
    tags = Tag.query.order_by(Tag.id).all()
    return render_template(
        "links.html", links=links_list, tags=tags, q=q, category=category
    )


@app.route("/links.json")
def links_json():
    """Live state for every link, used to animate cards during classification."""
    rows = Link.query.order_by(Link.created_at.desc()).all()
    return {"links": [_serialize_link(row) for row in rows]}


@app.route("/settings")
def settings():
    total = Link.query.count()
    pending = Link.query.filter_by(status="pending").count()
    tags = Tag.query.order_by(Tag.id).all()
    return render_template(
        "settings.html", total=total, pending=pending, tags=tags
    )


@app.route("/tags/add", methods=["POST"])
def add_tag():
    name = (request.form.get("name") or "").strip()
    description = (request.form.get("description") or "").strip()
    if not name:
        abort(400, "tag name is required")
    if not Tag.query.filter(db.func.lower(Tag.name) == name.lower()).first():
        db.session.add(Tag(name=name, description=description))
        db.session.commit()
    return redirect(url_for("settings"))


@app.route("/tags/<int:tag_id>/edit", methods=["POST"])
def edit_tag(tag_id):
    tag = Tag.query.get_or_404(tag_id)
    name = (request.form.get("name") or "").strip()
    if not name:
        abort(400, "tag name is required")
    # Reject a rename that would collide with a different existing tag.
    clash = Tag.query.filter(
        db.func.lower(Tag.name) == name.lower(), Tag.id != tag_id
    ).first()
    if not clash:
        tag.name = name
    tag.description = (request.form.get("description") or "").strip()
    db.session.commit()
    return redirect(url_for("settings"))


@app.route("/tags/<int:tag_id>/delete", methods=["POST"])
def delete_tag(tag_id):
    tag = Tag.query.get_or_404(tag_id)
    db.session.delete(tag)
    db.session.commit()
    return redirect(url_for("settings"))


_job_state = {
    "retrieve": {"running": False, "finished_at": 0.0, "error": None},
    "reclassify": {"running": False, "finished_at": 0.0, "error": None},
}
_job_lock = threading.Lock()


def _run_job(name, target):
    with _job_lock:
        _job_state[name]["running"] = True
        _job_state[name]["error"] = None
    error = None
    try:
        target()
    except Exception as exc:
        error = str(exc)
    finally:
        with _job_lock:
            _job_state[name]["running"] = False
            _job_state[name]["finished_at"] = time.time()
            _job_state[name]["error"] = error


def _start_job(name, target):
    """Start a background job unless one of the same kind is already running."""
    with _job_lock:
        if _job_state[name]["running"]:
            return False
    threading.Thread(target=_run_job, args=(name, target), daemon=True).start()
    return True


def _run_classify():
    from classify import classify_pending

    classify_pending()


@app.route("/status")
def status():
    with _job_lock:
        state = {k: dict(v) for k, v in _job_state.items()}
    state["reclassify"]["pending"] = Link.query.filter_by(status="pending").count()
    state["reclassify"]["total"] = Link.query.count()
    return state


def _window_cutoff(window, custom_days):
    """Translate a window choice into a cutoff datetime, or None for 'all'.

    Links created on/after the cutoff are eligible for reclassification.
    """
    preset_days = {"7": 7, "30": 30}
    if window == "all" or not window:
        return None
    if window == "custom":
        try:
            days = int(custom_days)
        except (TypeError, ValueError):
            days = 0
        days = max(days, 0)
        return datetime.utcnow() - timedelta(days=days)
    days = preset_days.get(window)
    if days is None:
        return None
    return datetime.utcnow() - timedelta(days=days)


@app.route("/reclassify", methods=["POST"])
def reclassify():
    """Reset matching links to pending and re-run classification in the background.

    An optional ``window`` (all | 7 | 30 | custom, with ``custom_days``) limits
    the reset to links created within that many days.
    """
    cutoff = _window_cutoff(
        request.form.get("window"), request.form.get("custom_days")
    )

    query = Link.query
    if cutoff is not None:
        query = query.filter(Link.created_at >= cutoff)

    query.update(
        {
            "status": "pending",
            "title": None,
            "category": None,
            "summary": None,
            "keep_as_bookmark": None,
            "reason": None,
        },
        synchronize_session=False,
    )
    db.session.commit()

    _start_job("reclassify", _run_classify)

    return redirect(url_for("links"))


@app.route("/retrieve", methods=["POST"])
def retrieve():
    """Re-run the Messages export in the background (regenerates data/messages.csv)."""
    from retrieve_messages import retrieve as retrieve_messages

    _start_job("retrieve", retrieve_messages)

    return redirect(url_for("settings"))


@app.route("/links/<int:link_id>/update", methods=["POST"])
def update_link(link_id):
    """Update a link's classification or keep/skip decision from the UI.

    Accepts JSON or form data with keys: `keep_as_bookmark` (bool-like),
    `category` (string), and optional `reason` (string). Returns the updated
    serialized link as JSON.
    """
    link = Link.query.get_or_404(link_id)

    if request.is_json:
        data = request.get_json()
    else:
        data = request.form

    # Parse keep_as_bookmark with some flexibility ("true"/"1"/boolean)
    keep = data.get("keep_as_bookmark")
    if keep is not None:
        if isinstance(keep, str):
            link.keep_as_bookmark = keep.lower() in ("1", "true", "yes", "on")
        else:
            link.keep_as_bookmark = bool(keep)

    # Category may be an empty string to clear
    if "category" in data:
        cat = data.get("category")
        link.category = cat if cat else None

    if "reason" in data:
        link.reason = data.get("reason") or None

    db.session.commit()
    return _serialize_link(link)


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)