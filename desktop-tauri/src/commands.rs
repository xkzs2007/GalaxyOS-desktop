use crate::AppState;

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
    let galaxyos_port = state.galaxyos_port;
    let client = reqwest::Client::new();

    let agentserver_ok = client
        .get(format!("http://127.0.0.1:{}/health", swarm_port))
        .timeout(std::time::Duration::from_secs(2))
        .send()
        .await
        .map(|r| r.status().is_success())
        .unwrap_or(false);

    let galaxyos_ok = client
        .get(format!("http://127.0.0.1:{}/health", galaxyos_port))
        .timeout(std::time::Duration::from_secs(2))
        .send()
        .await
        .map(|r| r.status().is_success())
        .unwrap_or(false);

    Ok(serde_json::json!({
        "agentserver": agentserver_ok,
        "galaxyos": galaxyos_ok,
    }))
}
