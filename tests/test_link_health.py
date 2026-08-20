import os
import tempfile
import unittest
from datetime import datetime, timedelta
import hashlib
from unittest.mock import patch

from bs4 import BeautifulSoup
from werkzeug.security import generate_password_hash


_temp_dir = tempfile.TemporaryDirectory()
os.environ["BOOKMARK_DB_PATH"] = os.path.join(_temp_dir.name, "test.db")
os.environ["HEALTH_CHECK_INTERVAL_HOURS"] = "0"

import app as application
import ai
import classify
from models import Admin, Link, Setting, Tag, UploadCheckpoint, db


class LinkHealthTest(unittest.TestCase):
    def setUp(self):
        application.app.config["TESTING"] = True
        with application.app.app_context():
            db.drop_all()
            db.create_all()
            application.seed_tags()
            db.session.add(
                Admin(
                    email="admin@example.com",
                    password_hash=generate_password_hash("test-password"),
                )
            )
            db.session.commit()

    def admin_client(self):
        client = application.app.test_client()
        with client.session_transaction() as session:
            session["admin_id"] = 1
            session["csrf_token"] = "test-csrf"
        return client

    def add_link(self, **values):
        with application.app.app_context():
            tag_names = values.pop("tags", [])
            link = Link(url=values.pop("url", "https://example.com"), **values)
            link.tags = Tag.query.filter(Tag.name.in_(tag_names)).all()
            db.session.add(link)
            db.session.commit()
            return link.id

    def test_retry_resets_a_classified_link(self):
        link_id = self.add_link(
            status="classified",
            title="Example",
            tags=["Article"],
            summary="Saved page",
            keep_as_bookmark=True,
            reason="Useful",
            health_status="reachable",
        )

        with patch.object(application, "_start_job", return_value=True) as start:
            response = self.admin_client().post(
                f"/links/{link_id}/retry",
                headers={"X-CSRF-Token": "test-csrf"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["status"], "pending")
        with application.app.app_context():
            link = db.session.get(Link, link_id)
            self.assertEqual(link.tags, [])
            self.assertIsNone(link.reason)
            self.assertIsNone(link.health_status)
        start.assert_called_once_with("reclassify", application._run_classify)

    def test_active_classifier_queues_another_pass(self):
        state = application._job_state["reclassify"]
        with application._job_lock:
            state.update(running=True, rerun=False, error=None)

        try:
            started = application._start_job("reclassify", application._run_classify)
            self.assertFalse(started)
            self.assertTrue(state["rerun"])
        finally:
            with application._job_lock:
                state.update(running=False, rerun=False, error=None)

    def test_health_check_reclassifies_a_recovered_fetch_failure(self):
        link_id = self.add_link(
            status="classified",
            title="https://example.com",
            tags=["Other"],
            summary="",
            keep_as_bookmark=False,
            reason="unreachable",
            health_status="unreachable",
            last_checked_at=datetime.utcnow() - timedelta(days=2),
        )
        page = ("Example", "Description", "Body")
        result = {
            "title": "Example",
            "tags": ["Article", "Repo/Tool"],
            "summary": "Recovered page",
            "keep_as_bookmark": True,
            "reason": "Useful",
        }

        with patch.object(classify.config, "HEALTH_CHECK_INTERVAL_HOURS", 24), patch.object(
            classify, "fetch_page", side_effect=[page, page]
        ), patch.object(classify.ai, "classify", return_value=result), patch.object(
            classify.time, "sleep"
        ):
            recovered = classify.check_link_health()
            classify.classify_pending()

        self.assertTrue(recovered)
        with application.app.app_context():
            link = db.session.get(Link, link_id)
            self.assertEqual(link.status, "classified")
            self.assertEqual(link.health_status, "reachable")
            self.assertEqual([tag.name for tag in link.tags], ["Article", "Repo/Tool"])
            self.assertTrue(link.keep_as_bookmark)

    def test_health_check_preserves_a_bookmark_when_it_goes_down(self):
        link_id = self.add_link(
            status="classified",
            title="Saved page",
            tags=["Article"],
            summary="Worth keeping",
            keep_as_bookmark=True,
            reason="Useful",
            health_status="reachable",
            last_checked_at=datetime.utcnow() - timedelta(days=2),
        )

        with patch.object(classify.config, "HEALTH_CHECK_INTERVAL_HOURS", 24), patch.object(
            classify, "fetch_page", return_value=None
        ):
            classify.check_link_health()

        with application.app.app_context():
            link = db.session.get(Link, link_id)
            self.assertEqual(link.health_status, "unreachable")
            self.assertEqual([tag.name for tag in link.tags], ["Article"])
            self.assertTrue(link.keep_as_bookmark)
            self.assertEqual(link.reason, "Useful")

    def test_fetch_rejects_private_network_urls(self):
        with patch.object(classify.requests, "get") as get:
            page = classify.fetch_page("http://127.0.0.1/private")

        self.assertIsNone(page)
        get.assert_not_called()

    def test_link_can_be_updated_with_multiple_tags(self):
        link_id = self.add_link(status="classified", tags=["Article"])

        response = self.admin_client().post(
            f"/links/{link_id}/update",
            json={"tags": ["Video", "Social"]},
            headers={"X-CSRF-Token": "test-csrf"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["tags"], ["Video", "Social"])
        with application.app.app_context():
            link = db.session.get(Link, link_id)
            self.assertEqual([tag.name for tag in link.tags], ["Video", "Social"])

    def test_unknown_tag_is_rejected(self):
        link_id = self.add_link(status="classified")

        response = self.admin_client().post(
            f"/links/{link_id}/update",
            json={"tags": ["Not configured"]},
            headers={"X-CSRF-Token": "test-csrf"},
        )

        self.assertEqual(response.status_code, 400)

    def test_links_page_renders_all_assigned_tags(self):
        self.add_link(
            status="classified",
            title="Tagged link",
            tags=["Article", "Social"],
            keep_as_bookmark=True,
        )

        response = self.admin_client().get("/links?tag=Social")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Article", response.data)
        self.assertIn(b"Social", response.data)
        self.assertIn(b'data-tags="[&#34;Article&#34;, &#34;Social&#34;]"', response.data)

    def test_legacy_category_is_migrated_to_a_tag(self):
        link_id = self.add_link(status="classified")
        with application.app.app_context():
            db.session.execute(db.text("ALTER TABLE links ADD COLUMN category VARCHAR"))
            db.session.execute(
                db.text("UPDATE links SET category = 'Article' WHERE id = :id"),
                {"id": link_id},
            )
            db.session.commit()
            application.migrate_link_categories()

            link = db.session.get(Link, link_id)
            self.assertEqual([tag.name for tag in link.tags], ["Article"])
            category = db.session.execute(
                db.text("SELECT category FROM links WHERE id = :id"),
                {"id": link_id},
            ).scalar_one()
            self.assertIsNone(category)

    def test_ai_accepts_multiple_known_tags_and_drops_unknown_tags(self):
        content = """{
            "title": "Example",
            "tags": ["Article", "Unknown", "Article", "Social"],
            "summary": "Summary",
            "keep_as_bookmark": true,
            "reason": "Useful"
        }"""

        result = ai._parse_json(content, ["Article", "Social", "Other"])

        self.assertEqual(result["tags"], ["Article", "Social"])

    def test_private_library_requires_admin(self):
        response = application.app.test_client().get("/links")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"This library is private", response.data)
        self.assertNotIn(b"Settings", response.data)

    def test_admin_can_log_in_and_generate_an_upload_token(self):
        client = application.app.test_client()
        client.get("/login")
        with client.session_transaction() as session:
            csrf_token = session["csrf_token"]

        login = client.post(
            "/login",
            data={
                "email": "admin@example.com",
                "password": "test-password",
                "csrf_token": csrf_token,
            },
        )
        settings = client.get("/settings")
        with client.session_transaction() as session:
            csrf_token = session["csrf_token"]
        token = client.post(
            "/settings/upload-token", data={"csrf_token": csrf_token}
        )

        self.assertEqual(login.status_code, 302)
        self.assertEqual(settings.status_code, 200)
        self.assertIn(b"Upload from Messages", settings.data)
        self.assertIn(b"Copy this token now", token.data)
        with application.app.app_context():
            self.assertIsNotNone(db.session.get(Setting, "upload_token_hash"))

    def test_public_library_only_renders_kept_links(self):
        self.add_link(
            url="https://kept.example",
            status="classified",
            title="Kept link",
            keep_as_bookmark=True,
            reason="private model reasoning",
        )
        self.add_link(
            url="https://skipped.example",
            status="classified",
            title="Skipped link",
            keep_as_bookmark=False,
        )
        with application.app.app_context():
            db.session.add(Setting(key="public_viewing", value="true"))
            db.session.commit()

        response = application.app.test_client().get("/links")

        self.assertIn(b"Kept link", response.data)
        self.assertNotIn(b"Skipped link", response.data)
        self.assertNotIn(b"private model reasoning", response.data)
        page = BeautifulSoup(response.data, "html.parser")
        self.assertFalse(
            any("Retry" in button.get_text() for button in page.find_all("button"))
        )
        self.assertEqual(
            application.app.test_client().get("/links.json").status_code, 302
        )

    def test_upload_token_adds_links_and_advances_checkpoint(self):
        token = "upload-token"
        contact_id = "a" * 64
        with application.app.app_context():
            db.session.add(
                Setting(
                    key="upload_token_hash",
                    value=hashlib.sha256(token.encode()).hexdigest(),
                )
            )
            db.session.commit()
        headers = {"Authorization": f"Bearer {token}"}

        with patch.object(application, "_start_job", return_value=True):
            response = application.app.test_client().post(
                "/api/upload",
                headers=headers,
                json={
                    "contact_id": contact_id,
                    "message_date": 123,
                    "urls": ["https://example.com/new"],
                },
            )
            duplicate = application.app.test_client().post(
                "/api/upload",
                headers=headers,
                json={
                    "contact_id": contact_id,
                    "message_date": 456,
                    "urls": ["https://example.com/new"],
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["new"], 1)
        self.assertEqual(duplicate.get_json()["duplicates"], 1)
        with application.app.app_context():
            self.assertEqual(Link.query.count(), 1)
            self.assertEqual(
                db.session.get(UploadCheckpoint, contact_id).message_date, 456
            )

    def test_upload_rejects_an_invalid_token(self):
        response = application.app.test_client().post(
            "/api/upload",
            headers={"Authorization": "Bearer wrong"},
            json={"contact_id": "a" * 64, "message_date": 1, "urls": []},
        )

        self.assertEqual(response.status_code, 401)

    def test_ai_outage_keeps_link_retryable(self):
        link_id = self.add_link(url="https://failed.example", status="pending")
        page = ("Failed page", "Description", "Body")

        with patch.object(classify, "fetch_page", return_value=page), patch.object(
            classify.ai, "classify", return_value=None
        ):
            classify.classify_pending()

        with application.app.app_context():
            link = db.session.get(Link, link_id)
            self.assertEqual(link.status, "failed")
            self.assertEqual(link.reason, "AI model not available at this time")
            self.assertIsNone(link.keep_as_bookmark)


if __name__ == "__main__":
    unittest.main()
