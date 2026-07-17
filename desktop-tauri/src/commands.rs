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

#[tauri::command]
pub async fn request_cognitive_data(tab: String, state: tauri::State<'_, AppState>) -> Result<serde_json::Value, String> {
    let locale = state.locale.lock().map_err(|e| e.to_string())?.clone();

    let html = match tab.as_str() {
        "memory" => {
            let title = if locale == "zh" { "液态神经记忆" } else { "Liquid Neural Memory" };
            format!("<div style='padding:8px'><h4 style='margin:0 0 8px'>{}</h4><p style='color:#999;font-size:12px'>{}</p></div>",
                title,
                if locale == "zh" { "等待 JiuwenSwarm Agent 数据..." } else { "Waiting for JiuwenSwarm Agent data..." }
            )
        }
        "rccam" => {
            let title = if locale == "zh" { "R-CCAM 认知循环" } else { "R-CCAM Loop" };
            format!("<div style='padding:8px'><h4 style='margin:0 0 8px'>{}</h4><p style='color:#999;font-size:12px'>{}</p></div>",
                title,
                if locale == "zh" { "等待认知循环状态..." } else { "Waiting for cognitive loop state..." }
            )
        }
        "dag" => {
            let title = if locale == "zh" { "DAG 上下文树" } else { "DAG Context Tree" };
            format!("<div style='padding:8px'><h4 style='margin:0 0 8px'>{}</h4><p style='color:#999;font-size:12px'>{}</p></div>",
                title,
                if locale == "zh" { "等待上下文节点数据..." } else { "Waiting for context node data..." }
            )
        }
        "search" => {
            let placeholder = if locale == "zh" { "搜索记忆..." } else { "Search memories..." };
            let btn_text = if locale == "zh" { "搜索" } else { "Search" };
            format!("<div style='padding:8px'><div style='display:flex;gap:6px'><input type='text' placeholder='{}' style='flex:1;padding:6px 10px;font-size:13px;border:1px solid #ccc;border-radius:4px' /><button style='padding:6px 14px;font-size:13px;background:#1976d2;color:#fff;border:none;border-radius:4px;cursor:pointer'>{}</button></div></div>",
                placeholder, btn_text
            )
        }
        _ => format!("<div style='padding:8px;color:#999'>Unknown tab: {}</div>", tab),
    };

    Ok(serde_json::json!({ "html": html, "tab": tab, "locale": locale }))
}
