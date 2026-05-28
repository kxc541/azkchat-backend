import pytest
from unittest.mock import MagicMock, patch
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

import auth_decorators


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_decoded_token(uid="user1", email="user@example.com", role=None, is_admin=False):
    claims = {"uid": uid, "email": email, "email_verified": True}
    if role:
        claims["role"] = role
    if is_admin:
        claims["is_admin"] = True
    return claims


def make_user_doc(subscribed=True, disabled=False):
    return {
        "email": "user@example.com",
        "subscribed": subscribed,
        "disabled": disabled,
        "widget_tier": "pro",
        "allowed_domains": [],
    }


# ---------------------------------------------------------------------------
# ensure_user_doc
# ---------------------------------------------------------------------------

class TestEnsureUserDoc:
    def _mock_ref(self, exists=True, data=None):
        snap = MagicMock()
        snap.exists = exists
        snap.to_dict.return_value = data or make_user_doc()
        ref = MagicMock()
        ref.get.return_value = snap
        return ref

    def test_creates_new_user_when_not_exists(self):
        ref = self._mock_ref(exists=False)
        auth_decorators.db.collection.return_value.document.return_value = ref

        result = auth_decorators.ensure_user_doc("newuid", email="new@example.com")

        ref.set.assert_called_once()
        assert result["subscribed"] is False

    def test_returns_existing_user_data(self):
        existing_data = make_user_doc(subscribed=True)
        ref = self._mock_ref(exists=True, data=existing_data)
        auth_decorators.db.collection.return_value.document.return_value = ref

        result = auth_decorators.ensure_user_doc("uid1")

        assert result["subscribed"] is True


# ---------------------------------------------------------------------------
# ensure_api_key
# ---------------------------------------------------------------------------

class TestEnsureApiKey:
    def test_returns_existing_key(self):
        snap = MagicMock()
        snap.to_dict.return_value = {"api_key": "azk_existingkey"}
        ref = MagicMock()
        ref.get.return_value = snap
        auth_decorators.db.collection.return_value.document.return_value = ref

        key = auth_decorators.ensure_api_key("user1")
        assert key == "azk_existingkey"

    def test_generates_key_when_missing(self):
        snap = MagicMock()
        snap.to_dict.return_value = {}
        ref = MagicMock()
        ref.get.return_value = snap
        auth_decorators.db.collection.return_value.document.return_value = ref

        key = auth_decorators.ensure_api_key("user1")
        assert key.startswith("azk_")
        ref.set.assert_called_once()


# ---------------------------------------------------------------------------
# require_auth decorator
# ---------------------------------------------------------------------------

def make_auth_app():
    app = FastAPI()

    @app.get("/protected")
    @auth_decorators.require_auth
    async def protected(request: Request):
        return {"uid": request.state.uid}

    return TestClient(app)


class TestRequireAuth:
    def test_missing_bearer_returns_401(self):
        client = make_auth_app()
        response = client.get("/protected")
        assert response.status_code == 401

    def test_invalid_token_returns_401(self):
        with patch("auth_decorators.fb_auth.verify_id_token", side_effect=Exception("invalid")):
            client = make_auth_app()
            response = client.get("/protected", headers={"Authorization": "Bearer badtoken"})
        assert response.status_code == 401

    def test_valid_token_passes(self):
        decoded = make_decoded_token()
        user_data = make_user_doc()

        with patch("auth_decorators.fb_auth.verify_id_token", return_value=decoded), \
             patch("auth_decorators.ensure_user_doc", return_value=user_data):
            client = make_auth_app()
            response = client.get("/protected", headers={"Authorization": "Bearer validtoken"})

        assert response.status_code == 200
        assert response.json()["uid"] == "user1"


# ---------------------------------------------------------------------------
# widget_key_required decorator
# ---------------------------------------------------------------------------

def make_widget_app():
    app = FastAPI()

    @app.get("/widget")
    @auth_decorators.widget_key_required
    async def widget_route(request: Request):
        return {"uid": request.state.uid}

    return app


class TestWidgetKeyRequired:
    def test_missing_api_key_returns_401(self):
        client = TestClient(make_widget_app())
        response = client.get("/widget")
        assert response.status_code == 401

    def test_invalid_api_key_returns_401(self):
        with patch("auth_decorators._lookup_api_key_cached", return_value=(None, None)):
            client = TestClient(make_widget_app())
            response = client.get("/widget", headers={"X-API-Key": "bad_key"})
        assert response.status_code == 401

    def test_disabled_user_returns_403(self):
        user_doc = make_user_doc(disabled=True)
        with patch("auth_decorators._lookup_api_key_cached", return_value=("user1", user_doc)):
            client = TestClient(make_widget_app())
            response = client.get("/widget", headers={"X-API-Key": "azk_valid"})
        assert response.status_code == 403

    def test_domain_not_in_allowlist_returns_403(self):
        user_doc = {**make_user_doc(), "allowed_domains": ["https://allowed.com"]}
        with patch("auth_decorators._lookup_api_key_cached", return_value=("user1", user_doc)):
            client = TestClient(make_widget_app())
            response = client.get(
                "/widget",
                headers={"X-API-Key": "azk_valid", "Origin": "https://notallowed.com"},
            )
        assert response.status_code == 403

    def test_valid_key_with_no_domain_restriction_passes(self):
        user_doc = make_user_doc()
        with patch("auth_decorators._lookup_api_key_cached", return_value=("user1", user_doc)):
            client = TestClient(make_widget_app())
            response = client.get("/widget", headers={"X-API-Key": "azk_valid"})
        assert response.status_code == 200

    def test_valid_key_with_matching_domain_passes(self):
        user_doc = {**make_user_doc(), "allowed_domains": ["https://mysite.com"]}
        with patch("auth_decorators._lookup_api_key_cached", return_value=("user1", user_doc)):
            client = TestClient(make_widget_app())
            response = client.get(
                "/widget",
                headers={"X-API-Key": "azk_valid", "Origin": "https://mysite.com"},
            )
        assert response.status_code == 200
