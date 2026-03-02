#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::net::{TcpListener, TcpStream};
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::thread;
use std::time::{Duration, Instant};

use tauri::{AppHandle, Manager};

const BACKEND_HOST: &str = "127.0.0.1";

struct BackendState(Mutex<Option<Child>>);

fn project_root_from_manifest() -> PathBuf {
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("../..")
        .canonicalize()
        .unwrap_or_else(|_| PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../.."));
    root
}

fn pick_available_port() -> Result<u16, String> {
    let listener =
        TcpListener::bind((BACKEND_HOST, 0)).map_err(|err| format!("Could not bind localhost: {}", err))?;
    let port = listener
        .local_addr()
        .map_err(|err| format!("Could not read local address: {}", err))?
        .port();
    drop(listener);
    Ok(port)
}

fn start_backend(port: u16) -> Result<Child, String> {
    let project_root = project_root_from_manifest();
    let script_path = project_root.join("scripts/run_flask_backend.sh");
    if !script_path.exists() {
        return Err(format!("Backend script missing: {}", script_path.display()));
    }

    Command::new("zsh")
        .arg(script_path)
        .current_dir(project_root)
        .env("PREPPY_PORT", port.to_string())
        .env("PREPPY_DEBUG", "0")
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .map_err(|err| format!("Could not start Flask backend: {}", err))
}

fn wait_for_backend(port: u16, timeout: Duration) -> bool {
    let start = Instant::now();
    while start.elapsed() < timeout {
        if TcpStream::connect((BACKEND_HOST, port)).is_ok() {
            return true;
        }
        thread::sleep(Duration::from_millis(120));
    }
    false
}

fn stop_backend(app: &AppHandle) {
    if let Some(state) = app.try_state::<BackendState>() {
        if let Ok(mut guard) = state.0.lock() {
            if let Some(mut child) = guard.take() {
                let _ = child.kill();
                let _ = child.wait();
            }
        }
    }
}

fn main() {
    tauri::Builder::default()
        .manage(BackendState(Mutex::new(None)))
        .setup(|app| {
            let port = pick_available_port()?;
            let child = start_backend(port)?;
            {
                let state = app.state::<BackendState>();
                let mut guard = state
                    .0
                    .lock()
                    .map_err(|_| "Backend state lock poisoned".to_string())?;
                *guard = Some(child);
            }

            if !wait_for_backend(port, Duration::from_secs(15)) {
                stop_backend(app.handle());
                return Err("Flask backend did not become ready in time.".into());
            }

            let url = format!("http://{}:{}/", BACKEND_HOST, port);
            if let Some(window) = app.get_webview_window("main") {
                let script = format!("window.location.replace('{}');", url);
                let _ = window.eval(script.as_str());
            }

            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building tauri application")
        .run(|app_handle, event| {
            if let tauri::RunEvent::Exit { .. } = event {
                stop_backend(app_handle);
            }
        });
}
