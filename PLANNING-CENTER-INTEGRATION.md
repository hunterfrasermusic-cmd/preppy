# Planning Center Integration Plan

## Overview

This document defines all planned Planning Center Online (PCO) integration work for Preppy. It is structured as a series of self-contained phases, each of which can be assigned to an independent agent or developer. Each phase includes full context, the exact PCO API endpoints involved, the backend changes required, the frontend changes required, and a clear definition of done.

---

## Current State (already built)

The following PCO integration is already complete and in production:

| Feature | File | Description |
|---|---|---|
| PCO OAuth login | `preppy/auth.py` | Full OAuth2 flow, token storage in `users` table |
| Token refresh | `preppy/pco.py` | Auto-refreshes access token before expiry |
| List upcoming plans | `preppy/pco.py` `GET /api/pco/plans` | Fetches all future plans across all service types |
| Import plan as setlist | `preppy/pco.py` `POST /api/pco/import/<id>` | Imports PCO plan items as Preppy setlist + auto-creates songs/arrangements |
| PCO import modal | `static/app.js` | Frontend modal listing upcoming plans with Import button |

### Existing helper in `preppy/pco.py`

All new backend work should reuse these helpers:

```python
_pco_get(uid, path, params=None)   # authenticated GET with auto token refresh
_refresh_token_if_needed(uid)       # returns valid access token
```

For write operations (POST, PUT, PATCH), add a `_pco_post` / `_pco_put` helper following the same pattern.

---

## Database schema (current)

```sql
users          (id, pco_person_id, pco_org_id, name, email, access_token, refresh_token, token_expires_at)
songs          (id, user_id, pco_song_id, title, artist)
arrangements   (id, song_id, pco_arrangement_id, name, key, bpm)
sections       (id, arrangement_id, position, label, energy, notes)
setlists       (id, user_id, pco_plan_id, name, date)
setlist_items  (id, setlist_id, arrangement_id, position)
```

---

## Phase 4 — PCO Song Library Search

### Goal

Let users search the PCO song library and pull any song (with its arrangements) directly into their Preppy library, without going through a service plan.

### Why

Currently songs only enter Preppy via plan import. Many songs in the PCO library have never been scheduled but musicians still want to prep them.

### PCO API endpoints

```
GET /services/v2/songs
  ?filter=search&query=<search_term>
  &per_page=25
  → returns Song objects with title, author, themes, ccli_number

GET /services/v2/songs/<pco_song_id>/arrangements
  → returns Arrangement objects with name, chord_chart_key, bpm, chord_chart (URL to PDF)

GET /services/v2/songs/<pco_song_id>/arrangements/<arr_id>
  → single arrangement detail, includes chord_chart_key, bpm, sequence (section order)
```

### Backend changes

**File: `preppy/pco.py`**

Add three new routes to `pco_bp`:

```
GET /api/pco/songs?q=<query>
  → calls GET /services/v2/songs?filter=search&query=<q>
  → returns [{pcoSongId, title, author, ccliNumber, arrangementCount}]

GET /api/pco/songs/<pco_song_id>/arrangements
  → calls GET /services/v2/songs/<id>/arrangements
  → returns [{pcoArrangementId, name, key, bpm, hasChordChart}]

POST /api/pco/songs/<pco_song_id>/import
  body: { pcoArrangementId: "..." }  (optional — imports all if omitted)
  → upserts song + arrangement(s) into Preppy DB (same logic as plan import)
  → returns { songId, arrangementIds: [...] }
```

Upsert logic (already exists in `import_plan`, extract to shared helper):
- Check `songs.pco_song_id` — if exists, reuse song row
- Check `arrangements.pco_arrangement_id` — if exists, skip
- Otherwise insert

### Frontend changes

**File: `static/app.js`**

Add a "Search Planning Center" button in the Library tab (next to the search filters). Opens a modal:

- Text input → debounced calls to `GET /api/pco/songs?q=<query>`
- Results list: song title, author, arrangement count
- Expand row → shows arrangements with key/BPM
- "Import" button per arrangement → calls `POST /api/pco/songs/<id>/import`
- On success: reload `songLibrary` from `GET /api/songs`, close modal, show toast

**File: `templates/index.html`**

Add "Search Planning Center" button in library panel (only rendered when `db_enabled and user_id`). Add modal shell `#pco-song-search-modal`.

### Definition of done

- [x] Search returns results within 1s for common song names
- [x] Importing a song/arrangement creates rows in `songs` and `arrangements` tables with `pco_song_id` / `pco_arrangement_id` populated
- [x] Re-importing the same song is idempotent (no duplicates)
- [x] Song appears in Preppy library immediately after import
- [x] Works with the existing token refresh flow

---

## Phase 5 — Rich Plan Import (Full Service Order)

### Goal

Improve the existing plan import to capture the full service order structure from PCO, not just songs — including headers, non-song items, and the arrangement's section sequence.

### Why

The current `POST /api/pco/import/<id>` only imports `item_type=song` items and discards everything else. A full service order in PCO might look like:

```
Opening / Welcome       ← header item
Amazing Grace           ← song
How Great Thou Art      ← song
Prayer                  ← item (non-song)
Sermon                  ← item
What A Beautiful Name   ← song
Offering                ← item
```

Preppy should preserve headers and ordering so the exported prep sheet reflects the actual service flow.

### PCO API endpoints

```
GET /services/v2/service_types/<id>/plans/<plan_id>/items
  ?include=song,arrangement
  &per_page=50
  → each item has:
      item_type: "song" | "header" | "item"
      title: string
      sequence: int (order position)
      length: int (seconds)

GET /services/v2/songs/<id>/arrangements/<arr_id>
  → arrangement.sequence: array of section names in order (e.g. ["Intro","V1","C1","V2","C2","Bridge","C3"])
  → use this to pre-populate Preppy sections in the correct order
```

### Backend changes

**File: `preppy/pco.py` — update `import_plan`**

1. Process all item types, not just songs:
   - `item_type=song` → existing song import logic
   - `item_type=header` → store as a special `setlist_item` with no `arrangement_id` (need schema addition — see below)
   - `item_type=item` → same as header

2. For song items: fetch the arrangement's `sequence` array and use it to pre-populate `sections` rows in order (labels only, energy/notes empty)

**File: `migrations/002_setlist_item_headers.sql`** *(new)*

```sql
-- Allow setlist items to be headers/notes rather than arrangements
ALTER TABLE setlist_items
  ADD COLUMN item_type TEXT NOT NULL DEFAULT 'song',
  ADD COLUMN label     TEXT,
  ALTER COLUMN arrangement_id DROP NOT NULL;
```

**File: `preppy/api.py` — update `list_setlists`**

Return non-song items in the `items` array with `{ itemType: "header", label: "..." }` shape.

### Frontend changes

**File: `static/app.js`**

Update `renderSetlistUI` to render header items as non-draggable dividers in the setlist (styled differently from song rows).

Update `saveCurrentSetlistSnapshot` to include header items in the items payload.

### Definition of done

- [x] Imported setlist preserves full service order including headers
- [x] Song sections are pre-populated from PCO arrangement sequence
- [x] Header items render as visual dividers in the setlist UI
- [x] Existing setlists without headers are unaffected

---

## Phase 6 — Upload Prep Sheet to PCO Plan

### Goal

After generating a prep sheet, let the user upload the `.docx` file directly to the corresponding PCO plan as an attachment under "Files", so the whole team can access it from PCO without any manual file transfer.

### Why

Currently the workflow is: generate in Preppy → download `.docx` → manually upload to PCO plan. This phase collapses that to a single button click.

### PCO API endpoints

PCO uses a **two-step file upload** process (discovered via [issue #325](https://github.com/planningcenter/developers/issues/325)):

```
Step 1: Upload file → get UUID
POST https://upload.planningcenteronline.com/v2/files
  Content-Type: multipart/form-data
  Authorization: Bearer <token>
  body: file=<binary>
  → returns { "data": { "id": "<uuid>", ... } }

Step 2: Create attachment on plan using UUID
POST /services/v2/service_types/<id>/plans/<plan_id>/attachments
  Content-Type: application/json
  body: { "data": { "type": "Attachment", "attributes": { "file_upload_identifier": "<uuid>", "filename": "Prep Sheet.docx" } } }
  → returns Attachment object with { id, filename, url }
```

Notes:
- Requires `services` OAuth scope (already requested)
- File size limit: 25MB (well within .docx range)
- PCO stores the file and makes it available to all team members with plan access

### Backend changes

**File: `preppy/pco.py`**

Add two helpers for the two-step process:
- `_pco_upload_file(uid, filename, file_bytes, content_type)` — uploads to `upload.planningcenteronline.com`, returns UUID
- `_pco_create_attachment(uid, path, upload_id, filename)` — creates JSON API attachment record using the UUID

Add new route:

```
POST /api/pco/plans/<pco_plan_id>/upload-prep-sheet
  body: multipart/form-data
    file: .docx binary
    serviceTypeId: string
    filename: string

  → Step 1: uploads file to PCO upload service
  → Step 2: creates attachment on the plan
  → returns { attachmentId, url }
```

**Note:** The `pco_plan_id` must be stored on the setlist row (already is: `setlists.pco_plan_id`). The `serviceTypeId` is not currently stored — it needs to be added:

**File: `migrations/003_setlist_service_type.sql`** *(new)*

```sql
ALTER TABLE setlists ADD COLUMN pco_service_type_id TEXT;
```

Update `import_plan` in `preppy/pco.py` to store `serviceTypeId` when creating the setlist row.

### Frontend changes

**File: `static/app.js`**

In the setlist panel, after the "Download .docx" button, add a "Upload to Planning Center" button. Only show it when:
1. `PREPPY_CONFIG.dbEnabled` is true
2. The current setlist has a `pco_plan_id` (i.e. it was imported from PCO)

Button click flow:
1. Call existing docx export logic to get the binary blob
2. Build a `FormData` with the blob + `serviceTypeId` + `filename`
3. `POST /api/pco/plans/<pco_plan_id>/upload-prep-sheet`
4. Show success status with a link to the PCO plan

**File: `templates/index.html`**

No structural changes needed — the button is added dynamically by `app.js`.

### Definition of done

- [x] "Upload to Planning Center" button only appears for setlists imported from PCO
- [x] Clicking it generates the docx and uploads without a separate download step
- [x] The file appears in PCO under the plan's Files/Attachments tab
- [x] Upload errors are surfaced to the user with the PCO error message
- [x] `pco_service_type_id` is stored on setlist rows going forward

---

## Phase 7 — PCO Sync (keep Preppy in sync with PCO changes)

### Goal

When a PCO plan is updated (songs reordered, songs swapped, key changes), reflect those changes in the corresponding Preppy setlist without a full re-import.

### Why

Plans evolve in PCO throughout the week. Currently once imported, a Preppy setlist is a static snapshot. This phase adds a "Sync with PCO" action.

### PCO API endpoints

Same as Phase 5 (`GET .../plans/<id>/items?include=song,arrangement`) plus:

```
GET /services/v2/service_types/<id>/plans/<plan_id>
  → plan.attributes.updated_at  ← use to check if sync is needed
```

### Backend changes

**File: `preppy/pco.py`**

Add route:

```
POST /api/pco/plans/<pco_plan_id>/sync
  body: { serviceTypeId: string, setlistId: int }

  Logic:
  1. Fetch current plan items from PCO
  2. For each song item: upsert song/arrangement (same as import)
  3. Replace setlist_items for this setlist with the new ordered list
  4. Preserve existing section notes/energy — only update order and metadata (key, bpm)
  5. Return { setlistId, changes: { added: n, removed: n, reordered: bool } }
```

Key constraint: **do not overwrite section notes or energy values** the user has already entered. Only update `key`, `bpm`, and `position`.

### Frontend changes

**File: `static/app.js`**

Add "Sync with PCO" button next to "Upload to Planning Center" (only for PCO-sourced setlists). Show a diff summary after sync: "2 songs added, 1 removed, order updated."

### Definition of done

- [x] Sync updates song order and metadata without destroying user-entered notes
- [x] New songs added to the PCO plan are added to the Preppy setlist
- [x] Songs removed from the PCO plan are removed from the setlist
- [x] A summary of changes is shown to the user

---

## Implementation order

```
Phase 4 (Song Search)     ← COMPLETE
Phase 5 (Rich Import)     ← COMPLETE
Phase 6 (Upload to PCO)   ← COMPLETE (corrected to use PCO two-step file upload)
Phase 7 (Sync)            ← COMPLETE
```

### Enhancement: Arrange Once, Auto-Populate

Added to `_upsert_pco_song` in `preppy/pco.py`: when a new arrangement is created from a PCO import, the system checks for any existing arrangement with the same `pco_arrangement_id` that already has sections (energy, notes, labels). If found, the sections are copied to the new arrangement automatically. This means arranging a song once in Preppy propagates to all future imports of plans containing that arrangement. Prefers the current user's data, then falls back to any user in the DB.

---

## Key files reference

| File | Purpose |
|---|---|
| `preppy/pco.py` | All PCO API routes + helpers. Add new routes here. |
| `preppy/api.py` | Preppy CRUD API. Update if DB schema changes affect list endpoints. |
| `preppy/db.py` | DB connection pool + migration runner. |
| `migrations/` | SQL migration files. Name sequentially: `002_...sql`, `003_...sql` |
| `static/app.js` | All frontend logic. Modal patterns follow `openPcoImportModal` / `loadPcoPlans`. |
| `templates/index.html` | HTML shell. Add modal divs here; wire them in `app.js`. |
| `static/styles.css` | Styles. Modal CSS pattern is at the bottom of the file. |

---

## PCO API reference links

- Services API overview: `https://developer.planning.center/docs/#/apps/services`
- Songs: `GET /services/v2/songs`
- Plans: `GET /services/v2/service_types/{id}/plans`
- Plan items: `GET /services/v2/service_types/{id}/plans/{id}/items`
- Attachments: `POST /services/v2/service_types/{id}/plans/{id}/attachments`
- OAuth scopes needed: `people services` (already configured)
