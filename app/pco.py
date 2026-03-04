"""
Planning Center Online API client with automatic token refresh.
"""

import os
from datetime import datetime, timezone, timedelta

import requests
from flask import Blueprint, jsonify, request

from .auth import login_required, current_user_id
from .db import Db

pco_bp = Blueprint("pco", __name__)

PCO_API = "https://api.planningcenteronline.com"
PCO_TOKEN_URL = "https://api.planningcenteronline.com/oauth/token"


# ---------------------------------------------------------------------------
# Token management
# ---------------------------------------------------------------------------

def _get_user_tokens(uid):
    with Db() as cur:
        cur.execute(
            "SELECT access_token, refresh_token, token_expires_at FROM users WHERE id=%s",
            (uid,),
        )
        return cur.fetchone()


def _refresh_token_if_needed(uid):
    """Return a valid access token, refreshing if necessary."""
    row = _get_user_tokens(uid)
    if not row:
        raise RuntimeError("User not found")

    access_token = row["access_token"]
    expires_at = row["token_expires_at"]

    # Refresh if token expires within 5 minutes
    needs_refresh = (
        expires_at is None or
        expires_at <= datetime.now(timezone.utc) + timedelta(minutes=5)
    )

    if needs_refresh and row["refresh_token"]:
        resp = requests.post(
            PCO_TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "refresh_token": row["refresh_token"],
                "client_id": os.environ["PCO_CLIENT_ID"],
                "client_secret": os.environ["PCO_CLIENT_SECRET"],
            },
            timeout=10,
        )
        if resp.ok:
            data = resp.json()
            access_token = data["access_token"]
            new_refresh = data.get("refresh_token", row["refresh_token"])
            new_expires = None
            if data.get("expires_in"):
                new_expires = datetime.now(timezone.utc) + timedelta(seconds=int(data["expires_in"]))

            with Db() as cur:
                cur.execute(
                    "UPDATE users SET access_token=%s, refresh_token=%s, token_expires_at=%s WHERE id=%s",
                    (access_token, new_refresh, new_expires, uid),
                )

    return access_token


def _pco_get(uid, path, params=None):
    token = _refresh_token_if_needed(uid)
    resp = requests.get(
        f"{PCO_API}{path}",
        headers={"Authorization": f"Bearer {token}"},
        params=params or {},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# API routes
# ---------------------------------------------------------------------------

@pco_bp.get("/api/pco/plans")
@login_required
def list_plans():
    """List upcoming service plans across all service types."""
    uid = current_user_id()
    try:
        service_types_data = _pco_get(uid, "/services/v2/service_types")
    except requests.HTTPError as e:
        return jsonify({"error": f"PCO API error: {e.response.status_code}"}), 502

    plans = []
    for st in service_types_data.get("data", []):
        st_id = st["id"]
        st_name = st["attributes"].get("name", "")
        try:
            plans_data = _pco_get(
                uid,
                f"/services/v2/service_types/{st_id}/plans",
                params={"filter": "future", "per_page": 25, "order": "sort_date"},
            )
        except requests.HTTPError:
            continue

        for plan in plans_data.get("data", []):
            attrs = plan["attributes"]
            plans.append({
                "id": plan["id"],
                "serviceTypeId": st_id,
                "serviceTypeName": st_name,
                "title": attrs.get("title") or attrs.get("series_title") or "",
                "date": attrs.get("sort_date", "")[:10] if attrs.get("sort_date") else "",
                "itemCount": attrs.get("items_count", 0),
            })

    plans.sort(key=lambda p: p["date"])
    return jsonify(plans)


@pco_bp.get("/api/pco/plans/<pco_plan_id>")
@login_required
def get_plan(pco_plan_id):
    """Get plan details including songs."""
    uid = current_user_id()

    # Find the service type for this plan
    service_type_id = request.args.get("serviceTypeId")
    if not service_type_id:
        return jsonify({"error": "serviceTypeId query param required"}), 400

    try:
        items_data = _pco_get(
            uid,
            f"/services/v2/service_types/{service_type_id}/plans/{pco_plan_id}/items",
            params={"include": "song,arrangement", "per_page": 50},
        )
    except requests.HTTPError as e:
        return jsonify({"error": f"PCO API error: {e.response.status_code}"}), 502

    songs = []
    included = items_data.get("included", [])
    included_songs = {i["id"]: i for i in included if i["type"] == "Song"}
    included_arrs = {i["id"]: i for i in included if i["type"] == "Arrangement"}

    for item in items_data.get("data", []):
        if item["attributes"].get("item_type") != "song":
            continue
        rels = item.get("relationships", {})
        song_id = rels.get("song", {}).get("data", {}).get("id")
        arr_id = rels.get("arrangement", {}).get("data", {}).get("id")

        song_attrs = included_songs.get(song_id, {}).get("attributes", {})
        arr_attrs = included_arrs.get(arr_id, {}).get("attributes", {})

        songs.append({
            "pcoSongId": song_id,
            "pcoArrangementId": arr_id,
            "title": song_attrs.get("title", ""),
            "author": song_attrs.get("author", ""),
            "key": arr_attrs.get("chord_chart_key", "") or arr_attrs.get("key", ""),
            "bpm": str(arr_attrs.get("bpm") or ""),
            "arrangementName": arr_attrs.get("name", "Main"),
        })

    return jsonify({"songs": songs})


@pco_bp.post("/api/pco/import/<pco_plan_id>")
@login_required
def import_plan(pco_plan_id):
    """
    Import a PCO plan as a Preppy setlist.
    Auto-creates missing songs/arrangements in the library.
    """
    uid = current_user_id()
    body = request.get_json(silent=True) or {}
    service_type_id = body.get("serviceTypeId")
    plan_date = body.get("date", "")
    plan_title = body.get("title", "")

    if not service_type_id:
        return jsonify({"error": "serviceTypeId is required"}), 400

    try:
        items_data = _pco_get(
            uid,
            f"/services/v2/service_types/{service_type_id}/plans/{pco_plan_id}/items",
            params={"include": "song,arrangement", "per_page": 50},
        )
    except requests.HTTPError as e:
        return jsonify({"error": f"PCO API error: {e.response.status_code}"}), 502

    included = items_data.get("included", [])
    included_songs = {i["id"]: i for i in included if i["type"] == "Song"}
    included_arrs = {i["id"]: i for i in included if i["type"] == "Arrangement"}

    setlist_item_arr_ids = []

    with Db() as cur:
        for item in items_data.get("data", []):
            if item["attributes"].get("item_type") != "song":
                continue
            rels = item.get("relationships", {})
            pco_song_id = rels.get("song", {}).get("data", {}).get("id")
            pco_arr_id = rels.get("arrangement", {}).get("data", {}).get("id")

            if not pco_song_id:
                continue

            song_attrs = included_songs.get(pco_song_id, {}).get("attributes", {})
            arr_attrs = included_arrs.get(pco_arr_id, {}).get("attributes", {}) if pco_arr_id else {}

            title = song_attrs.get("title", "").strip() or "Untitled"
            artist = song_attrs.get("author", "").strip()
            arr_name = arr_attrs.get("name", "Main").strip() or "Main"
            key = arr_attrs.get("chord_chart_key", "") or arr_attrs.get("key", "") or ""
            bpm = str(arr_attrs.get("bpm") or "")

            # Check if arrangement already imported from PCO
            if pco_arr_id:
                cur.execute(
                    "SELECT a.id FROM arrangements a JOIN songs s ON s.id=a.song_id "
                    "WHERE a.pco_arrangement_id=%s AND s.user_id=%s",
                    (pco_arr_id, uid),
                )
                existing = cur.fetchone()
                if existing:
                    setlist_item_arr_ids.append(existing["id"])
                    continue

            # Check by pco_song_id
            cur.execute(
                "SELECT id FROM songs WHERE pco_song_id=%s AND user_id=%s",
                (pco_song_id, uid),
            )
            existing_song = cur.fetchone()

            if existing_song:
                song_id = existing_song["id"]
            else:
                cur.execute(
                    "INSERT INTO songs (user_id, pco_song_id, title, artist) "
                    "VALUES (%s, %s, %s, %s) RETURNING id",
                    (uid, pco_song_id, title, artist),
                )
                song_id = cur.fetchone()["id"]

            cur.execute(
                "INSERT INTO arrangements (song_id, pco_arrangement_id, name, key, bpm) "
                "VALUES (%s, %s, %s, %s, %s) RETURNING id",
                (song_id, pco_arr_id, arr_name, key, bpm),
            )
            arr_id = cur.fetchone()["id"]
            setlist_item_arr_ids.append(arr_id)

        # Create setlist
        setlist_name = plan_title or (f"Service {plan_date}" if plan_date else "Imported Plan")
        cur.execute(
            "INSERT INTO setlists (user_id, pco_plan_id, name, date) "
            "VALUES (%s, %s, %s, %s) RETURNING id",
            (uid, pco_plan_id, setlist_name, plan_date or None),
        )
        sl_id = cur.fetchone()["id"]

        for pos, arr_id in enumerate(setlist_item_arr_ids):
            cur.execute(
                "INSERT INTO setlist_items (setlist_id, arrangement_id, position) VALUES (%s, %s, %s)",
                (sl_id, arr_id, pos),
            )

    return jsonify({"setlistId": sl_id}), 201
