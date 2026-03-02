from io import BytesIO
import os
import re
import zipfile
from datetime import datetime, timezone
from xml.sax.saxutils import escape as xml_escape

from flask import Flask, Response, jsonify, render_template, request

try:
    from pypdf import PdfReader  # type: ignore
except ModuleNotFoundError:
    PdfReader = None

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


@app.route("/")
def index():
    return render_template("index.html")


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
            }
        )
    except Exception as exc:
        return jsonify({"error": f"Could not parse chart: {str(exc)}"}), 500


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
    port_raw = os.getenv("PREPPY_PORT", "5000").strip()
    debug_mode = os.getenv("PREPPY_DEBUG", "").strip().lower() in {"1", "true", "yes", "on"}
    try:
        port = int(port_raw)
    except ValueError:
        port = 5000
    app.run(debug=debug_mode, use_reloader=debug_mode, host="127.0.0.1", port=port)
