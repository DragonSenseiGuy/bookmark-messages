from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

db = SQLAlchemy()


link_tags = db.Table(
    "link_tags",
    db.Column(
        "link_id",
        db.Integer,
        db.ForeignKey("links.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    db.Column(
        "tag_id",
        db.Integer,
        db.ForeignKey("tags.id", ondelete="CASCADE"),
        primary_key=True,
    ),
)


class Setting(db.Model):
    __tablename__ = "settings"
    key = db.Column(db.String, primary_key=True)
    value = db.Column(db.String, nullable=False, default="")


class Admin(db.Model):
    __tablename__ = "admins"
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String, nullable=False, unique=True)
    password_hash = db.Column(db.String, nullable=False)


class UploadCheckpoint(db.Model):
    __tablename__ = "upload_checkpoints"
    contact_id = db.Column(db.String(64), primary_key=True)
    message_date = db.Column(db.Integer, nullable=False, default=0)
    updated_at = db.Column(
        db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class Tag(db.Model):
    """An editable label that can be assigned to many links.

    Seeded from config.CATEGORIES on first run, then owned by the user via the
    settings UI. The set of tag names is the taxonomy handed to the classifier.
    """

    __tablename__ = "tags"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String, nullable=False, unique=True)
    # Optional hint shown to the model to disambiguate when to use this tag.
    description = db.Column(db.String, default="")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    links = db.relationship(
        "Link", secondary=link_tags, back_populates="tags"
    )


class Link(db.Model):
    __tablename__ = "links"
    id = db.Column(db.Integer, primary_key=True)
    url = db.Column(db.String, nullable=False, unique=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    status = db.Column(db.String, nullable=False, default="pending")
    title = db.Column(db.String)
    summary = db.Column(db.Text)
    keep_as_bookmark = db.Column(db.Boolean)
    reason = db.Column(db.String)
    health_status = db.Column(db.String)
    last_checked_at = db.Column(db.DateTime)
    tags = db.relationship(
        "Tag", secondary=link_tags, back_populates="links", order_by="Tag.id"
    )
