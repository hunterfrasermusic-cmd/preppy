"""
Unit tests for app.py — PDF parsing, docx export, section inference.
No database required.
"""

import json


# ---------------------------------------------------------------------------
# Docx export
# ---------------------------------------------------------------------------

class TestExportDocx:
    def test_export_returns_docx(self, app_client):
        resp = app_client.post(
            "/api/export-docx",
            json={"lines": ["Prep Sheet March 9, 2026", "Amazing Grace [G]"], "filename": "test.docx"},
        )
        assert resp.status_code == 200
        assert resp.content_type.startswith("application/vnd.openxmlformats")
        assert b"PK" in resp.data[:4]  # zip magic bytes

    def test_export_rejects_missing_lines(self, app_client):
        resp = app_client.post("/api/export-docx", json={"filename": "test.docx"})
        assert resp.status_code == 400

    def test_export_rejects_non_list_lines(self, app_client):
        resp = app_client.post("/api/export-docx", json={"lines": "not a list"})
        assert resp.status_code == 400

    def test_export_uses_default_filename(self, app_client):
        resp = app_client.post("/api/export-docx", json={"lines": ["Hello"]})
        assert resp.status_code == 200
        assert "Prep Sheet.docx" in resp.headers.get("Content-Disposition", "")

    def test_export_with_header_lines(self, app_client):
        resp = app_client.post(
            "/api/export-docx",
            json={
                "lines": ["Title Line", "↓V1 - soft"],
                "header_lines": ["Custom Header"],
                "filename": "custom.docx",
            },
        )
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Song meta inference (pure functions — import directly)
# ---------------------------------------------------------------------------

class TestInferSongMeta:
    def _infer(self, lines, filename="test.pdf"):
        from app import infer_song_meta
        return infer_song_meta(lines, filename)

    def test_title_from_filename(self):
        meta = self._infer([], "Amazing Grace - Hillsong.pdf")
        assert meta["title"] == "Amazing Grace"
        assert meta["artist"] == "Hillsong"

    def test_key_from_filename(self):
        meta = self._infer([], "Song Title - Artist - G.pdf")
        assert meta["key"] == "G"

    def test_bpm_from_lines(self):
        meta = self._infer(["Song Title", "Key: G  72 BPM"], "test.pdf")
        assert meta["bpm"] == "72"

    def test_arrangement_from_filename(self):
        meta = self._infer([], "Song - Artist - Acoustic.pdf")
        assert meta["arrangement"] == "Acoustic"

    def test_fallback_title(self):
        meta = self._infer([], "test.pdf")
        assert meta["title"]  # should not be empty


class TestInferSections:
    def _infer(self, lines):
        from app import infer_sections
        return infer_sections(lines)

    def test_finds_basic_sections(self):
        sections = self._infer(["VERSE 1", "CHORUS 1", "BRIDGE 1"])
        labels = [s["label"] for s in sections]
        assert "V1" in labels
        assert "C1" in labels
        assert "B1" in labels

    def test_ignores_chord_lines(self):
        sections = self._infer(["G D Em C", "VERSE 1"])
        assert len(sections) == 1

    def test_empty_input(self):
        assert self._infer([]) == []


class TestNormalizeLines:
    def test_collapses_whitespace(self):
        from app import normalize_lines
        result = normalize_lines("hello   world\n  foo  bar  ")
        assert result == ["hello world", "foo bar"]

    def test_strips_empty(self):
        from app import normalize_lines
        result = normalize_lines("\n\n\n")
        assert result == []


# ---------------------------------------------------------------------------
# Line classification
# ---------------------------------------------------------------------------

class TestClassifyLineStyle:
    def _classify(self, line, index=0):
        from app import classify_line_style
        return classify_line_style(line, index)

    def test_prep_header(self):
        assert self._classify("Prep Sheet March 9, 2026", 0) == "PrepHeader"

    def test_section_line_with_arrow(self):
        assert self._classify("↓Intro - soft") == "SectionLine"

    def test_empty_line(self):
        assert self._classify("") == ""
