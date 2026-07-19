use regex::Regex;
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::collections::HashMap;
use std::time::Instant;

const CONVERSION_TIMEOUT_MS: f64 = 5.0;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DslBridgeResult {
    pub output_dsl: String,
    pub mapping_confidence: f64,
    pub unsupported_components: Vec<String>,
    pub dropped_attrs: Vec<String>,
    pub conversion_time_ms: f64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct MappingEntry {
    eui_type: String,
    #[serde(default)]
    attrs: HashMap<String, Value>,
    #[serde(default = "default_confidence")]
    confidence: f64,
    #[serde(default)]
    builder: Option<String>,
}

fn default_confidence() -> f64 {
    0.5
}

fn default_mappings() -> HashMap<&'static str, MappingEntry> {
    let mut m = HashMap::new();
    let basic = [
        ("card", "card", 1.0),
        ("p", "text", 1.0),
        ("btn", "button", 1.0),
        ("table", "dataTable", 0.9),
        ("chart", "lineChart", 0.8),
        ("img", "image", 1.0),
        ("input", "input", 1.0),
        ("list", "scrollView", 0.8),
        ("vlist", "virtualList", 0.9),
        ("menu", "contextMenu", 0.9),
        ("tabs", "tabs", 1.0),
        ("dialog", "dialog", 1.0),
        ("progress", "progress", 1.0),
        ("markdown", "markdown", 1.0),
        ("sidebar", "sidebar", 1.0),
        ("slider", "slider", 1.0),
        ("switch", "switch", 1.0),
        ("checkbox", "checkbox", 1.0),
        ("radio", "radio", 1.0),
        ("dropdown", "dropdown", 1.0),
        ("tooltip", "tooltip", 1.0),
        ("toast", "toast", 1.0),
        ("carousel", "carousel", 1.0),
        ("stepper", "stepper", 1.0),
        ("segmented", "segmented", 1.0),
        ("date-picker", "datePicker", 1.0),
        ("time-picker", "timePicker", 1.0),
        ("color-picker", "colorPicker", 1.0),
        ("bar-chart", "barChart", 1.0),
        ("pie-chart", "pieChart", 1.0),
    ];
    for (tokui, eui, conf) in basic {
        m.insert(tokui, MappingEntry {
            eui_type: eui.to_string(),
            attrs: HashMap::new(),
            confidence: conf,
            builder: None,
        });
    }

    let cognitive = [
        ("memory-panel", "panel", 1.0, "ProgressBuilder + InputBuilder + SegmentedBuilder"),
        ("rccam-progress", "panel", 1.0, "ProgressBuilder + ButtonBuilder + SliderBuilder + DropdownBuilder"),
        ("dag-tree", "panel", 1.0, "TextBuilder + ButtonBuilder (recursive)"),
        ("memory-search", "panel", 1.0, "InputBuilder + SegmentedBuilder + ScrollViewBuilder"),
        ("rccam-control", "panel", 1.0, "ButtonBuilder + SliderBuilder + DropdownBuilder"),
        ("dag-node-expand", "panel", 1.0, "TextBuilder + ButtonBuilder"),
        ("cognitive-panel", "panel", 1.0, "SidebarBuilder + TabsBuilder"),
        ("chat-renderer", "panel", 1.0, "ScrollViewBuilder + TextBuilder"),
        ("message-renderer", "panel", 1.0, "TextBuilder + MarkdownBuilder"),
    ];
    for (tokui, eui, conf, builder) in cognitive {
        let mut attrs = HashMap::new();
        attrs.insert("panel_type".to_string(), Value::String(tokui.replace("-panel", "").replace("-progress", "").replace("-tree", "").replace("-search", "").replace("-control", "").replace("-expand", "").replace("-renderer", "").to_string()));
        m.insert(tokui, MappingEntry {
            eui_type: eui.to_string(),
            attrs,
            confidence: conf,
            builder: Some(builder.to_string()),
        });
    }

    m
}

#[derive(Debug, Clone)]
struct TokenizedComponent {
    comp_type: String,
    attrs: HashMap<String, String>,
    content: String,
}

pub struct DslBridge {
    mappings: HashMap<String, MappingEntry>,
}

impl DslBridge {
    pub fn new() -> Self {
        let mut mappings = HashMap::new();
        for (k, v) in default_mappings() {
            mappings.insert(k.to_string(), v);
        }
        Self { mappings }
    }

    pub fn tokui_to_eui(&self, tokui_dsl: &str) -> DslBridgeResult {
        let start = Instant::now();
        let mut unsupported = Vec::new();
        let mut dropped = Vec::new();
        let mut total_confidence = 0.0;
        let mut mapped_count = 0;

        let tokens = self.tokenize_tokui(tokui_dsl);
        let mut eui_components = Vec::new();

        for token in &tokens {
            let mapping = self.mappings.get(&token.comp_type);

            if mapping.is_none() {
                unsupported.push(token.comp_type.clone());
                eui_components.push(self.fallback_component(token));
                continue;
            }

            let m = mapping.unwrap();
            let mut merged_attrs = m.attrs.clone();
            for (k, v) in &token.attrs {
                merged_attrs.insert(k.clone(), Value::String(v.clone()));
            }

            let mut dropped_keys = Vec::new();
            let keys: Vec<String> = merged_attrs.keys().cloned().collect();
            for k in keys {
                if k.starts_with('_') {
                    dropped.push(format!("{}.{}", token.comp_type, k));
                    dropped_keys.push(k);
                }
            }
            for k in dropped_keys {
                merged_attrs.remove(&k);
            }

            eui_components.push(serde_json::json!({
                "type": m.eui_type,
                "attrs": merged_attrs,
                "children": [],
                "content": token.content,
            }));

            total_confidence += m.confidence;
            mapped_count += 1;
        }

        let avg_confidence = total_confidence / mapped_count.max(1) as f64;
        let elapsed_ms = start.elapsed().as_secs_f64() * 1000.0;

        if elapsed_ms > CONVERSION_TIMEOUT_MS {
            log::warn!("DSL conversion took {:.1}ms (timeout: {}ms)", elapsed_ms, CONVERSION_TIMEOUT_MS);
        }

        let output = serde_json::json!({"components": eui_components}).to_string();

        DslBridgeResult {
            output_dsl: output,
            mapping_confidence: (avg_confidence * 1000.0).round() / 1000.0,
            unsupported_components: unsupported,
            dropped_attrs: dropped,
            conversion_time_ms: (elapsed_ms * 100.0).round() / 100.0,
        }
    }

    pub fn eui_to_tokui(&self, eui_dsl: &str) -> DslBridgeResult {
        let start = Instant::now();
        let mut unsupported = Vec::new();
        let mut total_confidence = 0.0;
        let mut mapped_count = 0;

        let mut reverse_map: HashMap<&str, (&str, f64)> = HashMap::new();
        for (tokui_type, mapping) in &self.mappings {
            reverse_map.insert(&mapping.eui_type, (tokui_type.as_str(), mapping.confidence));
        }

        let eui_data: Value = match serde_json::from_str(eui_dsl) {
            Ok(v) => v,
            Err(_) => {
                return DslBridgeResult {
                    output_dsl: String::new(),
                    mapping_confidence: 0.0,
                    unsupported_components: vec!["invalid_json".into()],
                    dropped_attrs: Vec::new(),
                    conversion_time_ms: 0.0,
                }
            }
        };

        let mut tokui_components = Vec::new();

        if let Some(components) = eui_data.get("components").and_then(|c| c.as_array()) {
            for comp in components {
                let eui_type = comp.get("type").and_then(|t| t.as_str()).unwrap_or("");
                let rev = reverse_map.get(eui_type);

                if rev.is_none() {
                    unsupported.push(eui_type.to_string());
                    tokui_components.push(format!("[{}]", eui_type));
                    continue;
                }

                let (tokui_type, confidence) = rev.unwrap();
                let attrs = comp.get("attrs").and_then(|a| a.as_object());
                let content = comp.get("content").and_then(|c| c.as_str()).unwrap_or("");

                let attr_str = attrs.map(|a| {
                    let pairs: Vec<String> = a.iter()
                        .filter(|(k, _)| !k.starts_with('_'))
                        .map(|(k, v)| format!("{}:{}", k, v))
                        .collect();
                    pairs.join(" ")
                }).unwrap_or_default();

                if attr_str.is_empty() {
                    tokui_components.push(format!("[{}]{}[/{}/]", tokui_type, content, tokui_type));
                } else {
                    tokui_components.push(format!("[{} {}]{}[/{}/]", tokui_type, attr_str, content, tokui_type));
                }

                total_confidence += confidence;
                mapped_count += 1;
            }
        }

        let avg_confidence = total_confidence / mapped_count.max(1) as f64;
        let elapsed_ms = start.elapsed().as_secs_f64() * 1000.0;

        DslBridgeResult {
            output_dsl: tokui_components.join(""),
            mapping_confidence: (avg_confidence * 1000.0).round() / 1000.0,
            unsupported_components: unsupported,
            dropped_attrs: Vec::new(),
            conversion_time_ms: (elapsed_ms * 100.0).round() / 100.0,
        }
    }

    pub fn register_mapping(
        &mut self,
        tokui_type: &str,
        eui_type: &str,
        confidence: f64,
    ) {
        self.mappings.insert(tokui_type.to_string(), MappingEntry {
            eui_type: eui_type.to_string(),
            attrs: HashMap::new(),
            confidence,
            builder: None,
        });
        log::info!("Registered DSL mapping: {} -> {} (confidence={})", tokui_type, eui_type, confidence);
    }

    pub fn get_mapping_stats(&self) -> serde_json::Value {
        let total = self.mappings.len();
        let cognitive: Vec<&String> = self.mappings.keys()
            .filter(|k| self.mappings.get(*k).map_or(false, |m| m.confidence >= 0.6 && m.builder.is_some()))
            .collect();
        let basic: Vec<&String> = self.mappings.keys()
            .filter(|k| self.mappings.get(*k).map_or(false, |m| m.builder.is_none()))
            .collect();

        serde_json::json!({
            "total_mappings": total,
            "cognitive_components": cognitive.len(),
            "basic_components": basic.len(),
        })
    }

    fn tokenize_tokui(&self, dsl: &str) -> Vec<TokenizedComponent> {
        let mut tokens = Vec::new();
        let re = Regex::new(r"\[(\S+?)(?:\s+([^\]]*))?\](.*?)\[/\1\]").unwrap();

        for cap in re.captures_iter(dsl) {
            let comp_type = cap.get(1).map(|m| m.as_str().to_string()).unwrap_or_default();
            let attr_str = cap.get(2).map(|m| m.as_str()).unwrap_or("");
            let content = cap.get(3).map(|m| m.as_str().trim().to_string()).unwrap_or_default();

            let mut attrs = HashMap::new();
            let attr_re = Regex::new(r"(\w+):([^\s]+)").unwrap();
            for attr_cap in attr_re.captures_iter(attr_str) {
                if let (Some(k), Some(v)) = (attr_cap.get(1), attr_cap.get(2)) {
                    attrs.insert(k.as_str().to_string(), v.as_str().to_string());
                }
            }

            tokens.push(TokenizedComponent {
                comp_type,
                attrs,
                content,
            });
        }

        if tokens.is_empty() {
            tokens.push(TokenizedComponent {
                comp_type: "p".to_string(),
                attrs: HashMap::new(),
                content: dsl.trim().to_string(),
            });
        }

        tokens
    }

    fn fallback_component(&self, token: &TokenizedComponent) -> Value {
        serde_json::json!({
            "type": "Text",
            "attrs": {"fallback": true, "original_type": token.comp_type},
            "content": token.content,
            "children": [],
        })
    }
}
