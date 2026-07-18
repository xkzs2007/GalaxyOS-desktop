use crate::eui_neo::RenderSurface;
use std::collections::HashMap;
use std::fmt;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub enum RenderChannel {
    EuiNative,
    WebviewDom,
    PlainText,
}

impl fmt::Display for RenderChannel {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            RenderChannel::EuiNative => write!(f, "eui_native"),
            RenderChannel::WebviewDom => write!(f, "webview_dom"),
            RenderChannel::PlainText => write!(f, "plain_text"),
        }
    }
}

use serde::{Deserialize, Serialize};

#[derive(Debug, Clone)]
pub struct DegradationRecord {
    pub surface_id: String,
    pub reason: DegradationReason,
    pub target_channel: RenderChannel,
    pub timestamp: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub enum DegradationReason {
    FfiTimeout,
    DslParseFailure,
    NativeUnavailable,
    SurfaceCrash,
}

pub struct RenderChannelRouter {
    stats: HashMap<RenderChannel, u32>,
    degradation_log: Vec<DegradationRecord>,
}

impl RenderChannelRouter {
    pub fn new() -> Self {
        let mut stats = HashMap::new();
        stats.insert(RenderChannel::EuiNative, 0);
        stats.insert(RenderChannel::WebviewDom, 0);
        stats.insert(RenderChannel::PlainText, 0);
        Self {
            stats,
            degradation_log: Vec::new(),
        }
    }

    pub fn route(&self, surface: &RenderSurface) -> RenderChannel {
        if surface.degraded {
            match surface.active_channel.as_str() {
                "eui_native" => RenderChannel::EuiNative,
                "plain_text" => RenderChannel::PlainText,
                _ => RenderChannel::WebviewDom,
            }
        } else if surface.active_channel == "eui_native" {
            RenderChannel::EuiNative
        } else if surface.active_channel == "plain_text" {
            RenderChannel::PlainText
        } else {
            RenderChannel::WebviewDom
        }
    }

    pub fn force_channel(&mut self, surface: &mut RenderSurface, channel: RenderChannel) {
        surface.active_channel = channel.to_string();
        if matches!(
            channel,
            RenderChannel::WebviewDom | RenderChannel::PlainText
        ) {
            surface.degraded = true;
        }
        *self.stats.entry(channel).or_insert(0) += 1;
    }

    pub fn degrade(
        &mut self,
        surface: &mut RenderSurface,
        reason: DegradationReason,
    ) -> RenderChannel {
        let current = self.route(surface);
        let target = match current {
            RenderChannel::EuiNative => RenderChannel::WebviewDom,
            RenderChannel::WebviewDom => RenderChannel::PlainText,
            RenderChannel::PlainText => RenderChannel::PlainText,
        };

        surface.active_channel = target.to_string();
        surface.degraded = true;

        let now = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap_or_default()
            .as_millis() as u64;

        self.degradation_log.push(DegradationRecord {
            surface_id: surface.surface_id.clone(),
            reason,
            target_channel: target,
            timestamp: now,
        });

        *self.stats.entry(target).or_insert(0) += 1;
        log::warn!(
            "Surface {} degraded to {} channel",
            surface.surface_id,
            target
        );

        target
    }

    pub fn get_stats(&self) -> &HashMap<RenderChannel, u32> {
        &self.stats
    }

    pub fn degradation_count(&self) -> usize {
        self.degradation_log.len()
    }
}
