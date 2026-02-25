# Deploy Preppy on Vercel (Flask + S3 + Planning Center)

## 1. Prerequisites
- Vercel account
- AWS S3 bucket (for chart PDF storage)
- Planning Center app credentials (`app_id` + `secret`) with Services API access

## 2. Deploy
1. Push this project to GitHub.
2. In Vercel, import the repo.
3. Framework preset: `Other`.
4. Build/Output commands: leave default (Vercel reads `vercel.json`).

## 3. Environment variables (Vercel)
Set these in Project Settings -> Environment Variables:

- `PCO_APP_ID`
- `PCO_SECRET`
- `PCO_API_ROOT` (optional, default: `https://api.planningcenteronline.com`)
- `PREPPY_S3_BUCKET`
- `AWS_REGION`
- `AWS_ACCESS_KEY_ID`
- `AWS_SECRET_ACCESS_KEY`

## 4. What this setup enables
- Upload + parse chart PDFs through Flask.
- Store uploaded chart PDFs in S3 when `PREPPY_S3_BUCKET` is set.
- Pull upcoming plans from Planning Center.
- Import plan songs into Preppy library/setlist references.
- Parse a chart from a Planning Center URL (`/api/pco/parse-plan-chart`).
- Write prep text into a plan notes field when writable (`/api/pco/write-plan-note`).

## 5. Notes
- On Vercel, local filesystem writes are ephemeral. Use environment variables for stable credentials.
- Runtime-saved PCO credentials (`/api/pco/credentials`) are mainly useful for local development.
- Planning Center write access depends on your app permissions and plan field mutability.

## 6. Key endpoints
- `GET /api/storage-status`
- `GET /api/pco/status`
- `GET /api/pco/upcoming-plans`
- `GET /api/pco/import-plan?service_type_id=...&plan_id=...`
- `GET /api/pco/plan-charts?service_type_id=...&plan_id=...`
- `GET /api/pco/parse-plan-chart?service_type_id=...&plan_id=...&chart_url=...&chart_name=...`
- `POST /api/pco/write-plan-note`
