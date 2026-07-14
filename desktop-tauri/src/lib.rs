use tauri::Manager;
use std::sync::Mutex;

mod backend;
mod commands;

pub struct AppState {
    studio_process: Mutex<Option<std::process::Child>>,
    galaxyos_process: Mutex<Option<std::process::Child>>,
    studio_port: u16,
    galaxyos_port: u16,
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_process::init())
        .manage(AppState {
            studio_process: Mutex::new(None),
            galaxyos_process: Mutex::new(None),
            studio_port: 8000,
            galaxyos_port: 8765,
        })
        .invoke_handler(tauri::generate_handler![
            commands::start_backends,
            commands::stop_backends,
            commands::check_health,
        ])
        .setup(|app| {
            let handle = app.handle().clone();
            tauri::async_runtime::spawn(async move {
                let state = handle.state::<AppState>();
                let _ = backend::start_all(&state, &handle).await;
            });
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
