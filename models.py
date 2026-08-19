from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

db = SQLAlchemy()


class Submission(db.Model):
    __tablename__ = "submissions"
    id = db.Column(db.Integer, primary_key=True)
    text = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    links = db.relationship("Link", backref="submission", lazy=True)


class Tag(db.Model):
    """An editable classification category.

    Seeded from config.CATEGORIES on first run, then owned by the user via the
    settings UI. The set of tag names is the taxonomy handed to the classifier.
    """

    __tablename__ = "tags"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String, nullable=False, unique=True)
    # Optional hint shown to the model to disambiguate when to use this tag.
    description = db.Column(db.String, default="")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class Link(db.Model):
    __tablename__ = "links"
    id = db.Column(db.Integer, primary_key=True)
    url = db.Column(db.String, nullable=False, unique=True)
    submission_id = db.Column(db.Integer, db.ForeignKey("submissions.id", ondelete="SET NULL"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    status = db.Column(db.String, nullable=False, default="pending")
    title = db.Column(db.String)
    category = db.Column(db.String)
    summary = db.Column(db.Text)
    keep_as_bookmark = db.Column(db.Boolean)
    reason = db.Column(db.String)
