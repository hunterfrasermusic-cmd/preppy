#!/bin/zsh
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
TAURI_DIR="$PROJECT_DIR/tauri-wrapper"
LOCAL_NODE_BIN="$PROJECT_DIR/.toolchain/node/bin"
LOCAL_CARGO_BIN="$PROJECT_DIR/.toolchain/cargo/bin"

if [ -d "$LOCAL_NODE_BIN" ]; then
  export PATH="$LOCAL_NODE_BIN:$PATH"
fi

if [ -d "$LOCAL_CARGO_BIN" ]; then
  export PATH="$LOCAL_CARGO_BIN:$PATH"
fi

if [ -d "$PROJECT_DIR/.toolchain/rustup" ]; then
  export RUSTUP_HOME="$PROJECT_DIR/.toolchain/rustup"
fi

if [ -d "$PROJECT_DIR/.toolchain/cargo" ]; then
  export CARGO_HOME="$PROJECT_DIR/.toolchain/cargo"
fi

if ! command -v node >/dev/null 2>&1; then
  /usr/bin/osascript -e 'display alert "Node.js missing" message "Install Node.js first (https://nodejs.org), then run this launcher again." as critical'
  exit 1
fi

if ! command -v cargo >/dev/null 2>&1; then
  /usr/bin/osascript -e 'display alert "Rust toolchain missing" message "Install Rust (https://rustup.rs), then run this launcher again." as critical'
  exit 1
fi

cd "$TAURI_DIR"

if [ ! -d "node_modules" ]; then
  npm install
fi

npm run tauri:dev
