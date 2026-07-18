use crate::eui_neo::RenderSurface;
use crate::render_channel::{DegradationReason, RenderChannelRouter};

pub struct TokuiStreamRenderer {
    dsl_buffer: String,
    surface_id: Option<String>,
    degraded: bool,
}

impl TokuiStreamRenderer {
    pub fn new() -> Self {
        Self {
            dsl_buffer: String::new(),
            surface_id: None,
            degraded: false,
        }
    }

    pub fn append_chunk(&mut self, chunk: &str) {
        self.dsl_buffer.push_str(chunk);
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
        self.degraded = false;
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
}
