"""
AZKChat Ingestion Watchdog

Purpose:
- Enforce terminal truth for ingestion jobs
- Ensure no Firestore document remains stuck in `processing`
- Operates independently of RQ workers and API
"""

from datetime import datetime, timedelta, timezone
import time

from firebase_admin_init import db
from redis import Redis
from rq import Queue
from logger import get_logger

log = get_logger(__name__)

# ---------------------------
# Configuration
# ---------------------------

MAX_STALE_MINUTES = 15
MAX_DOCS_PER_RUN = 500
SLEEP_ACTIVE = 60    # seconds between scans when jobs are in flight
SLEEP_IDLE = 300     # seconds between scans when nothing is queued or running

# ---------------------------
# Helpers
# ---------------------------

def utcnow():
    return datetime.now(timezone.utc)


def parse_ts(ts):
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:
        return None


def mark_failed(uid, doc_id, reason):
    now = utcnow().isoformat().replace("+00:00", "Z")
    db.collection("users") \
      .document(uid) \
      .collection("files") \
      .document(doc_id) \
      .set(
          {
              "status": "failed",
              "stage": "failed",
              "error": reason,
              "failed_at": now,
              "last_update": now,
              "last_progress_at": now,
          },
          merge=True,
      )
    log.warning("watchdog_job_reaped", uid=uid, doc_id=doc_id, reason=reason)


def has_active_rq_jobs():
    try:
        redis_conn = Redis(host="redis", port=6379)
        q = Queue(connection=redis_conn)
        return len(q) > 0 or q.started_job_registry.count > 0
    except Exception:
        return True  # fail safe: assume active if Redis is unreachable


# ---------------------------
# Core watchdog logic
# ---------------------------

def reap_stale_jobs():
    """
    Single collection group query across all tenants replaces the previous
    per-user scan, eliminating N+1 Firestore reads.
    Returns count of reaped jobs.
    """
    cutoff = utcnow() - timedelta(minutes=MAX_STALE_MINUTES)
    reaped = 0
    scanned = 0

    stuck_query = (
        db.collection_group("files")
          .where("status", "==", "processing")
          .limit(MAX_DOCS_PER_RUN)
    )

    for file_doc in stuck_query.stream():
        scanned += 1
        data = file_doc.to_dict() or {}
        doc_id = file_doc.id
        uid = file_doc.reference.parent.parent.id

        last_update = parse_ts(data.get("last_update"))
        last_progress = parse_ts(data.get("last_progress_at"))
        started_at = parse_ts(data.get("processing_started_at"))
        reference_ts = last_update or last_progress or started_at

        if not reference_ts:
            mark_failed(uid, doc_id, "missing_timestamps")
            reaped += 1
            continue

        if reference_ts < cutoff:
            reason = (
                "stuck_in_storing"
                if data.get("stage") == "storing"
                else "stale_job_reaped"
            )
            mark_failed(uid, doc_id, reason)
            reaped += 1

    log.info("watchdog_scan_complete", scanned=scanned, reaped=reaped)
    return reaped


# ---------------------------
# Entrypoint
# ---------------------------

def run_loop():
    log.info("watchdog_started")
    while True:
        reap_stale_jobs()
        sleep_secs = SLEEP_ACTIVE if has_active_rq_jobs() else SLEEP_IDLE
        time.sleep(sleep_secs)


if __name__ == "__main__":
    run_loop()
