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


PCO_UPLOAD_URL = "https://upload.planningcenteronline.com/v2/files"


def _pco_upload_file(uid, filename, file_bytes, content_type):
    """
    Two-step PCO file upload:
    1. POST file to upload.planningcenteronline.com → get UUID
    2. Return the UUID for use with file_upload_identifier
    """
    token = _refresh_token_if_needed(uid)
    resp = requests.post(
        PCO_UPLOAD_URL,
        headers={"Authorization": f"Bearer {token}"},
        files={"file": (filename, file_bytes, content_type)},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json().get("data", {})
    upload_id = data.get("id") or data.get("attributes", {}).get("id")
    if not upload_id:
        raise RuntimeError("PCO file upload did not return an identifier")
    return upload_id


def _pco_create_attachment(uid, path, upload_id, filename):
    """Create an attachment record on a PCO resource using a file_upload_identifier."""
    token = _refresh_token_if_needed(uid)
    payload = {
        "data": {
            "type": "Attachment",
            "attributes": {
                "file_upload_identifier": upload_id,
                "filename": filename,
            },
        }
    }
    resp = requests.post(
        f"{PCO_API}{path}",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# Shared upsert helper
# ---------------------------------------------------------------------------

def _upsert_pco_song(cur, uid, pco_song_id, title, artist, pco_arr_id, arr_name, key, bpm):
    """
    Upsert a PCO song + arrangement into the Preppy DB.
    Returns the arrangement DB id.  Idempotent — safe to call repeatedly.
    """
    # Check if arrangement already imported
    if pco_arr_id:
        cur.execute(
            "SELECT a.id FROM arrangements a JOIN songs s ON s.id=a.song_id "
            "WHERE a.pco_arrangement_id=%s AND s.user_id=%s",
            (pco_arr_id, uid),
        )
        existing = cur.fetchone()
        if existing:
            return existing["id"]

    # Find or create song
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

    # Create arrangement
    cur.execute(
        "INSERT INTO arrangements (song_id, pco_arrangement_id, name, key, bpm) "
        "VALUES (%s, %s, %s, %s, %s) RETURNING id",
        (song_id, pco_arr_id, arr_name, key, bpm),
    )
    new_arr_id = cur.fetchone()["id"]

    # Copy sections from a previous import of the same PCO arrangement (if any).
    # This means "arrange once, auto-populate on future imports".
    # Picks the most-recently-updated donor arrangement first, preferring
    # the same user's data over other users'.
    if pco_arr_id:
        cur.execute(
            "SELECT a.id FROM arrangements a "
            "JOIN songs so ON so.id = a.song_id "
            "JOIN sections sec ON sec.arrangement_id = a.id "
            "WHERE a.pco_arrangement_id = %s AND a.id != %s "
            "ORDER BY (so.user_id = %s) DESC, a.updated_at DESC "
            "LIMIT 1",
            (pco_arr_id, new_arr_id, uid),
        )
        donor = cur.fetchone()
        if donor:
            cur.execute(
                "SELECT position, label, energy, notes "
                "FROM sections WHERE arrangement_id = %s ORDER BY position",
                (donor["id"],),
            )
            for sec in cur.fetchall():
                cur.execute(
                    "INSERT INTO sections (arrangement_id, position, label, energy, notes) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    (new_arr_id, sec["position"], sec["label"],
                     sec["energy"], sec["notes"]),
                )

    return new_arr_id


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

    # Build ordered list of setlist items (songs + headers)
    setlist_items = []  # list of dicts: {type, arr_id?, label?}

    with Db() as cur:
        for item in items_data.get("data", []):
            item_type = item["attributes"].get("item_type", "")

            if item_type == "song":
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

                arr_db_id = _upsert_pco_song(cur, uid, pco_song_id, title, artist, pco_arr_id, arr_name, key, bpm)

                # Try to fetch arrangement sequence and pre-populate sections
                if pco_arr_id:
                    try:
                        arr_detail = _pco_get(
                            uid,
                            f"/services/v2/songs/{pco_song_id}/arrangements/{pco_arr_id}",
                        )
                        sequence = arr_detail.get("data", {}).get("attributes", {}).get("sequence", [])
                        if sequence:
                            # Only populate if arrangement has no sections yet
                            cur.execute(
                                "SELECT count(*) as cnt FROM sections WHERE arrangement_id=%s",
                                (arr_db_id,),
                            )
                            if cur.fetchone()["cnt"] == 0:
                                for pos, label in enumerate(sequence):
                                    cur.execute(
                                        "INSERT INTO sections (arrangement_id, position, label, energy, notes) "
                                        "VALUES (%s, %s, %s, %s, %s)",
                                        (arr_db_id, pos, str(label), "", ""),
                                    )
                    except requests.HTTPError:
                        pass  # Non-critical: skip section pre-population

                setlist_items.append({"type": "song", "arr_id": arr_db_id})

            elif item_type in ("header", "item"):
                label = item["attributes"].get("title", "").strip()
                if label:
                    setlist_items.append({"type": "header", "label": label})

        # Create setlist
        setlist_name = plan_title or (f"Service {plan_date}" if plan_date else "Imported Plan")
        cur.execute(
            "INSERT INTO setlists (user_id, pco_plan_id, pco_service_type_id, name, date) "
            "VALUES (%s, %s, %s, %s, %s) RETURNING id",
            (uid, pco_plan_id, service_type_id, setlist_name, plan_date or None),
        )
        sl_id = cur.fetchone()["id"]

        for pos, sl_item in enumerate(setlist_items):
            if sl_item["type"] == "song":
                cur.execute(
                    "INSERT INTO setlist_items (setlist_id, arrangement_id, position, item_type) "
                    "VALUES (%s, %s, %s, 'song')",
                    (sl_id, sl_item["arr_id"], pos),
                )
            else:
                cur.execute(
                    "INSERT INTO setlist_items (setlist_id, position, item_type, label) "
                    "VALUES (%s, %s, 'header', %s)",
                    (sl_id, pos, sl_item["label"]),
                )

    return jsonify({"setlistId": sl_id}), 201


# ---------------------------------------------------------------------------
# Phase 4 — PCO Song Library Search
# ---------------------------------------------------------------------------

@pco_bp.get("/api/pco/songs")
@login_required
def search_pco_songs():
    """Search the PCO song library."""
    uid = current_user_id()
    query = request.args.get("q", "").strip()
    if not query:
        return jsonify([])

    try:
        data = _pco_get(
            uid,
            "/services/v2/songs",
            params={"filter": "search", "query": query, "per_page": 25},
        )
    except requests.HTTPError as e:
        return jsonify({"error": f"PCO API error: {e.response.status_code}"}), 502

    results = []
    for song in data.get("data", []):
        attrs = song["attributes"]
        results.append({
            "pcoSongId": song["id"],
            "title": attrs.get("title", ""),
            "author": attrs.get("author", ""),
            "ccliNumber": attrs.get("ccli_number") or "",
            "arrangementCount": attrs.get("arrangement_count", 0),
        })

    return jsonify(results)


@pco_bp.get("/api/pco/songs/<pco_song_id>/arrangements")
@login_required
def list_pco_song_arrangements(pco_song_id):
    """List arrangements for a PCO song."""
    uid = current_user_id()
    try:
        data = _pco_get(uid, f"/services/v2/songs/{pco_song_id}/arrangements")
    except requests.HTTPError as e:
        return jsonify({"error": f"PCO API error: {e.response.status_code}"}), 502

    results = []
    for arr in data.get("data", []):
        attrs = arr["attributes"]
        results.append({
            "pcoArrangementId": arr["id"],
            "name": attrs.get("name", "Main"),
            "key": attrs.get("chord_chart_key", "") or attrs.get("key", "") or "",
            "bpm": str(attrs.get("bpm") or ""),
            "hasChordChart": bool(attrs.get("chord_chart")),
        })

    return jsonify(results)


@pco_bp.post("/api/pco/songs/<pco_song_id>/import")
@login_required
def import_pco_song(pco_song_id):
    """Import a PCO song (and optionally a specific arrangement) into Preppy."""
    uid = current_user_id()
    body = request.get_json(silent=True) or {}
    target_arr_id = body.get("pcoArrangementId")

    # Fetch song metadata
    try:
        song_data = _pco_get(uid, f"/services/v2/songs/{pco_song_id}")
    except requests.HTTPError as e:
        return jsonify({"error": f"PCO API error: {e.response.status_code}"}), 502

    song_attrs = song_data.get("data", {}).get("attributes", {})
    title = song_attrs.get("title", "").strip() or "Untitled"
    artist = song_attrs.get("author", "").strip()

    # Fetch arrangements
    try:
        arr_data = _pco_get(uid, f"/services/v2/songs/{pco_song_id}/arrangements")
    except requests.HTTPError as e:
        return jsonify({"error": f"PCO API error: {e.response.status_code}"}), 502

    arrangements = arr_data.get("data", [])
    if target_arr_id:
        arrangements = [a for a in arrangements if a["id"] == target_arr_id]

    if not arrangements:
        return jsonify({"error": "No arrangements found"}), 404

    arrangement_ids = []
    song_id = None

    with Db() as cur:
        for arr in arrangements:
            arr_attrs = arr["attributes"]
            arr_db_id = _upsert_pco_song(
                cur, uid, pco_song_id, title, artist,
                arr["id"],
                arr_attrs.get("name", "Main").strip() or "Main",
                arr_attrs.get("chord_chart_key", "") or arr_attrs.get("key", "") or "",
                str(arr_attrs.get("bpm") or ""),
            )
            arrangement_ids.append(arr_db_id)

        # Get the song DB id
        cur.execute(
            "SELECT id FROM songs WHERE pco_song_id=%s AND user_id=%s",
            (pco_song_id, uid),
        )
        row = cur.fetchone()
        if row:
            song_id = row["id"]

    return jsonify({"songId": song_id, "arrangementIds": arrangement_ids}), 201


# ---------------------------------------------------------------------------
# Phase 6 — Upload prep sheet to PCO plan
# ---------------------------------------------------------------------------

@pco_bp.post("/api/pco/plans/<pco_plan_id>/upload-prep-sheet")
@login_required
def upload_prep_sheet(pco_plan_id):
    """Upload a .docx prep sheet as an attachment to a PCO plan (two-step)."""
    uid = current_user_id()
    file = request.files.get("file")
    service_type_id = request.form.get("serviceTypeId")
    filename = request.form.get("filename", "Prep Sheet.docx")

    if not file:
        return jsonify({"error": "No file provided"}), 400
    if not service_type_id:
        return jsonify({"error": "serviceTypeId is required"}), 400

    file_bytes = file.read()
    content_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

    try:
        # Step 1: Upload file to PCO's upload service → get UUID
        upload_id = _pco_upload_file(uid, filename, file_bytes, content_type)

        # Step 2: Create attachment record on the plan using the UUID
        result = _pco_create_attachment(
            uid,
            f"/services/v2/service_types/{service_type_id}/plans/{pco_plan_id}/attachments",
            upload_id,
            filename,
        )
    except requests.HTTPError as e:
        body = ""
        try:
            body = e.response.text[:200]
        except Exception:
            pass
        return jsonify({"error": f"PCO upload failed ({e.response.status_code}): {body}"}), 502
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 502

    attachment = result.get("data", {})
    return jsonify({
        "attachmentId": attachment.get("id"),
        "url": attachment.get("attributes", {}).get("url", ""),
    })


# ---------------------------------------------------------------------------
# Phase 7 — Sync setlist with PCO plan
# ---------------------------------------------------------------------------

@pco_bp.post("/api/pco/plans/<pco_plan_id>/sync")
@login_required
def sync_plan(pco_plan_id):
    """Re-sync a Preppy setlist with the current state of a PCO plan."""
    uid = current_user_id()
    body = request.get_json(silent=True) or {}
    service_type_id = body.get("serviceTypeId")
    setlist_id = body.get("setlistId")

    if not service_type_id or not setlist_id:
        return jsonify({"error": "serviceTypeId and setlistId are required"}), 400

    # Verify setlist belongs to user
    with Db() as cur:
        cur.execute(
            "SELECT id FROM setlists WHERE id=%s AND user_id=%s AND pco_plan_id=%s",
            (setlist_id, uid, pco_plan_id),
        )
        if not cur.fetchone():
            return jsonify({"error": "Setlist not found"}), 404

    # Fetch current plan items
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

    new_items = []
    added = 0

    with Db() as cur:
        # Collect existing arrangement IDs in this setlist (to detect additions)
        cur.execute(
            "SELECT arrangement_id FROM setlist_items WHERE setlist_id=%s AND item_type='song'",
            (setlist_id,),
        )
        old_arr_ids = {row["arrangement_id"] for row in cur.fetchall()}

        for item in items_data.get("data", []):
            item_type = item["attributes"].get("item_type", "")

            if item_type == "song":
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

                arr_db_id = _upsert_pco_song(cur, uid, pco_song_id, title, artist, pco_arr_id, arr_name, key, bpm)

                # Update key/bpm on existing arrangements (but NOT sections/notes)
                cur.execute(
                    "UPDATE arrangements SET key=%s, bpm=%s, updated_at=now() WHERE id=%s",
                    (key, bpm, arr_db_id),
                )

                if arr_db_id not in old_arr_ids:
                    added += 1

                new_items.append({"type": "song", "arr_id": arr_db_id})

            elif item_type in ("header", "item"):
                label = item["attributes"].get("title", "").strip()
                if label:
                    new_items.append({"type": "header", "label": label})

        # Count removals
        new_arr_ids = {it["arr_id"] for it in new_items if it["type"] == "song"}
        removed = len(old_arr_ids - new_arr_ids)

        # Replace setlist items
        cur.execute("DELETE FROM setlist_items WHERE setlist_id=%s", (setlist_id,))
        for pos, sl_item in enumerate(new_items):
            if sl_item["type"] == "song":
                cur.execute(
                    "INSERT INTO setlist_items (setlist_id, arrangement_id, position, item_type) "
                    "VALUES (%s, %s, %s, 'song')",
                    (setlist_id, sl_item["arr_id"], pos),
                )
            else:
                cur.execute(
                    "INSERT INTO setlist_items (setlist_id, position, item_type, label) "
                    "VALUES (%s, %s, 'header', %s)",
                    (setlist_id, pos, sl_item["label"]),
                )

        cur.execute("UPDATE setlists SET updated_at=now() WHERE id=%s", (setlist_id,))

    return jsonify({
        "setlistId": setlist_id,
        "changes": {"added": added, "removed": removed, "reordered": True},
    })
