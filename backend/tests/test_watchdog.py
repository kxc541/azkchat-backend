from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
import watchdog


def make_file_doc(uid, doc_id, stage, minutes_ago):
    ts = (
        datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    ).isoformat().replace("+00:00", "Z")

    doc = MagicMock()
    doc.id = doc_id
    doc.to_dict.return_value = {
        "status": "processing",
        "stage": stage,
        "last_update": ts,
        "last_progress_at": ts,
        "processing_started_at": ts,
    }
    doc.reference.parent.parent.id = uid
    return doc


# ---------------------------
# has_active_rq_jobs
# ---------------------------

class TestHasActiveRqJobs:
    def _make_queue(self, queued=0, started=0):
        q = MagicMock()
        q.__len__ = MagicMock(return_value=queued)
        q.started_job_registry.count = started
        return q

    def test_true_when_jobs_queued(self):
        with patch("watchdog.Redis"), patch("watchdog.Queue", return_value=self._make_queue(queued=2)):
            assert watchdog.has_active_rq_jobs() is True

    def test_true_when_jobs_started(self):
        with patch("watchdog.Redis"), patch("watchdog.Queue", return_value=self._make_queue(started=1)):
            assert watchdog.has_active_rq_jobs() is True

    def test_false_when_idle(self):
        with patch("watchdog.Redis"), patch("watchdog.Queue", return_value=self._make_queue()):
            assert watchdog.has_active_rq_jobs() is False

    def test_true_on_redis_error(self):
        with patch("watchdog.Redis", side_effect=Exception("connection refused")):
            assert watchdog.has_active_rq_jobs() is True


# ---------------------------
# reap_stale_jobs
# ---------------------------

class TestReapStaleJobs:
    def _stub_query(self, docs):
        mock_db = watchdog.db
        mock_db.collection_group.return_value \
               .where.return_value \
               .limit.return_value \
               .stream.return_value = iter(docs)

    def test_uses_collection_group_not_per_user_scan(self):
        self._stub_query([])
        watchdog.db.collection.reset_mock()
        watchdog.reap_stale_jobs()
        watchdog.db.collection_group.assert_called_with("files")
        watchdog.db.collection.assert_not_called()

    def test_no_reaped_when_no_stuck_docs(self):
        self._stub_query([])
        with patch.object(watchdog, "mark_failed") as mock_mark:
            assert watchdog.reap_stale_jobs() == 0
        mock_mark.assert_not_called()

    def test_reaps_stale_storing_job(self):
        doc = make_file_doc("user1", "doc1", stage="storing", minutes_ago=20)
        self._stub_query([doc])
        with patch.object(watchdog, "mark_failed") as mock_mark:
            assert watchdog.reap_stale_jobs() == 1
        mock_mark.assert_called_once_with("user1", "doc1", "stuck_in_storing")

    def test_reaps_stale_non_storing_job_with_generic_reason(self):
        doc = make_file_doc("user1", "doc1", stage="embedding", minutes_ago=20)
        self._stub_query([doc])
        with patch.object(watchdog, "mark_failed") as mock_mark:
            watchdog.reap_stale_jobs()
        mock_mark.assert_called_once_with("user1", "doc1", "stale_job_reaped")

    def test_leaves_fresh_job_alone(self):
        doc = make_file_doc("user1", "doc1", stage="embedding", minutes_ago=5)
        self._stub_query([doc])
        with patch.object(watchdog, "mark_failed") as mock_mark:
            assert watchdog.reap_stale_jobs() == 0
        mock_mark.assert_not_called()

    def test_reaps_doc_with_missing_timestamps(self):
        doc = MagicMock()
        doc.id = "doc2"
        doc.to_dict.return_value = {"status": "processing", "stage": "loading"}
        doc.reference.parent.parent.id = "user2"
        self._stub_query([doc])
        with patch.object(watchdog, "mark_failed") as mock_mark:
            assert watchdog.reap_stale_jobs() == 1
        mock_mark.assert_called_once_with("user2", "doc2", "missing_timestamps")
