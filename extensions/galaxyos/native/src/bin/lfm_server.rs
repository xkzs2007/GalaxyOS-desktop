//! LFM Server v2 — UDS socket + ONNX 推理引擎 with state management
//!
//! 方法:
//!   ping / get_info / embed_text / update_state / reset_state
//!   get_state / get_hidden / shutdown

use ort::session::Session;
use ort::value::{Tensor, Value};
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::io::{BufRead, BufReader, Write};
use std::os::unix::net::{UnixListener, UnixStream};
use std::path::Path;
use std::sync::{Arc, Mutex};
use std::time::Instant;

const CONV_LAYER_IDS: &[usize] = &[0, 1, 3, 4, 6, 7, 9, 11, 13, 15];
const ATTN_LAYER_IDS: &[usize] = &[2, 5, 8, 10, 12, 14];
const DIM: usize = 2048;
const WINDOW: usize = 3;
// 兼容旧版 claw_worker 的 UDS 路径
const DEFAULT_UDS: &str = "extensions/galaxyos/var/lfm.sock";

#[derive(Debug)]
struct Config { model_dir: String, uds_path: String }

fn parse_args() -> Config {
    let mut model_dir = String::new();
    let mut uds_path = String::new();
    let args: Vec<String> = std::env::args().collect();
    let mut i = 1;
    while i < args.len() {
        match args[i].as_str() {
            "--model-dir" => { i += 1; if i < args.len() { model_dir = args[i].clone(); } }
            "--uds" => { i += 1; if i < args.len() { uds_path = args[i].clone(); } }
            _ => {}
        }
        i += 1;
    }
    if model_dir.is_empty() {
        let home = std::env::var("HOME").unwrap_or_else(|_| "/root".to_string());
        model_dir = format!("{}/.openclaw/workspace/models/LFM2.5-1.2B-ONNX", home);
    }
    if uds_path.is_empty() {
        let home = std::env::var("HOME").unwrap_or_else(|_| "/root".to_string());
        uds_path = format!("{}/.openclaw/{}", home, DEFAULT_UDS);
    }
    Config { model_dir, uds_path }
}

#[derive(Deserialize)]
struct Request { id: i64, method: String, #[serde(default)] params: serde_json::Value }
#[derive(Serialize)]
struct Response { id: i64, #[serde(skip_serializing_if = "Option::is_none")] result: Option<serde_json::Value>, #[serde(skip_serializing_if = "Option::is_none")] error: Option<String>, #[serde(skip_serializing_if = "Option::is_none")] timing_ms: Option<f64> }

// ── engine ──

struct LFMEngine {
    session: Session,
    input_names: Vec<String>,
    output_names: Vec<String>,
    conv_states: HashMap<String, Vec<f32>>,
    kv_caches: HashMap<String, Vec<f32>>,
    total_seq: usize,
    has_state: bool,
}

impl LFMEngine {
    fn new(path: &str) -> Result<Self, String> {
        ort::environment::init().commit();
        let session = Session::builder().map_err(|e| format!("builder: {}", e))?
            .commit_from_file(path).map_err(|e| format!("load: {}", e))?;
        let inames: Vec<String> = session.inputs().iter().map(|i| i.name().to_string()).collect();
        let onames: Vec<String> = session.outputs().iter().map(|o| o.name().to_string()).collect();
        Ok(Self {
            session, input_names: inames, output_names: onames,
            conv_states: HashMap::new(), kv_caches: HashMap::new(),
            total_seq: 0, has_state: false,
        })
    }

    fn run_and_persist(&mut self, input_ids: Vec<i64>, attention_mask: Vec<i64>)
        -> Result<Vec<f32>, String>
    {
        let seq = input_ids.len();

        // ── build feed ──
        let mut feed: HashMap<String, Value> = HashMap::new();

        let ids_b: Box<[i64]> = input_ids.into_boxed_slice();
        feed.insert("input_ids".into(), Tensor::from_array(([1usize, seq], ids_b))
            .map_err(|e| format!("ids: {}", e))?.into_dyn());

        let mask_b: Box<[i64]> = attention_mask.into_boxed_slice();
        feed.insert("attention_mask".into(), Tensor::from_array(([1usize, seq], mask_b))
            .map_err(|e| format!("mask: {}", e))?.into_dyn());

        for &layer in CONV_LAYER_IDS {
            let name = format!("past_conv.{}", layer);
            let data = self.conv_states.get(&name).cloned()
                .unwrap_or_else(|| vec![0.0f32; DIM * WINDOW]);
            feed.insert(name, Tensor::from_array((&[1usize, DIM, WINDOW][..], data.into_boxed_slice()))
                .map_err(|e| format!("conv tensor: {}", e))?.into_dyn());
        }

        // KV cache seq_len 从实际数据长度反推，避免 total_seq 和初始 1-dummy 不一致
        let past_len = if let Some(data) = self.kv_caches.get("past_key_values.2.key") {
            data.len() / (8 * 64)
        } else {
            1usize
        };
        for &layer in ATTN_LAYER_IDS {
            for suf in ["key", "value"] {
                let name = format!("past_key_values.{}.{}", layer, suf);
                let data = self.kv_caches.get(&name).cloned()
                    .unwrap_or_else(|| vec![0.0f32; 8 * past_len * 64]);
                feed.insert(name, Tensor::from_array((&[1usize, 8, past_len, 64][..], data.into_boxed_slice()))
                    .map_err(|e| format!("kv tensor: {}", e))?.into_dyn());
            }
        }

        // ── run ──
        let outputs = self.session.run(feed)
            .map_err(|e| format!("run: {}", e))?;

        // ── absorb conv states ──
        for &layer in CONV_LAYER_IDS {
            let oname = format!("present_conv.{}", layer);
            let sname = format!("past_conv.{}", layer);
            let idx = match self.output_names.iter().position(|n| *n == oname) { Some(i) => i, None => continue };
            if let Ok((_s, data)) = outputs[idx].try_extract_tensor::<f32>() {
                let ns = data.len() / DIM;
                if ns >= WINDOW {
                    let mut buf = vec![0.0f32; DIM * WINDOW];
                    for i in 0..DIM {
                        for j in 0..WINDOW {
                            buf[i + j * DIM] = data[(ns - WINDOW + j) * DIM + i];
                        }
                    }
                    self.conv_states.insert(sname, buf);
                }
            }
        }

        // ── absorb KV cache ──
        for &layer in ATTN_LAYER_IDS {
            for suf in ["key", "value"] {
                let oname = format!("present.{}.{}", layer, suf);
                let sname = format!("past_key_values.{}.{}", layer, suf);
                let idx = match self.output_names.iter().position(|n| *n == oname) { Some(i) => i, None => continue };
                if let Ok((_s, data)) = outputs[idx].try_extract_tensor::<f32>() {
                    self.kv_caches.insert(sname, data.to_vec());
                }
            }
        }

        // ── extract embedding from conv15 ──
        let eidx = self.output_names.iter().position(|n| n == "present_conv.15")
            .ok_or_else(|| "no conv15".to_string())?;
        let (_s, data) = outputs[eidx].try_extract_tensor::<f32>()
            .map_err(|e| format!("extract: {}", e))?;
        let ns = data.len() / DIM;
        if ns == 0 { return Err("empty conv15".into()); }
        let mut emb = vec![0.0f32; DIM];
        for i in 0..DIM {
            let mut s = 0.0f32;
            for j in 0..ns { s += data[i + j * DIM]; }
            emb[i] = s / ns as f32;
        }

        Ok(emb)
    }

    fn embed_text(&mut self, ids: Vec<i64>, mask: Vec<i64>) -> Result<Vec<f32>, String> {
        let seq = ids.len();
        let mut feed: HashMap<String, Value> = HashMap::new();
        let ids_b: Box<[i64]> = ids.into_boxed_slice();
        feed.insert("input_ids".into(), Tensor::from_array(([1usize, seq], ids_b))
            .map_err(|e| format!("ids: {}", e))?.into_dyn());
        let mask_b: Box<[i64]> = mask.into_boxed_slice();
        feed.insert("attention_mask".into(), Tensor::from_array(([1usize, seq], mask_b))
            .map_err(|e| format!("mask: {}", e))?.into_dyn());
        // all past_* zero (unused by embed_text, but model requires them)
        for inp in &self.input_names {
            let n = inp.as_str();
            if n == "input_ids" || n == "attention_mask" || n == "position_ids" { continue; }
            let inp_obj = self.session.inputs().iter().find(|i| i.name() == n).unwrap();
            let si = inp_obj.dtype().tensor_shape().ok_or("no shape")?;
            let sh: Vec<usize> = si.iter().map(|d| if *d <= 0 { 1usize } else { *d as usize }).collect();
            let num: usize = sh.iter().product();
            feed.insert(n.to_string(), Tensor::from_array((&sh[..], vec![0.0f32; num].into_boxed_slice()))
                .map_err(|e| format!("{}: {}", n, e))?.into_dyn());
        }
        let outputs = self.session.run(feed).map_err(|e| format!("run: {}", e))?;
        let eidx = self.output_names.iter().position(|n| n == "present_conv.15")
            .ok_or_else(|| "no conv15".to_string())?;
        let (_s, data) = outputs[eidx].try_extract_tensor::<f32>()
            .map_err(|e| format!("extract: {}", e))?;
        let ns = data.len() / DIM;
        if ns == 0 { return Err("empty conv15".into()); }
        let mut emb = vec![0.0f32; DIM];
        for i in 0..DIM {
            let mut s = 0.0f32;
            for j in 0..ns { s += data[i + j * DIM]; }
            emb[i] = s / ns as f32;
        }
        Ok(emb)
    }

    fn update_state(&mut self, ids: Vec<i64>) -> Result<Vec<f32>, String> {
        let new_n = ids.len();
        let total = self.total_seq + new_n;
        // mask 只对新增 token（past context 由 conv state 和 KV cache 隐含携带）
        let mask = vec![1i64; new_n];
        let emb = self.run_and_persist(ids, mask)?;
        self.total_seq = total;
        self.has_state = true;
        Ok(emb)
    }

    fn reset(&mut self) {
        self.conv_states.clear();
        self.kv_caches.clear();
        self.total_seq = 0;
        self.has_state = false;
    }

    fn get_state_embedding(&self) -> Vec<f32> {
        if !self.has_state { return vec![0.0f32; DIM]; }
        if let Some(data) = self.conv_states.get("past_conv.15") {
            let mut emb = vec![0.0f32; DIM];
            for i in 0..DIM {
                let mut s = 0.0f32;
                for j in 0..WINDOW { s += data[i + j * DIM]; }
                emb[i] = s / WINDOW as f32;
            }
            return emb;
        }
        vec![0.0f32; DIM]
    }

    fn get_hidden(&mut self, ids: Vec<i64>, layers: &[usize]) -> Result<HashMap<String, Vec<f32>>, String> {
        let new_n = ids.len();
        let mask = vec![1i64; new_n];
        let emb = self.run_and_persist(ids, mask)?;
        self.total_seq += new_n;
        self.has_state = true;

        let mut result = HashMap::new();
        for &layer in layers {
            if CONV_LAYER_IDS.contains(&layer) {
                // state 以 past_conv.N 存储，返回用 present_conv.N 命名
                let store_name = format!("past_conv.{}", layer);
                let show_name = format!("present_conv.{}", layer);
                if let Some(data) = self.conv_states.get(&store_name) {
                    result.insert(show_name, data.clone());
                }
            }
        }
        result.insert("embedding".into(), emb);
        Ok(result)
    }
}

fn handle_request(line: &str, eng: &mut LFMEngine) -> String {
    let req: Request = match serde_json::from_str(line) {
        Ok(r) => r, Err(e) => return mk_err(-1, &format!("bad json: {}", e), None),
    };
    let start = Instant::now();

    match req.method.as_str() {
        "ping" => mk_ok(req.id, serde_json::json!("pong"), None),

        "shutdown" => { let _ = mk_ok(req.id, serde_json::json!("ok"), None); std::process::exit(0); }

        "get_info" => mk_ok(req.id, serde_json::json!({
            "version":"2.0.0","backend":"ort(Q4)","model":"LFM2.5-1.2B","dim":DIM,
            "conv_layers": CONV_LAYER_IDS, "attn_layers": ATTN_LAYER_IDS,
            "has_state": eng.has_state, "total_seq_len": eng.total_seq,
        }), None),

        "embed_text" => {
            let ids = extract_ids(&req.params);
            let msk = extract_msk(&req.params, ids.len());
            let ms = start.elapsed().as_secs_f64() * 1000.0;
            match eng.embed_text(ids, msk) {
                Ok(e) => mk_ok(req.id, serde_json::json!({"embedding": v32(&e), "dim": e.len()}), Some(ms)),
                Err(e) => mk_err(req.id, &e, Some(ms)),
            }
        }

        "update_state" => {
            let ids = extract_ids(&req.params);
            if ids.is_empty() { return mk_err(req.id, "empty ids", Some(0.0)); }
            let ms = start.elapsed().as_secs_f64() * 1000.0;
            match eng.update_state(ids) {
                Ok(e) => mk_ok(req.id, serde_json::json!({"embedding": v32(&e), "dim": e.len(), "total_seq_len": eng.total_seq}), Some(ms)),
                Err(e) => mk_err(req.id, &e, Some(ms)),
            }
        }

        "reset_state" => { eng.reset(); mk_ok(req.id, serde_json::json!({"ok":true}), None) }

        "get_state" => {
            let e = eng.get_state_embedding();
            mk_ok(req.id, serde_json::json!({"embedding": v32(&e), "dim": e.len(), "initialized": eng.has_state, "total_seq_len": eng.total_seq}), None)
        }

        "get_hidden" => {
            let ids = extract_ids(&req.params);
            let layers: Vec<usize> = req.params["layers"].as_array()
                .map(|a| a.iter().filter_map(|v| v.as_u64()).map(|v| v as usize).collect())
                .unwrap_or_else(|| CONV_LAYER_IDS.to_vec());
            let ms = start.elapsed().as_secs_f64() * 1000.0;
            match eng.get_hidden(ids, &layers) {
                Ok(h) => {
                    let mut out = serde_json::Map::new();
                    for (k, v) in h { out.insert(k, serde_json::json!(v32(&v))); }
                    mk_ok(req.id, serde_json::Value::Object(out), Some(ms))
                }
                Err(e) => mk_err(req.id, &e, Some(ms)),
            }
        }

        _ => mk_err(req.id, &format!("unknown: {}", req.method), None),
    }
}

fn extract_ids(p: &serde_json::Value) -> Vec<i64> {
    p["input_ids"].as_array().map(|a| a.iter().map(|v| v.as_i64().unwrap_or(0)).collect()).unwrap_or_default()
}
fn extract_msk(p: &serde_json::Value, d: usize) -> Vec<i64> {
    p["attention_mask"].as_array().map(|a| a.iter().map(|v| v.as_i64().unwrap_or(1)).collect()).unwrap_or_else(|| vec![1i64; d])
}
fn v32(v: &[f32]) -> Vec<serde_json::Value> { v.iter().map(|x| serde_json::json!(x)).collect() }
fn mk_ok(id: i64, r: serde_json::Value, t: Option<f64>) -> String {
    serde_json::to_string(&Response { id, result: Some(r), error: None, timing_ms: t }).unwrap()
}
fn mk_err(id: i64, msg: &str, t: Option<f64>) -> String {
    serde_json::to_string(&Response { id, result: None, error: Some(msg.into()), timing_ms: t }).unwrap()
}

fn main() {
    let cfg = parse_args();
    let onnx = format!("{}/onnx/model_q4.onnx", cfg.model_dir);
    if !Path::new(&onnx).exists() { eprintln!("[lfm] ❌ no model"); std::process::exit(1); }

    eprintln!("[lfm] 加载 {} ...", onnx);
    let eng = Arc::new(Mutex::new(match LFMEngine::new(&onnx) {
        Ok(e) => { eprintln!("[lfm] ✅ loaded"); e }
        Err(e) => { eprintln!("[lfm] ❌ {}", e); std::process::exit(1); }
    }));

    if let Some(p) = Path::new(&cfg.uds_path).parent() { let _ = std::fs::create_dir_all(p); }
    let _ = std::fs::remove_file(&cfg.uds_path);
    let lis = UnixListener::bind(&cfg.uds_path).unwrap_or_else(|e| { eprintln!("[lfm] bind: {}", e); std::process::exit(1); });
    std::fs::set_permissions(&cfg.uds_path, std::os::unix::fs::PermissionsExt::from_mode(0o777)).ok();
    eprintln!("[lfm] 🚀 {}", cfg.uds_path);
    println!("{}", serde_json::json!({"event":"ready","pid":std::process::id(),"uds":cfg.uds_path,"model":"LFM2.5-1.2B-Q4","version":"2.0.0"}));
    std::io::stdout().flush().ok();

    let eng_ref = eng.clone();
    for s in lis.incoming() {
        match s {
            Ok(s) => {
                let eng = eng_ref.clone();
                std::thread::spawn(move || handle_client(s, eng));
            }
            Err(e) => eprintln!("[lfm] accept: {}", e),
        }
    }
}

fn handle_client(s: UnixStream, eng: Arc<Mutex<LFMEngine>>) {
    let mut r = BufReader::new(&s);
    let mut w = &s;
    let mut line = String::new();
    loop {
        line.clear();
        match r.read_line(&mut line) { Ok(0) | Err(_) => break, Ok(_) => {} }
        let line = line.trim();
        if line.is_empty() { continue; }
        let mut guard = eng.lock().unwrap();
        let resp = handle_request(line, &mut *guard);
        drop(guard);
        if let Err(e) = writeln!(w, "{}", resp) { eprintln!("[lfm] write: {}", e); break; }
        w.flush().ok();
    }
}
