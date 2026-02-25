import base64
import json
from io import BytesIO
import os
import re
import zipfile
from datetime import datetime, timezone
from typing import Optional
from urllib import parse as urlparse
from urllib import request as urlrequest
from urllib.error import HTTPError, URLError
from xml.sax.saxutils import escape as xml_escape

from flask import Flask, Response, jsonify, render_template, request

try:
    from pypdf import PdfReader  # type: ignore
except ModuleNotFoundError:
    PdfReader = None

try:
    import boto3  # type: ignore
except ModuleNotFoundError:
    boto3 = None

app = Flask(__name__)

DEFAULT_HEADER_LINES = [
    "Shorthand Key",
    "Dynamics: ↓=soft, →=medium, ↗=build, ↑ big/loud, PNO=piano, EG 1=lead electric, EG 2=rhythm electric, AG=acoustic,",
    "8va=octave (assumed up), vmp=vamp, <>s=diamonds or whole notes or changes. ¼=quarter note, ⅛=eighth note,",
]


SECTION_TOKEN_RE = re.compile(
    r"\b(INTRO|VERSE|V|PRE[- ]?CHORUS|PRE|CHORUS|CH|C|BRIDGE|B|TAG|OUTRO|"
    r"INSTRUMENTAL|INST|INTERLUDE|TURN|HOLD|VAMP)\b\s*([0-9]+)?(?:\s*[Xx]\s*([0-9]+))?",
    re.IGNORECASE,
)
BPM_RE = re.compile(r"(\d{2,3}(?:\.\d+)?)\s*BPM", re.IGNORECASE)
KEY_RE = re.compile(r"\b([A-G](?:#|b)?m?)\b")
CHORD_LINE_RE = re.compile(
    r"^(?:[A-G](?:#|b)?m?(?:/[A-G](?:#|b)?m?)?)(?:\s+[A-G](?:#|b)?m?(?:/[A-G](?:#|b)?m?)?)*$"
)

PCO_API_ROOT = os.getenv("PCO_API_ROOT", "https://api.planningcenteronline.com").rstrip("/")
PCO_SETTINGS_FILE = os.path.join(app.root_path, "data", "integrations.json")
_S3_CLIENT = None


@app.route("/")
def index():
    return render_template("index.html")


def load_pco_settings() -> dict:
    if not os.path.exists(PCO_SETTINGS_FILE):
        return {}
    try:
        with open(PCO_SETTINGS_FILE, "r", encoding="utf-8") as handle:
            raw = json.load(handle)
        return raw if isinstance(raw, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def save_pco_settings(app_id: str, secret: str) -> None:
    os.makedirs(os.path.dirname(PCO_SETTINGS_FILE), exist_ok=True)
    payload = {"pco": {"app_id": app_id.strip(), "secret": secret.strip()}}
    with open(PCO_SETTINGS_FILE, "w", encoding="utf-8") as handle:
        json.dump(payload, handle)


def get_pco_credentials() -> tuple[str, str, str]:
    env_app_id = os.getenv("PCO_APP_ID", "").strip()
    env_secret = os.getenv("PCO_SECRET", "").strip()
    if env_app_id and env_secret:
        return env_app_id, env_secret, "environment"

    settings = load_pco_settings().get("pco", {})
    if not isinstance(settings, dict):
        return "", "", "missing"

    app_id = str(settings.get("app_id") or "").strip()
    secret = str(settings.get("secret") or "").strip()
    if app_id and secret:
        return app_id, secret, "local"

    return "", "", "missing"


def get_s3_client():
    global _S3_CLIENT
    if _S3_CLIENT is not None:
        return _S3_CLIENT
    if boto3 is None:
        raise RuntimeError("boto3 dependency is missing.")
    _S3_CLIENT = boto3.client("s3", region_name=os.getenv("AWS_REGION") or None)
    return _S3_CLIENT


def maybe_store_chart_pdf(filename: str, payload: bytes) -> Optional[dict]:
    bucket = os.getenv("PREPPY_S3_BUCKET", "").strip()
    if not bucket:
        return None

    client = get_s3_client()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", filename).strip("_") or "chart.pdf"
    key = f"charts/{stamp}_{safe_name}"
    if not key.lower().endswith(".pdf"):
        key = f"{key}.pdf"

    client.put_object(
        Bucket=bucket,
        Key=key,
        Body=payload,
        ContentType="application/pdf",
    )
    url = client.generate_presigned_url(
        "get_object",
        Params={"Bucket": bucket, "Key": key},
        ExpiresIn=3600,
    )
    return {"provider": "s3", "bucket": bucket, "key": key, "download_url": url}


def pco_request(
    path_or_url: str,
    app_id: str,
    secret: str,
    method: str = "GET",
    params: Optional[dict] = None,
    payload: Optional[dict] = None,
):
    if path_or_url.startswith("http://") or path_or_url.startswith("https://"):
        url = path_or_url
    else:
        path = path_or_url if path_or_url.startswith("/") else f"/{path_or_url}"
        url = f"{PCO_API_ROOT}{path}"

    if params:
        query = urlparse.urlencode(params, doseq=True)
        joiner = "&" if "?" in url else "?"
        url = f"{url}{joiner}{query}"

    body = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/vnd.api+json"

    auth = base64.b64encode(f"{app_id}:{secret}".encode("utf-8")).decode("utf-8")
    headers["Authorization"] = f"Basic {auth}"
    req = urlrequest.Request(url=url, data=body, method=method.upper(), headers=headers)

    try:
        with urlrequest.urlopen(req, timeout=30) as response:
            raw = response.read()
    except HTTPError as exc:
        detail = ""
        try:
            detail = extract_api_error(exc.read().decode("utf-8", errors="ignore"))
        except Exception:
            detail = str(exc)
        raise RuntimeError(f"PCO API error {exc.code}: {detail}") from exc
    except URLError as exc:
        raise RuntimeError(f"PCO API request failed: {str(exc)}") from exc

    if not raw:
        return {}
    try:
        return json.loads(raw.decode("utf-8"))
    except ValueError as exc:
        raise RuntimeError("PCO API returned invalid JSON.") from exc


def extract_api_error(raw: str) -> str:
    if not raw:
        return "Unknown API error."
    try:
        payload = json.loads(raw)
        errors = payload.get("errors")
        if isinstance(errors, list) and errors:
            first = errors[0] if isinstance(errors[0], dict) else {}
            detail = first.get("detail") or first.get("title")
            if detail:
                return str(detail)
    except ValueError:
        pass
    return raw[:260]


def pco_paginate(
    path: str, app_id: str, secret: str, params: Optional[dict] = None, max_pages: int = 6
) -> list[dict]:
    items: list[dict] = []
    next_url: Optional[str] = path
    next_params = params
    pages = 0

    while next_url and pages < max_pages:
        payload = pco_request(next_url, app_id, secret, params=next_params)
        page_data = payload.get("data")
        if isinstance(page_data, list):
            items.extend([entry for entry in page_data if isinstance(entry, dict)])
        elif isinstance(page_data, dict):
            items.append(page_data)

        links = payload.get("links") if isinstance(payload, dict) else {}
        next_link = links.get("next") if isinstance(links, dict) else None
        next_url = str(next_link) if next_link else None
        next_params = None
        pages += 1

    return items


def first_nonempty(*values) -> str:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def fetch_pco_upcoming_plans(app_id: str, secret: str) -> list[dict]:
    plans: list[dict] = []
    service_types = pco_paginate(
        "/services/v2/service_types",
        app_id,
        secret,
        params={"per_page": 25},
        max_pages=2,
    )

    for service_type in service_types[:12]:
        sid = str(service_type.get("id") or "").strip()
        if not sid:
            continue
        st_attrs = service_type.get("attributes") if isinstance(service_type, dict) else {}
        st_name = first_nonempty((st_attrs or {}).get("name"), "Service Type")
        st_plans = pco_paginate(
            f"/services/v2/service_types/{sid}/plans",
            app_id,
            secret,
            params={"filter": "future", "per_page": 12},
            max_pages=1,
        )

        for plan in st_plans:
            attrs = plan.get("attributes") if isinstance(plan, dict) else {}
            sort_date = first_nonempty((attrs or {}).get("sort_date"))
            title = first_nonempty((attrs or {}).get("title"), (attrs or {}).get("series_title"), sort_date, "Plan")
            plans.append(
                {
                    "service_type_id": sid,
                    "service_type_name": st_name,
                    "plan_id": str(plan.get("id") or ""),
                    "title": title,
                    "sort_date": sort_date,
                }
            )

    plans.sort(key=lambda item: (item.get("sort_date") or "", item.get("service_type_name") or ""))
    return plans[:80]


def fetch_pco_plan(service_type_id: str, plan_id: str, app_id: str, secret: str) -> dict:
    payload = pco_request(
        f"/services/v2/service_types/{service_type_id}/plans/{plan_id}",
        app_id,
        secret,
    )
    data = payload.get("data") if isinstance(payload, dict) else {}
    attrs = data.get("attributes") if isinstance(data, dict) else {}
    return {
        "service_type_id": service_type_id,
        "plan_id": plan_id,
        "title": first_nonempty((attrs or {}).get("title"), (attrs or {}).get("series_title"), "Plan"),
        "sort_date": first_nonempty((attrs or {}).get("sort_date")),
        "raw_type": first_nonempty((data or {}).get("type"), "Plan"),
    }


def fetch_pco_plan_songs(service_type_id: str, plan_id: str, app_id: str, secret: str) -> list[dict]:
    response = pco_request(
        f"/services/v2/service_types/{service_type_id}/plans/{plan_id}/items",
        app_id,
        secret,
        params={"per_page": 100, "include": "song,arrangement"},
    )
    items = response.get("data") if isinstance(response, dict) else []
    included = response.get("included") if isinstance(response, dict) else []
    if not isinstance(items, list):
        items = []
    if not isinstance(included, list):
        included = []

    songs_by_id: dict[str, dict] = {}
    arrangements_by_id: dict[str, dict] = {}
    for entry in included:
        if not isinstance(entry, dict):
            continue
        entry_id = str(entry.get("id") or "")
        attrs = entry.get("attributes") if isinstance(entry.get("attributes"), dict) else {}
        entry_type = str(entry.get("type") or "").lower()
        if "song" in entry_type:
            songs_by_id[entry_id] = attrs
        if "arrangement" in entry_type:
            arrangements_by_id[entry_id] = attrs

    songs: list[dict] = []
    for idx, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        attrs = item.get("attributes") if isinstance(item.get("attributes"), dict) else {}
        relationships = item.get("relationships") if isinstance(item.get("relationships"), dict) else {}

        song_rel = relationships.get("song") if isinstance(relationships.get("song"), dict) else {}
        song_data = song_rel.get("data") if isinstance(song_rel.get("data"), dict) else {}
        song_id = str(song_data.get("id") or "")

        arrangement_rel = (
            relationships.get("arrangement") if isinstance(relationships.get("arrangement"), dict) else {}
        )
        arrangement_data = arrangement_rel.get("data") if isinstance(arrangement_rel.get("data"), dict) else {}
        arrangement_id = str(arrangement_data.get("id") or "")

        song_attrs = songs_by_id.get(song_id, {})
        arrangement_attrs = arrangements_by_id.get(arrangement_id, {})
        item_type = str(attrs.get("item_type") or "").lower()
        likely_song = bool(song_id) or "song" in item_type
        if not likely_song:
            continue

        title = first_nonempty(song_attrs.get("title"), attrs.get("title"), attrs.get("name"))
        if not title:
            continue

        songs.append(
            {
                "order": idx,
                "title": title,
                "artist": first_nonempty(
                    song_attrs.get("artist_name"),
                    song_attrs.get("artist"),
                    song_attrs.get("author"),
                ),
                "arrangement": first_nonempty(arrangement_attrs.get("name"), attrs.get("arrangement_name"), "Main"),
                "key": first_nonempty(
                    arrangement_attrs.get("key_name"),
                    arrangement_attrs.get("key"),
                    attrs.get("key_name"),
                    attrs.get("key"),
                ),
                "bpm": first_nonempty(arrangement_attrs.get("bpm"), attrs.get("bpm")),
            }
        )

    return songs


def fetch_pco_plan_charts(service_type_id: str, plan_id: str, app_id: str, secret: str) -> list[dict]:
    attachments = pco_paginate(
        f"/services/v2/service_types/{service_type_id}/plans/{plan_id}/attachments",
        app_id,
        secret,
        params={"per_page": 100},
        max_pages=2,
    )
    charts: list[dict] = []

    for attachment in attachments:
        attrs = attachment.get("attributes") if isinstance(attachment, dict) else {}
        if not isinstance(attrs, dict):
            attrs = {}
        filename = first_nonempty(
            attrs.get("filename"),
            attrs.get("file_name"),
            attrs.get("name"),
            attrs.get("title"),
        )
        url = first_nonempty(
            attrs.get("attachment_url"),
            attrs.get("download_url"),
            attrs.get("url"),
            attrs.get("file_url"),
        )
        content_type = first_nonempty(attrs.get("content_type")).lower()

        if not filename and not url:
            continue
        is_pdf = filename.lower().endswith(".pdf") or "pdf" in content_type
        if not is_pdf:
            continue
        charts.append(
            {
                "attachment_id": str(attachment.get("id") or ""),
                "filename": filename or "Chart.pdf",
                "url": url,
            }
        )

    return charts


def pco_fetch_binary(url: str, app_id: str, secret: str) -> bytes:
    auth = base64.b64encode(f"{app_id}:{secret}".encode("utf-8")).decode("utf-8")
    headers = {"Authorization": f"Basic {auth}"}
    req = urlrequest.Request(url=url, method="GET", headers=headers)
    with urlrequest.urlopen(req, timeout=30) as response:
        return response.read()


@app.post("/api/parse-chart")
def parse_chart():
    if PdfReader is None:
        return (
            jsonify(
                {
                    "error": "PDF parser dependency is missing. Run the launcher again to install requirements."
                }
            ),
            500,
        )

    upload = request.files.get("chart")
    if not upload or not upload.filename:
        return jsonify({"error": "Please choose a chart file first."}), 400

    filename = upload.filename
    if not filename.lower().endswith(".pdf"):
        return jsonify({"error": "Only PDF chord charts are supported right now."}), 400

    try:
        payload = upload.read()
        storage = None
        storage_error = ""
        try:
            storage = maybe_store_chart_pdf(filename, payload)
        except Exception as storage_exc:
            storage_error = str(storage_exc)

        chart_text = extract_pdf_text(payload)
        lines = normalize_lines(chart_text)
        song_meta = infer_song_meta(lines, filename)
        sections = infer_sections(lines)

        if not sections:
            sections = default_sections_stub()

        return jsonify(
            {
                "song": song_meta,
                "sections": sections,
                "source": {
                    "filename": filename,
                    "line_count": len(lines),
                },
                "storage": storage,
                "storage_error": storage_error,
            }
        )
    except Exception as exc:
        return jsonify({"error": f"Could not parse chart: {str(exc)}"}), 500


@app.get("/api/storage-status")
def storage_status():
    bucket = os.getenv("PREPPY_S3_BUCKET", "").strip()
    return jsonify(
        {
            "s3_enabled": bool(bucket),
            "bucket": bucket,
            "region": os.getenv("AWS_REGION", ""),
            "boto3_available": boto3 is not None,
        }
    )


@app.get("/api/pco/status")
def pco_status():
    app_id, secret, source = get_pco_credentials()
    return jsonify(
        {
            "configured": bool(app_id and secret),
            "source": source,
            "app_id_hint": f"{app_id[:4]}..." if app_id else "",
        }
    )


@app.post("/api/pco/credentials")
def set_pco_credentials():
    payload = request.get_json(silent=True) or {}
    app_id = str(payload.get("app_id") or "").strip()
    secret = str(payload.get("secret") or "").strip()
    if not app_id or not secret:
        return jsonify({"error": "Both app_id and secret are required."}), 400

    try:
        save_pco_settings(app_id, secret)
    except OSError as exc:
        return jsonify({"error": f"Could not save credentials: {str(exc)}"}), 500

    return jsonify({"ok": True, "configured": True, "source": "local"})


@app.get("/api/pco/upcoming-plans")
def pco_upcoming_plans():
    app_id, secret, _source = get_pco_credentials()
    if not app_id or not secret:
        return jsonify({"error": "Planning Center credentials are not configured."}), 400

    try:
        plans = fetch_pco_upcoming_plans(app_id, secret)
        return jsonify({"plans": plans})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.get("/api/pco/import-plan")
def pco_import_plan():
    app_id, secret, _source = get_pco_credentials()
    if not app_id or not secret:
        return jsonify({"error": "Planning Center credentials are not configured."}), 400

    service_type_id = str(request.args.get("service_type_id") or "").strip()
    plan_id = str(request.args.get("plan_id") or "").strip()
    if not service_type_id or not plan_id:
        return jsonify({"error": "service_type_id and plan_id are required."}), 400

    try:
        plan = fetch_pco_plan(service_type_id, plan_id, app_id, secret)
        songs = fetch_pco_plan_songs(service_type_id, plan_id, app_id, secret)
        return jsonify({"plan": plan, "songs": songs})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.get("/api/pco/plan-charts")
def pco_plan_charts():
    app_id, secret, _source = get_pco_credentials()
    if not app_id or not secret:
        return jsonify({"error": "Planning Center credentials are not configured."}), 400

    service_type_id = str(request.args.get("service_type_id") or "").strip()
    plan_id = str(request.args.get("plan_id") or "").strip()
    if not service_type_id or not plan_id:
        return jsonify({"error": "service_type_id and plan_id are required."}), 400

    try:
        charts = fetch_pco_plan_charts(service_type_id, plan_id, app_id, secret)
        return jsonify({"charts": charts})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.get("/api/pco/parse-plan-chart")
def pco_parse_plan_chart():
    if PdfReader is None:
        return jsonify({"error": "PDF parser dependency is missing."}), 500

    app_id, secret, _source = get_pco_credentials()
    if not app_id or not secret:
        return jsonify({"error": "Planning Center credentials are not configured."}), 400

    service_type_id = str(request.args.get("service_type_id") or "").strip()
    plan_id = str(request.args.get("plan_id") or "").strip()
    chart_url = str(request.args.get("chart_url") or "").strip()
    chart_name = str(request.args.get("chart_name") or "").strip() or "Plan Chart.pdf"

    if not chart_url:
        return jsonify({"error": "chart_url is required."}), 400
    if not service_type_id or not plan_id:
        return jsonify({"error": "service_type_id and plan_id are required."}), 400

    try:
        payload = pco_fetch_binary(chart_url, app_id, secret)
        storage = None
        storage_error = ""
        try:
            storage = maybe_store_chart_pdf(chart_name, payload)
        except Exception as storage_exc:
            storage_error = str(storage_exc)

        chart_text = extract_pdf_text(payload)
        lines = normalize_lines(chart_text)
        song_meta = infer_song_meta(lines, chart_name)
        sections = infer_sections(lines) or default_sections_stub()
        return jsonify(
            {
                "song": song_meta,
                "sections": sections,
                "source": {
                    "service_type_id": service_type_id,
                    "plan_id": plan_id,
                    "chart_url": chart_url,
                    "line_count": len(lines),
                },
                "storage": storage,
                "storage_error": storage_error,
            }
        )
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.post("/api/pco/write-plan-note")
def pco_write_plan_note():
    app_id, secret, _source = get_pco_credentials()
    if not app_id or not secret:
        return jsonify({"error": "Planning Center credentials are not configured."}), 400

    payload = request.get_json(silent=True) or {}
    service_type_id = str(payload.get("service_type_id") or "").strip()
    plan_id = str(payload.get("plan_id") or "").strip()
    prep_text = str(payload.get("prep_text") or "").strip()
    target_attribute = str(payload.get("attribute") or "").strip()

    if not service_type_id or not plan_id or not prep_text:
        return jsonify({"error": "service_type_id, plan_id, and prep_text are required."}), 400

    try:
        plan = pco_request(f"/services/v2/service_types/{service_type_id}/plans/{plan_id}", app_id, secret)
        data = plan.get("data") if isinstance(plan, dict) else {}
        attrs = data.get("attributes") if isinstance(data, dict) else {}
        if not isinstance(attrs, dict):
            attrs = {}

        candidates = []
        if target_attribute:
            candidates.append(target_attribute)
        candidates.extend(["plan_notes", "notes", "other_notes"])
        candidates.extend([key for key in attrs.keys() if "note" in key.lower()])

        write_key = ""
        for key in candidates:
            if key in attrs:
                write_key = key
                break

        if not write_key:
            return (
                jsonify(
                    {
                        "error": "No writable notes field found on this plan.",
                        "available_attributes": sorted(attrs.keys()),
                    }
                ),
                400,
            )

        pco_request(
            f"/services/v2/service_types/{service_type_id}/plans/{plan_id}",
            app_id,
            secret,
            method="PATCH",
            payload={
                "data": {
                    "type": first_nonempty((data or {}).get("type"), "Plan"),
                    "id": plan_id,
                    "attributes": {write_key: prep_text},
                }
            },
        )
        return jsonify({"ok": True, "attribute": write_key})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.post("/api/export-docx")
def export_docx():
    payload = request.get_json(silent=True) or {}
    lines = payload.get("lines")
    header_lines = payload.get("header_lines")
    filename = str(payload.get("filename") or "").strip()

    if not isinstance(lines, list) or not all(isinstance(item, str) for item in lines):
        return jsonify({"error": "Invalid payload. 'lines' must be an array of strings."}), 400

    if not filename:
        filename = "Prep Sheet.docx"

    safe_filename = sanitize_docx_filename(filename)
    if not isinstance(header_lines, list) or not all(isinstance(item, str) for item in header_lines):
        header_lines = DEFAULT_HEADER_LINES

    docx_data = build_docx(lines, header_lines)

    return Response(
        docx_data,
        mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f'attachment; filename="{safe_filename}"'},
    )


def extract_pdf_text(payload: bytes) -> str:
    if PdfReader is None:
        raise RuntimeError("PDF parser not available")

    reader = PdfReader(BytesIO(payload))
    pages = []
    for page in reader.pages:
        pages.append(page.extract_text() or "")
    return "\n".join(pages)


def sanitize_docx_filename(value: str) -> str:
    name = re.sub(r"[\\\\/:*?\"<>|]+", "_", value).strip()
    if not name.lower().endswith(".docx"):
        name = f"{name}.docx"
    return name or "Prep Sheet.docx"


def build_docx(lines: list[str], header_lines: list[str]) -> bytes:
    document_xml = build_document_xml(lines)
    header_xml = build_header_xml(header_lines)
    created = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    memory = BytesIO()

    with zipfile.ZipFile(memory, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "[Content_Types].xml",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>
  <Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
  <Override PartName="/word/header1.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.header+xml"/>
  <Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
</Types>""",
        )
        archive.writestr(
            "_rels/.rels",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>
  <Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>
</Relationships>""",
        )
        archive.writestr(
            "docProps/app.xml",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"
            xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">
  <Application>Preppy</Application>
</Properties>""",
        )
        archive.writestr(
            "docProps/core.xml",
            f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
                   xmlns:dc="http://purl.org/dc/elements/1.1/"
                   xmlns:dcterms="http://purl.org/dc/terms/"
                   xmlns:dcmitype="http://purl.org/dc/dcmitype/"
                   xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <dc:title>Prep Sheet</dc:title>
  <dc:creator>Preppy</dc:creator>
  <cp:lastModifiedBy>Preppy</cp:lastModifiedBy>
  <dcterms:created xsi:type="dcterms:W3CDTF">{created}</dcterms:created>
  <dcterms:modified xsi:type="dcterms:W3CDTF">{created}</dcterms:modified>
</cp:coreProperties>""",
        )
        archive.writestr(
            "word/_rels/document.xml.rels",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/header" Target="header1.xml"/>
</Relationships>""",
        )
        archive.writestr(
            "word/styles.xml",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:style w:type="paragraph" w:default="1" w:styleId="Normal">
    <w:name w:val="Normal"/>
    <w:qFormat/>
    <w:pPr>
      <w:spacing w:before="0" w:after="0" w:line="240" w:lineRule="auto"/>
    </w:pPr>
    <w:rPr>
      <w:rFonts w:ascii="Calibri" w:hAnsi="Calibri"/>
      <w:sz w:val="22"/>
      <w:szCs w:val="22"/>
    </w:rPr>
  </w:style>
  <w:style w:type="paragraph" w:styleId="PrepHeader">
    <w:name w:val="PrepHeader"/>
    <w:basedOn w:val="Normal"/>
    <w:pPr>
      <w:spacing w:before="0" w:after="120" w:line="240" w:lineRule="auto"/>
    </w:pPr>
    <w:rPr>
      <w:b/>
      <w:u w:val="single"/>
      <w:rFonts w:ascii="Calibri" w:hAnsi="Calibri"/>
      <w:sz w:val="24"/>
      <w:szCs w:val="24"/>
    </w:rPr>
  </w:style>
  <w:style w:type="paragraph" w:styleId="SongTitle">
    <w:name w:val="SongTitle"/>
    <w:basedOn w:val="Normal"/>
    <w:pPr>
      <w:spacing w:before="120" w:after="20" w:line="240" w:lineRule="auto"/>
    </w:pPr>
    <w:rPr>
      <w:b/>
      <w:u w:val="single"/>
      <w:rFonts w:ascii="Calibri" w:hAnsi="Calibri"/>
      <w:sz w:val="22"/>
      <w:szCs w:val="22"/>
    </w:rPr>
  </w:style>
  <w:style w:type="paragraph" w:styleId="SectionLine">
    <w:name w:val="SectionLine"/>
    <w:basedOn w:val="Normal"/>
    <w:pPr>
      <w:spacing w:before="0" w:after="0" w:line="240" w:lineRule="auto"/>
    </w:pPr>
    <w:rPr>
      <w:rFonts w:ascii="Calibri" w:hAnsi="Calibri"/>
      <w:sz w:val="22"/>
      <w:szCs w:val="22"/>
    </w:rPr>
  </w:style>
</w:styles>""",
        )
        archive.writestr(
            "word/document.xml",
            document_xml,
        )
        archive.writestr(
            "word/header1.xml",
            header_xml,
        )

    return memory.getvalue()


def build_document_xml(lines: list[str]) -> str:
    line_styles = [classify_line_style(line, idx) for idx, line in enumerate(lines)]
    paragraphs = []
    for idx, line in enumerate(lines):
        safe_text = xml_escape(line)
        style_id = line_styles[idx]
        if not safe_text:
            paragraphs.append("<w:p/>")
            continue

        ppr = build_paragraph_props_xml(style_id, line_styles, idx)
        runs_xml = build_runs_xml(line, style_id)
        paragraphs.append(
            f"<w:p>{ppr}{runs_xml}</w:p>"
        )

    body = "".join(paragraphs)
    return (
        "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>"
        "<w:document xmlns:w=\"http://schemas.openxmlformats.org/wordprocessingml/2006/main\" "
        "xmlns:r=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships\">"
        "<w:body>"
        f"{body}"
        "<w:sectPr>"
        "<w:headerReference w:type=\"default\" r:id=\"rId2\"/>"
        "<w:cols w:num=\"2\" w:space=\"720\"/>"
        "</w:sectPr>"
        "</w:body>"
        "</w:document>"
    )


def build_paragraph_props_xml(style_id: str, line_styles: list[str], index: int) -> str:
    if not style_id:
        return ""

    props = [f"<w:pStyle w:val=\"{style_id}\"/>"]

    # Keep each song block together so titles/sections don't split across columns/pages.
    if style_id in {"SongTitle", "SectionLine"}:
        props.append("<w:keepLines/>")
        next_style = line_styles[index + 1] if index + 1 < len(line_styles) else ""
        if next_style == "SectionLine":
            props.append("<w:keepNext/>")

    return f"<w:pPr>{''.join(props)}</w:pPr>"


def build_runs_xml(line: str, style_id: str) -> str:
    if style_id != "SectionLine":
        safe = xml_escape(line)
        return f"<w:r><w:t xml:space=\"preserve\">{safe}</w:t></w:r>"

    # Bold the section tag while leaving detail notes normal.
    # Example: "↓Intro x2 - EG hook" => arrow normal, "Intro x2" bold, " - EG hook" normal.
    match = re.match(r"^([↓→↗↑]?)([^-]+?)(\s*-\s*.*)?$", line)
    if not match:
        safe = xml_escape(line)
        return f"<w:r><w:t xml:space=\"preserve\">{safe}</w:t></w:r>"

    arrow = match.group(1) or ""
    section_tag = (match.group(2) or "").strip()
    remainder = match.group(3) or ""

    runs = []
    if arrow:
        runs.append(f"<w:r><w:t xml:space=\"preserve\">{xml_escape(arrow)}</w:t></w:r>")
    if section_tag:
        runs.append(
            "<w:r><w:rPr><w:b/></w:rPr>"
            f"<w:t xml:space=\"preserve\">{xml_escape(section_tag)}</w:t>"
            "</w:r>"
        )
    if remainder:
        runs.append(f"<w:r><w:t xml:space=\"preserve\">{xml_escape(remainder)}</w:t></w:r>")

    return "".join(runs) if runs else f"<w:r><w:t xml:space=\"preserve\">{xml_escape(line)}</w:t></w:r>"


def build_header_xml(lines: list[str]) -> str:
    paragraphs = []
    for idx, line in enumerate(lines):
        safe = xml_escape(line)
        if not safe:
            paragraphs.append("<w:p/>")
            continue

        if idx == 0:
            paragraphs.append(
                "<w:p><w:r><w:rPr><w:i/><w:u w:val=\"single\"/></w:rPr>"
                f"<w:t xml:space=\"preserve\">{safe}</w:t></w:r></w:p>"
            )
        else:
            paragraphs.append(
                "<w:p><w:r><w:rPr><w:i/></w:rPr>"
                f"<w:t xml:space=\"preserve\">{safe}</w:t></w:r></w:p>"
            )

    body = "".join(paragraphs)
    return (
        "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>"
        "<w:hdr xmlns:w=\"http://schemas.openxmlformats.org/wordprocessingml/2006/main\" "
        "xmlns:r=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships\">"
        f"{body}"
        "</w:hdr>"
    )


def classify_line_style(line: str, index: int) -> str:
    stripped = line.strip()
    if not stripped:
        return ""
    if index == 0 and stripped.lower().startswith("prep sheet "):
        return "PrepHeader"
    if is_section_line(stripped):
        return "SectionLine"
    if is_song_title_line(stripped):
        return "SongTitle"
    return "Normal"


def is_song_title_line(text: str) -> bool:
    # Exclude common section prefixes and utility lines.
    section_prefixes = (
        "↓",
        "→",
        "↗",
        "↑",
        "intro",
        "v",
        "pre",
        "c",
        "b",
        "tag",
        "turn",
        "inst",
        "outro",
        "end",
        "read ",
        "_",
    )
    lowered = text.lower()
    if lowered.startswith(section_prefixes):
        return False

    # Song title lines are plain title text and may include [Key] and BPM suffixes.
    return bool(
        re.fullmatch(
            r"[A-Za-z0-9'&()., !?-]+(?: \[[A-G](?:#|b)?m?\])?(?: ?- ?\d+(?:\.\d+)?\s*[bB][pP][mM])?",
            text,
        )
    )


def is_section_line(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return False
    if stripped[0] in {"↓", "→", "↗", "↑"}:
        return True
    return bool(re.match(r"^(Intro|V\\d|V\\d+|Pre|C\\d|B\\d|Tag|Turn|Inst|Outro|END)\\b", stripped, flags=re.IGNORECASE))


def normalize_lines(text: str) -> list[str]:
    cleaned = re.sub(r"\r\n?", "\n", text)
    lines = []
    for raw in cleaned.split("\n"):
        line = re.sub(r"\s+", " ", raw).strip()
        if line:
            lines.append(line)
    return lines


def infer_song_meta(lines: list[str], filename: str) -> dict:
    title = ""
    artist = ""
    arrangement = "Main"
    key = ""
    bpm = ""

    base_name = os.path.splitext(os.path.basename(filename))[0]
    cleaned_name = re.sub(r"^CHART\s+", "", base_name, flags=re.IGNORECASE).replace("_", " ").strip()
    parts = [part.strip() for part in cleaned_name.split(" - ") if part.strip()]
    arrangement_tokens = ["acoustic", "radio", "studio", "live", "alt", "stripped"]
    if len(parts) >= 1:
        title = parts[0]

    for token in parts[1:]:
        if token.lower() in arrangement_tokens:
            arrangement = token.capitalize()

    for token in reversed(parts):
        if re.fullmatch(r"[A-G](?:#|b)?m?", token):
            key = token
            break

    if len(parts) >= 2 and not artist:
        artist_candidate = parts[1]
        if (
            not re.fullmatch(r"[A-G](?:#|b)?m?", artist_candidate)
            and artist_candidate.lower() not in arrangement_tokens
        ):
            artist = artist_candidate

    for line in lines[:8]:
        bpm_match = BPM_RE.search(line)
        if bpm_match:
            bpm = bpm_match.group(1)
            break

    if lines and not title:
        title_line = lines[0]
        title_line = re.sub(r"^CHART\s+", "", title_line, flags=re.IGNORECASE).strip()
        line_parts = [part.strip() for part in title_line.split(" - ") if part.strip()]

        if len(line_parts) >= 1:
            title = line_parts[0]

        if len(line_parts) >= 2:
            candidate_key = line_parts[-1]
            if re.fullmatch(r"[A-G](?:#|b)?m?", candidate_key):
                key = candidate_key

    if not key:
        for line in lines[:12]:
            bracket_key = re.search(r"\[([A-G](?:#|b)?m?)\]", line)
            if bracket_key:
                key = bracket_key.group(1)
                break

            for match in KEY_RE.finditer(line):
                candidate = match.group(1)
                if candidate in {"A", "B", "C", "D", "E", "F", "G"} and " " not in line:
                    key = candidate
                    break
            if key:
                break

    for token in arrangement_tokens:
        if re.search(rf"\b{token}\b", cleaned_name, flags=re.IGNORECASE):
            arrangement = token.capitalize()
            break

    return {
        "title": title or "Untitled Song",
        "artist": artist,
        "arrangement": arrangement,
        "key": key,
        "bpm": bpm,
    }


def infer_sections(lines: list[str]) -> list[dict]:
    sections = []

    for line in lines:
        normalized = line.replace("|", " ").replace("-", " ")
        if CHORD_LINE_RE.match(normalized):
            continue

        for match in SECTION_TOKEN_RE.finditer(line):
            token = match.group(1).upper()
            num = (match.group(2) or "").strip()
            repeat = (match.group(3) or "").strip()

            if token in {"C", "B", "V"} and not num:
                continue

            label = to_section_label(token, num)
            if not label:
                continue

            section = {
                "label": label,
                "energy": "",
                "notes": "",
            }
            if repeat:
                section["repeat"] = int(repeat)
            sections.append(section)

    return sections[:64]


def to_section_label(token: str, num: str) -> str:
    if token == "INTRO":
        return "Intro" if not num else f"Intro {num}"
    if token in {"VERSE", "V"}:
        return f"V{num}" if num else "Verse"
    if token in {"PRE", "PRE-CHORUS", "PRE CHORUS"}:
        return f"Pre {num}" if num else "Pre"
    if token in {"CHORUS", "CH", "C"}:
        return f"C{num}" if num else "Chorus"
    if token in {"BRIDGE", "B"}:
        return f"B{num}" if num else "Bridge"
    if token == "TAG":
        return "Tag" if not num else f"Tag {num}"
    if token == "OUTRO":
        return "Outro" if not num else f"Outro {num}"
    if token in {"INSTRUMENTAL", "INST", "INTERLUDE"}:
        return "Instr" if not num else f"Instr {num}"
    if token == "TURN":
        return "Turn"
    if token == "HOLD":
        return "Hold"
    if token == "VAMP":
        return "Vamp"
    return ""


def default_sections_stub() -> list[dict]:
    defaults = ["Intro", "V1", "Pre 1", "C1", "V2", "Pre 2", "C2", "Bridge", "C3", "Outro"]
    return [{"label": label, "energy": "", "notes": ""} for label in defaults]


if __name__ == "__main__":
    app.run(debug=True, host="127.0.0.1", port=5000)
