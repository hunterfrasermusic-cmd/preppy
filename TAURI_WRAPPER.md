# Preppy Tauri Wrapper (Wrapper-First Track)

## Branching
- Wrapper work stays on: `main`
- Cloud/PCO prototype is isolated on: `codex/cloud-pco-prototype`
  - commit: `da841ee`

## What this wrapper does
- Starts Flask backend via `scripts/run_flask_backend.sh`
- Waits for backend readiness on `127.0.0.1:5000`
- Opens a native Tauri window and navigates to local Preppy URL
- Kills backend when app exits

## Files
- `scripts/run_flask_backend.sh`
- `tauri-wrapper/package.json`
- `tauri-wrapper/app/index.html`
- `tauri-wrapper/src-tauri/Cargo.toml`
- `tauri-wrapper/src-tauri/build.rs`
- `tauri-wrapper/src-tauri/tauri.conf.json`
- `tauri-wrapper/src-tauri/src/main.rs`
- `Run Preppy Tauri.command`

## Prerequisites
- Node.js + npm (or local `.toolchain/node`)
- Rust toolchain (`cargo`, `rustc`) (or local `.toolchain/cargo`)
- Python 3 (already used by existing Flask app)

## Run (Dev)
1. Install prerequisites.
2. In terminal:
   - `cd /Users/hfraser/Documents/New project/tauri-wrapper`
   - `npm install`
   - `npm run tauri:dev`

Or use launcher:
- Double-click `/Users/hfraser/Documents/New project/Run Preppy Tauri.command`

## Notes
- This is a local wrapper-first iteration and expects project files to remain in this workspace path.
- Flask now respects `PREPPY_PORT` and `PREPPY_DEBUG` env vars for wrapper control.
- The launcher auto-detects local toolchains in `.toolchain/` first.
- Built app bundle path:
  - `/Users/hfraser/Documents/New project/tauri-wrapper/src-tauri/target/release/bundle/macos/Preppy.app`
