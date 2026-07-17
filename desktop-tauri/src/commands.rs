use crate::AppState;
use tauri::Emitter;

#[tauri::command]
pub async fn start_backends(state: tauri::State<'_, AppState>, handle: tauri::AppHandle) -> Result<String, String> {
    crate::backend::start_all(&state, &handle).await?;
    Ok("ok".into())
}

#[tauri::command]
pub async fn stop_backends(state: tauri::State<'_, AppState>) -> Result<String, String> {
    crate::backend::stop_all(&state)?;
    Ok("ok".into())
}

#[tauri::command]
pub async fn check_health(state: tauri::State<'_, AppState>) -> Result<serde_json::Value, String> {
    let swarm_port = state.swarm_port;
    let gateway_port = state.gateway_port;
    let galaxyos_port = state.galaxyos_port;

    let agentserver_ok = tokio::net::TcpStream::connect(format!("127.0.0.1:{}", swarm_port))
        .await
        .is_ok();

    let gateway_ok = tokio::net::TcpStream::connect(format!("127.0.0.1:{}", gateway_port))
        .await
        .is_ok();

    let galaxyos_ok = tokio::net::TcpStream::connect(format!("127.0.0.1:{}", galaxyos_port))
        .await
        .is_ok();

    Ok(serde_json::json!({
        "agentserver": agentserver_ok,
        "gateway": gateway_ok,
        "galaxyos": galaxyos_ok,
        "eui_neo": {
            "status": "unavailable",
            "native_render_available": false,
        },
    }))
}

#[tauri::command]
pub async fn get_locale(state: tauri::State<'_, AppState>) -> Result<String, String> {
    let locale = state.locale.lock().map_err(|e| e.to_string())?;
    Ok(locale.clone())
}

#[tauri::command]
pub async fn set_locale(locale: String, state: tauri::State<'_, AppState>, handle: tauri::AppHandle) -> Result<String, String> {
    let supported = vec!["zh", "en"];
    if !supported.contains(&locale.as_str()) {
        return Err(format!("Unsupported locale: {}", locale));
    }
    {
        let mut l = state.locale.lock().map_err(|e| e.to_string())?;
        *l = locale.clone();
    }
    {
        let mut bridge = state.i18n_bridge.lock().map_err(|e| e.to_string())?;
        bridge.set_locale(&locale);
    }
    if let Err(e) = handle.emit("galaxyos://locale-changed", &locale) {
        log::warn!("Failed to emit locale_changed: {}", e);
    }
    Ok(locale)
}

#[tauri::command]
pub async fn get_supported_locales() -> Result<serde_json::Value, String> {
    Ok(serde_json::json!(["zh", "en"]))
}
