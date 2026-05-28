from dotenv import load_dotenv
import os
import openai
import shutil
import stripe
import time
from pathlib import Path
from datetime import datetime

from fastapi import FastAPI, UploadFile, File, Request, Response, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel
from logger import get_logger

log = get_logger(__name__)

from weaviate_utils import create_schema, delete_file_chunks, delete_all_chunks
from query_utils import query_answer

from config_uploads import FREE_LIMIT, PAID_LIMIT, ALLOWED_TYPES

from firebase_admin_init import db
from google.cloud import firestore as gcf

from auth_decorators import (
    require_subscription,
    require_paid_subscription,
    require_auth,
    ensure_user_doc,
    ensure_api_key,
    widget_key_required,
)

from rq import Queue
from redis import Redis
from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

load_dotenv()
openai.api_key = os.getenv("OPENAI_API_KEY")
stripe.api_key = os.getenv("STRIPE_SECRET_KEY")
STRIPE_PRICE_ID = os.getenv("STRIPE_PRICE_ID")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET")
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:3000")

for folder in ["images", "temp_uploads", "scripts"]:
    os.makedirs(folder, exist_ok=True)

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/images", StaticFiles(directory="images"), name="images")
create_schema()


@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = time.time()
    response = await call_next(request)
    log.info(
        "request",
        method=request.method,
        path=request.url.path,
        status_code=response.status_code,
        duration_ms=round((time.time() - start) * 1000),
        uid=getattr(request.state, "uid", None),
    )
    return response

redis_conn = Redis(host="redis", port=6379)
q = Queue(connection=redis_conn, default_timeout=1200)


def get_uid_or_ip(request: Request):
    return getattr(request.state, "uid", get_remote_address(request))


limiter = Limiter(key_func=get_uid_or_ip)
app.state.limiter = limiter


@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request: Request, exc):
    return JSONResponse(
        status_code=429,
        content={"error": "Too many requests. Please slow down."},
    )


@app.get("/widget/me")
@widget_key_required
async def widget_me(request: Request):
    uid = request.state.uid
    user_doc = request.state.user_doc or {}

    return {
        "uid": uid,
        "email": user_doc.get("email"),
        "subscribed": bool(user_doc.get("subscribed", False)),
        "widget_tier": user_doc.get("widget_tier", "demo"),
        "branding": user_doc.get("branding", {}),
        "allowed_domains": user_doc.get("allowed_domains", []),
        "widgets": user_doc.get("widgets", []),
    }

def validate_file(file: UploadFile, is_paid_user: bool):
    if file.content_type not in ALLOWED_TYPES:
        raise HTTPException(400, "Invalid file type.")

    size_limit = PAID_LIMIT if is_paid_user else FREE_LIMIT

    total = 0
    chunk_size = 1024 * 1024

    while True:
        chunk = file.file.read(chunk_size)
        if not chunk:
            break

        total += len(chunk)

        if total > size_limit:
            raise HTTPException(
                400,
                f"File exceeds {'paid' if is_paid_user else 'trial'} size limit."
            )

    file.file.seek(0)


@app.post("/upload")
@require_subscription
@limiter.limit("3/minute")
async def upload_file(request: Request, file: UploadFile = File(...)):
    import secrets

    acting_uid = request.state.uid
    is_paid_user = request.state.subscribed

    if not is_paid_user:
        user_ref = db.collection("users").document(acting_uid)
        snap = user_ref.get()
        data = snap.to_dict() or {}

        if data.get("has_uploaded_trial_file") is True:
            raise HTTPException(
                status_code=403,
                detail="Trial users may upload only one file total."
            )

    validate_file(file, is_paid_user)

    files_ref = (
        db.collection("users")
        .document(acting_uid)
        .collection("files")
    )

    in_flight_processing = (
        files_ref.where("status", "==", "processing").limit(1).get()
    )

    in_flight_queued = (
        files_ref.where("status", "==", "queued").limit(1).get()
    )

    if in_flight_processing or in_flight_queued:
        raise HTTPException(
            status_code=409,
            detail="An upload is already in progress."
        )

    os.makedirs("temp_uploads", exist_ok=True)
    upload_id = secrets.token_hex(8)
    temp_file_path = f"temp_uploads/{upload_id}_{file.filename}"

    with open(temp_file_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    try:
        log.info("upload_received", uid=acting_uid, filename=file.filename)

        now = datetime.utcnow().isoformat() + "Z"
        doc_ref = files_ref.document()

        doc_ref.set({
            "filename": file.filename,
            "chunks": 0,
            "timestamp": now,
            "status": "queued",
            "stage": "queued",
            "processing_started_at": now,
            "last_update": now,
        })

        if not is_paid_user:
            db.collection("users").document(acting_uid).set(
                {"has_uploaded_trial_file": True},
                merge=True
            )

        job = q.enqueue(
            "worker.process_file",
            temp_file_path,
            file.filename,
            acting_uid,
            doc_ref.id,
            job_timeout=900,
            failure_ttl=86400,
        )

        return {
            "status": "queued",
            "job_id": job.id,
            "doc_id": doc_ref.id,
            "filename": file.filename,
        }

    except HTTPException:
        raise
    except Exception as e:
        log.error("upload_failed", uid=acting_uid, error=str(e), exc_info=True)
        raise HTTPException(500, detail=str(e))


class QueryRequest(BaseModel):
    question: str


@app.post("/query")
@require_subscription
@limiter.limit("10/minute")
async def query(request: Request, query: QueryRequest):
    try:
        acting_uid = request.headers.get("X-Acting-Uid") or request.state.uid

        if acting_uid != request.state.uid:
            claims = getattr(request.state, "firebase_claims", {})
            if claims.get("role") != "admin":
                raise HTTPException(403, "Only admin may use actingUid")

        log.info("query_received", uid=acting_uid)
        result = query_answer(query.question, acting_uid)
        return result

    except HTTPException:
        raise
    except Exception as e:
        log.error("query_failed", uid=acting_uid, error=str(e), exc_info=True)
        raise HTTPException(500, detail=str(e))

@app.options("/api/widget-query")
async def widget_query_preflight():
    return Response(status_code=204)


@app.post("/widget-query")
@widget_key_required
@limiter.limit("10/minute")
async def widget_query(request: Request):
    body = await request.json()

    question = body.get("question", "").strip()
    if not question:
        raise HTTPException(status_code=400, detail="Missing question")

    user_doc = request.state.user_doc
    uid = request.state.uid

    if not user_doc.get("subscribed", False):
        raise HTTPException(
            status_code=403,
            detail="This business's AI assistant is inactive. Please contact the business owner."
        )

    log.info("widget_query_received", uid=uid)

    result = query_answer(question, uid)
    result["subscribed"] = True
    return result

@app.get("/q/{qr_id}")
async def public_qr_redirect(qr_id: str):
    if not qr_id.startswith("qr_"):
        raise HTTPException(status_code=404, detail="Invalid QR code")

    qr_ref = db.collection("qr_inventory").document(qr_id)
    snap = qr_ref.get()

    if not snap.exists:
        raise HTTPException(status_code=404, detail="QR code not found")

    data = snap.to_dict() or {}

    if not data.get("bound") or not data.get("api_key"):
        return RedirectResponse(url=f"{FRONTEND_URL}/qr-unassigned")

    return RedirectResponse(url=f"{FRONTEND_URL}/chat?k={data['api_key']}")


@app.get("/public/branding")
async def public_get_branding(x_api_key: str = Header(None)):
    if not x_api_key:
        raise HTTPException(status_code=400, detail="Missing X-API-Key header.")

    users_ref = db.collection("users")
    query = users_ref.where("api_key", "==", x_api_key).limit(1).get()

    if not query:
        raise HTTPException(status_code=404, detail="Invalid API key.")

    user_doc = query[0]
    uid = user_doc.id

    branding_ref = (
        db.collection("users")
        .document(uid)
        .collection("config")
        .document("branding")
    )
    snap = branding_ref.get()

    if not snap.exists:
        return {
            "logoUrl": "",
            "businessName": "",
            "primaryColor": "#6366f1",
            "secondaryColor": "#7c5cf6",
        }

    return snap.to_dict()

@app.post("/regenerate-api-key")
@require_paid_subscription
async def regenerate_api_key(request: Request):
    import secrets

    uid = request.state.uid
    new_key = f"azk_{secrets.token_hex(12)}"
    db.collection("users").document(uid).update({"api_key": new_key})
    return {"api_key": new_key}


def get_price_by_lookup_key(lookup_key: str):
    prices = stripe.Price.list(
        lookup_keys=[lookup_key],
        active=True,
        limit=1,
    ).data

    if not prices:
        raise Exception(f"Stripe price not found for lookup key: {lookup_key}")

    return prices[0].id

class CheckoutRequest(BaseModel):
    uid: str


@app.post("/create-checkout-session")
async def create_checkout_session(data: CheckoutRequest):
    uid = data.uid
    user_ref = db.collection("users").document(uid)
    snap = user_ref.get()

    if not snap.exists:
        raise HTTPException(404, "User not found")

    user = snap.to_dict() or {}
    stripe_customer_id = user.get("stripe_customer_id")
    email = user.get("email")

    try:
        if not stripe_customer_id:
            customer = stripe.Customer.create(
                email=email,
                metadata={"uid": uid},
            )
            stripe_customer_id = customer.id
            user_ref.set(
                {"stripe_customer_id": stripe_customer_id},
                merge=True,
            )

        price_id = get_price_by_lookup_key("us_self_1499")
        session = stripe.checkout.Session.create(
            customer=stripe_customer_id,
            mode="subscription",
            payment_method_types=["card"],
            line_items=[
                {"price": price_id, "quantity": 1}
            ],
            success_url=f"{FRONTEND_URL}/playground?billing=success",
            cancel_url=f"{FRONTEND_URL}/playground?billing=cancel",
            metadata={
                "uid": uid,
                "source": "self_serve",
            },
        )

        return {"url": session.url}

    except Exception as e:
        log.error("checkout_failed", uid=uid, error=str(e), exc_info=True)
        raise HTTPException(500, "Failed to create checkout session")


def _user_ref_from_customer_id(customer_id: str):
    if not customer_id:
        return None, None

    q = (
        db.collection("users")
        .where("stripe_customer_id", "==", customer_id)
        .limit(1)
        .stream()
    )
    doc = next(q, None)
    if not doc:
        return None, None

    return db.collection("users").document(doc.id), doc.id


@app.post("/webhook")
async def stripe_webhook(request: Request):
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")

    try:
        event = stripe.Webhook.construct_event(
            payload=payload,
            sig_header=sig_header,
            secret=STRIPE_WEBHOOK_SECRET,
        )
    except stripe.error.SignatureVerificationError:
        raise HTTPException(400, "Invalid Stripe signature")

    etype = event["type"]
    obj = event["data"]["object"]

    if etype == "checkout.session.completed":
        metadata = obj.get("metadata") or {}
        uid = metadata.get("tenant_uid") or metadata.get("uid")

        if not uid:
            log.warning("stripe_webhook_missing_uid", metadata=metadata)
            return {"status": "ignored"}

        customer_id = obj.get("customer")
        subscription_id = obj.get("subscription")

        email = (
            (obj.get("customer_details") or {}).get("email")
            or obj.get("customer_email")
        )

        user_ref = db.collection("users").document(uid)
        snap = user_ref.get()
        if not snap.exists:
            return {"status": "ignored"}

        existing = snap.to_dict() or {}

        if (
            existing.get("stripe_customer_id")
            and existing["stripe_customer_id"] != customer_id
        ):
            log.warning("stripe_customer_mismatch", uid=uid, incoming_customer=customer_id)
            return {"status": "ignored"}

        user_ref.set(
            {
                "email": email,
                "subscribed": True,
                "widget_tier": "pro",
                "stripe_customer_id": customer_id,
                "stripe_subscription_id": subscription_id,
                "stripe_status": "active",
                "subscribed_at": gcf.SERVER_TIMESTAMP,
            },
            merge=True,
        )

        return {"status": "ok"}

    if etype in (
        "customer.subscription.created",
        "customer.subscription.updated",
        "customer.subscription.deleted",
    ):
        customer_id = obj.get("customer")
        ref, uid = _user_ref_from_customer_id(customer_id)
        if not ref:
            return {"status": "ok"}

        status = obj.get("status")
        subscribed = status in ("active", "trialing")

        ref.set(
            {
                "stripe_status": status,
                "stripe_subscription_id": obj.get("id"),
                "cancel_at_period_end": bool(obj.get("cancel_at_period_end")),
                "cancel_at": obj.get("cancel_at"),
                "current_period_end": obj.get("current_period_end"),
                "subscribed": subscribed,
            },
            merge=True,
        )

        return {"status": "ok"}

    return {"status": "ok"}


@app.post("/create-customer-portal-session")
@require_subscription
async def create_customer_portal_session(request: Request):
    try:
        uid = request.state.uid
        snap = db.collection("users").document(uid).get()
        data = snap.to_dict() or {}
        customer_id = data.get("stripe_customer_id")
        if not customer_id:
            raise HTTPException(404, "No Stripe customer found")

        session = stripe.billing_portal.Session.create(
            customer=customer_id,
            return_url=f"{FRONTEND_URL}/playground",
        )
        return {"url": session.url}

    except Exception as e:
        raise HTTPException(500, "Failed to create portal session")


@app.delete("/delete-file/{doc_id}")
@require_subscription
async def delete_file(request: Request, doc_id: str):
    uid = request.state.uid

    files_ref = db.collection("users").document(uid).collection("files")
    doc_ref = files_ref.document(doc_id)
    snap = doc_ref.get()

    if not snap.exists:
        raise HTTPException(404, "File not found")

    data = snap.to_dict() or {}
    status = data.get("status")
    filename = data.get("filename")

    if status not in ("ready", "failed"):
        raise HTTPException(
            status_code=409,
            detail="File is still processing and cannot be deleted"
        )

    delete_file_chunks(doc_id, uid)
    doc_ref.delete()

    folder = Path("images") / uid
    if folder.exists() and filename:
        for f in folder.glob(f"{filename}*"):
            f.unlink(missing_ok=True)

    return {"status": "ok"}


@app.delete("/delete-all-files")
@require_subscription
async def delete_all_files(request: Request):
    uid = request.state.uid

    files_ref = db.collection("users").document(uid).collection("files")

    deleted = 0
    skipped_processing = 0

    for snap in files_ref.stream():
        data = snap.to_dict() or {}
        status = data.get("status")
        doc_id = snap.id
        filename = data.get("filename")

        if status not in ("ready", "failed"):
            skipped_processing += 1
            continue

        delete_file_chunks(doc_id, uid)
        snap.reference.delete()

        folder = Path("images") / uid
        if folder.exists() and filename:
            for f in folder.glob(f"{filename}*"):
                f.unlink(missing_ok=True)

        deleted += 1

    return {
        "status": "ok",
        "deleted": deleted,
        "skipped_processing": skipped_processing
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)
