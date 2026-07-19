use std::ffi::{CStr, CString};
use std::os::raw::c_char;
use std::sync::atomic::{AtomicBool, Ordering};

static NATIVE_PROBED: AtomicBool = AtomicBool::new(false);
static NATIVE_AVAILABLE: AtomicBool = AtomicBool::new(false);

#[repr(C)]
pub struct EuiNeoRenderRequest {
    pub dsl_json: *const c_char,
    pub surface_id: *const c_char,
    pub width: u32,
    pub height: u32,
}

#[repr(C)]
pub struct EuiNeoRenderResponse {
    pub status: *mut c_char,
    pub render_handle: *mut c_char,
    pub error_message: *mut c_char,
}

#[repr(C)]
pub struct EuiNeoNodeSpec {
    pub node_id: *const c_char,
    pub parent_id: *const c_char,
    pub node_type: *const c_char,
    pub text_content: *const c_char,
    pub x: f32,
    pub y: f32,
    pub width: f32,
    pub height: f32,
}

#[repr(C)]
pub struct EuiNeoPropertyUpdate {
    pub node_id: *const c_char,
    pub property: *const c_char,
    pub value: *const c_char,
}

#[cfg(feature = "eui-neo")]
extern "C" {
    fn eui_neo_init() -> bool;
    fn eui_neo_render(request: EuiNeoRenderRequest, response: *mut EuiNeoRenderResponse) -> bool;
    fn eui_neo_destroy_surface(surface_id: *const c_char) -> bool;
    fn eui_neo_check_health() -> bool;
    fn eui_neo_free_response(response: *mut EuiNeoRenderResponse);
    fn eui_neo_begin_stream(stream_id: *const c_char, surface_id: *const c_char) -> bool;
    fn eui_neo_create_node(stream_id: *const c_char, spec: EuiNeoNodeSpec) -> bool;
    fn eui_neo_update_text(stream_id: *const c_char, node_id: *const c_char, text: *const c_char) -> bool;
    fn eui_neo_update_property(stream_id: *const c_char, update: EuiNeoPropertyUpdate) -> bool;
    fn eui_neo_close_node(stream_id: *const c_char, node_id: *const c_char) -> bool;
    fn eui_neo_end_stream(stream_id: *const c_char) -> bool;
    fn eui_neo_probe() -> bool;
}

pub fn probe_native() -> bool {
    if NATIVE_PROBED.load(Ordering::Acquire) {
        return NATIVE_AVAILABLE.load(Ordering::Acquire);
    }

    let available = probe_native_impl();
    NATIVE_AVAILABLE.store(available, Ordering::Release);
    NATIVE_PROBED.store(true, Ordering::Release);
    log::info!("EUI-NEO native probe result: available={}", available);
    available
}

#[cfg(feature = "eui-neo")]
fn probe_native_impl() -> bool {
    unsafe { eui_neo_probe() }
}

#[cfg(not(feature = "eui-neo"))]
fn probe_native_impl() -> bool {
    let dll_names: &[&str] = if cfg!(target_os = "windows") {
        &["eui_neo.dll", "eui-neo.dll"]
    } else if cfg!(target_os = "macos") {
        &["libeui_neo.dylib", "libeui-neo.dylib"]
    } else {
        &["libeui_neo.so", "libeui-neo.so"]
    };

    for name in dll_names {
        if unsafe { libloading::Library::new(name).is_ok() } {
            log::info!("Found EUI-NEO native library: {}", name);
            return true;
        }
    }

    if let Ok(exe_dir) = std::env::current_exe() {
        if let Some(parent) = exe_dir.parent() {
            for name in dll_names {
                let path = parent.join(name);
                if unsafe { libloading::Library::new(&path).is_ok() } {
                    log::info!("Found EUI-NEO native library: {}", path.display());
                    return true;
                }
            }
        }
    }

    false
}

pub fn is_native_available() -> bool {
    if !NATIVE_PROBED.load(Ordering::Acquire) {
        probe_native();
    }
    NATIVE_AVAILABLE.load(Ordering::Acquire)
}

pub fn reset_probe() {
    NATIVE_PROBED.store(false, Ordering::Release);
    NATIVE_AVAILABLE.store(false, Ordering::Release);
}

#[cfg(feature = "eui-neo")]
pub fn safe_init() -> Result<bool, String> {
    Ok(unsafe { eui_neo_init() })
}

#[cfg(feature = "eui-neo")]
pub fn safe_render(
    dsl_json: &str,
    surface_id: &str,
    width: u32,
    height: u32,
) -> Result<String, String> {
    let c_dsl = CString::new(dsl_json).map_err(|e| format!("DSL CString error: {}", e))?;
    let c_sid = CString::new(surface_id).map_err(|e| format!("SurfaceID CString error: {}", e))?;

    let request = EuiNeoRenderRequest {
        dsl_json: c_dsl.as_ptr(),
        surface_id: c_sid.as_ptr(),
        width,
        height,
    };

    let mut response = EuiNeoRenderResponse {
        status: std::ptr::null_mut(),
        render_handle: std::ptr::null_mut(),
        error_message: std::ptr::null_mut(),
    };

    let ok = unsafe { eui_neo_render(request, &mut response) };

    let result = if ok {
        let status = if response.status.is_null() {
            "ok".to_string()
        } else {
            unsafe { CStr::from_ptr(response.status) }
                .to_string_lossy()
                .into_owned()
        };
        Ok(status)
    } else {
        let err = if response.error_message.is_null() {
            "Unknown FFI render error".to_string()
        } else {
            unsafe {
                CStr::from_ptr(response.error_message)
                    .to_string_lossy()
                    .into_owned()
            }
        };
        Err(err)
    };

    unsafe { eui_neo_free_response(&mut response) };

    result
}

#[cfg(feature = "eui-neo")]
pub fn safe_destroy_surface(surface_id: &str) -> Result<bool, String> {
    let c_sid = CString::new(surface_id).map_err(|e| format!("SurfaceID CString error: {}", e))?;
    Ok(unsafe { eui_neo_destroy_surface(c_sid.as_ptr()) })
}

#[cfg(feature = "eui-neo")]
pub fn safe_check_health() -> Result<bool, String> {
    Ok(unsafe { eui_neo_check_health() })
}

#[cfg(feature = "eui-neo")]
pub fn safe_begin_stream(stream_id: &str, surface_id: &str) -> Result<bool, String> {
    let c_stream = CString::new(stream_id).map_err(|e| format!("StreamID CString error: {}", e))?;
    let c_sid = CString::new(surface_id).map_err(|e| format!("SurfaceID CString error: {}", e))?;
    Ok(unsafe { eui_neo_begin_stream(c_stream.as_ptr(), c_sid.as_ptr()) })
}

#[cfg(feature = "eui-neo")]
pub fn safe_create_node(
    stream_id: &str,
    node_id: &str,
    parent_id: &str,
    node_type: &str,
    text_content: &str,
    x: f32,
    y: f32,
    width: f32,
    height: f32,
) -> Result<bool, String> {
    let c_stream = CString::new(stream_id).map_err(|e| format!("StreamID CString error: {}", e))?;
    let c_nid = CString::new(node_id).map_err(|e| format!("NodeID CString error: {}", e))?;
    let c_pid = CString::new(parent_id).map_err(|e| format!("ParentID CString error: {}", e))?;
    let c_nt = CString::new(node_type).map_err(|e| format!("NodeType CString error: {}", e))?;
    let c_tc = CString::new(text_content).map_err(|e| format!("TextContent CString error: {}", e))?;

    let spec = EuiNeoNodeSpec {
        node_id: c_nid.as_ptr(),
        parent_id: c_pid.as_ptr(),
        node_type: c_nt.as_ptr(),
        text_content: c_tc.as_ptr(),
        x,
        y,
        width,
        height,
    };

    Ok(unsafe { eui_neo_create_node(c_stream.as_ptr(), spec) })
}

#[cfg(feature = "eui-neo")]
pub fn safe_update_text(stream_id: &str, node_id: &str, text: &str) -> Result<bool, String> {
    let c_stream = CString::new(stream_id).map_err(|e| format!("StreamID CString error: {}", e))?;
    let c_nid = CString::new(node_id).map_err(|e| format!("NodeID CString error: {}", e))?;
    let c_text = CString::new(text).map_err(|e| format!("Text CString error: {}", e))?;
    Ok(unsafe { eui_neo_update_text(c_stream.as_ptr(), c_nid.as_ptr(), c_text.as_ptr()) })
}

#[cfg(feature = "eui-neo")]
pub fn safe_update_property(
    stream_id: &str,
    node_id: &str,
    property: &str,
    value: &str,
) -> Result<bool, String> {
    let c_stream = CString::new(stream_id).map_err(|e| format!("StreamID CString error: {}", e))?;
    let c_nid = CString::new(node_id).map_err(|e| format!("NodeID CString error: {}", e))?;
    let c_prop = CString::new(property).map_err(|e| format!("Property CString error: {}", e))?;
    let c_val = CString::new(value).map_err(|e| format!("Value CString error: {}", e))?;

    let update = EuiNeoPropertyUpdate {
        node_id: c_nid.as_ptr(),
        property: c_prop.as_ptr(),
        value: c_val.as_ptr(),
    };

    Ok(unsafe { eui_neo_update_property(c_stream.as_ptr(), update) })
}

#[cfg(feature = "eui-neo")]
pub fn safe_close_node(stream_id: &str, node_id: &str) -> Result<bool, String> {
    let c_stream = CString::new(stream_id).map_err(|e| format!("StreamID CString error: {}", e))?;
    let c_nid = CString::new(node_id).map_err(|e| format!("NodeID CString error: {}", e))?;
    Ok(unsafe { eui_neo_close_node(c_stream.as_ptr(), c_nid.as_ptr()) })
}

#[cfg(feature = "eui-neo")]
pub fn safe_end_stream(stream_id: &str) -> Result<bool, String> {
    let c_stream = CString::new(stream_id).map_err(|e| format!("StreamID CString error: {}", e))?;
    Ok(unsafe { eui_neo_end_stream(c_stream.as_ptr()) })
}

#[cfg(not(feature = "eui-neo"))]
pub fn safe_init() -> Result<bool, String> {
    Ok(false)
}

#[cfg(not(feature = "eui-neo"))]
pub fn safe_render(
    _dsl_json: &str,
    _surface_id: &str,
    _width: u32,
    _height: u32,
) -> Result<String, String> {
    Err("EUI-NEO feature not enabled".into())
}

#[cfg(not(feature = "eui-neo"))]
pub fn safe_destroy_surface(_surface_id: &str) -> Result<bool, String> {
    Err("EUI-NEO feature not enabled".into())
}

#[cfg(not(feature = "eui-neo"))]
pub fn safe_check_health() -> Result<bool, String> {
    Ok(false)
}

#[cfg(not(feature = "eui-neo"))]
pub fn safe_begin_stream(_stream_id: &str, _surface_id: &str) -> Result<bool, String> {
    Err("EUI-NEO feature not enabled".into())
}

#[cfg(not(feature = "eui-neo"))]
pub fn safe_create_node(
    _stream_id: &str,
    _node_id: &str,
    _parent_id: &str,
    _node_type: &str,
    _text_content: &str,
    _x: f32,
    _y: f32,
    _width: f32,
    _height: f32,
) -> Result<bool, String> {
    Err("EUI-NEO feature not enabled".into())
}

#[cfg(not(feature = "eui-neo"))]
pub fn safe_update_text(_stream_id: &str, _node_id: &str, _text: &str) -> Result<bool, String> {
    Err("EUI-NEO feature not enabled".into())
}

#[cfg(not(feature = "eui-neo"))]
pub fn safe_update_property(
    _stream_id: &str,
    _node_id: &str,
    _property: &str,
    _value: &str,
) -> Result<bool, String> {
    Err("EUI-NEO feature not enabled".into())
}

#[cfg(not(feature = "eui-neo"))]
pub fn safe_close_node(_stream_id: &str, _node_id: &str) -> Result<bool, String> {
    Err("EUI-NEO feature not enabled".into())
}

#[cfg(not(feature = "eui-neo"))]
pub fn safe_end_stream(_stream_id: &str) -> Result<bool, String> {
    Err("EUI-NEO feature not enabled".into())
}
