"""
PCO OAuth blueprint.
Routes: /auth/pco  /auth/callback  /auth/logout
"""

import os
import secrets

import requests
from flask import Blueprint, redirect, request, session, url_for, jsonify

from .db import Db

auth_bp = Blueprint("auth", __name__)

PCO_AUTH_URL = "https://api.planningcenteronline.com/oauth/authorize"
PCO_TOKEN_URL = "https://api.planningcenteronline.com/oauth/token"
PCO_ME_URL = "https://api.planningcenteronline.com/people/v2/me"


def _client_id():
    return os.environ["PCO_CLIENT_ID"]


def _client_secret():
    return os.environ["PCO_CLIENT_SECRET"]


def _redirect_uri():
    return os.environ["PCO_REDIRECT_URI"]


@auth_bp.get("/auth/pco")
def login():
    state = secrets.token_urlsafe(16)
    session["oauth_state"] = state
    params = {
        "client_id": _client_id(),
        "redirect_uri": _redirect_uri(),
        "response_type": "code",
        "scope": "people services",
        "state": state,
    }
    from urllib.parse import urlencode
    return redirect(f"{PCO_AUTH_URL}?{urlencode(params)}")


@auth_bp.get("/auth/callback")
def callback():
    error = request.args.get("error")
    if error:
        return f"OAuth error: {error}", 400

    code = request.args.get("code")
    state = request.args.get("state")

    if not code:
        return "Missing authorization code", 400

    # Validate state to prevent CSRF
    expected_state = session.pop("oauth_state", None)
    if not expected_state or state != expected_state:
        return "Invalid state parameter", 400

    # Exchange code for tokens
    token_resp = requests.post(
        PCO_TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "client_id": _client_id(),
            "client_secret": _client_secret(),
            "redirect_uri": _redirect_uri(),
        },
        timeout=10,
    )
    if not token_resp.ok:
        return f"Token exchange failed: {token_resp.text}", 500

    token_data = token_resp.json()
    access_token = token_data["access_token"]
    refresh_token = token_data.get("refresh_token", "")
    expires_in = token_data.get("expires_in")

    # Fetch user identity
    me_resp = requests.get(
        PCO_ME_URL,
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=10,
    )
    if not me_resp.ok:
        return f"Failed to fetch user identity: {me_resp.text}", 500

    me_data = me_resp.json()
    attrs = me_data["data"]["attributes"]
    pco_person_id = me_data["data"]["id"]
    pco_org_id = me_data.get("meta", {}).get("org_id", "")
    name = f"{attrs.get('first_name', '')} {attrs.get('last_name', '')}".strip()
    email = attrs.get("primary_email", "") or ""

    from datetime import datetime, timezone, timedelta
    expires_at = None
    if expires_in:
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=int(expires_in))

    # Upsert user
    with Db() as cur:
        cur.execute(
            """
            INSERT INTO users
              (pco_person_id, pco_org_id, name, email, access_token, refresh_token, token_expires_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (pco_person_id) DO UPDATE SET
              pco_org_id       = EXCLUDED.pco_org_id,
              name             = EXCLUDED.name,
              email            = EXCLUDED.email,
              access_token     = EXCLUDED.access_token,
              refresh_token    = EXCLUDED.refresh_token,
              token_expires_at = EXCLUDED.token_expires_at
            RETURNING id, name, email
            """,
            (pco_person_id, pco_org_id, name, email, access_token, refresh_token, expires_at),
        )
        user = cur.fetchone()

    session["user_id"] = user["id"]
    session["user_name"] = user["name"]
    session["user_email"] = user["email"]

    return redirect(url_for("index"))


@auth_bp.post("/auth/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))


def current_user_id():
    """Return the logged-in user's DB id, or None."""
    return session.get("user_id")


def login_required(f):
    """Decorator: return 401 JSON if not logged in (for API routes)."""
    from functools import wraps

    @wraps(f)
    def wrapper(*args, **kwargs):
        if not current_user_id():
            return jsonify({"error": "Not authenticated"}), 401
        return f(*args, **kwargs)

    return wrapper
