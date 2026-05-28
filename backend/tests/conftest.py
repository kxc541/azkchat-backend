import sys
from unittest.mock import MagicMock

# Firebase — initializes on import, must be first
sys.modules["firebase_admin_init"] = MagicMock()
sys.modules["firebase_admin"] = MagicMock()
sys.modules["firebase_admin.auth"] = MagicMock()
sys.modules["firebase_admin.credentials"] = MagicMock()
sys.modules["firebase_admin.firestore"] = MagicMock()

# Google Cloud — imported by auth_decorators
sys.modules["google"] = MagicMock()
sys.modules["google.cloud"] = MagicMock()
sys.modules["google.cloud.firestore"] = MagicMock()
sys.modules["google.cloud.firestore_v1"] = MagicMock()
sys.modules["google.cloud.firestore_v1.base_query"] = MagicMock()

# Weaviate — connects on import in weaviate_utils and query_utils
sys.modules["weaviate"] = MagicMock()

# Redis / RQ
sys.modules["redis"] = MagicMock()
sys.modules["rq"] = MagicMock()

# boto3 — creates S3 client on import in aws_utils
sys.modules["boto3"] = MagicMock()

# Stripe — imported in app.py / admin_routes
sys.modules["stripe"] = MagicMock()

# slowapi — imported in app.py
sys.modules["slowapi"] = MagicMock()
sys.modules["slowapi.util"] = MagicMock()
sys.modules["slowapi.errors"] = MagicMock()
