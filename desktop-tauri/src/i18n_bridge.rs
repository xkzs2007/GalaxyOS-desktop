use std::collections::HashMap;
use std::fs;
use std::path::PathBuf;

pub struct EuiNeoI18nBridge {
    translations: HashMap<String, HashMap<String, String>>,
    current_locale: String,
}

impl EuiNeoI18nBridge {
    pub fn new() -> Self {
        let mut bridge = Self {
            translations: HashMap::new(),
            current_locale: "zh".to_string(),
        };
        bridge.load_translations();
        bridge
    }

    pub fn translate(&self, key: &str) -> String {
        if let Some(locale_map) = self.translations.get(&self.current_locale) {
            if let Some(value) = locale_map.get(key) {
                return value.clone();
            }
        }
        if self.current_locale != "zh" {
            if let Some(locale_map) = self.translations.get("zh") {
                if let Some(value) = locale_map.get(key) {
                    return value.clone();
                }
            }
        }
        key.to_string()
    }

    pub fn set_locale(&mut self, locale: &str) {
        if locale == "zh" || locale == "en" {
            self.current_locale = locale.to_string();
        }
    }

    pub fn inject_into_dsl(&self, dsl: &str) -> String {
        let mut result = dsl.to_string();
        let re = regex::Regex::new(r"\{\{i18n:([^}]+)\}\}").unwrap_or_else(|_| {
            log::warn!("Regex compilation failed for i18n DSL injection");
            return regex::Regex::new(r"$^").unwrap();
        });
        for cap in re.captures_iter(dsl) {
            if let Some(key) = cap.get(1) {
                let translated = self.translate(key.as_str());
                result = result.replace(&format!("{{{{i18n:{}}}}}", key.as_str()), &translated);
            }
        }
        result
    }

    fn load_translations(&mut self) {
        let translations_dir = self._resolve_translations_dir();
        if !translations_dir.exists() {
            log::info!("Native translations dir not found, using empty translations");
            return;
        }

        for locale in &["zh", "en"] {
            let mut locale_map = HashMap::new();
            let file_path = translations_dir.join(format!("{}.json", locale));
            if let Ok(content) = fs::read_to_string(&file_path) {
                if let Ok(json) = serde_json::from_str::<serde_json::Value>(&content) {
                    Self::flatten_json(&json, "", &mut locale_map);
                }
            }
            self.translations.insert(locale.to_string(), locale_map);
        }

        log::info!(
            "EuiNeoI18nBridge loaded: zh={} keys, en={} keys",
            self.translations.get("zh").map(|m| m.len()).unwrap_or(0),
            self.translations.get("en").map(|m| m.len()).unwrap_or(0),
        );
    }

    fn flatten_json(json: &serde_json::Value, prefix: &str, map: &mut HashMap<String, String>) {
        if let serde_json::Value::Object(obj) = json {
            for (key, value) in obj {
                let new_prefix = if prefix.is_empty() {
                    key.clone()
                } else {
                    format!("{}.{}", prefix, key)
                };
                Self::flatten_json(value, &new_prefix, map);
            }
        } else {
            map.insert(prefix.to_string(), json.to_string().trim_matches('"').to_string());
        }
    }

    fn _resolve_translations_dir(&self) -> PathBuf {
        if let Ok(exe_dir) = std::env::current_exe() {
            if let Some(dir) = exe_dir.parent() {
                let native_dir = dir.join("native_translations");
                if native_dir.exists() {
                    return native_dir;
                }
            }
        }
        PathBuf::from("native_translations")
    }
}