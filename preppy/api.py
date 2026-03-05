"""
CRUD API blueprint — all routes require login, all scoped to current_user_id().
"""

from flask import Blueprint, jsonify, request

from .auth import login_required, current_user_id
from .db import Db

api_bp = Blueprint("api", __name__)


# ---------------------------------------------------------------------------
# Songs + arrangements + sections
# ---------------------------------------------------------------------------

@api_bp.get("/api/songs")
@login_required
def list_songs():
    uid = current_user_id()
    with Db() as cur:
        # Songs
        cur.execute(
            "SELECT id, title, artist, pco_song_id FROM songs WHERE user_id = %s ORDER BY title",
            (uid,),
        )
        songs = [dict(r) for r in cur.fetchall()]

        if not songs:
            return jsonify([])

        song_ids = [s["id"] for s in songs]

        # Arrangements
        cur.execute(
            "SELECT id, song_id, name, key, bpm, pco_arrangement_id "
            "FROM arrangements WHERE song_id = ANY(%s) ORDER BY id",
            (song_ids,),
        )
        arrangements = [dict(r) for r in cur.fetchall()]

        arr_ids = [a["id"] for a in arrangements]

        # Sections
        if arr_ids:
            cur.execute(
                "SELECT id, arrangement_id, position, label, energy, notes "
                "FROM sections WHERE arrangement_id = ANY(%s) ORDER BY arrangement_id, position",
                (arr_ids,),
            )
            sections = [dict(r) for r in cur.fetchall()]
        else:
            sections = []

    # Assemble nested structure matching the frontend's songLibrary format:
    # [{ id, title, artist, arrangements: [{ id, name, key, bpm, sections: [...] }] }]
    sec_by_arr = {}
    for sec in sections:
        sec_by_arr.setdefault(sec["arrangement_id"], []).append(sec)

    arr_by_song = {}
    for arr in arrangements:
        arr["sections"] = [
            {"label": s["label"], "energy": s["energy"] or "", "notes": s["notes"] or ""}
            for s in sec_by_arr.get(arr["id"], [])
        ]
        arr_by_song.setdefault(arr["song_id"], []).append(arr)

    result = []
    for song in songs:
        song["arrangements"] = arr_by_song.get(song["id"], [])
        result.append(song)

    return jsonify(result)


@api_bp.post("/api/songs")
@login_required
def create_song():
    uid = current_user_id()
    body = request.get_json(silent=True) or {}
    title = str(body.get("title") or "").strip()
    artist = str(body.get("artist") or "").strip()
    arrangements = body.get("arrangements") or []

    if not title:
        return jsonify({"error": "title is required"}), 400

    with Db() as cur:
        cur.execute(
            "INSERT INTO songs (user_id, title, artist) VALUES (%s, %s, %s) RETURNING id",
            (uid, title, artist),
        )
        song_id = cur.fetchone()["id"]

        for arr in arrangements:
            arr_name = str(arr.get("name") or "Main").strip()
            cur.execute(
                "INSERT INTO arrangements (song_id, name, key, bpm) VALUES (%s, %s, %s, %s) RETURNING id",
                (song_id, arr_name, arr.get("key") or "", str(arr.get("bpm") or "")),
            )
            arr_id = cur.fetchone()["id"]
            _replace_sections(cur, arr_id, arr.get("sections") or [])

    return jsonify({"id": song_id}), 201


@api_bp.patch("/api/songs/<int:song_id>")
@login_required
def update_song(song_id):
    uid = current_user_id()
    body = request.get_json(silent=True) or {}
    title = str(body.get("title") or "").strip()
    artist = str(body.get("artist") or "").strip()

    if not title:
        return jsonify({"error": "title is required"}), 400

    with Db() as cur:
        cur.execute(
            "UPDATE songs SET title=%s, artist=%s, updated_at=now() "
            "WHERE id=%s AND user_id=%s RETURNING id",
            (title, artist, song_id, uid),
        )
        if not cur.fetchone():
            return jsonify({"error": "not found"}), 404

    return jsonify({"ok": True})


@api_bp.delete("/api/songs/<int:song_id>")
@login_required
def delete_song(song_id):
    uid = current_user_id()
    with Db() as cur:
        cur.execute(
            "DELETE FROM songs WHERE id=%s AND user_id=%s RETURNING id",
            (song_id, uid),
        )
        if not cur.fetchone():
            return jsonify({"error": "not found"}), 404
    return jsonify({"ok": True})


@api_bp.post("/api/songs/<int:song_id>/arrangements")
@login_required
def add_arrangement(song_id):
    uid = current_user_id()
    body = request.get_json(silent=True) or {}
    name = str(body.get("name") or "Main").strip()

    with Db() as cur:
        # Verify song belongs to user
        cur.execute("SELECT id FROM songs WHERE id=%s AND user_id=%s", (song_id, uid))
        if not cur.fetchone():
            return jsonify({"error": "not found"}), 404

        cur.execute(
            "INSERT INTO arrangements (song_id, name, key, bpm) VALUES (%s, %s, %s, %s) RETURNING id",
            (song_id, name, body.get("key") or "", str(body.get("bpm") or "")),
        )
        arr_id = cur.fetchone()["id"]
        _replace_sections(cur, arr_id, body.get("sections") or [])

    return jsonify({"id": arr_id}), 201


@api_bp.patch("/api/arrangements/<int:arr_id>")
@login_required
def update_arrangement(arr_id):
    uid = current_user_id()
    body = request.get_json(silent=True) or {}

    with Db() as cur:
        # Ensure ownership
        cur.execute(
            "SELECT a.id FROM arrangements a JOIN songs s ON s.id=a.song_id "
            "WHERE a.id=%s AND s.user_id=%s",
            (arr_id, uid),
        )
        if not cur.fetchone():
            return jsonify({"error": "not found"}), 404

        fields = []
        vals = []
        for col in ("name", "key", "bpm"):
            if col in body:
                fields.append(f"{col}=%s")
                vals.append(str(body[col] or ""))

        if fields:
            vals.append(arr_id)
            cur.execute(
                f"UPDATE arrangements SET {', '.join(fields)}, updated_at=now() WHERE id=%s",
                vals,
            )

        if "sections" in body:
            _replace_sections(cur, arr_id, body["sections"])

    return jsonify({"ok": True})


@api_bp.delete("/api/arrangements/<int:arr_id>")
@login_required
def delete_arrangement(arr_id):
    uid = current_user_id()
    with Db() as cur:
        cur.execute(
            "DELETE FROM arrangements a USING songs s "
            "WHERE a.song_id=s.id AND a.id=%s AND s.user_id=%s RETURNING a.id",
            (arr_id, uid),
        )
        if not cur.fetchone():
            return jsonify({"error": "not found"}), 404
    return jsonify({"ok": True})


@api_bp.post("/api/arrangements/<int:arr_id>/sections")
@login_required
def replace_sections(arr_id):
    uid = current_user_id()
    body = request.get_json(silent=True) or {}

    with Db() as cur:
        cur.execute(
            "SELECT a.id FROM arrangements a JOIN songs s ON s.id=a.song_id "
            "WHERE a.id=%s AND s.user_id=%s",
            (arr_id, uid),
        )
        if not cur.fetchone():
            return jsonify({"error": "not found"}), 404
        _replace_sections(cur, arr_id, body.get("sections") or [])

    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Setlists
# ---------------------------------------------------------------------------

@api_bp.get("/api/setlists")
@login_required
def list_setlists():
    uid = current_user_id()
    with Db() as cur:
        cur.execute(
            "SELECT id, name, date::text, pco_plan_id, "
            "COALESCE(pco_service_type_id, '') as pco_service_type_id "
            "FROM setlists "
            "WHERE user_id=%s ORDER BY date DESC NULLS LAST",
            (uid,),
        )
        setlists = [dict(r) for r in cur.fetchall()]

        if not setlists:
            return jsonify([])

        sl_ids = [s["id"] for s in setlists]
        cur.execute(
            "SELECT si.setlist_id, si.position, "
            "COALESCE(si.item_type, 'song') as item_type, si.label, "
            "a.id as arrangement_id, a.name as arrangement_name, a.key, a.bpm, "
            "s.id as song_id, s.title, s.artist "
            "FROM setlist_items si "
            "LEFT JOIN arrangements a ON a.id=si.arrangement_id "
            "LEFT JOIN songs s ON s.id=a.song_id "
            "WHERE si.setlist_id = ANY(%s) ORDER BY si.setlist_id, si.position",
            (sl_ids,),
        )
        items = [dict(r) for r in cur.fetchall()]

    items_by_sl = {}
    for item in items:
        if item["item_type"] in ("header", "item"):
            items_by_sl.setdefault(item["setlist_id"], []).append({
                "itemType": item["item_type"],
                "label": item["label"] or "",
            })
        else:
            items_by_sl.setdefault(item["setlist_id"], []).append({
                "itemType": "song",
                "arrangementId": item["arrangement_id"],
                "arrangementName": item["arrangement_name"],
                "key": item["key"],
                "bpm": item["bpm"],
                "songId": item["song_id"],
                "title": item["title"],
                "artist": item["artist"],
            })

    for sl in setlists:
        sl["items"] = items_by_sl.get(sl["id"], [])

    return jsonify(setlists)


@api_bp.post("/api/setlists")
@login_required
def create_setlist():
    uid = current_user_id()
    body = request.get_json(silent=True) or {}
    name = str(body.get("name") or "").strip()
    date = body.get("date") or None
    items = body.get("items") or []

    with Db() as cur:
        cur.execute(
            "INSERT INTO setlists (user_id, name, date) VALUES (%s, %s, %s) RETURNING id",
            (uid, name, date),
        )
        sl_id = cur.fetchone()["id"]
        _replace_setlist_items(cur, sl_id, items)

    return jsonify({"id": sl_id}), 201


@api_bp.patch("/api/setlists/<int:sl_id>")
@login_required
def update_setlist(sl_id):
    uid = current_user_id()
    body = request.get_json(silent=True) or {}

    with Db() as cur:
        cur.execute(
            "SELECT id FROM setlists WHERE id=%s AND user_id=%s", (sl_id, uid)
        )
        if not cur.fetchone():
            return jsonify({"error": "not found"}), 404

        fields = []
        vals = []
        for col in ("name", "date"):
            if col in body:
                fields.append(f"{col}=%s")
                vals.append(body[col])
        if fields:
            vals.append(sl_id)
            cur.execute(
                f"UPDATE setlists SET {', '.join(fields)}, updated_at=now() WHERE id=%s",
                vals,
            )

        if "items" in body:
            _replace_setlist_items(cur, sl_id, body["items"])

    return jsonify({"ok": True})


@api_bp.delete("/api/setlists/<int:sl_id>")
@login_required
def delete_setlist(sl_id):
    uid = current_user_id()
    with Db() as cur:
        cur.execute(
            "DELETE FROM setlists WHERE id=%s AND user_id=%s RETURNING id",
            (sl_id, uid),
        )
        if not cur.fetchone():
            return jsonify({"error": "not found"}), 404
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# localStorage migration
# ---------------------------------------------------------------------------

@api_bp.post("/api/migrate")
@login_required
def migrate_localstorage():
    """
    Accept raw localStorage export { songLibrary: [...], savedSetlists: [...] }
    and idempotently import into DB.  Safe to call multiple times.
    """
    uid = current_user_id()
    body = request.get_json(silent=True) or {}
    songs_payload = body.get("songLibrary") or []
    setlists_payload = body.get("savedSetlists") or []

    imported_songs = 0
    imported_setlists = 0

    with Db() as cur:
        # song_key → db arrangement id (for setlist linking)
        arr_map = {}  # (title_lower, artist_lower, arr_name_lower) → arrangement_id

        for song in songs_payload:
            title = str(song.get("title") or "").strip()
            artist = str(song.get("artist") or "").strip()
            if not title:
                continue

            # Check if song already exists
            cur.execute(
                "SELECT id FROM songs WHERE user_id=%s AND lower(title)=lower(%s) AND lower(artist)=lower(%s)",
                (uid, title, artist),
            )
            existing = cur.fetchone()
            if existing:
                song_id = existing["id"]
            else:
                cur.execute(
                    "INSERT INTO songs (user_id, title, artist) VALUES (%s, %s, %s) RETURNING id",
                    (uid, title, artist),
                )
                song_id = cur.fetchone()["id"]
                imported_songs += 1

            for arr in (song.get("arrangements") or []):
                arr_name = str(arr.get("name") or "Main").strip()
                key = str(arr.get("key") or "")
                bpm = str(arr.get("bpm") or "")

                cur.execute(
                    "SELECT id FROM arrangements WHERE song_id=%s AND lower(name)=lower(%s)",
                    (song_id, arr_name),
                )
                existing_arr = cur.fetchone()
                if existing_arr:
                    arr_id = existing_arr["id"]
                else:
                    cur.execute(
                        "INSERT INTO arrangements (song_id, name, key, bpm) "
                        "VALUES (%s, %s, %s, %s) RETURNING id",
                        (song_id, arr_name, key, bpm),
                    )
                    arr_id = cur.fetchone()["id"]
                    _replace_sections(cur, arr_id, arr.get("sections") or [])

                arr_map[(title.lower(), artist.lower(), arr_name.lower())] = arr_id

        for sl in setlists_payload:
            name = str(sl.get("name") or "").strip()
            date = sl.get("date") or None
            items = sl.get("items") or []

            cur.execute(
                "SELECT id FROM setlists WHERE user_id=%s AND name=%s AND date=%s",
                (uid, name, date),
            )
            if cur.fetchone():
                continue  # already imported

            cur.execute(
                "INSERT INTO setlists (user_id, name, date) VALUES (%s, %s, %s) RETURNING id",
                (uid, name, date),
            )
            sl_id = cur.fetchone()["id"]
            imported_setlists += 1

            # Map items using arr_map
            db_items = []
            for item in items:
                title = str(item.get("title") or "").strip().lower()
                artist = str(item.get("artist") or "").strip().lower()
                arr_name = str(item.get("arrangementName") or "main").strip().lower()
                arr_id = arr_map.get((title, artist, arr_name))
                if arr_id:
                    db_items.append({"arrangementId": arr_id})
            _replace_setlist_items(cur, sl_id, db_items)

    return jsonify({
        "ok": True,
        "importedSongs": imported_songs,
        "importedSetlists": imported_setlists,
    })


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _replace_sections(cur, arr_id, sections):
    cur.execute("DELETE FROM sections WHERE arrangement_id=%s", (arr_id,))
    for pos, sec in enumerate(sections):
        cur.execute(
            "INSERT INTO sections (arrangement_id, position, label, energy, notes) "
            "VALUES (%s, %s, %s, %s, %s)",
            (arr_id, pos, str(sec.get("label") or ""), str(sec.get("energy") or ""), str(sec.get("notes") or "")),
        )


def _replace_setlist_items(cur, sl_id, items):
    cur.execute("DELETE FROM setlist_items WHERE setlist_id=%s", (sl_id,))
    for pos, item in enumerate(items):
        item_type = item.get("itemType", "song")
        if item_type in ("header", "item"):
            cur.execute(
                "INSERT INTO setlist_items (setlist_id, position, item_type, label) "
                "VALUES (%s, %s, %s, %s)",
                (sl_id, pos, item_type, item.get("label", "")),
            )
        else:
            arr_id = item.get("arrangementId")
            if arr_id:
                cur.execute(
                    "INSERT INTO setlist_items (setlist_id, arrangement_id, position, item_type) "
                    "VALUES (%s, %s, %s, 'song')",
                    (sl_id, arr_id, pos),
                )
