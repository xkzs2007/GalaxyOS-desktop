use crate::AppState;
use std::fs::File;
use std::process::Command;
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

    agent_core_available: bool,
    fallback_active: bool,
    eui_neo_healthy: bool,
    native_render_available: bool,
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

    emit_status(
        handle,
        StartupStatusEvent {
            stage: "McpStarting".into(),
            mcp_healthy: false,
            agent_core_available: false,
            fallback_active: false,
            eui_neo_healthy: false,
            native_render_available: false,
            error: None,
        },
    );

    let galaxyos_child = match start_galaxyos_mcp(galaxyos_port) {
        Ok(child) => child,
        Err(e) => {
            emit_status(
                handle,
                StartupStatusEvent {
                    stage: "Failed".into(),
                    mcp_healthy: false,
                    agent_core_available: false,
                    fallback_active: false,
                    eui_neo_healthy: false,
                    native_render_available: false,
                    error: Some(StartupError {
                        stage: "McpStarting".into(),
                        error_type: "process_spawn".into(),
                        message: e.clone(),
                        suggestion: "Check if galaxyos-mcp binary or Python is available".into(),
                    }),
                },
            );
            return Err(e);
        }
    };
    *state.galaxyos_process.lock().map_err(|e| e.to_string())? = Some(galaxyos_child);

    match wait_for_health(galaxyos_port, "galaxyos-mcp", 30).await {
        Ok(_) => {
            emit_status(
                handle,
                StartupStatusEvent {
                    stage: "McpReady".into(),
                    mcp_healthy: true,
                    agent_core_available: false,
                    fallback_active: false,
                    eui_neo_healthy: false,
                    native_render_available: false,
                    error: None,
                },
            );
        }
        Err(e) => {
            emit_status(
                handle,
                StartupStatusEvent {
                    stage: "Failed".into(),
                    mcp_healthy: false,
                    agent_core_available: false,
                    fallback_active: false,
                    eui_neo_healthy: false,
                    native_render_available: false,
                    error: Some(StartupError {
                        stage: "McpStarting".into(),
                        error_type: "health_timeout".into(),
                        message: e.clone(),
                        suggestion: "Check if MCP Server port 8765 is accessible".into(),
                    }),
                },
            );
            return Err(e);
        }
    }

    // AgentCoreBridge initializes inside MCP Server process
    // Check if agent-core is available via MCP health endpoint
    let agent_core_ok = check_agent_core_health(galaxyos_port).await;

    if agent_core_ok {
        emit_status(
            handle,
            StartupStatusEvent {
                stage: "AgentCoreReady".into(),
                mcp_healthy: true,
                agent_core_available: true,
                fallback_active: false,
                eui_neo_healthy: false,
                native_render_available: false,
                error: None,
            },
        );
    } else {
        log::warn!("AgentCoreBridge initialization failed, running in degraded mode");
        emit_status(
            handle,
            StartupStatusEvent {
                stage: "AgentCoreDegraded".into(),
                mcp_healthy: true,
                agent_core_available: false,
                fallback_active: true,
                eui_neo_healthy: false,
                native_render_available: false,
                error: Some(StartupError {
                    stage: "AgentCoreDegraded".into(),
                    error_type: "agent_core_init_failed".into(),
                    message: "AgentCoreBridge initialization failed".into(),
                    suggestion: "Cognitive tools available, agent chat unavailable".into(),
                }),
            },
        );
    }

    log::info!("GalaxyOS MCP started on port {}", galaxyos_port);
    Ok(())
}

pub fn stop_all(state: &AppState) -> Result<(), String> {
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
            "-m",
            "galaxyos.kernel.mcp_server_entry",
            "--transport",
            "sse",
            "--host",
            "127.0.0.1",
            "--port",
            &port.to_string(),
        ]);
    } else {
        cmd.args([
            "--transport",
            "sse",
            "--host",
            "127.0.0.1",
            "--port",
            &port.to_string(),
        ]);
    }

    let log_path = log_dir().join("galaxyos-mcp.log");
    let log_file =
        File::create(&log_path).map_err(|e| format!("Failed to create log file: {}", e))?;
    cmd.env("GALAXYOS_MODE", "desktop")
        .stdout(log_file.try_clone().map_err(|e| e.to_string())?)
        .stderr(log_file);
    #[cfg(windows)]
    cmd.creation_flags(CREATE_NO_WINDOW);
    cmd.spawn()
        .map_err(|e| format!("Failed to start GalaxyOS MCP: {}", e))
}

async fn check_agent_core_health(mcp_port: u16) -> bool {
    let url = format!("http://127.0.0.1:{}/health", mcp_port);
    match reqwest::get(&url).await {
        Ok(resp) => {
            if let Ok(body) = resp.text().await {
                body.contains("\"agent_core_available\":true")
            } else {
                false
            }
        }
        Err(_) => false,
    }
}

fn log_dir() -> std::path::PathBuf {
    let dir = std::path::PathBuf::from("../logs");
    let _ = std::fs::create_dir_all(&dir);
    dir
}

fn find_galaxyos_binary() -> Result<BinaryResult, String> {
    if let Ok(exe_dir) = std::env::current_exe() {
        if let Some(dir) = exe_dir.parent() {
            let bundled = dir.join("galaxyos-mcp");
            let bundled_exe = dir.join("galaxyos-mcp.exe");
            if bundled.exists() {
                return Ok(BinaryResult {
                    path: bundled.to_string_lossy().to_string(),
                    is_python_fallback: false,
                });
            }
            if bundled_exe.exists() {
                return Ok(BinaryResult {
                    path: bundled_exe.to_string_lossy().to_string(),
                    is_python_fallback: false,
                });
            }

            let up_dist = dir
                .join("_up_")
                .join("galaxyos-mcp-dist")
                .join("galaxyos-mcp");
            let up_dist_exe = dir
                .join("_up_")
                .join("galaxyos-mcp-dist")
                .join("galaxyos-mcp.exe");
            if up_dist.exists() {
                return Ok(BinaryResult {
                    path: up_dist.to_string_lossy().to_string(),
                    is_python_fallback: false,
                });
            }
            if up_dist_exe.exists() {
                return Ok(BinaryResult {
                    path: up_dist_exe.to_string_lossy().to_string(),
                    is_python_fallback: false,
                });
            }

            let resources_dir = dir.join("resources").join("galaxyos-mcp");
            let resources_exe = dir.join("resources").join("galaxyos-mcp.exe");
            if resources_dir.exists() {
                return Ok(BinaryResult {
                    path: resources_dir.to_string_lossy().to_string(),
                    is_python_fallback: false,
                });
            }
            if resources_exe.exists() {
                return Ok(BinaryResult {
                    path: resources_exe.to_string_lossy().to_string(),
                    is_python_fallback: false,
                });
            }
        }
    }
    let python = find_python()?;
    Ok(BinaryResult {
        path: python,
        is_python_fallback: true,
    })
}

fn venv_python_path(base: &std::path::Path) -> std::path::PathBuf {
    #[cfg(windows)]
    {
        base.join(".venv313").join("Scripts").join("python.exe")
    }
    #[cfg(not(windows))]
    {
        base.join(".venv313").join("bin").join("python")
    }
}

fn find_python() -> Result<String, String> {
    if let Ok(exe_dir) = std::env::current_exe() {
        if let Some(dir) = exe_dir.parent() {
            let venv_python = venv_python_path(dir);
            if venv_python.exists() {
                return Ok(venv_python.to_string_lossy().to_string());
            }
            let project_root = venv_python_path(&dir.join(".."));
            if project_root.exists() {
                return Ok(project_root.to_string_lossy().to_string());
            }
        }
    }
    for name in &["python3", "python"] {
        if Command::new(name).arg("--version").output().is_ok() {
            return Ok(name.to_string());
        }
    }
    Err("Python not found".into())
}

async fn wait_for_health(port: u16, name: &str, max_secs: u64) -> Result<(), String> {
    for i in 0..max_secs {
        if tokio::net::TcpStream::connect(format!("127.0.0.1:{}", port))
            .await
            .is_ok()
        {
            log::info!("{} health check passed (TCP) after {}s", name, i);
            return Ok(());
        }
        tokio::time::sleep(Duration::from_secs(1)).await;
    }
    Err(format!(
        "{} health check timed out after {}s",
        name, max_secs
    ))
}
