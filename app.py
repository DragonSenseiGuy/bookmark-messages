from flask import Flask, request, abort, render_template, redirect, url_for
from sqlalchemy.exc import IntegrityError
import os
import re
from models import db, Submission, Link

app = Flask(__name__)

# Use an absolute path for the data directory so the DB file can always be created
BASEDIR = os.path.abspath(os.path.dirname(__file__))
DATA_DIR = os.path.join(BASEDIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, "bookmarks.db")
app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{DB_PATH}"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db.init_app(app)

url_pattern = re.compile(r"(?:https?://|www\.)[^\s<>\")\]]+")
md_pattern = re.compile(r"\[[^\]]+\]\((https?://[^)\s]+)\)")
with app.app_context():
    db.create_all()


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


@app.route("/links")
def links():
    rows = Link.query.order_by(Link.created_at.desc()).all()
    links_list = [r.url for r in rows]
    return render_template("links.html", links=links_list)


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)