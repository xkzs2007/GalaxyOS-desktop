use std::sync::Mutex;
use tauri::Manager;

mod backend;
mod commands;
mod dsl_bridge;
mod eui_neo;
mod eui_neo_ffi;
mod i18n_bridge;
mod render_channel;
mod render_pipeline;
mod spring_animation;
mod sse_client;
mod tokui_renderer;

use eui_neo::{EuiNeoContext, NativeRenderSurfaceManager};
use i18n_bridge::EuiNeoI18nBridge;
use render_pipeline::RenderPipeline;
use dsl_bridge::DslBridge;

pub struct AppState {
    galaxyos_process: Mutex<Option<std::process::Child>>,
    galaxyos_port: u16,
    locale: Mutex<String>,
    eui_neo_context: Mutex<EuiNeoContext>,
    i18n_bridge: Mutex<EuiNeoI18nBridge>,
    render_pipeline: Mutex<RenderPipeline>,
    dsl_bridge: Mutex<DslBridge>,
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let surface_manager = NativeRenderSurfaceManager::new();
    let native_available = eui_neo_ffi::probe_native();

    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_process::init())
        .manage(AppState {
            galaxyos_process: Mutex::new(None),
            galaxyos_port: 8765,
            locale: Mutex::new("zh".into()),
            eui_neo_context: Mutex::new(EuiNeoContext {
                native_available,
                surfaces: std::collections::HashMap::new(),
                surface_manager,
                panel_spring: eui_neo::SpringAnimationConfig::panel_spring(),
                momentum_spring: eui_neo::SpringAnimationConfig::momentum_spring(),
                cognitive_state: eui_neo::CognitivePanelState::default(),
            }),
            i18n_bridge: Mutex::new(EuiNeoI18nBridge::new()),
            render_pipeline: Mutex::new(RenderPipeline::new(native_available)),
            dsl_bridge: Mutex::new(DslBridge::new()),
        })
        .invoke_handler(tauri::generate_handler![
            commands::start_backends,
            commands::stop_backends,
            commands::check_health,
            commands::get_locale,
            commands::set_locale,
            commands::get_supported_locales,
            commands::request_cognitive_data,
            commands::sse_stream_chat,
            commands::render_pipeline_status,
            eui_neo::render_native,
            eui_neo::create_surface,
            eui_neo::destroy_surface,
            eui_neo::update_surface,
            eui_neo::check_eui_neo_health,
            eui_neo::rebuild_surface,
            eui_neo::open_cognitive_overlay,
            eui_neo::close_cognitive_overlay,
            eui_neo::get_memory_stats,
            eui_neo::rccam_control,
            eui_neo::get_dag_tree,
            eui_neo::search_memory,
            eui_neo::set_panel_layout,
        ])
        .setup(|app| {
            let handle = app.handle().clone();
            tauri::async_runtime::spawn(async move {
                let state = handle.state::<AppState>();
                let _ = backend::start_all(&state, &handle).await;
            });

            let window = app
                .get_webview_window("main")
                .expect("main window not found");
            let inject_script = include_str!("inject_cognitive_panel.js");
            let _ = window.eval(inject_script);

            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
