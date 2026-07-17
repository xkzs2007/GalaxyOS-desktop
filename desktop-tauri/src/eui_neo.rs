use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::sync::Mutex;
use tauri::Emitter;

pub struct EuiNeoContext {
    pub native_available: bool,
    pub surfaces: HashMap<String, RenderSurface>,
    pub surface_manager: NativeRenderSurfaceManager,
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
}

#[derive(Debug, Clone, Serialize, Deserialize)]
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
}

pub struct NativeRenderSurfaceManager {
    surfaces: HashMap<String, RenderSurface>,
    native_available: bool,
}

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
        };

        self.surfaces.insert(config.surface_id.clone(), surface.clone());
        Ok(surface)
    }

    pub fn destroy_surface(&mut self, surface_id: &str) -> Result<(), String> {
        self.surfaces
            .remove(surface_id)
            .ok_or_else(|| format!("Surface {} not found", surface_id))?;
        Ok(())
    }

    pub fn update_surface(&mut self, surface_id: &str, dsl: &str) -> Result<RenderSurface, String> {
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
        };

        self.surfaces.insert(surface_id.to_string(), rebuilt.clone());
        Ok(rebuilt)
    }
}

pub struct NativeEventBridge {
    surface_event_log: Vec<serde_json::Value>,
}

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

    pub fn emit_event(handle: &tauri::AppHandle, surface_id: &str, event_type: &str, event_data: &serde_json::Value) {
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

#[tauri::command]
pub async fn render_native(
    dsl: String,
    surface_id: String,
    state: tauri::State<'_, crate::AppState>,
) -> Result<serde_json::Value, String> {
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
        }));
    }

    match ctx.surface_manager.update_surface(&surface_id, &dsl) {
        Ok(surface) => Ok(serde_json::json!({
            "status": "success",
            "surface_id": surface.surface_id,
            "render_handle": surface.render_handle,
            "channel": surface.active_channel,
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