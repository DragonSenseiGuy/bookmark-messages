from flask import (
    Flask,
    Response,
    abort,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from sqlalchemy import text
from urllib.parse import urlparse
from datetime import datetime, timedelta
from functools import wraps
import hashlib
import hmac
import os
import re
import secrets
import threading
import time
import config
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash, generate_password_hash

from models import Admin, db, Link, Setting, Tag, UploadCheckpoint, link_tags

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY") or secrets.token_hex(32)
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = os.environ.get("PUBLIC_URL", "").startswith(
    "https://"
)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

BASEDIR = os.path.abspath(os.path.dirname(__file__))
DATA_DIR = os.path.join(BASEDIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH = os.environ.get("BOOKMARK_DB_PATH", os.path.join(DATA_DIR, "bookmarks.db"))
app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{DB_PATH}"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db.init_app(app)

def seed_tags():
    """Populate the tags table from the built-in defaults on first run."""
    if Tag.query.count() == 0:
        for name in config.CATEGORIES:
            db.session.add(Tag(name=name, description=""))
        db.session.commit()


def seed_admin():
    """Create the single administrator from first-boot environment values."""
    if Admin.query.first() is not None:
        return
    email = os.environ.get("ADMIN_EMAIL", "").strip().lower()
    password = os.environ.get("ADMIN_PASSWORD", "")
    if email and password:
        db.session.add(
            Admin(email=email, password_hash=generate_password_hash(password))
        )
        db.session.commit()


def migrate_link_health_columns():
    """Add health columns to databases created before health checks existed."""
    columns = {
        row[1]
        for row in db.session.execute(text("PRAGMA table_info(links)"))
    }
    if "health_status" not in columns:
        db.session.execute(text("ALTER TABLE links ADD COLUMN health_status VARCHAR"))
    if "last_checked_at" not in columns:
        db.session.execute(text("ALTER TABLE links ADD COLUMN last_checked_at DATETIME"))
    db.session.commit()


def migrate_link_categories():
    """Move values from the old links.category column into link_tags."""
    columns = {
        row[1]
        for row in db.session.execute(text("PRAGMA table_info(links)"))
    }
    if "category" not in columns:
        return

    legacy = db.session.execute(
        text("SELECT id, category FROM links WHERE category IS NOT NULL")
    ).all()
    for link_id, name in legacy:
        name = name.strip()
        if not name:
            continue
        tag = Tag.query.filter(db.func.lower(Tag.name) == name.lower()).first()
        if tag is None:
            tag = Tag(name=name, description="")
            db.session.add(tag)
            db.session.flush()
        db.session.execute(
            link_tags.insert().prefix_with("OR IGNORE"),
            {"link_id": link_id, "tag_id": tag.id},
        )
    db.session.execute(text("UPDATE links SET category = NULL"))
    db.session.commit()


with app.app_context():
    db.create_all()
    migrate_link_health_columns()
    seed_tags()
    migrate_link_categories()
    seed_admin()


def _setting_enabled(key):
    setting = db.session.get(Setting, key)
    return setting is not None and setting.value == "true"


def _is_admin():
    admin_id = session.get("admin_id")
    return admin_id is not None and db.session.get(Admin, admin_id) is not None


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not _is_admin():
            return redirect(url_for("login", next=request.full_path))
        return view(*args, **kwargs)

    return wrapped


def _csrf_token():
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_urlsafe(32)
    return session["csrf_token"]


@app.context_processor
def _template_context():
    return {"csrf_token": _csrf_token, "is_admin": _is_admin()}


@app.before_request
def _protect_forms():
    if request.method != "POST" or request.endpoint in {
        "api_upload",
        "api_upload_status",
    }:
        return
    supplied = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token")
    if not supplied or not hmac.compare_digest(supplied, _csrf_token()):
        abort(400, "invalid CSRF token")


@app.route("/")
def main():
    return redirect(url_for("links"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if _is_admin():
        return redirect(url_for("links"))
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""
        admin = Admin.query.filter_by(email=email).first()
        if admin and check_password_hash(admin.password_hash, password):
            session.clear()
            session["admin_id"] = admin.id
            session.permanent = True
            return redirect(url_for("links"))
        flash("Email or password is incorrect.", "error")
    return render_template("login.html", configured=Admin.query.first() is not None)


@app.post("/logout")
@admin_required
def logout():
    session.clear()
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
        "tags": [tag.name for tag in row.tags],
        "summary": row.summary,
        "keep_as_bookmark": bool(row.keep_as_bookmark),
        "reason": row.reason,
        "health_status": row.health_status,
        "last_checked_at": (
            row.last_checked_at.isoformat() if row.last_checked_at else None
        ),
    }


def _is_skipped(row):
    """A link that finished classification and was not worth keeping."""
    return row.status == "classified" and not row.keep_as_bookmark


@app.route("/links")
def links():
    admin = _is_admin()
    if not admin and not _setting_enabled("public_viewing"):
        return render_template("private.html")

    # Search and tag filtering happen client-side, so the full set is always
    # rendered. ``q``/``tag`` only seed the initial UI
    # state (from a shared/bookmarked URL); the browser applies them on load.
    q = (request.args.get("q") or "").strip()
    tag = (request.args.get("tag") or "").strip()
    include_skipped = admin and request.args.get("include") == "skipped"

    query = Link.query.order_by(Link.created_at.desc())
    if admin:
        rows = query.all()
        links_list = [
            _serialize_link(row)
            for row in rows
            if _is_skipped(row) == include_skipped
        ]
        tags = Tag.query.order_by(Tag.id).all()
    else:
        rows = query.filter_by(status="classified", keep_as_bookmark=True).all()
        links_list = [_serialize_link(row) for row in rows]
        visible_tag_ids = {tag.id for row in rows for tag in row.tags}
        tags = [
            tag
            for tag in Tag.query.order_by(Tag.id).all()
            if tag.id in visible_tag_ids
        ]
    return render_template(
        "links.html",
        links=links_list,
        tags=tags,
        q=q,
        selected_tag=tag,
        include_skipped=include_skipped,
    )


@app.route("/links.json")
@admin_required
def links_json():
    """Live state for every link, used to animate cards during classification."""
    rows = Link.query.order_by(Link.created_at.desc()).all()
    return {"links": [_serialize_link(row) for row in rows]}


@app.route("/links/<int:link_id>/delete", methods=["POST"])
@admin_required
def delete_link(link_id):
    link = Link.query.get_or_404(link_id)
    db.session.delete(link)
    db.session.commit()
    return redirect(
        url_for(
            "links",
            q=request.args.get("q") or None,
            tag=request.args.get("tag") or None,
            include=request.args.get("include") or None,
        )
    )


@app.route("/settings")
@admin_required
def settings():
    return _render_settings()


def _render_settings(upload_token=None):
    total = Link.query.count()
    pending = Link.query.filter_by(status="pending").count()
    tags = Tag.query.order_by(Tag.id).all()
    return render_template(
        "settings.html",
        total=total,
        pending=pending,
        tags=tags,
        public_viewing=_setting_enabled("public_viewing"),
        has_upload_token=db.session.get(Setting, "upload_token_hash") is not None,
        upload_token=upload_token,
        public_url=(os.environ.get("PUBLIC_URL") or request.url_root).rstrip("/"),
    )


@app.post("/settings/public-viewing")
@admin_required
def set_public_viewing():
    setting = db.session.get(Setting, "public_viewing")
    value = "true" if request.form.get("enabled") == "true" else "false"
    if setting:
        setting.value = value
    else:
        db.session.add(Setting(key="public_viewing", value=value))
    db.session.commit()
    flash("Public viewing updated.", "success")
    return redirect(url_for("settings"))


@app.post("/settings/upload-token")
@admin_required
def generate_upload_token():
    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    setting = db.session.get(Setting, "upload_token_hash")
    if setting:
        setting.value = token_hash
    else:
        db.session.add(Setting(key="upload_token_hash", value=token_hash))
    db.session.commit()
    return _render_settings(upload_token=token)


@app.post("/settings/password")
@admin_required
def change_password():
    current = request.form.get("current_password") or ""
    new = request.form.get("new_password") or ""
    admin = db.session.get(Admin, session["admin_id"])
    if not check_password_hash(admin.password_hash, current):
        flash("Current password is incorrect.", "error")
    elif len(new) < 10:
        flash("New password must be at least 10 characters.", "error")
    else:
        admin.password_hash = generate_password_hash(new)
        db.session.commit()
        flash("Password changed.", "success")
    return redirect(url_for("settings"))


@app.route("/tags/add", methods=["POST"])
@admin_required
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
@admin_required
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
@admin_required
def delete_tag(tag_id):
    tag = Tag.query.get_or_404(tag_id)
    tag.links.clear()
    db.session.delete(tag)
    db.session.commit()
    return redirect(url_for("settings"))


_job_state = {
    "reclassify": {
        "running": False,
        "rerun": False,
        "finished_at": 0.0,
        "error": None,
    },
    "health": {"running": False, "finished_at": 0.0, "error": None},
}
_job_lock = threading.Lock()


def _run_job(name, target):
    error = None
    while True:
        try:
            target()
        except Exception as exc:
            error = str(exc)
            break

        with _job_lock:
            if _job_state[name].get("rerun"):
                _job_state[name]["rerun"] = False
                continue
            _job_state[name]["running"] = False
            _job_state[name]["finished_at"] = time.time()
            _job_state[name]["error"] = None
            return

    with _job_lock:
        _job_state[name]["running"] = False
        _job_state[name]["rerun"] = False
        _job_state[name]["finished_at"] = time.time()
        _job_state[name]["error"] = error


def _start_job(name, target):
    """Start a background job unless one of the same kind is already running."""
    with _job_lock:
        if _job_state[name]["running"]:
            if name == "reclassify":
                _job_state[name]["rerun"] = True
            return False
        _job_state[name]["running"] = True
        if name == "reclassify":
            _job_state[name]["rerun"] = False
        _job_state[name]["error"] = None
    threading.Thread(target=_run_job, args=(name, target), daemon=True).start()
    return True


def _run_classify():
    from classify import classify_pending

    classify_pending()


def _run_health_check():
    from classify import check_link_health

    if check_link_health():
        _start_job("reclassify", _run_classify)


@app.before_request
def _maybe_start_health_check():
    """Start one health scan per interval while the application is in use."""
    with _job_lock:
        classifier_running = _job_state["reclassify"]["running"]
    if (
        not classifier_running
        and Link.query.filter_by(status="pending").first() is not None
    ):
        _start_job("reclassify", _run_classify)

    interval = config.HEALTH_CHECK_INTERVAL_HOURS
    if interval <= 0:
        return

    now = datetime.utcnow()
    setting = db.session.get(Setting, "last_health_check_at")
    try:
        last_run = datetime.fromisoformat(setting.value) if setting else None
    except ValueError:
        last_run = None
    if last_run and now - last_run < timedelta(hours=interval):
        return

    if setting:
        setting.value = now.isoformat()
    else:
        db.session.add(Setting(key="last_health_check_at", value=now.isoformat()))
    db.session.commit()
    _start_job("health", _run_health_check)


@app.route("/status")
@admin_required
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
@admin_required
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

    link_ids = [link_id for (link_id,) in query.with_entities(Link.id).all()]
    query.update(
        {
            "status": "pending",
            "title": None,
            "summary": None,
            "keep_as_bookmark": None,
            "reason": None,
        },
        synchronize_session=False,
    )
    if link_ids:
        db.session.execute(link_tags.delete().where(link_tags.c.link_id.in_(link_ids)))
    db.session.commit()

    _start_job("reclassify", _run_classify)

    return redirect(url_for("links"))


@app.route("/links/<int:link_id>/retry", methods=["POST"])
@admin_required
def retry_link(link_id):
    """Reset one link and fetch/classify it again in the background."""
    link = Link.query.get_or_404(link_id)

    link.status = "pending"
    link.title = None
    link.tags = []
    link.summary = None
    link.keep_as_bookmark = None
    link.reason = None
    link.health_status = None
    db.session.commit()
    _start_job("reclassify", _run_classify)
    return _serialize_link(link)


@app.route("/links/<int:link_id>/update", methods=["POST"])
@admin_required
def update_link(link_id):
    """Update a link's classification or keep/skip decision from the UI.

    Accepts JSON or form data with keys: `keep_as_bookmark` (bool-like),
    `tags` (a list or comma-separated string), and optional `reason`. Returns the updated
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

    if "tags" in data:
        names = data.get("tags") or []
        if isinstance(names, str):
            names = [name.strip() for name in names.split(",") if name.strip()]
        if not isinstance(names, list) or not all(
            isinstance(name, str) for name in names
        ):
            abort(400, "tags must be a list of tag names")
        tags_by_name = {
            tag.name: tag for tag in Tag.query.filter(Tag.name.in_(names)).all()
        }
        unknown = [name for name in names if name not in tags_by_name]
        if unknown:
            abort(400, f"unknown tag: {unknown[0]}")
        link.tags = [tags_by_name[name] for name in dict.fromkeys(names)]

    if "reason" in data:
        link.reason = data.get("reason") or None

    db.session.commit()
    return _serialize_link(link)


def _valid_upload_token():
    authorization = request.headers.get("Authorization", "")
    if not authorization.startswith("Bearer "):
        return False
    expected = db.session.get(Setting, "upload_token_hash")
    if expected is None:
        return False
    supplied = hashlib.sha256(authorization[7:].encode()).hexdigest()
    return hmac.compare_digest(supplied, expected.value)


def upload_token_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not _valid_upload_token():
            return {"error": "invalid upload token"}, 401
        return view(*args, **kwargs)

    return wrapped


def _valid_contact_id(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


@app.get("/cli")
def cli_script():
    server_url = (os.environ.get("PUBLIC_URL") or request.url_root).rstrip("/")
    script = render_template("upload-links.sh", server_url=server_url)
    return Response(script, mimetype="text/x-shellscript")


@app.get("/api/upload/checkpoint/<contact_id>")
@upload_token_required
def api_upload_checkpoint(contact_id):
    if not _valid_contact_id(contact_id):
        return {"error": "invalid contact id"}, 400
    checkpoint = db.session.get(UploadCheckpoint, contact_id)
    return {"message_date": checkpoint.message_date if checkpoint else None}


@app.post("/api/upload")
@upload_token_required
def api_upload():
    payload = request.get_json(silent=True) or {}
    contact_id = payload.get("contact_id")
    urls = payload.get("urls")
    try:
        message_date = int(payload.get("message_date"))
    except (TypeError, ValueError):
        return {"error": "message_date must be an integer"}, 400
    if not _valid_contact_id(contact_id):
        return {"error": "invalid contact id"}, 400
    if message_date < 0:
        return {"error": "message_date must not be negative"}, 400
    if not isinstance(urls, list) or len(urls) > 5000:
        return {"error": "urls must be a list of at most 5000 items"}, 400

    cleaned = []
    for value in urls:
        if not isinstance(value, str):
            return {"error": "every URL must be a string"}, 400
        value = value.strip()
        if value.startswith("www."):
            value = "https://" + value
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return {"error": f"invalid URL: {value[:120]}"}, 400
        if len(value) > 4096:
            return {"error": "URL is too long"}, 400
        if value not in cleaned:
            cleaned.append(value)

    existing = {
        link.url: link for link in Link.query.filter(Link.url.in_(cleaned)).all()
    } if cleaned else {}
    new_count = 0
    links = []
    for url in cleaned:
        link = existing.get(url)
        if link is None:
            link = Link(url=url)
            db.session.add(link)
            existing[url] = link
            new_count += 1
        links.append(link)

    checkpoint = db.session.get(UploadCheckpoint, contact_id)
    if checkpoint is None:
        checkpoint = UploadCheckpoint(contact_id=contact_id, message_date=message_date)
        db.session.add(checkpoint)
    elif message_date > checkpoint.message_date:
        checkpoint.message_date = message_date
    db.session.commit()

    if new_count:
        _start_job("reclassify", _run_classify)
    return {
        "new": new_count,
        "duplicates": len(cleaned) - new_count,
        "link_ids": [link.id for link in links],
        "message_date": checkpoint.message_date,
    }


@app.post("/api/upload/status")
@upload_token_required
def api_upload_status():
    payload = request.get_json(silent=True) or {}
    ids = payload.get("link_ids")
    if not isinstance(ids, list) or len(ids) > 5000 or not all(
        isinstance(link_id, int) for link_id in ids
    ):
        return {"error": "link_ids must be a list of integers"}, 400
    rows = Link.query.filter(Link.id.in_(ids)).all() if ids else []
    states = {str(row.id): row.status for row in rows}
    failed = [
        row.id
        for row in rows
        if row.status == "failed" and row.reason == "AI model not available at this time"
    ]
    return {"states": states, "failed": failed}


@app.get("/health")
def health():
    db.session.execute(text("SELECT 1"))
    return {"status": "ok"}


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
