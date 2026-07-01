//! LFM Server v2 — 跨平台 IPC + ONNX 推理引擎 with state management
//!
//! 平台支持:
//!   - Linux/macOS (Unix): UDS (Unix Domain Socket)
//!   - Windows: TCP localhost (127.0.0.1:auto-port)
//!
//! 方法:
//!   ping / get_info / embed_text / update_state / reset_state
//!   get_state / get_hidden / shutdown

use ort::session::Session;
use ort::value::{Tensor, Value};
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::io::{BufRead, BufReader, Write};
use std::path::Path;
use std::sync::{Arc, Mutex};
use std::time::Instant;

const CONV_LAYER_IDS: &[usize] = &[0, 1, 3, 4, 6, 7, 9, 11, 13, 15];
const ATTN_LAYER_IDS: &[usize] = &[2, 5, 8, 10, 12, 14];
const DIM: usize = 2048;
const WINDOW: usize = 3;

// ════════════════════════════════════════════════════════════════
// 跨平台 IPC 抽象层
// ════════════════════════════════════════════════════════════════

#[cfg(unix)]
mod ipc {
    use std::os::unix::net::{UnixListener, UnixStream};
    use std::path::Path;

    pub type Listener = UnixListener;
    pub type Stream = UnixStream;

    /// 绑定 UDS socket
    pub fn bind(path: &str) -> std::io::Result<Listener> {
        if let Some(p) = Path::new(path).parent() {
            let _ = std::fs::create_dir_all(p);
        }
        let _ = std::fs::remove_file(path);
        let lis = UnixListener::bind(path)?;
        std::fs::set_permissions(path, std::os::unix::fs::PermissionsExt::from_mode(0o777)).ok();
        Ok(lis)
    }

    /// 构建 ready 消息（Unix: uds 路径）
    pub fn ready_json(path: &str, pid: u32) -> serde_json::Value {
        serde_json::json!({
            "event": "ready",
            "pid": pid,
            "uds": path,
            "ipc": "uds",
            "model": "LFM2.5-1.2B-Q4",
            "version": "2.0.0"
        })
    }

    /// 获取 listener 的 incoming 迭代器
    pub fn incoming(lis: &Listener) -> impl Iterator<Item = std::io::Result<Stream>> + '_ {
        lis.incoming()
    }
}

#[cfg(windows)]
mod ipc {
    use std::net::{TcpListener, TcpStream};

    pub type Listener = TcpListener;
    pub type Stream = TcpStream;

    /// 绑定 TCP localhost（自动分配端口）
    pub fn bind(_path: &str) -> std::io::Result<Listener> {
        // Windows: 绑定 127.0.0.1:0 让 OS 自动分配端口
        let lis = TcpListener::bind("127.0.0.1:0")?;
        lis.set_nonblocking(false).ok();
        Ok(lis)
    }

    /// 构建 ready 消息（Windows: TCP 端口）
    pub fn ready_json(path: &str, pid: u32, port: u16) -> serde_json::Value {
        serde_json::json!({
            "event": "ready",
            "pid": pid,
            "tcp_port": port,
            "ipc": "tcp",
            "model": "LFM2.5-1.2B-Q4",
            "version": "2.0.0"
        })
    }

    /// 获取 listener 的 incoming 迭代器
    pub fn incoming(lis: &Listener) -> impl Iterator<Item = std::io::Result<Stream>> + '_ {
        lis.incoming()
    }
}

// ════════════════════════════════════════════════════════════════
// Config
// ════════════════════════════════════════════════════════════════

#[derive(Debug)]
struct Config {
    model_dir: String,
    uds_path: String, // Unix: UDS 路径; Windows: 未使用（TCP 自动分配端口）
}

fn parse_args() -> Config {
    let mut model_dir = std::env::var("GALAXYOS_LFM_MODEL_DIR").unwrap_or_default();
    let mut uds_path = std::env::var("GALAXYOS_LFM_UDS_PATH").unwrap_or_default();
    if model_dir.is_empty() {
        let home = std::env::var("HOME").unwrap_or_else(|_| {
            // Windows 没有 HOME，用 USERPROFILE 或默认
            if cfg!(windows) {
                std::env::var("USERPROFILE").unwrap_or_else(|_| "C:\\Users\\Public".to_string())
            } else {
                "/root".to_string()
            }
        });
        let model_sub = if cfg!(windows) {
            "\\.openclaw\\workspace\\models\\LFM2.5-1.2B-ONNX"
        } else {
            "/.openclaw/workspace/models/LFM2.5-1.2B-ONNX"
        };
        model_dir = format!("{}{}", home, model_sub);
    }
    if uds_path.is_empty() {
        let home = std::env::var("HOME").unwrap_or_else(|_| {
            if cfg!(windows) {
                std::env::var("USERPROFILE").unwrap_or_else(|_| "C:\\Users\\Public".to_string())
            } else {
                "/root".to_string()
            }
        });
        if cfg!(windows) {
            // Windows: 路径仅用于日志，实际用 TCP
            uds_path = format!("{}\\.openclaw\\extensions\\galaxyos\\var\\lfm.tcp", home);
        } else {
            uds_path = format!("{}/.openclaw/extensions/galaxyos/var/lfm.sock", home);
        }
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

        // 动态线程数：物理核数的一半，下限 1 上限 8
        let phys_cores = std::thread::available_parallelism()
            .map(|n| n.get())
            .unwrap_or(4);
        let num_threads = (phys_cores / 2).clamp(1, 8);

        let session = Session::builder().map_err(|e| format!("builder: {}", e))?
            .with_intra_threads(num_threads).map_err(|e| format!("intra_threads: {}", e))?
            .with_inter_threads(1).map_err(|e| format!("inter_threads: {}", e))?
            .with_optimization_level(ort::session::GraphOptimizationLevel::Level3)
            .map_err(|e| format!("opt_level: {}", e))?
            .commit_from_file(path).map_err(|e| format!("load: {}", e))?;

        // GPU 检测
        let available = ort::session::get_available_providers()
            .unwrap_or_default();
        let has_cuda = available.iter().any(|p| p == "CUDAExecutionProvider");
        if has_cuda {
            eprintln!("[lfm] CUDA GPU detected — using CUDAExecutionProvider");
        } else {
            eprintln!("[lfm] CPU mode — {} threads ({} cores detected)", num_threads, phys_cores);
        }

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

        "get_info" => {
            let phys_cores = std::thread::available_parallelism()
                .map(|n| n.get()).unwrap_or(0);
            let available = ort::session::get_available_providers()
                .unwrap_or_default();
            let has_cuda = available.iter().any(|p| p == "CUDAExecutionProvider");
            let num_threads = (phys_cores / 2).clamp(1, 8);
            mk_ok(req.id, serde_json::json!({
                "version":"2.0.0","backend":"ort(Q4)","model":"LFM2.5-1.2B","dim":DIM,
                "conv_layers": CONV_LAYER_IDS, "attn_layers": ATTN_LAYER_IDS,
                "has_state": eng.has_state, "total_seq_len": eng.total_seq,
                "platform": if cfg!(windows) { "windows" } else { "unix" },
                "ipc": if cfg!(windows) { "tcp" } else { "uds" },
                "provider": if has_cuda { "cuda" } else { "cpu" },
                "num_threads": num_threads,
                "cpu_cores": phys_cores,
            }), None)
        },

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

// ════════════════════════════════════════════════════════════════
// 跨平台 main + handle_client
// ════════════════════════════════════════════════════════════════

fn main() {
    let cfg = parse_args();
    let onnx = format!("{}/onnx/model_q4.onnx", cfg.model_dir);
    if !Path::new(&onnx).exists() { eprintln!("[lfm] ❌ no model at {}", onnx); std::process::exit(1); }

    eprintln!("[lfm] 加载 {} ...", onnx);
    let eng = Arc::new(Mutex::new(match LFMEngine::new(&onnx) {
        Ok(e) => { eprintln!("[lfm] ✅ model loaded"); e }
        Err(e) => { eprintln!("[lfm] ❌ model init: {}", e); std::process::exit(1); }
    }));

    // ── 跨平台 IPC 绑定 ──
    let lis = ipc::bind(&cfg.uds_path).unwrap_or_else(|e| {
        eprintln!("[lfm] bind failed: {}", e);
        std::process::exit(1);
    });

    // Windows: 获取实际分配的 TCP 端口
    #[cfg(windows)]
    let tcp_port = {
        use std::net::ToSocketAddrs;
        lis.local_addr().map(|a| a.port()).unwrap_or(0)
    };

    // stdout ready line — Python lfm_start() 解析此行获取连接信息
    let pid = std::process::id();
    #[cfg(unix)]
    let ready = ipc::ready_json(&cfg.uds_path, pid);
    #[cfg(windows)]
    let ready = ipc::ready_json(&cfg.uds_path, pid, tcp_port);

    println!("{}", ready);
    std::io::stdout().flush().ok();

    let eng_ref = eng.clone();
    for s in ipc::incoming(&lis) {
        match s {
            Ok(s) => {
                let eng = eng_ref.clone();
                std::thread::spawn(move || handle_client(s, eng));
            }
            Err(e) => eprintln!("[lfm] accept: {}", e),
        }
    }
}

const LOCK_SPIN_COUNT: u32 = 64;
const LOCK_SPIN_INTERVAL_MS: u64 = 10;

/// 泛型 handle_client — UnixStream 和 TcpStream 都实现 Read + Write
fn handle_client<S: std::io::Read + std::io::Write + Send>(s: S, eng: Arc<Mutex<LFMEngine>>) {
    let mut r = BufReader::new(s);
    let mut line = String::new();
    loop {
        line.clear();
        match r.read_line(&mut line) { Ok(0) | Err(_) => break, Ok(_) => {} }
        let line = line.trim();
        if line.is_empty() { continue; }

        // Non-blocking lock acquisition with spin + yield.
        // Prevents permanent hang when ONNX inference in another thread
        // is slow (OOM, GPU timeout). Python client has socket timeout
        // and retry to handle transient "engine_busy" responses.
        let mut guard = match eng.try_lock() {
            Ok(g) => g,
            Err(_) => {
                let mut acquired: Option<std::sync::MutexGuard<'_, LFMEngine>> = None;
                for _ in 0..LOCK_SPIN_COUNT {
                    std::thread::sleep(std::time::Duration::from_millis(LOCK_SPIN_INTERVAL_MS));
                    if let Ok(g) = eng.try_lock() {
                        acquired = Some(g);
                        break;
                    }
                }
                match acquired {
                    Some(g) => g,
                    None => {
                        // Engine busy: client will retry
                        let _ = r.get_mut().write_all(b"{\"id\":0,\"error\":\"engine_busy\"}\n");
                        r.get_mut().flush().ok();
                        continue;
                    }
                }
            }
        };
        let resp = handle_request(line, &mut *guard);
        drop(guard);
        if let Err(_) = r.get_mut().write_all(format!("{}\n", resp).as_bytes()) {
            eprintln!("[lfm] write err");
            break;
        }
        r.get_mut().flush().ok();
    }
}
