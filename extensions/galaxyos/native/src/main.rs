//! GalaxyOS native worker — 独立二进制（stdin/stdout JSON-RPC）
//!
//! 协议与 pil_worker.py 完全兼容。
//! 底层调用 lib.rs 中的共享核心函数（同时被 PyO3 扩展使用）。

use galaxyos_native::{
    resize_core, enhance_core, ocr_preprocess_core,
    vector_dot_core, vector_cosine_core,
};
use base64::Engine;
use serde::{Deserialize, Serialize};
use std::io::{self, BufRead, Write};
use std::time::Instant;

// ── JSON-RPC 类型 ──

#[derive(Deserialize)]
struct Request {
    id: i64,
    method: String,
    #[serde(default)]
    params: serde_json::Value,
}

#[derive(Serialize)]
struct Response {
    id: i64,
    #[serde(skip_serializing_if = "Option::is_none")]
    result: Option<serde_json::Value>,
    #[serde(skip_serializing_if = "Option::is_none")]
    error: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    timing_ms: Option<f64>,
}

// ── JSON-RPC handler 包装 ──

fn parse_f32_vec(arr: &serde_json::Value) -> Result<Vec<f32>, String> {
    arr.as_array()
        .ok_or_else(|| "expected float array".to_string())?
        .iter()
        .map(|v| v.as_f64().map(|f| f as f32).ok_or_else(|| "expected number".to_string()))
        .collect()
}

fn handle_resize(params: &serde_json::Value) -> Result<serde_json::Value, String> {
    let data_b64 = params["data_b64"].as_str().unwrap_or("");
    let width = params["width"].as_u64().unwrap_or(800) as u32;
    let height = params["height"].as_u64().unwrap_or(600) as u32;
    let keep_ratio = params["keep_ratio"].as_bool().unwrap_or(true);
    let fmt_str = params["fmt"].as_str().unwrap_or("jpeg");

    let data = base64::engine::general_purpose::STANDARD
        .decode(data_b64)
        .map_err(|e| format!("base64 decode: {}", e))?;

    let (b64, size) = resize_core(&data, width, height, keep_ratio, fmt_str)?;

    Ok(serde_json::json!({
        "data_b64": b64,
        "size": size.as_slice()
    }))
}

fn handle_enhance(params: &serde_json::Value) -> Result<serde_json::Value, String> {
    let data_b64 = params["data_b64"].as_str().unwrap_or("");
    let brightness = params["brightness"].as_f64().unwrap_or(1.0) as f32;
    let contrast = params["contrast"].as_f64().unwrap_or(1.0) as f32;
    let sharpness = params["sharpness"].as_f64().unwrap_or(1.0) as f32;
    let fmt_str = params["fmt"].as_str().unwrap_or("jpeg");

    let data = base64::engine::general_purpose::STANDARD
        .decode(data_b64)
        .map_err(|e| format!("base64 decode: {}", e))?;

    let result_b64 = enhance_core(&data, brightness, contrast, sharpness, fmt_str)?;
    Ok(serde_json::json!({"data_b64": result_b64}))
}

fn handle_ocr_preprocess(params: &serde_json::Value) -> Result<serde_json::Value, String> {
    let data_b64 = params["data_b64"].as_str().unwrap_or("");
    let fmt_str = params["fmt"].as_str().unwrap_or("png");

    let data = base64::engine::general_purpose::STANDARD
        .decode(data_b64)
        .map_err(|e| format!("base64 decode: {}", e))?;

    let result_b64 = ocr_preprocess_core(&data, fmt_str)?;
    Ok(serde_json::json!({"data_b64": result_b64}))
}

fn handle_vector_dot(params: &serde_json::Value) -> Result<serde_json::Value, String> {
    let a = parse_f32_vec(&params["a"])?;
    let b = parse_f32_vec(&params["b"])?;
    let dot = vector_dot_core(&a, &b)?;
    Ok(serde_json::json!({"dot": dot}))
}

fn handle_vector_cosine(params: &serde_json::Value) -> Result<serde_json::Value, String> {
    let a = parse_f32_vec(&params["a"])?;
    let b = parse_f32_vec(&params["b"])?;
    let cosine = vector_cosine_core(&a, &b)?;
    Ok(serde_json::json!({"cosine": cosine}))
}

// ── 主循环 ──

fn main() {
    let pid = std::process::id();

    let ready = serde_json::json!({
        "id": 0,
        "event": "ready",
        "pid": pid,
        "pil_available": true,
        "vector_available": true
    });
    println!("{}", serde_json::to_string(&ready).unwrap());
    io::stdout().flush().unwrap();
    eprintln!("[galaxyos-native] ready (pid={})", pid);

    let stdin = io::stdin();
    for line in stdin.lock().lines() {
        let line = match line {
            Ok(l) => l,
            Err(_) => break,
        };
        let line = line.trim().to_string();
        if line.is_empty() {
            continue;
        }

        let req: Request = match serde_json::from_str(&line) {
            Ok(r) => r,
            Err(e) => {
                let resp = Response {
                    id: -1,
                    result: None,
                    error: Some(format!("invalid JSON: {}", e)),
                    timing_ms: None,
                };
                println!("{}", serde_json::to_string(&resp).unwrap());
                io::stdout().flush().unwrap();
                continue;
            }
        };

        let start = Instant::now();

        match req.method.as_str() {
            "shutdown" => {
                let resp = Response {
                    id: req.id,
                    result: Some(serde_json::json!("ok")),
                    error: None,
                    timing_ms: None,
                };
                println!("{}", serde_json::to_string(&resp).unwrap());
                io::stdout().flush().unwrap();
                eprintln!("[galaxyos-native] shutdown");
                std::process::exit(0);
            }
            "ping" => {
                let resp = Response {
                    id: req.id,
                    result: Some(serde_json::json!("pong")),
                    error: None,
                    timing_ms: None,
                };
                println!("{}", serde_json::to_string(&resp).unwrap());
                io::stdout().flush().unwrap();
            }
            method => {
                let handler: fn(&serde_json::Value) -> Result<serde_json::Value, String> =
                    match method {
                        "resize" => handle_resize,
                        "enhance" => handle_enhance,
                        "ocr_preprocess" => handle_ocr_preprocess,
                        "vector_dot" => handle_vector_dot,
                        "vector_cosine" => handle_vector_cosine,
                        _ => {
                            let resp = Response {
                                id: req.id,
                                result: None,
                                error: Some(format!("unknown method: {}", method)),
                                timing_ms: None,
                            };
                            println!("{}", serde_json::to_string(&resp).unwrap());
                            io::stdout().flush().unwrap();
                            continue;
                        }
                    };

                let timing_ms = start.elapsed().as_secs_f64() * 1000.0;

                match handler(&req.params) {
                    Ok(result) => {
                        let resp = Response {
                            id: req.id,
                            result: Some(result),
                            error: None,
                            timing_ms: Some(timing_ms),
                        };
                        println!("{}", serde_json::to_string(&resp).unwrap());
                    }
                    Err(e) => {
                        let resp = Response {
                            id: req.id,
                            result: None,
                            error: Some(e),
                            timing_ms: Some(timing_ms),
                        };
                        println!("{}", serde_json::to_string(&resp).unwrap());
                    }
                }
                io::stdout().flush().unwrap();
            }
        }
    }
}
