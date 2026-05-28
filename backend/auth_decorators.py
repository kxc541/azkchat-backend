from fastapi import Request, HTTPException
from functools import wraps
from firebase_admin_init import db
from firebase_admin import auth as fb_auth
from google.cloud import firestore
from urllib.parse import urlparse
from logger import get_logger
import secrets
import os

log = get_logger(__name__)


def _extract_request(args, kwargs) -> Request:
    if "request" in kwargs and isinstance(kwargs["request"], Request):
        return kwargs["request"]

    for a in args:
        if isinstance(a, Request):
            return a

    raise HTTPException(400, "Missing request context")


def ensure_user_doc(uid: str, email: str | None = None) -> dict:
    ref = db.collection("users").document(uid)
    snap = ref.get()

    if not snap.exists:
        data = {
            "email": email,
            "count": 0,
            "subscribed": False,
            "widget_tier": "demo",
            "allowed_domains": [],
            "branding": {},
            "disabled": False,
            "createdAt": firestore.SERVER_TIMESTAMP,
        }
        ref.set(data)
        log.info("user_created", uid=uid, email=email)
        return data

    data = snap.to_dict() or {}
    patch = {}

    # Safe metadata repairs
    if email and not data.get("email"):
        patch["email"] = email

    if "count" not in data:
        patch["count"] = int(data.get("question_count", 0))
        patch["question_count"] = firestore.DELETE_FIELD

    if "allowed_domains" not in data:
        patch["allowed_domains"] = []

    if "branding" not in data:
        patch["branding"] = {}

    if "disabled" not in data:
        patch["disabled"] = False

    if "createdAt" not in data:
        patch["createdAt"] = firestore.SERVER_TIMESTAMP

    if patch:
        ref.set(patch, merge=True)
        data.update(patch)

    return data

def ensure_api_key(uid: str) -> str:
    user_ref = db.collection("users").document(uid)
    data = user_ref.get().to_dict() or {}

    if "api_key" not in data:
        api_key = f"azk_{secrets.token_hex(12)}"
        user_ref.set({"api_key": api_key}, merge=True)
        log.info("api_key_generated", uid=uid)
        return api_key

    return data["api_key"]


def require_auth(func):
    @wraps(func)
    async def wrapper(*args, **kwargs):
        request = _extract_request(args, kwargs)

        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            raise HTTPException(401, "Missing or invalid auth token")

        token = auth_header.split(" ", 1)[1].strip()

        try:
            decoded = fb_auth.verify_id_token(token, check_revoked=True)
        except Exception as e:
            log.warning("auth_token_invalid", error=str(e))
            raise HTTPException(401, "Invalid token")

        request.state.firebase_claims = decoded

        uid = decoded.get("uid")
        email = decoded.get("email")

        data = ensure_user_doc(uid, email=email)

        request.state.uid = uid
        request.state.email = email
        request.state.subscribed = bool(data.get("subscribed", False))
        request.state.email_verified = decoded.get("email_verified", False)

        return await func(*args, **kwargs)

    return wrapper


def require_subscription(func):
    @wraps(func)
    async def wrapper(*args, **kwargs):
        request = _extract_request(args, kwargs)

        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            raise HTTPException(401, "Missing or invalid auth token")

        token = auth_header.split(" ", 1)[1].strip()

        try:
            decoded = fb_auth.verify_id_token(token, check_revoked=True)
        except Exception as e:
            log.warning("auth_token_invalid", error=str(e))
            raise HTTPException(401, "Invalid token")

        uid = decoded["uid"]
        email = decoded.get("email")

        data = ensure_user_doc(uid, email=email)

        request.state.uid = uid
        request.state.email = email
        request.state.subscribed = bool(data.get("subscribed", False))
        return await func(*args, **kwargs)

    return wrapper


def require_paid_subscription(func):
    @wraps(func)
    async def wrapper(*args, **kwargs):
        request = _extract_request(args, kwargs)

        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            raise HTTPException(401, "Missing or invalid auth token")

        token = auth_header.split(" ", 1)[1].strip()

        decoded = fb_auth.verify_id_token(token)  # skip check_revoked — fast path for paid endpoints
        uid = decoded["uid"]
        email = decoded.get("email")

        data = ensure_user_doc(uid, email=email)

        if not data.get("subscribed", False):
            raise HTTPException(403, "Subscription required")

        request.state.uid = uid
        request.state.email = email
        request.state.subscribed = True

        return await func(*args, **kwargs)

    return wrapper

def require_admin(func):
    @wraps(func)
    async def wrapper(*args, **kwargs):
        request = _extract_request(args, kwargs)

        claims = getattr(request.state, "firebase_claims", None)
        if not claims:
            raise HTTPException(401, "Authentication required")

        role = claims.get("role")
        admin_flag = claims.get("is_admin")

        if not (role == "admin" or admin_flag is True):
            log.warning("admin_access_denied", uid=getattr(request.state, "uid", None))
            raise HTTPException(403, "Admins only")

        request.state.is_admin = True

        return await func(*args, **kwargs)

    return wrapper


def _normalize_origin(o: str) -> str:
    try:
        p = urlparse(o)
        return f"{p.scheme}://{p.netloc}".rstrip("/")
    except Exception:
        return o.rstrip("/")


def _lookup_api_key_cached(key: str):
    q = db.collection("users").where("api_key", "==", key).limit(1).stream()
    d = next(q, None)
    if not d:
        return None, None
    return d.id, d.to_dict() or {}


def widget_key_required(func):
    @wraps(func)
    async def wrapper(*args, **kwargs):
        request = _extract_request(args, kwargs)

        api_key = (
            request.headers.get("X-API-Key")
            or request.query_params.get("api_key")
        )
        if not api_key:
            raise HTTPException(401, "Missing API key")

        origin = request.headers.get("Origin")
        if not origin:
            # PublicChat / QRChat / local dev
            normalized_origin = "http://localhost:3000"
        else:
            normalized_origin = _normalize_origin(origin)

        uid, user_doc = _lookup_api_key_cached(api_key)
        if not uid:
            raise HTTPException(401, "Invalid API key")

        if user_doc.get("disabled"):
            raise HTTPException(403, "API key disabled")

        allowed_domains = {
            _normalize_origin(d)
            for d in user_doc.get("allowed_domains", [])
        }

        if allowed_domains and normalized_origin not in allowed_domains:
            log.warning("widget_domain_rejected", origin=normalized_origin, uid=uid)
            raise HTTPException(
                403,
                f"Unauthorized domain: {normalized_origin}"
            )

        request.state.uid = uid
        request.state.user_doc = user_doc
        request.state.api_key = api_key

        return await func(*args, **kwargs)

    return wrapper
