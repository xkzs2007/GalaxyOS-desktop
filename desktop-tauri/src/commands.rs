use crate::eui_neo;
use crate::AppState;
use tauri::Emitter;

#[tauri::command]
pub async fn start_backends(
    state: tauri::State<'_, AppState>,
    handle: tauri::AppHandle,
) -> Result<String, String> {
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
    let galaxyos_port = state.galaxyos_port;

    let galaxyos_ok = tokio::net::TcpStream::connect(format!("127.0.0.1:{}", galaxyos_port))
        .await
        .is_ok();

    let eui_neo_status = {
        let ctx = state.eui_neo_context.lock().map_err(|e| e.to_string())?;
        ctx.surface_manager.is_native_available()
    };

    Ok(serde_json::json!({
        "galaxyos": galaxyos_ok,
        "eui_neo": {
            "status": if eui_neo_status { "available" } else { "unavailable" },
            "native_render_available": eui_neo_status,
        },
    }))
}

#[tauri::command]
pub async fn get_locale(state: tauri::State<'_, AppState>) -> Result<String, String> {
    let locale = state.locale.lock().map_err(|e| e.to_string())?;
    Ok(locale.clone())
}

#[tauri::command]
pub async fn set_locale(
    locale: String,
    state: tauri::State<'_, AppState>,
    handle: tauri::AppHandle,
) -> Result<String, String> {
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
pub async fn request_cognitive_data(
    tab: String,
    state: tauri::State<'_, AppState>,
) -> Result<serde_json::Value, String> {
    let locale = state.locale.lock().map_err(|e| e.to_string())?.clone();
    let active_channel = {
        let ctx = state.eui_neo_context.lock().map_err(|e| e.to_string())?;
        ctx.surface_manager.is_native_available()
    };

    if active_channel {
        let dsl = eui_neo::build_cognitive_panel_dsl(&locale);
        Ok(serde_json::json!({ "dsl": dsl, "tab": tab, "locale": locale, "channel": "eui_native" }))
    } else {
        let html = match tab.as_str() {
            "memory" => {
                let title = if locale == "zh" {
                    "液态神经记忆"
                } else {
                    "Liquid Neural Memory"
                };
                format!("<div style='padding:8px'><h4 style='margin:0 0 8px'>{}</h4><p style='color:#999;font-size:12px'>{}</p></div>",
                    title,
                    if locale == "zh" { "等待 AgentCore 数据..." } else { "Waiting for AgentCore data..." }
                )
            }
            "rccam" => {
                let title = if locale == "zh" {
                    "R-CCAM 认知循环"
                } else {
                    "R-CCAM Loop"
                };
                format!("<div style='padding:8px'><h4 style='margin:0 0 8px'>{}</h4><p style='color:#999;font-size:12px'>{}</p></div>",
                    title,
                    if locale == "zh" { "等待认知循环状态..." } else { "Waiting for cognitive loop state..." }
                )
            }
            "dag" => {
                let title = if locale == "zh" {
                    "DAG 上下文树"
                } else {
                    "DAG Context Tree"
                };
                format!("<div style='padding:8px'><h4 style='margin:0 0 8px'>{}</h4><p style='color:#999;font-size:12px'>{}</p></div>",
                    title,
                    if locale == "zh" { "等待上下文节点数据..." } else { "Waiting for context node data..." }
                )
            }
            "search" => {
                let placeholder = if locale == "zh" {
                    "搜索记忆..."
                } else {
                    "Search memories..."
                };
                let btn_text = if locale == "zh" { "搜索" } else { "Search" };
                format!("<div style='padding:8px'><div style='display:flex;gap:6px'><input type='text' placeholder='{}' style='flex:1;padding:6px 10px;font-size:13px;border:1px solid #ccc;border-radius:4px' /><button style='padding:6px 14px;font-size:13px;background:#1976d2;color:#fff;border:none;border-radius:4px;cursor:pointer'>{}</button></div></div>",
                    placeholder, btn_text
                )
            }
            _ => format!(
                "<div style='padding:8px;color:#999'>Unknown tab: {}</div>",
                tab
            ),
        };
        Ok(
            serde_json::json!({ "html": html, "tab": tab, "locale": locale, "channel": "webview_dom" }),
        )
    }
}

#[tauri::command]
pub async fn sse_stream_chat(
    _message: String,
    workspace_id: String,
    state: tauri::State<'_, AppState>,
    handle: tauri::AppHandle,
) -> Result<serde_json::Value, String> {
    let port = state.galaxyos_port;
    let base_url = format!("http://127.0.0.1:{}", port);

    let initial_channel = {
        let pipeline = state.render_pipeline.lock().map_err(|e| e.to_string())?;
        pipeline.current_channel()
    };

    let mut sse = crate::sse_client::SseClient::new(&base_url, &workspace_id);

    let mut chunks_rendered = 0u32;
    let mut final_event = String::new();
    let stream_id = format!("stream-{}", std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis());

    {
        let mut pipeline = state.render_pipeline.lock().map_err(|e| e.to_string())?;
        let mut surface = crate::eui_neo::RenderSurface {
            surface_id: "chat-main".into(),
            surface_type: "chat".into(),
            position: "center".into(),
            width: 800,
            height: 600,
            active_channel: if initial_channel == crate::render_channel::RenderChannel::EuiNative {
                "eui_native".into()
            } else {
                "webview_dom".into()
            },
            render_handle: None,
            created_at: 0,
            last_updated: 0,
            degraded: false,
            layout_mode: "sidebar".into(),
        };
        let _ = pipeline.begin_sse_stream(&stream_id, &mut surface);
    }

    sse.listen(|event_type, event_data| {
        if event_type == "tokui_chunk" || event_type == "agent_token" {
            if let Ok(mut pipeline) = state.render_pipeline.lock() {
                let mut surface = crate::eui_neo::RenderSurface {
                    surface_id: "chat-main".into(),
                    surface_type: "chat".into(),
                    position: "center".into(),
                    width: 800,
                    height: 600,
                    active_channel: if initial_channel == crate::render_channel::RenderChannel::EuiNative {
                        "eui_native".into()
                    } else {
                        "webview_dom".into()
                    },
                    render_handle: None,
                    created_at: 0,
                    last_updated: 0,
                    degraded: false,
                    layout_mode: "sidebar".into(),
                };

                let node_id = format!("chunk-{}", chunks_rendered);
                let rendered = pipeline.on_sse_stream_chunk(event_data, &node_id, &mut surface)
                    .unwrap_or_else(|_| event_data.to_string());

                chunks_rendered += 1;
                let _ = handle.emit("galaxyos://chat-chunk", serde_json::json!({
                    "chunk": rendered,
                    "surface_id": surface.surface_id,
                    "channel": pipeline.current_channel().to_string(),
                    "stream_id": stream_id,
                    "node_id": node_id,
                }));
            }
        } else if event_type == "agent_done" {
            final_event = "done".into();
        }
    }).await?;

    {
        let mut pipeline = state.render_pipeline.lock().map_err(|e| e.to_string())?;
        let mut surface = crate::eui_neo::RenderSurface {
            surface_id: "chat-main".into(),
            surface_type: "chat".into(),
            position: "center".into(),
            width: 800,
            height: 600,
            active_channel: "webview_dom".into(),
            render_handle: None,
            created_at: 0,
            last_updated: 0,
            degraded: false,
            layout_mode: "sidebar".into(),
        };
        let _ = pipeline.end_sse_stream(&mut surface);
    }

    let (buffer_len, channel, streaming, node_count) = {
        let pipeline = state.render_pipeline.lock().map_err(|e| e.to_string())?;
        (pipeline.renderer_buffer_len(), pipeline.current_channel(), pipeline.is_streaming(), pipeline.stream_node_count())
    };

    Ok(serde_json::json!({
        "status": final_event,
        "chunks_rendered": chunks_rendered,
        "channel": channel.to_string(),
        "buffer_len": buffer_len,
        "stream_id": stream_id,
        "streaming": streaming,
        "node_count": node_count,
    }))
}

#[tauri::command]
pub async fn render_pipeline_status(
    state: tauri::State<'_, AppState>,
) -> Result<serde_json::Value, String> {
    let pipeline = state.render_pipeline.lock().map_err(|e| e.to_string())?;
    Ok(serde_json::json!({
        "channel": pipeline.current_channel().to_string(),
        "buffer_len": pipeline.renderer_buffer_len(),
        "degradation_count": pipeline.degradation_count(),
        "spring_running": pipeline.spring_is_running(),
        "streaming": pipeline.is_streaming(),
        "stream_node_count": pipeline.stream_node_count(),
    }))
}
