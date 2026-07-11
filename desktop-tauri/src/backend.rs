use crate::AppState;
use std::process::{Command, Stdio};
use std::time::Duration;

pub async fn start_all(state: &AppState) -> Result<(), String> {
    let galaxyos_port = state.galaxyos_port;
    let studio_port = state.studio_port;

    let galaxyos_child = start_galaxyos_mcp(galaxyos_port)?;
    *state.galaxyos_process.lock().map_err(|e| e.to_string())? = Some(galaxyos_child);

    wait_for_health(galaxyos_port, "galaxyos-mcp", 30).await?;

    let studio_child = start_studio(studio_port)?;
    *state.studio_process.lock().map_err(|e| e.to_string())? = Some(studio_child);

    wait_for_health(studio_port, "studio", 60).await?;

    log::info!("All backends started: galaxyos=:{} studio=:{}", galaxyos_port, studio_port);
    Ok(())
}

pub fn stop_all(state: &AppState) -> Result<(), String> {
    if let Ok(mut guard) = state.studio_process.lock() {
        if let Some(ref mut child) = *guard {
            let _ = child.kill();
        }
        *guard = None;
    }
    if let Ok(mut guard) = state.galaxyos_process.lock() {
        if let Some(ref mut child) = *guard {
            let _ = child.kill();
        }
        *guard = None;
    }
    log::info!("All backends stopped");
    Ok(())
}

fn start_galaxyos_mcp(port: u16) -> Result<std::process::Child, String> {
    let python = find_python()?;
    Command::new(&python)
        .args(["-m", "galaxyos.kernel.mcp_server_entry"])
        .env("GALAXYOS_MODE", "desktop")
        .env("GALAXYOS_MCP_TRANSPORT", "sse")
        .env("GALAXYOS_MCP_PORT", &port.to_string())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|e| format!("Failed to start GalaxyOS MCP: {}", e))
}

fn start_studio(port: u16) -> Result<std::process::Child, String> {
    let python = find_python()?;
    let module = find_studio_module()?;
    Command::new(&python)
        .args(["-m", &module])
        .env("PORT", &port.to_string())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|e| format!("Failed to start Studio: {}", e))
}

fn find_python() -> Result<String, String> {
    for name in &["python3", "python"] {
        if Command::new(name)
            .arg("--version")
            .output()
            .is_ok()
        {
            return Ok(name.to_string());
        }
    }
    Err("Python not found".into())
}

fn find_studio_module() -> Result<String, String> {
    Ok("openjiuwen_studio.server.main".to_string())
}

async fn wait_for_health(port: u16, name: &str, max_secs: u64) -> Result<(), String> {
    let client = reqwest::Client::new();
    let url = format!("http://127.0.0.1:{}/health", port);
    for i in 0..max_secs {
        if let Ok(resp) = client.get(&url).timeout(Duration::from_secs(2)).send().await {
            if resp.status().is_success() {
                log::info!("{} health check passed after {}s", name, i);
                return Ok(());
            }
        }
        tokio::time::sleep(Duration::from_secs(1)).await;
    }
    Err(format!("{} health check timed out after {}s", name, max_secs))
}