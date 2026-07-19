use crate::eui_neo::RenderSurface;
use crate::eui_neo_ffi;
use crate::render_channel::{DegradationReason, RenderChannel, RenderChannelRouter};
use crate::spring_animation::SpringAnimationEngine;
use crate::tokui_renderer::TokuiStreamRenderer;

pub struct RenderPipeline {
    channel: RenderChannel,
    router: RenderChannelRouter,
    renderer: TokuiStreamRenderer,
    spring: SpringAnimationEngine,
}

impl RenderPipeline {
    pub fn new(native_available: bool) -> Self {
        let channel = if native_available {
            RenderChannel::EuiNative
        } else {
            RenderChannel::WebviewDom
        };
        Self {
            channel,
            router: RenderChannelRouter::new(),
            renderer: TokuiStreamRenderer::new(),
            spring: SpringAnimationEngine::new(
                crate::eui_neo::SpringAnimationConfig::panel_spring(),
            ),
        }
    }

    pub fn on_sse_chunk(
        &mut self,
        chunk: &str,
        surface: &mut RenderSurface,
    ) -> Result<String, String> {
        self.renderer.append_chunk(chunk);

        match self.channel {
            RenderChannel::EuiNative => {
                let surface_id = surface.surface_id.clone();
                let result =
                    self.renderer
                        .render_to_surface(&surface_id, &mut self.router, surface);
                if self.renderer.is_degraded() {
                    self.auto_degrade(surface, DegradationReason::DslParseFailure);
                }
                result
            }
            RenderChannel::WebviewDom | RenderChannel::PlainText => Ok(chunk.to_string()),
        }
    }

    pub fn begin_sse_stream(
        &mut self,
        stream_id: &str,
        surface: &mut RenderSurface,
    ) -> Result<bool, String> {
        match self.channel {
            RenderChannel::EuiNative => {
                let result = self.renderer.begin_stream(stream_id, &surface.surface_id);
                if result.is_err() {
                    log::warn!("Failed to begin native stream, degrading");
                    self.auto_degrade(surface, DegradationReason::NativeUnavailable);
                }
                result
            }
            RenderChannel::WebviewDom | RenderChannel::PlainText => {
                self.renderer.begin_stream(stream_id, &surface.surface_id)
            }
        }
    }

    pub fn on_sse_stream_chunk(
        &mut self,
        chunk: &str,
        node_id: &str,
        surface: &mut RenderSurface,
    ) -> Result<String, String> {
        self.renderer.append_chunk(chunk);

        match self.channel {
            RenderChannel::EuiNative => {
                if let Err(e) = self.renderer.stream_update_text(node_id, chunk) {
                    log::warn!("Stream update_text failed: {}, falling back to full render", e);
                    let surface_id = surface.surface_id.clone();
                    return self.renderer.render_to_surface(&surface_id, &mut self.router, surface);
                }
                Ok(chunk.to_string())
            }
            RenderChannel::WebviewDom | RenderChannel::PlainText => Ok(chunk.to_string()),
        }
    }

    pub fn end_sse_stream(
        &mut self,
        _surface: &mut RenderSurface,
    ) -> Result<bool, String> {
        let result = self.renderer.end_stream();
        if result.is_err() && self.channel == RenderChannel::EuiNative {
            log::warn!("Failed to end native stream");
        }
        result
    }

    pub fn render_full(
        &mut self,
        dsl: &str,
        surface: &mut RenderSurface,
    ) -> Result<String, String> {
        match self.channel {
            RenderChannel::EuiNative => {
                match eui_neo_ffi::safe_render(
                    dsl,
                    &surface.surface_id,
                    surface.width,
                    surface.height,
                ) {
                    Ok(status) => Ok(status),
                    Err(e) => {
                        log::warn!("Full render failed, degrading: {}", e);
                        self.auto_degrade(surface, DegradationReason::DslParseFailure);
                        Ok(dsl.to_string())
                    }
                }
            }
            RenderChannel::WebviewDom | RenderChannel::PlainText => Ok(dsl.to_string()),
        }
    }

    fn auto_degrade(&mut self, surface: &mut RenderSurface, reason: DegradationReason) {
        let new_channel = self.router.degrade(surface, reason);
        self.channel = new_channel;
        log::warn!("RenderPipeline auto-degraded to {}", self.channel);
    }

    pub fn force_channel(&mut self, surface: &mut RenderSurface, channel: RenderChannel) {
        self.router.force_channel(surface, channel);
        self.channel = channel;
    }

    pub fn current_channel(&self) -> RenderChannel {
        self.channel
    }

    pub fn spring_update(&mut self, delta_ms: u32) -> f64 {
        self.spring.update(delta_ms)
    }

    pub fn spring_start(&mut self, from: f64, to: f64) {
        let config = crate::eui_neo::SpringAnimationConfig::panel_spring();
        self.spring.start(&config, from, to);
    }

    pub fn spring_interrupt(&mut self, new_target: f64) {
        self.spring.interrupt(new_target);
    }

    pub fn spring_is_running(&self) -> bool {
        self.spring.is_running()
    }

    pub fn renderer_buffer_len(&self) -> usize {
        self.renderer.buffer_len()
    }

    pub fn reset_renderer(&mut self) {
        self.renderer.reset();
    }

    pub fn degradation_count(&self) -> usize {
        self.router.degradation_count()
    }

    pub fn is_streaming(&self) -> bool {
        self.renderer.is_streaming()
    }

    pub fn stream_node_count(&self) -> u32 {
        self.renderer.node_count()
    }
}
