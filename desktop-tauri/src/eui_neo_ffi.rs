#[cfg(feature = "eui-neo")]
use std::ffi::{CStr, CString};
#[cfg(feature = "eui-neo")]
use std::os::raw::c_char;

#[cfg(feature = "eui-neo")]
#[repr(C)]
pub struct EuiNeoRenderRequest {
    pub dsl_json: *const c_char,
    pub surface_id: *const c_char,
    pub width: u32,
    pub height: u32,
}

#[cfg(feature = "eui-neo")]
#[repr(C)]
pub struct EuiNeoRenderResponse {
    pub status: *mut c_char,
    pub render_handle: *mut c_char,
    pub error_message: *mut c_char,
}

#[cfg(feature = "eui-neo")]
extern "C" {
    fn eui_neo_init() -> bool;
    fn eui_neo_render(request: EuiNeoRenderRequest, response: *mut EuiNeoRenderResponse) -> bool;
    fn eui_neo_destroy_surface(surface_id: *const c_char) -> bool;
    fn eui_neo_check_health() -> bool;
    fn eui_neo_free_response(response: *mut EuiNeoRenderResponse);
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
