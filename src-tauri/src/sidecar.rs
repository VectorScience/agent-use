// Spawn the FastAPI backend as a child process on desktop.
//
// Strategy: locate `python` (Windows) or `python3` (Unix), then run
// `python -m server.main --port <port>` from the project root. We keep a
// handle on the child so it dies with the app.
//
// Why not bundle Python itself: Tauri sidecars via PyOxidizer add ~30MB and
// complicate cross-compilation. The user already has Python for `click_send.py`,
// so we reuse it.

use std::process::{Child, Command, Stdio};
use tauri::{AppHandle, Manager};

/// Try to start the server. Returns the child on success so the caller can
/// hold it alive. Errors are logged but never fatal — the UI still works if
/// the user started the server themselves.
pub fn start_server(_app: &AppHandle) -> Option<Child> {
    let project_root = find_project_root()?;
    let server_dir = project_root.join("server");
    if !server_dir.exists() {
        eprintln!("[sidecar] server/ not found under project root, skipping");
        return None;
    }

    let python = locate_python()?;
    let port = "8000";

    let mut cmd = Command::new(&python);
    cmd.arg("-m")
        .arg("server.main")
        .arg("--port")
        .arg(port)
        .current_dir(&project_root)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null());

    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        // Detach so a Ctrl-C in a parent terminal doesn't kill our server.
        const CREATE_NEW_PROCESS_GROUP: u32 = 0x00000200;
        const DETACHED_PROCESS: u32 = 0x00000008;
        cmd.creation_flags(CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS);
    }

    match cmd.spawn() {
        Ok(child) => {
            eprintln!("[sidecar] FastAPI server started on port {}", port);
            Some(child)
        }
        Err(e) => {
            eprintln!("[sidecar] failed to spawn server: {}", e);
            None
        }
    }
}

fn locate_python() -> Option<String> {
    for candidate in ["python", "python3", "py"] {
        if Command::new(candidate)
            .arg("--version")
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .status()
            .is_ok()
        {
            return Some(candidate.to_string());
        }
    }
    None
}

fn find_project_root() -> Option<std::path::PathBuf> {
    // The Tauri binary lives in src-tauri/target/{debug,release}/; project
    // root is three levels up. In dev (cargo run) it's the same.
    let exe = std::env::current_exe().ok()?;
    let mut dir = exe.parent()?;
    for _ in 0..6 {
        if dir.join("server").is_dir() && dir.join("pyproject.toml").is_file() {
            return Some(dir.to_path_buf());
        }
        dir = dir.parent()?;
    }
    None
}
