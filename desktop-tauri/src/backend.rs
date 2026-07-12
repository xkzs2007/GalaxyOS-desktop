use crate::AppState;
use std::process::{Command, Stdio};
use std::time::Duration;

#[cfg(windows)]
use std::os::windows::process::CommandExt;

#[cfg(windows)]
const CREATE_NO_WINDOW: u32 = 0x08000000;

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
    let binary = find_galaxyos_binary()?;
    let mut cmd = Command::new(&binary);
    cmd.args(["--transport", "sse", "--host", "127.0.0.1", "--port", &port.to_string()])
        .env("GALAXYOS_MODE", "desktop")
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    #[cfg(windows)]
    cmd.creation_flags(CREATE_NO_WINDOW);
    cmd.spawn().map_err(|e| format!("Failed to start GalaxyOS MCP: {}", e))
}

fn start_studio(port: u16) -> Result<std::process::Child, String> {
    let python = find_python()?;
    let module = find_studio_module()?;
    let mut cmd = Command::new(&python);
    cmd.args(["-m", &module])
        .env("PORT", &port.to_string())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    #[cfg(windows)]
    cmd.creation_flags(CREATE_NO_WINDOW);
    cmd.spawn().map_err(|e| format!("Failed to start Studio: {}", e))
}

fn find_galaxyos_binary() -> Result<String, String> {
    if let Ok(exe_dir) = std::env::current_exe() {
        if let Some(dir) = exe_dir.parent() {
            let bundled = dir.join("galaxyos-mcp");
            let bundled_exe = dir.join("galaxyos-mcp.exe");
            if bundled.exists() {
                return Ok(bundled.to_string_lossy().to_string());
            }
            if bundled_exe.exists() {
                return Ok(bundled_exe.to_string_lossy().to_string());
            }
            let resources_dir = dir.join("resources").join("galaxyos-mcp");
            let resources_exe = dir.join("resources").join("galaxyos-mcp.exe");
            if resources_dir.exists() {
                return Ok(resources_dir.to_string_lossy().to_string());
            }
            if resources_exe.exists() {
                return Ok(resources_exe.to_string_lossy().to_string());
            }
        }
    }
    let python = find_python()?;
    Ok(python)
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
