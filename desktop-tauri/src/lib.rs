use tauri::Manager;
use std::sync::Mutex;

mod backend;
mod commands;
mod eui_neo;
mod i18n_bridge;

use eui_neo::{EuiNeoContext, NativeRenderSurfaceManager};
use i18n_bridge::EuiNeoI18nBridge;

pub struct AppState {
    swarm_agentserver: Mutex<Option<std::process::Child>>,
    swarm_gateway: Mutex<Option<std::process::Child>>,
    galaxyos_process: Mutex<Option<std::process::Child>>,
    swarm_port: u16,
    gateway_port: u16,
    galaxyos_port: u16,
    locale: Mutex<String>,
    eui_neo_context: Mutex<EuiNeoContext>,
    i18n_bridge: Mutex<EuiNeoI18nBridge>,
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let surface_manager = NativeRenderSurfaceManager::new();
    let native_available = false;

    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_process::init())
        .manage(AppState {
            swarm_agentserver: Mutex::new(None),
            swarm_gateway: Mutex::new(None),
            galaxyos_process: Mutex::new(None),
            swarm_port: 18092,
            gateway_port: 19000,
            galaxyos_port: 8765,
            locale: Mutex::new("zh".into()),
            eui_neo_context: Mutex::new(EuiNeoContext {
                native_available,
                surfaces: std::collections::HashMap::new(),
                surface_manager,
            }),
            i18n_bridge: Mutex::new(EuiNeoI18nBridge::new()),
        })
        .invoke_handler(tauri::generate_handler![
            commands::start_backends,
            commands::stop_backends,
            commands::check_health,
            commands::get_locale,
            commands::set_locale,
            commands::get_supported_locales,
            commands::request_cognitive_data,
            eui_neo::render_native,
            eui_neo::create_surface,
            eui_neo::destroy_surface,
            eui_neo::update_surface,
            eui_neo::check_eui_neo_health,
            eui_neo::rebuild_surface,
        ])
        .setup(|app| {
            let handle = app.handle().clone();
            tauri::async_runtime::spawn(async move {
                let state = handle.state::<AppState>();
                let _ = backend::start_all(&state, &handle).await;
            });

            let window = app.get_webview_window("main").expect("main window not found");
            let inject_script = include_str!("inject_cognitive_panel.js");
            let _ = window.eval(inject_script);

            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
