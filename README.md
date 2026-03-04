# Preppy

A musician prep sheet tool for worship teams. Upload a PDF chord chart or build a song manually, organise your song library, build setlists, and export a formatted two-column `.docx` prep sheet for your band.

---

## What it does

- **Parse PDF chord charts** — upload a chart PDF and Preppy extracts the title, artist, key, BPM, and song sections automatically.
- **Song library** — save songs with multiple arrangements (Main, Acoustic, Alt Key, etc.) and search by title, artist, key, or BPM.
- **Setlist builder** — drag songs from the library into a service order, set a date and name.
- **Prep sheet export** — generates a two-column Word `.docx` document with a dynamics shorthand key in the header and each song's section flow (with dynamics arrows and notes).

---

## Tech stack

| Layer | Tech |
|---|---|
| Backend | Python 3, Flask |
| PDF parsing | pypdf |
| Frontend | Vanilla JS / HTML / CSS (served by Flask) |
| `.docx` generation | Raw Office Open XML (no external docx library) |
| Desktop wrapper (optional) | Tauri v2 (Rust) |

Data is stored in **browser `localStorage`** — no database is required for local dev.

---

## Local development (plain Flask)

This is the recommended starting point. No Rust or Node.js required.

### Prerequisites

- Python 3.10 or later
- `pip` (comes with Python)

### Steps

```bash
# 1. Clone the repo
git clone https://github.com/hunterfrasermusic-cmd/preppy.git
cd preppy

# 2. Create a virtual environment
python3 -m venv .venv

# 3. Activate it
source .venv/bin/activate        # macOS / Linux
# .venv\Scripts\activate         # Windows

# 4. Install dependencies
pip install -r requirements.txt

# 5. Start the server
python app.py
```

Then open [http://127.0.0.1:5000](http://127.0.0.1:5000) in your browser.

### One-liner (macOS / zsh)

The repo includes a helper script that handles venv creation and activation automatically:

```bash
./scripts/run_flask_backend.sh
```

### Environment variables

| Variable | Default | Description |
|---|---|---|
| `PREPPY_PORT` | `5000` | Port Flask listens on |
| `PREPPY_DEBUG` | _(off)_ | Set to `1`, `true`, `yes`, or `on` to enable Flask debug/reloader |

Example:

```bash
PREPPY_PORT=8080 PREPPY_DEBUG=1 python app.py
```

---

## Desktop app (Tauri wrapper — optional)

The Tauri wrapper bundles the Flask backend into a native macOS app window. It automatically picks an available port, starts Flask, waits for it to be ready, then opens a Tauri window pointing at the local server.

### Additional prerequisites

- [Node.js](https://nodejs.org/) (LTS recommended) + npm
- [Rust toolchain](https://rustup.rs/) (`rustc` + `cargo`)

### Run in dev mode

```bash
cd tauri-wrapper
npm install
npm run tauri:dev
```

### Build a distributable `.app`

```bash
npm run tauri:build
# Output: tauri-wrapper/src-tauri/target/release/bundle/macos/Preppy.app
```

---

## Project structure

```
preppy/
├── app.py                        # Flask app — API routes + PDF parsing + docx generation
├── requirements.txt              # Python dependencies (Flask, pypdf)
├── templates/
│   └── index.html                # Single-page app shell (Jinja2, served by Flask)
├── static/
│   ├── app.js                    # All frontend logic (tabs, library, setlist, export)
│   ├── styles.css                # UI styles
│   └── preppy-logo.svg
├── scripts/
│   └── run_flask_backend.sh      # Helper: creates venv, installs deps, starts Flask
├── tauri-wrapper/                # Optional desktop wrapper
│   ├── package.json
│   ├── app/index.html            # Tauri loading screen (redirected to Flask URL on ready)
│   └── src-tauri/
│       ├── Cargo.toml
│       ├── tauri.conf.json
│       └── src/main.rs           # Rust: starts Flask, polls for readiness, opens window
└── mac/
    └── PreppyLauncher.applescript
```

---

## API reference

### `POST /api/parse-chart`

Accepts a `multipart/form-data` upload with a single field `chart` (PDF file).

Returns JSON:

```json
{
  "song": {
    "title": "Song Title",
    "artist": "Artist Name",
    "arrangement": "Main",
    "key": "G",
    "bpm": "120"
  },
  "sections": [
    { "label": "Intro", "energy": "", "notes": "" },
    { "label": "V1", "energy": "", "notes": "", "repeat": 2 }
  ],
  "source": {
    "filename": "chart.pdf",
    "line_count": 42
  }
}
```

### `POST /api/export-docx`

Accepts JSON:

```json
{
  "lines": ["Prep Sheet Sunday Service", "Song Title [G] - 120 BPM", "↓Intro x2", ...],
  "header_lines": ["Shorthand Key", "Dynamics: ↓=soft ..."],
  "filename": "Sunday Service Prep.docx"
}
```

Returns a `.docx` binary as `application/vnd.openxmlformats-officedocument.wordprocessingml.document`.

---

## Dynamics shorthand

| Symbol | Meaning |
|---|---|
| `↓` | Soft / low energy |
| `→` | Medium / steady |
| `↗` | Build / growing |
| `↑` | Big / loud |

Section lines in the prep sheet are formatted as: `↓Intro x2 - EG hook intro lick`

---

## Notes for contributors

- **No database** — song library and setlists are stored in `localStorage`. This is intentional for the current local-first architecture.
- **No build step** — the frontend is plain JS. Edit `static/app.js` and refresh the browser.
- **Python formatting** — the project uses no linter config yet; follow the existing style (PEP 8).
- **`.docx` is hand-rolled XML** — the Office Open XML is built as raw strings inside `build_docx()` in `app.py`. No `python-docx` dependency is needed.
