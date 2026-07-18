use serde::{Deserialize, Serialize};
use std::collections::HashMap;

use tauri::Emitter;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SpringAnimationConfig {
    pub damping: f64,
    pub stiffness: f64,
    pub mass: f64,
    pub max_duration_ms: u32,
    pub interruptible: bool,
}

impl Default for SpringAnimationConfig {
    fn default() -> Self {
        Self {
            damping: 1.0,
            stiffness: 200.0,
            mass: 1.0,
            max_duration_ms: 300,
            interruptible: true,
        }
    }
}

impl SpringAnimationConfig {
    pub fn panel_spring() -> Self {
        Self {
            damping: 1.0,
            stiffness: 200.0,
            mass: 1.0,
            max_duration_ms: 300,
            interruptible: true,
        }
    }

    pub fn momentum_spring() -> Self {
        Self {
            damping: 0.8,
            stiffness: 200.0,
            mass: 1.0,
            max_duration_ms: 100,
            interruptible: true,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RccamState {
    pub current_stage: String,
    pub stages_completed: u8,
    pub total_stages: u8,
    pub is_running: bool,
    pub strategy: String,
    pub depth: u8,
}

impl Default for RccamState {
    fn default() -> Self {
        Self {
            current_stage: "retrieval".into(),
            stages_completed: 0,
            total_stages: 5,
            is_running: false,
            strategy: "direct_reply".into(),
            depth: 3,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MemoryState {
    pub engram_count: u32,
    pub neural_count: u32,
    pub synapse_count: u32,
    pub consolidation_status: String,
    pub total: u32,
}

impl Default for MemoryState {
    fn default() -> Self {
        Self {
            engram_count: 0,
            neural_count: 0,
            synapse_count: 0,
            consolidation_status: "idle".into(),
            total: 0,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DagNodeData {
    pub id: String,
    pub role: String,
    pub content: String,
    pub importance: f64,
    pub summary: Option<String>,
    pub children: Vec<DagNodeData>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DagState {
    pub total_nodes: u32,
    pub sessions: u32,
    pub nodes: Vec<DagNodeData>,
}

impl Default for DagState {
    fn default() -> Self {
        Self {
            total_nodes: 0,
            sessions: 0,
            nodes: Vec::new(),
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CognitivePanelState {
    pub workspace_id: String,
    pub rccam: RccamState,
    pub memory: MemoryState,
    pub dag: DagState,
}

impl Default for CognitivePanelState {
    fn default() -> Self {
        Self {
            workspace_id: String::new(),
            rccam: RccamState::default(),
            memory: MemoryState::default(),
            dag: DagState::default(),
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MemorySearchResult {
    pub id: String,
    pub content: String,
    pub score: f64,
    pub memory_type: String,
    pub source: String,
}

#[allow(dead_code)]
pub struct EuiNeoContext {
    pub native_available: bool,
    pub surfaces: HashMap<String, RenderSurface>,
    pub surface_manager: NativeRenderSurfaceManager,
    pub panel_spring: SpringAnimationConfig,
    pub momentum_spring: SpringAnimationConfig,
    pub cognitive_state: CognitivePanelState,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RenderSurface {
    pub surface_id: String,
    pub surface_type: String,
    pub position: String,
    pub width: u32,
    pub height: u32,
    pub active_channel: String,
    pub render_handle: Option<String>,
    pub created_at: u64,
    pub last_updated: u64,
    #[serde(default)]
    pub degraded: bool,
    #[serde(default = "default_layout_mode")]
    pub layout_mode: String,
}

fn default_layout_mode() -> String {
    "sidebar".to_string()
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[allow(dead_code)]
pub struct NativeRenderResult {
    pub status: String,
    pub surface_id: String,
    pub render_handle: Option<String>,
    pub fallback: Option<String>,
    pub error: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SurfaceConfig {
    pub surface_id: String,
    pub surface_type: String,
    pub position: String,
    pub width: u32,
    pub height: u32,
    #[serde(default = "default_layout_mode")]
    pub layout_mode: String,
}

#[allow(dead_code)]
pub struct NativeRenderSurfaceManager {
    surfaces: HashMap<String, RenderSurface>,
    native_available: bool,
}

#[allow(dead_code)]
impl NativeRenderSurfaceManager {
    pub fn new() -> Self {
        Self {
            surfaces: HashMap::new(),
            native_available: false,
        }
    }

    pub fn set_native_available(&mut self, available: bool) {
        self.native_available = available;
    }

    pub fn create_surface(&mut self, config: &SurfaceConfig) -> Result<RenderSurface, String> {
        let now = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_secs())
            .unwrap_or(0);

        let surface = RenderSurface {
            surface_id: config.surface_id.clone(),
            surface_type: config.surface_type.clone(),
            position: config.position.clone(),
            width: config.width,
            height: config.height,
            active_channel: if self.native_available {
                "eui_native".to_string()
            } else {
                "webview_dom".to_string()
            },
            render_handle: if self.native_available {
                Some(format!("nh-{}-{}", config.surface_id, now))
            } else {
                None
            },
            created_at: now,
            last_updated: now,
            degraded: false,
            layout_mode: config.layout_mode.clone(),
        };

        self.surfaces
            .insert(config.surface_id.clone(), surface.clone());
        Ok(surface)
    }

    pub fn destroy_surface(&mut self, surface_id: &str) -> Result<(), String> {
        self.surfaces
            .remove(surface_id)
            .ok_or_else(|| format!("Surface {} not found", surface_id))?;
        Ok(())
    }

    pub fn update_surface(
        &mut self,
        surface_id: &str,
        _dsl: &str,
    ) -> Result<RenderSurface, String> {
        let surface = self
            .surfaces
            .get_mut(surface_id)
            .ok_or_else(|| format!("Surface {} not found", surface_id))?;

        let now = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_secs())
            .unwrap_or(0);

        surface.last_updated = now;
        Ok(surface.clone())
    }

    pub fn get_surface(&self, surface_id: &str) -> Option<&RenderSurface> {
        self.surfaces.get(surface_id)
    }

    pub fn list_surfaces(&self) -> Vec<RenderSurface> {
        self.surfaces.values().cloned().collect()
    }

    pub fn rebuild_surface(&mut self, surface_id: &str) -> Result<RenderSurface, String> {
        let old = self
            .surfaces
            .remove(surface_id)
            .ok_or_else(|| format!("Surface {} not found for rebuild", surface_id))?;

        let now = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_secs())
            .unwrap_or(0);

        let rebuilt = RenderSurface {
            surface_id: old.surface_id.clone(),
            surface_type: old.surface_type.clone(),
            position: old.position.clone(),
            width: old.width,
            height: old.height,
            active_channel: if self.native_available {
                "eui_native".to_string()
            } else {
                "webview_dom".to_string()
            },
            render_handle: if self.native_available {
                Some(format!("nh-{}-{}", old.surface_id, now))
            } else {
                None
            },
            created_at: old.created_at,
            last_updated: now,
            degraded: false,
            layout_mode: old.layout_mode.clone(),
        };

        self.surfaces
            .insert(surface_id.to_string(), rebuilt.clone());
        Ok(rebuilt)
    }
}

#[allow(dead_code)]
pub struct NativeEventBridge {
    surface_event_log: Vec<serde_json::Value>,
}

#[allow(dead_code)]
impl NativeEventBridge {
    pub fn new() -> Self {
        Self {
            surface_event_log: Vec::new(),
        }
    }

    pub fn on_native_event(
        &mut self,
        surface_id: &str,
        event_type: &str,
        event_data: &serde_json::Value,
    ) -> serde_json::Value {
        let event = serde_json::json!({
            "event": "native_ui_event",
            "data": {
                "surfaceId": surface_id,
                "eventType": event_type,
                "eventData": event_data,
            }
        });

        self.surface_event_log.push(event.clone());
        log::debug!("Native event: surface={}, type={}", surface_id, event_type);
        event
    }

    pub fn emit_event(
        handle: &tauri::AppHandle,
        surface_id: &str,
        event_type: &str,
        event_data: &serde_json::Value,
    ) {
        let payload = serde_json::json!({
            "event": "native_ui_event",
            "data": {
                "surfaceId": surface_id,
                "eventType": event_type,
                "eventData": event_data,
            }
        });

        if let Err(e) = handle.emit("galaxyos://native-ui-event", &payload) {
            log::warn!("Failed to emit native UI event: {}", e);
        }
    }
}

#[allow(dead_code)]
const FFI_TIMEOUT_MS: u64 = 5000;

#[tauri::command]
pub async fn render_native(
    dsl: String,
    surface_id: String,
    state: tauri::State<'_, crate::AppState>,
) -> Result<serde_json::Value, String> {
    let i18n_injected_dsl = {
        let bridge = state.i18n_bridge.lock().map_err(|e| e.to_string())?;
        bridge.inject_into_dsl(&dsl)
    };

    let mut ctx = state.eui_neo_context.lock().map_err(|e| e.to_string())?;

    if !ctx.surfaces.contains_key(&surface_id) {
        let config = SurfaceConfig {
            surface_id: surface_id.clone(),
            surface_type: "cognitive_panel".to_string(),
            position: "right".to_string(),
            width: 320,
            height: 600,
        };
        ctx.surface_manager.create_surface(&config)?;
    }

    if !ctx.surface_manager.native_available {
        return Ok(serde_json::json!({
            "status": "unavailable",
            "surface_id": surface_id,
            "fallback": "webview_dom",
            "i18n_injected": dsl != i18n_injected_dsl,
        }));
    }

    match ctx
        .surface_manager
        .update_surface(&surface_id, &i18n_injected_dsl)
    {
        Ok(surface) => Ok(serde_json::json!({
            "status": "success",
            "surface_id": surface.surface_id,
            "render_handle": surface.render_handle,
            "channel": surface.active_channel,
            "i18n_injected": dsl != i18n_injected_dsl,
        })),
        Err(e) => Ok(serde_json::json!({
            "status": "error",
            "surface_id": surface_id,
            "error": e,
            "fallback": "webview_dom",
        })),
    }
}

#[tauri::command]
pub async fn create_surface(
    config: SurfaceConfig,
    state: tauri::State<'_, crate::AppState>,
) -> Result<serde_json::Value, String> {
    let mut ctx = state.eui_neo_context.lock().map_err(|e| e.to_string())?;
    match ctx.surface_manager.create_surface(&config) {
        Ok(surface) => Ok(serde_json::json!({
            "status": "created",
            "surface": surface,
        })),
        Err(e) => Err(e),
    }
}

#[tauri::command]
pub async fn destroy_surface(
    surface_id: String,
    state: tauri::State<'_, crate::AppState>,
) -> Result<serde_json::Value, String> {
    let mut ctx = state.eui_neo_context.lock().map_err(|e| e.to_string())?;
    match ctx.surface_manager.destroy_surface(&surface_id) {
        Ok(()) => Ok(serde_json::json!({"status": "destroyed", "surface_id": surface_id})),
        Err(e) => Err(e),
    }
}

#[tauri::command]
pub async fn update_surface(
    surface_id: String,
    dsl: String,
    state: tauri::State<'_, crate::AppState>,
) -> Result<serde_json::Value, String> {
    let mut ctx = state.eui_neo_context.lock().map_err(|e| e.to_string())?;
    match ctx.surface_manager.update_surface(&surface_id, &dsl) {
        Ok(surface) => Ok(serde_json::json!({
            "status": "updated",
            "surface": surface,
        })),
        Err(e) => Err(e),
    }
}

#[tauri::command]
pub async fn check_eui_neo_health(
    state: tauri::State<'_, crate::AppState>,
) -> Result<serde_json::Value, String> {
    let ctx = state.eui_neo_context.lock().map_err(|e| e.to_string())?;
    Ok(serde_json::json!({
        "status": if ctx.surface_manager.native_available { "available" } else { "unavailable" },
        "native_render_available": ctx.surface_manager.native_available,
        "active_surfaces": ctx.surface_manager.list_surfaces().len(),
    }))
}

#[tauri::command]
pub async fn rebuild_surface(
    surface_id: String,
    state: tauri::State<'_, crate::AppState>,
) -> Result<serde_json::Value, String> {
    let mut ctx = state.eui_neo_context.lock().map_err(|e| e.to_string())?;
    match ctx.surface_manager.rebuild_surface(&surface_id) {
        Ok(surface) => Ok(serde_json::json!({
            "status": "rebuilt",
            "surface": surface,
        })),
        Err(e) => Ok(serde_json::json!({
            "status": "rebuild_failed",
            "error": e,
            "fallback": "webview_dom",
        })),
    }
}

#[tauri::command]
pub async fn open_cognitive_overlay(
    position: String,
    width: f64,
    height: f64,
    state: tauri::State<'_, crate::AppState>,
    _handle: tauri::AppHandle,
) -> Result<serde_json::Value, String> {
    let label = format!("cognitive-overlay-{}", chrono_like_timestamp());

    let mut ctx = state.eui_neo_context.lock().map_err(|e| e.to_string())?;

    if !ctx.surface_manager.native_available {
        return Ok(serde_json::json!({
            "status": "fallback_webview",
            "label": label,
            "message": "EUI-NEO native not available, using webview overlay",
        }));
    }

    let surface_id = format!("cognitive-overlay-{}", chrono_like_timestamp());
    let config = SurfaceConfig {
        surface_id: surface_id.clone(),
        surface_type: "cognitive_overlay".to_string(),
        position: position.clone(),
        width: width as u32,
        height: height as u32,
    };

    match ctx.surface_manager.create_surface(&config) {
        Ok(_surface) => {
            let locale = state.locale.lock().map_err(|e| e.to_string())?.clone();
            let dsl = build_cognitive_panel_dsl(&locale);
            match ctx.surface_manager.update_surface(&surface_id, &dsl) {
                Ok(updated) => Ok(serde_json::json!({
                    "status": "success",
                    "surface_id": surface_id,
                    "render_handle": updated.render_handle,
                    "channel": updated.active_channel,
                    "position": position,
                    "width": width,
                    "height": height,
                })),
                Err(e) => Ok(serde_json::json!({
                    "status": "surface_created_render_failed",
                    "surface_id": surface_id,
                    "error": e,
                })),
            }
        }
        Err(e) => Ok(serde_json::json!({
            "status": "surface_creation_failed",
            "error": e,
            "fallback": "webview_overlay",
        })),
    }
}

#[tauri::command]
pub async fn close_cognitive_overlay(
    surface_id: String,
    state: tauri::State<'_, crate::AppState>,
) -> Result<serde_json::Value, String> {
    let mut ctx = state.eui_neo_context.lock().map_err(|e| e.to_string())?;
    match ctx.surface_manager.destroy_surface(&surface_id) {
        Ok(_) => Ok(serde_json::json!({"status": "closed", "surface_id": surface_id})),
        Err(e) => Ok(serde_json::json!({"status": "close_failed", "error": e})),
    }
}

fn chrono_like_timestamp() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis() as u64
}

pub fn build_cognitive_panel_dsl(locale: &str) -> String {
    let zh = locale == "zh";
    let title = if zh {
        "认知面板"
    } else {
        "Cognitive Panel"
    };
    let memory = if zh {
        "液态神经记忆"
    } else {
        "Liquid Neural Memory"
    };
    let rccam = if zh { "R-CCAM 循环" } else { "R-CCAM Loop" };
    let dag = if zh {
        "DAG 上下文树"
    } else {
        "DAG Context Tree"
    };
    let search = if zh { "记忆检索" } else { "Memory Search" };
    let search_placeholder = if zh {
        "搜索记忆..."
    } else {
        "Search memories..."
    };
    let search_btn = if zh { "搜索" } else { "Search" };
    let all = if zh { "全部" } else { "All" };
    let pause = if zh { "暂停" } else { "Pause" };
    let resume = if zh { "继续" } else { "Resume" };
    let depth = if zh {
        "检索深度"
    } else {
        "Retrieval Depth"
    };
    let strategy = if zh {
        "认知策略"
    } else {
        "Cognitive Strategy"
    };
    let direct_reply = if zh { "直接回复" } else { "Direct Reply" };
    let deep_analysis = if zh { "深度分析" } else { "Deep Analysis" };
    let creative = if zh { "创意模式" } else { "Creative Mode" };

    serde_json::json!({
        "type": "cognitive_panel",
        "title": title,
        "layout_mode": "sidebar",
        "spring": {
            "damping": 1.0,
            "max_duration_ms": 300,
            "interruptible": true
        },
        "tabs": [
            {
                "id": "memory",
                "label": memory,
                "components": [
                    {"type": "progress", "id": "engram_progress", "label": "Engram", "value": 0, "max": 100},
                    {"type": "progress", "id": "neural_progress", "label": "Neural", "value": 0, "max": 100},
                    {"type": "progress", "id": "synapse_progress", "label": "Synapse", "value": 0, "max": 100},
                    {"type": "text", "id": "consolidation_status", "text": "idle", "color": "#999"},
                    {"type": "segmented", "id": "memory_filter", "options": [all, "Engram", "Neural", "Synapse"], "selected": 0},
                    {"type": "input", "id": "memory_search_input", "placeholder": search_placeholder},
                    {"type": "button", "id": "memory_search_btn", "text": search_btn}
                ]
            },
            {
                "id": "rccam",
                "label": rccam,
                "components": [
                    {"type": "progress", "id": "rccam_progress", "label": "R-CCAM", "value": 0, "max": 5, "stages": ["Retrieval", "Cognition", "Control", "Action", "Memory"]},
                    {"type": "button", "id": "rccam_toggle", "text": pause},
                    {"type": "slider", "id": "rccam_depth", "label": depth, "min": 1, "max": 5, "value": 3},
                    {"type": "dropdown", "id": "rccam_strategy", "label": strategy, "options": [direct_reply, deep_analysis, creative], "selected": 0}
                ]
            },
            {
                "id": "dag",
                "label": dag,
                "components": [
                    {"type": "scrollview", "id": "dag_tree", "children": []},
                    {"type": "text", "id": "dag_stats", "text": "0 nodes / 0 sessions"}
                ]
            },
            {
                "id": "search",
                "label": search,
                "components": [
                    {"type": "input", "id": "search_input", "placeholder": search_placeholder},
                    {"type": "segmented", "id": "search_filter", "options": [all, "Engram", "Neural", "Synapse"], "selected": 0},
                    {"type": "button", "id": "search_btn", "text": search_btn},
                    {"type": "scrollview", "id": "search_results", "children": []}
                ]
            }
        ],
        "locale": locale,
        "material": {"blur_radius": 20.0, "background_opacity": 0.6}
    }).to_string()
}

#[tauri::command]
pub async fn get_memory_stats(
    workspace_id: String,
    state: tauri::State<'_, crate::AppState>,
) -> Result<serde_json::Value, String> {
    let ctx = state.eui_neo_context.lock().map_err(|e| e.to_string())?;
    let mem = &ctx.cognitive_state.memory;
    Ok(serde_json::json!({
        "engram_count": mem.engram_count,
        "neural_count": mem.neural_count,
        "synapse_count": mem.synapse_count,
        "consolidation_status": mem.consolidation_status,
        "total": mem.total,
        "workspace_id": workspace_id,
    }))
}

#[tauri::command]
pub async fn rccam_control(
    action: String,
    strategy: Option<String>,
    depth: Option<u8>,
    state: tauri::State<'_, crate::AppState>,
) -> Result<serde_json::Value, String> {
    let mut ctx = state.eui_neo_context.lock().map_err(|e| e.to_string())?;
    let rccam = &mut ctx.cognitive_state.rccam;

    match action.as_str() {
        "pause" => rccam.is_running = false,
        "resume" => rccam.is_running = true,
        _ => {}
    }

    if let Some(s) = strategy {
        rccam.strategy = s;
    }
    if let Some(d) = depth {
        rccam.depth = d.clamp(1, 5);
    }

    Ok(serde_json::json!({
        "is_running": rccam.is_running,
        "current_stage": rccam.current_stage,
        "strategy": rccam.strategy,
        "depth": rccam.depth,
        "stages_completed": rccam.stages_completed,
    }))
}

#[tauri::command]
pub async fn get_dag_tree(
    workspace_id: String,
    state: tauri::State<'_, crate::AppState>,
) -> Result<serde_json::Value, String> {
    let ctx = state.eui_neo_context.lock().map_err(|e| e.to_string())?;
    let dag = &ctx.cognitive_state.dag;
    Ok(serde_json::json!({
        "total_nodes": dag.total_nodes,
        "sessions": dag.sessions,
        "nodes": dag.nodes,
        "workspace_id": workspace_id,
    }))
}

#[tauri::command]
pub async fn search_memory(
    query: String,
    memory_type: Option<String>,
    top_k: Option<u32>,
    state: tauri::State<'_, crate::AppState>,
) -> Result<serde_json::Value, String> {
    let ctx = state.eui_neo_context.lock().map_err(|e| e.to_string())?;
    Ok(serde_json::json!({
        "results": [],
        "query": query,
        "memory_type": memory_type.unwrap_or_else(|| "all".into()),
        "top_k": top_k.unwrap_or(10),
        "workspace_id": ctx.cognitive_state.workspace_id,
    }))
}

#[tauri::command]
pub async fn set_panel_layout(
    layout_mode: String,
    state: tauri::State<'_, crate::AppState>,
) -> Result<serde_json::Value, String> {
    let valid = ["sidebar", "inline", "floating"];
    if !valid.contains(&layout_mode.as_str()) {
        return Err(format!(
            "Invalid layout_mode: {}. Must be one of: sidebar, inline, floating",
            layout_mode
        ));
    }

    let mut ctx = state.eui_neo_context.lock().map_err(|e| e.to_string())?;

    for surface in ctx.surface_manager.surfaces.values_mut() {
        surface.layout_mode = layout_mode.clone();
    }

    Ok(serde_json::json!({
        "layout_mode": layout_mode,
        "updated_surfaces": ctx.surface_manager.surfaces.len(),
    }))
}
