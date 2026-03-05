"""
Integration tests for preppy/api.py — CRUD routes.
Requires a running Postgres (DATABASE_URL).
"""

import json
import pytest
from tests.conftest import requires_db


@requires_db
class TestSongCRUD:
    def test_list_songs_empty(self, db_app):
        client, uid = db_app
        resp = client.get("/api/songs")
        assert resp.status_code == 200
        assert resp.get_json() == []

    def test_create_song(self, db_app):
        client, uid = db_app
        resp = client.post("/api/songs", json={
            "title": "Amazing Grace",
            "artist": "John Newton",
            "arrangements": [
                {"name": "Main", "key": "G", "bpm": "72", "sections": [
                    {"label": "V1", "energy": "down", "notes": "soft start"},
                    {"label": "C1", "energy": "up", "notes": ""},
                ]},
            ],
        })
        assert resp.status_code == 201
        song_id = resp.get_json()["id"]
        assert isinstance(song_id, int)

        # Verify it appears in the list
        songs = client.get("/api/songs").get_json()
        assert len(songs) == 1
        assert songs[0]["title"] == "Amazing Grace"
        assert songs[0]["artist"] == "John Newton"
        assert len(songs[0]["arrangements"]) == 1
        arr = songs[0]["arrangements"][0]
        assert arr["key"] == "G"
        assert arr["bpm"] == "72"
        assert len(arr["sections"]) == 2
        assert arr["sections"][0]["label"] == "V1"
        assert arr["sections"][0]["energy"] == "down"

    def test_create_song_requires_title(self, db_app):
        client, uid = db_app
        resp = client.post("/api/songs", json={"title": "", "artist": "Someone"})
        assert resp.status_code == 400

    def test_update_song(self, db_app):
        client, uid = db_app
        create = client.post("/api/songs", json={"title": "Old Title", "artist": "Old Artist"})
        song_id = create.get_json()["id"]

        resp = client.patch(f"/api/songs/{song_id}", json={"title": "New Title", "artist": "New Artist"})
        assert resp.status_code == 200

        songs = client.get("/api/songs").get_json()
        assert songs[0]["title"] == "New Title"

    def test_delete_song(self, db_app):
        client, uid = db_app
        create = client.post("/api/songs", json={"title": "To Delete", "artist": ""})
        song_id = create.get_json()["id"]

        resp = client.delete(f"/api/songs/{song_id}")
        assert resp.status_code == 200

        songs = client.get("/api/songs").get_json()
        assert len(songs) == 0

    def test_delete_nonexistent_song(self, db_app):
        client, uid = db_app
        resp = client.delete("/api/songs/99999")
        assert resp.status_code == 404


@requires_db
class TestArrangementCRUD:
    def _create_song(self, client):
        resp = client.post("/api/songs", json={"title": "Test Song", "artist": "Artist"})
        return resp.get_json()["id"]

    def test_add_arrangement(self, db_app):
        client, uid = db_app
        song_id = self._create_song(client)
        resp = client.post(f"/api/songs/{song_id}/arrangements", json={
            "name": "Acoustic", "key": "D", "bpm": "85",
            "sections": [{"label": "Intro", "energy": "", "notes": ""}],
        })
        assert resp.status_code == 201
        arr_id = resp.get_json()["id"]

        songs = client.get("/api/songs").get_json()
        arrs = songs[0]["arrangements"]
        assert len(arrs) == 1
        assert arrs[0]["name"] == "Acoustic"
        assert arrs[0]["key"] == "D"

    def test_update_arrangement(self, db_app):
        client, uid = db_app
        song_id = self._create_song(client)
        arr_resp = client.post(f"/api/songs/{song_id}/arrangements", json={"name": "Main", "key": "G"})
        arr_id = arr_resp.get_json()["id"]

        resp = client.patch(f"/api/arrangements/{arr_id}", json={"key": "A", "bpm": "120"})
        assert resp.status_code == 200

        songs = client.get("/api/songs").get_json()
        assert songs[0]["arrangements"][0]["key"] == "A"
        assert songs[0]["arrangements"][0]["bpm"] == "120"

    def test_replace_sections(self, db_app):
        client, uid = db_app
        song_id = self._create_song(client)
        arr_resp = client.post(f"/api/songs/{song_id}/arrangements", json={"name": "Main"})
        arr_id = arr_resp.get_json()["id"]

        resp = client.post(f"/api/arrangements/{arr_id}/sections", json={
            "sections": [
                {"label": "V1", "energy": "down", "notes": "quiet"},
                {"label": "C1", "energy": "up", "notes": "loud"},
            ],
        })
        assert resp.status_code == 200

        songs = client.get("/api/songs").get_json()
        sections = songs[0]["arrangements"][0]["sections"]
        assert len(sections) == 2
        assert sections[0]["label"] == "V1"
        assert sections[1]["energy"] == "up"

    def test_delete_arrangement(self, db_app):
        client, uid = db_app
        song_id = self._create_song(client)
        arr_resp = client.post(f"/api/songs/{song_id}/arrangements", json={"name": "Main"})
        arr_id = arr_resp.get_json()["id"]

        resp = client.delete(f"/api/arrangements/{arr_id}")
        assert resp.status_code == 200

        songs = client.get("/api/songs").get_json()
        assert len(songs[0]["arrangements"]) == 0


@requires_db
class TestSetlistCRUD:
    def _create_song_with_arrangement(self, client):
        resp = client.post("/api/songs", json={
            "title": "Test Song", "artist": "Artist",
            "arrangements": [{"name": "Main", "key": "G", "bpm": "100"}],
        })
        song_id = resp.get_json()["id"]
        songs = client.get("/api/songs").get_json()
        arr_id = songs[0]["arrangements"][0]["id"]
        return song_id, arr_id

    def test_create_and_list_setlist(self, db_app):
        client, uid = db_app
        song_id, arr_id = self._create_song_with_arrangement(client)

        resp = client.post("/api/setlists", json={
            "name": "Sunday Service",
            "date": "2026-03-08",
            "items": [{"arrangementId": arr_id}],
        })
        assert resp.status_code == 201
        sl_id = resp.get_json()["id"]

        setlists = client.get("/api/setlists").get_json()
        assert len(setlists) == 1
        assert setlists[0]["name"] == "Sunday Service"
        assert setlists[0]["date"] == "2026-03-08"
        assert len(setlists[0]["items"]) == 1
        assert setlists[0]["items"][0]["arrangementId"] == arr_id
        assert setlists[0]["items"][0]["itemType"] == "song"

    def test_setlist_with_header_items(self, db_app):
        client, uid = db_app
        song_id, arr_id = self._create_song_with_arrangement(client)

        resp = client.post("/api/setlists", json={
            "name": "Full Service",
            "date": "2026-03-08",
            "items": [
                {"itemType": "header", "label": "Opening"},
                {"arrangementId": arr_id},
                {"itemType": "header", "label": "Message"},
            ],
        })
        assert resp.status_code == 201

        setlists = client.get("/api/setlists").get_json()
        items = setlists[0]["items"]
        assert len(items) == 3
        assert items[0]["itemType"] == "header"
        assert items[0]["label"] == "Opening"
        assert items[1]["itemType"] == "song"
        assert items[1]["arrangementId"] == arr_id
        assert items[2]["itemType"] == "header"
        assert items[2]["label"] == "Message"

    def test_update_setlist(self, db_app):
        client, uid = db_app
        song_id, arr_id = self._create_song_with_arrangement(client)

        create = client.post("/api/setlists", json={
            "name": "Old Name", "date": "2026-03-08",
            "items": [{"arrangementId": arr_id}],
        })
        sl_id = create.get_json()["id"]

        resp = client.patch(f"/api/setlists/{sl_id}", json={"name": "New Name"})
        assert resp.status_code == 200

        setlists = client.get("/api/setlists").get_json()
        assert setlists[0]["name"] == "New Name"

    def test_update_setlist_items_with_headers(self, db_app):
        client, uid = db_app
        song_id, arr_id = self._create_song_with_arrangement(client)

        create = client.post("/api/setlists", json={
            "name": "Service", "date": "2026-03-08",
            "items": [{"arrangementId": arr_id}],
        })
        sl_id = create.get_json()["id"]

        # Update with header items
        resp = client.patch(f"/api/setlists/{sl_id}", json={
            "items": [
                {"itemType": "header", "label": "Worship"},
                {"arrangementId": arr_id},
            ],
        })
        assert resp.status_code == 200

        setlists = client.get("/api/setlists").get_json()
        items = setlists[0]["items"]
        assert len(items) == 2
        assert items[0]["itemType"] == "header"

    def test_delete_setlist(self, db_app):
        client, uid = db_app
        create = client.post("/api/setlists", json={"name": "To Delete", "date": "2026-03-08"})
        sl_id = create.get_json()["id"]

        resp = client.delete(f"/api/setlists/{sl_id}")
        assert resp.status_code == 200

        setlists = client.get("/api/setlists").get_json()
        assert len(setlists) == 0

    def test_setlist_includes_pco_fields(self, db_app):
        client, uid = db_app
        # Setlists created via API won't have PCO fields, but the response should include them
        client.post("/api/setlists", json={"name": "Test", "date": "2026-03-08"})
        setlists = client.get("/api/setlists").get_json()
        assert setlists[0]["pco_plan_id"] is None
        assert "pco_service_type_id" in setlists[0]


@requires_db
class TestMigration:
    def test_migrate_localstorage(self, db_app):
        client, uid = db_app
        payload = {
            "songLibrary": [
                {
                    "title": "How Great Thou Art",
                    "artist": "Stuart Hine",
                    "arrangements": [
                        {"name": "Main", "key": "Bb", "bpm": "68", "sections": [
                            {"label": "V1", "energy": "down", "notes": ""},
                        ]},
                    ],
                },
            ],
            "savedSetlists": [
                {
                    "name": "Test Service",
                    "date": "2026-01-05",
                    "items": [
                        {"title": "How Great Thou Art", "artist": "Stuart Hine", "arrangementName": "Main"},
                    ],
                },
            ],
        }

        resp = client.post("/api/migrate", json=payload)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["importedSongs"] == 1
        assert data["importedSetlists"] == 1

        # Verify data
        songs = client.get("/api/songs").get_json()
        assert len(songs) == 1
        assert songs[0]["title"] == "How Great Thou Art"

        setlists = client.get("/api/setlists").get_json()
        assert len(setlists) == 1
        assert len(setlists[0]["items"]) == 1

    def test_migrate_idempotent(self, db_app):
        client, uid = db_app
        payload = {
            "songLibrary": [{"title": "Song A", "artist": "Artist", "arrangements": []}],
            "savedSetlists": [],
        }
        client.post("/api/migrate", json=payload)
        resp = client.post("/api/migrate", json=payload)
        data = resp.get_json()
        assert data["importedSongs"] == 0  # already exists

        songs = client.get("/api/songs").get_json()
        assert len(songs) == 1


@requires_db
class TestAuthRequired:
    def test_unauthenticated_returns_401(self, db_app):
        client, uid = db_app
        # Clear session
        with client.session_transaction() as sess:
            sess.clear()
        resp = client.get("/api/songs")
        assert resp.status_code == 401
