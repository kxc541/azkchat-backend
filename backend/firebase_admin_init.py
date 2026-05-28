import os
import json
import base64
import firebase_admin
from firebase_admin import credentials, firestore
from logger import get_logger

log = get_logger(__name__)

if not firebase_admin._apps:
    firebase_json_b64 = os.getenv("FIREBASE_ADMIN_BASE64")

    if firebase_json_b64:
        try:
            cred_dict = json.loads(base64.b64decode(firebase_json_b64))
            cred = credentials.Certificate(cred_dict)
            log.info("firebase_credentials_loaded", source="env_base64")
        except Exception as e:
            raise RuntimeError(f"Failed to decode FIREBASE_ADMIN_BASE64: {e}")
    else:
        # Local fallback for dev
        cred_path = "firebase_admin_sdk.json"
        if not os.path.exists(cred_path):
            raise FileNotFoundError(
                f"Firebase key not found at {cred_path} and FIREBASE_ADMIN_BASE64 not set."
            )
        cred = credentials.Certificate(cred_path)
        log.info("firebase_credentials_loaded", source="local_file")

    firebase_admin.initialize_app(
        cred,
        {
            "storageBucket": os.getenv("FIREBASE_STORAGE_BUCKET")
        }
    )


db = firestore.client()
