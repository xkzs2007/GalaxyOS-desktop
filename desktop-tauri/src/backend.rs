use crate::AppState;
use std::process::{Command, Stdio};
use std::time::Duration;
use tauri::{AppHandle, Emitter};

#[cfg(windows)]
use std::os::windows::process::CommandExt;

#[cfg(windows)]
const CREATE_NO_WINDOW: u32 = 0x08000000;

struct BinaryResult {
    path: String,
    is_python_fallback: bool,
}

#[derive(serde::Serialize, Clone)]
struct StartupStatusEvent {
    stage: String,
    mcp_healthy: bool,
    agentserver_healthy: bool,
    swarm_healthy: bool,
    gateway_healthy: bool,
    agent_core_available: bool,
    fallback_active: bool,
    error: Option<StartupError>,
}

#[derive(serde::Serialize, Clone)]
struct StartupError {
    stage: String,
    error_type: String,
    message: String,
    suggestion: String,
}

fn emit_status(handle: &AppHandle, event: StartupStatusEvent) {
    if let Err(e) = handle.emit("galaxyos://startup-status", &event) {
        log::warn!("Failed to emit startup status: {}", e);
    }
}

pub async fn start_all(state: &AppState, handle: &AppHandle) -> Result<(), String> {
    let galaxyos_port = state.galaxyos_port;
    let swarm_port = state.swarm_port;

    emit_status(handle, StartupStatusEvent {
        stage: "McpStarting".into(),
        mcp_healthy: false,
        agentserver_healthy: false,
        swarm_healthy: false,
        gateway_healthy: false,
        agent_core_available: false,
        fallback_active: false,
        error: None,
    });

    let galaxyos_child = match start_galaxyos_mcp(galaxyos_port) {
        Ok(child) => child,
        Err(e) => {
            emit_status(handle, StartupStatusEvent {
                stage: "Failed".into(),
                mcp_healthy: false,
                agentserver_healthy: false,
                swarm_healthy: false,
                gateway_healthy: false,
                agent_core_available: false,
                fallback_active: false,
                error: Some(StartupError {
                    stage: "McpStarting".into(),
                    error_type: "process_spawn".into(),
                    message: e.clone(),
                    suggestion: "Check if galaxyos-mcp binary or Python is available".into(),
                }),
            });
            return Err(e);
        }
    };
    *state.galaxyos_process.lock().map_err(|e| e.to_string())? = Some(galaxyos_child);

    match wait_for_health(galaxyos_port, "galaxyos-mcp", 30).await {
        Ok(_) => {
            emit_status(handle, StartupStatusEvent {
                stage: "McpReady".into(),
                mcp_healthy: true,
                agentserver_healthy: false,
                swarm_healthy: false,
                gateway_healthy: false,
                agent_core_available: false,
                fallback_active: false,
                error: None,
            });
        }
        Err(e) => {
            emit_status(handle, StartupStatusEvent {
                stage: "Failed".into(),
                mcp_healthy: false,
                agentserver_healthy: false,
                swarm_healthy: false,
                gateway_healthy: false,
                agent_core_available: false,
                fallback_active: false,
                error: Some(StartupError {
                    stage: "McpStarting".into(),
                    error_type: "health_timeout".into(),
                    message: e.clone(),
                    suggestion: "Check if MCP Server port 8765 is accessible".into(),
                }),
            });
            return Err(e);
        }
    }

    emit_status(handle, StartupStatusEvent {
        stage: "SwarmStarting".into(),
        mcp_healthy: true,
        agentserver_healthy: false,
        swarm_healthy: false,
        gateway_healthy: false,
        agent_core_available: false,
        fallback_active: false,
        error: None,
    });

    match start_swarm_agentserver(swarm_port) {
        Ok(swarm_child) => {
            *state.swarm_process.lock().map_err(|e| e.to_string())? = Some(swarm_child);
            match wait_for_health(swarm_port, "agentserver", 60).await {
                Ok(_) => {
                    emit_status(handle, StartupStatusEvent {
                        stage: "SwarmReady".into(),
                        mcp_healthy: true,
                        agentserver_healthy: true,
                        swarm_healthy: true,
                        gateway_healthy: true,
                        agent_core_available: true,
                        fallback_active: false,
                        error: None,
                    });
                    log::info!("All backends started: galaxyos=:{} agentserver=:{}", galaxyos_port, swarm_port);
                }
                Err(e) => {
                    log::warn!("AgentServer health check failed: {}, degrading to SwarmDegraded", e);
                    emit_status(handle, StartupStatusEvent {
                        stage: "SwarmDegraded".into(),
                        mcp_healthy: true,
                        agentserver_healthy: false,
                        swarm_healthy: false,
                        gateway_healthy: false,
                        agent_core_available: true,
                        fallback_active: true,
                        error: None,
                    });
                }
            }
        }
        Err(e) => {
            log::warn!("AgentServer start failed: {}, degrading to SwarmDegraded", e);
            emit_status(handle, StartupStatusEvent {
                stage: "SwarmDegraded".into(),
                mcp_healthy: true,
                agentserver_healthy: false,
                swarm_healthy: false,
                gateway_healthy: false,
                agent_core_available: true,
                fallback_active: true,
                error: None,
            });
        }
    }

    emit_status(handle, StartupStatusEvent {
        stage: "AgentCoreReady".into(),
        mcp_healthy: true,
        agentserver_healthy: state.swarm_process.lock().map(|g| g.is_some()).unwrap_or(false),
        swarm_healthy: state.swarm_process.lock().map(|g| g.is_some()).unwrap_or(false),
        gateway_healthy: state.swarm_process.lock().map(|g| g.is_some()).unwrap_or(false),
        agent_core_available: true,
        fallback_active: false,
        error: None,
    });

    log::info!("GalaxyOS MCP started on port {}", galaxyos_port);
    Ok(())
}

pub fn stop_all(state: &AppState) -> Result<(), String> {
    if let Ok(mut guard) = state.swarm_process.lock() {
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
    let binary_result = find_galaxyos_binary()?;
    let mut cmd = Command::new(&binary_result.path);

    if binary_result.is_python_fallback {
        cmd.args([
            "-m", "galaxyos.kernel.mcp_server_entry",
            "--transport", "sse",
            "--host", "127.0.0.1",
            "--port", &port.to_string(),
        ]);
    } else {
        cmd.args(["--transport", "sse", "--host", "127.0.0.1", "--port", &port.to_string()]);
    }

    cmd.env("GALAXYOS_MODE", "desktop")
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    #[cfg(windows)]
    cmd.creation_flags(CREATE_NO_WINDOW);
    cmd.spawn().map_err(|e| format!("Failed to start GalaxyOS MCP: {}", e))
}

fn start_swarm_agentserver(port: u16) -> Result<std::process::Child, String> {
    let python = find_python()?;
    let mut cmd = Command::new(&python);
    cmd.args(["-m", "jiuwenswarm.server.app_agentserver"])
        .env("AGENTSERVER_HOST", "127.0.0.1")
        .env("AGENTSERVER_PORT", &port.to_string())
        .env("GALAXYOS_MODE", "desktop")
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    #[cfg(windows)]
    cmd.creation_flags(CREATE_NO_WINDOW);
    cmd.spawn().map_err(|e| format!("Failed to start AgentServer: {}", e))
}

fn find_galaxyos_binary() -> Result<BinaryResult, String> {
    if let Ok(exe_dir) = std::env::current_exe() {
        if let Some(dir) = exe_dir.parent() {
            let bundled = dir.join("galaxyos-mcp");
            let bundled_exe = dir.join("galaxyos-mcp.exe");
            if bundled.exists() {
                return Ok(BinaryResult { path: bundled.to_string_lossy().to_string(), is_python_fallback: false });
            }
            if bundled_exe.exists() {
                return Ok(BinaryResult { path: bundled_exe.to_string_lossy().to_string(), is_python_fallback: false });
            }

            let up_dist = dir.join("_up_").join("galaxyos-mcp-dist").join("galaxyos-mcp");
            let up_dist_exe = dir.join("_up_").join("galaxyos-mcp-dist").join("galaxyos-mcp.exe");
            if up_dist.exists() {
                return Ok(BinaryResult { path: up_dist.to_string_lossy().to_string(), is_python_fallback: false });
            }
            if up_dist_exe.exists() {
                return Ok(BinaryResult { path: up_dist_exe.to_string_lossy().to_string(), is_python_fallback: false });
            }

            let resources_dir = dir.join("resources").join("galaxyos-mcp");
            let resources_exe = dir.join("resources").join("galaxyos-mcp.exe");
            if resources_dir.exists() {
                return Ok(BinaryResult { path: resources_dir.to_string_lossy().to_string(), is_python_fallback: false });
            }
            if resources_exe.exists() {
                return Ok(BinaryResult { path: resources_exe.to_string_lossy().to_string(), is_python_fallback: false });
            }
        }
    }
    let python = find_python()?;
    Ok(BinaryResult { path: python, is_python_fallback: true })
}

fn find_python() -> Result<String, String> {
    if let Ok(exe_dir) = std::env::current_exe() {
        if let Some(dir) = exe_dir.parent() {
            let venv_python = dir.join(".venv313").join("Scripts").join("python.exe");
            if venv_python.exists() {
                return Ok(venv_python.to_string_lossy().to_string());
            }
            let project_root = dir.join("..").join(".venv313").join("Scripts").join("python.exe");
            if project_root.exists() {
                return Ok(project_root.to_string_lossy().to_string());
            }
        }
    }
    let venv_path = std::path::Path::new("../.venv313/Scripts/python.exe");
    if venv_path.exists() {
        return Ok(venv_path.to_string_lossy().to_string());
    }
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
