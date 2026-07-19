use crate::eui_neo::RenderSurface;
use crate::eui_neo_ffi;
use crate::render_channel::{DegradationReason, RenderChannelRouter};

pub struct TokuiStreamRenderer {
    dsl_buffer: String,
    surface_id: Option<String>,
    stream_id: Option<String>,
    stream_active: bool,
    degraded: bool,
    node_counter: u32,
}

impl TokuiStreamRenderer {
    pub fn new() -> Self {
        Self {
            dsl_buffer: String::new(),
            surface_id: None,
            stream_id: None,
            stream_active: false,
            degraded: false,
            node_counter: 0,
        }
    }

    pub fn append_chunk(&mut self, chunk: &str) {
        self.dsl_buffer.push_str(chunk);
    }

    pub fn begin_stream(&mut self, stream_id: &str, surface_id: &str) -> Result<bool, String> {
        if self.degraded {
            self.stream_id = Some(stream_id.to_string());
            self.stream_active = true;
            return Ok(true);
        }

        let result = eui_neo_ffi::safe_begin_stream(stream_id, surface_id);
        if result.is_ok() && *result.as_ref().unwrap() {
            self.stream_id = Some(stream_id.to_string());
            self.surface_id = Some(surface_id.to_string());
            self.stream_active = true;
            self.node_counter = 0;
        }
        result
    }

    pub fn stream_create_node(
        &mut self,
        node_id: &str,
        parent_id: &str,
        node_type: &str,
        text_content: &str,
        x: f32,
        y: f32,
        width: f32,
        height: f32,
    ) -> Result<bool, String> {
        if self.degraded {
            return Ok(true);
        }

        let sid = self.stream_id.as_deref().unwrap_or("");
        let result = eui_neo_ffi::safe_create_node(
            sid, node_id, parent_id, node_type, text_content, x, y, width, height,
        );
        if result.is_ok() && *result.as_ref().unwrap() {
            self.node_counter += 1;
        }
        result
    }

    pub fn stream_update_text(&mut self, node_id: &str, text: &str) -> Result<bool, String> {
        if self.degraded {
            return Ok(true);
        }

        let sid = self.stream_id.as_deref().unwrap_or("");
        eui_neo_ffi::safe_update_text(sid, node_id, text)
    }

    pub fn stream_close_node(&mut self, node_id: &str) -> Result<bool, String> {
        if self.degraded {
            return Ok(true);
        }

        let sid = self.stream_id.as_deref().unwrap_or("");
        eui_neo_ffi::safe_close_node(sid, node_id)
    }

    pub fn end_stream(&mut self) -> Result<bool, String> {
        if self.degraded {
            self.stream_active = false;
            return Ok(true);
        }

        let sid = self.stream_id.as_deref().unwrap_or("");
        let result = eui_neo_ffi::safe_end_stream(sid);
        if result.is_ok() {
            self.stream_active = false;
        }
        result
    }

    pub fn render_to_surface(
        &mut self,
        surface_id: &str,
        router: &mut RenderChannelRouter,
        surface: &mut RenderSurface,
    ) -> Result<String, String> {
        if self.dsl_buffer.is_empty() {
            return Ok("".into());
        }

        self.surface_id = Some(surface_id.to_string());

        if self.degraded {
            return Ok(self.dsl_buffer.clone());
        }

        let channel = router.route(surface);

        match channel {
            crate::render_channel::RenderChannel::EuiNative => {
                match crate::eui_neo_ffi::safe_render(
                    &self.dsl_buffer,
                    surface_id,
                    surface.width,
                    surface.height,
                ) {
                    Ok(status) => Ok(status),
                    Err(e) => {
                        log::warn!("TokUI DSL render failed, degrading: {}", e);
                        router.degrade(surface, DegradationReason::DslParseFailure);
                        self.degraded = true;
                        Ok(format!("[DSL render error: {}]\n\n{}", e, self.dsl_buffer))
                    }
                }
            }
            _ => Ok(self.dsl_buffer.clone()),
        }
    }

    pub fn reset(&mut self) {
        self.dsl_buffer.clear();
        self.surface_id = None;
        self.stream_id = None;
        self.stream_active = false;
        self.degraded = false;
        self.node_counter = 0;
    }

    pub fn is_tokui_dsl(content: &str) -> bool {
        let trimmed = content.trim();
        trimmed.starts_with('[') && trimmed.ends_with(']')
    }

    pub fn buffer_len(&self) -> usize {
        self.dsl_buffer.len()
    }

    pub fn is_degraded(&self) -> bool {
        self.degraded
    }

    pub fn is_streaming(&self) -> bool {
        self.stream_active
    }

    pub fn node_count(&self) -> u32 {
        self.node_counter
    }
}
