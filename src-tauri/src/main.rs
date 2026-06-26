// Cursor Remote Tauri shell.
//
// Two responsibilities:
// 1. On desktop (Windows): spawn the bundled Python FastAPI server as a sidecar
//    process, then point the webview at it.
// 2. On Android: the Python backend can't run on-device, so we expect the user
//    to be running it on their PC. The app is a thin client that connects to
//    a configurable host (defaults to LAN auto-detect later).
//
// We don't bundle Python via PyOxidizer (overkill). Instead, on first run we
// look for `python`/`python3` on PATH; if missing, the webview still works
// when the user manually starts the server.

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod sidecar;

use tauri::Manager;

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .setup(|app| {
            #[cfg(desktop)]
            {
                let _ = sidecar::start_server(app.handle());
            }
            #[cfg(not(desktop))]
            {
                let _ = app;
            }
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
