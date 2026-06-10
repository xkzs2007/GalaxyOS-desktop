//! GalaxyOS native shared library — 图像处理 + 向量计算核心
//!
//! 双用途：
//! 1. Python 原生扩展（PyO3） → `import galaxyos_native`
//! 2. 被 main.rs 引用（独立二进制 stdin/stdout JSON-RPC）

use base64::Engine;
use image::{DynamicImage, ImageFormat};
use pyo3::prelude::*;
use std::collections::HashMap;

// ═══════════════════════════════════════════════════════════
// 共享核心函数（纯 Rust，不依赖 PyO3/serde_json）
// ═══════════════════════════════════════════════════════════

/// 图像缩放。返回 (base64 编码图片, [width, height])。
pub fn resize_core(data: &[u8], width: u32, height: u32, keep_ratio: bool, fmt: &str) -> Result<(String, [u32; 2]), String> {
    let img = image::load_from_memory(data).map_err(|e| format!("load: {}", e))?;
    let img = if img.color().has_alpha() {
        DynamicImage::ImageRgb8(img.to_rgb8())
    } else {
        img
    };

    let (w, h) = (img.width(), img.height());
    let (new_w, new_h) = if keep_ratio {
        let ratio = (width as f64 / w as f64).min(height as f64 / h as f64);
        ((w as f64 * ratio) as u32, (h as f64 * ratio) as u32)
    } else {
        (width, height)
    };

    let resized = img.resize(new_w, new_h, image::imageops::FilterType::Lanczos3);

    let format = match fmt.to_lowercase().as_str() {
        "png" => ImageFormat::Png,
        "webp" => ImageFormat::WebP,
        _ => ImageFormat::Jpeg,
    };

    let mut buf = std::io::Cursor::new(Vec::new());
    resized.write_to(&mut buf, format).map_err(|e| format!("encode: {}", e))?;
    let b64 = base64::engine::general_purpose::STANDARD.encode(buf.into_inner());

    Ok((b64, [new_w, new_h]))
}

/// 图像增强（亮度/对比度/锐化）。返回 base64 编码图片。
pub fn enhance_core(data: &[u8], brightness: f32, contrast: f32, sharpness: f32, fmt: &str) -> Result<String, String> {
    let mut img = image::load_from_memory(data).map_err(|e| format!("load: {}", e))?;
    if img.color().has_alpha() {
        img = DynamicImage::ImageRgb8(img.to_rgb8());
    }

    if (brightness - 1.0).abs() > 0.001 {
        img = img.brighten((brightness - 1.0) as i32);
    }
    if (contrast - 1.0).abs() > 0.001 {
        img = img.adjust_contrast(contrast);
    }
    if (sharpness - 1.0).abs() > 0.001 {
        let sigma = ((sharpness - 1.0) * 1.5).max(0.1);
        img = img.blur(sigma);
        img = img.brighten(-((sharpness - 1.0) * 10.0) as i32);
        img = img.adjust_contrast(sharpness);
    }

    let format = match fmt.to_lowercase().as_str() {
        "png" => ImageFormat::Png,
        "webp" => ImageFormat::WebP,
        _ => ImageFormat::Jpeg,
    };

    let mut buf = std::io::Cursor::new(Vec::new());
    img.write_to(&mut buf, format).map_err(|e| format!("encode: {}", e))?;
    Ok(base64::engine::general_purpose::STANDARD.encode(buf.into_inner()))
}

/// OCR 预处理（灰度 + 降噪 + 对比度增强）。返回 base64 编码图片。
pub fn ocr_preprocess_core(data: &[u8], fmt: &str) -> Result<String, String> {
    let img = image::load_from_memory(data).map_err(|e| format!("load: {}", e))?;
    let gray = img.grayscale();
    let denoised = gray.blur(0.8);
    let enhanced = denoised.adjust_contrast(1.5);

    let format = match fmt.to_lowercase().as_str() {
        "png" => ImageFormat::Png,
        "webp" => ImageFormat::WebP,
        _ => ImageFormat::Png,
    };

    let mut buf = std::io::Cursor::new(Vec::new());
    enhanced.write_to(&mut buf, format).map_err(|e| format!("encode: {}", e))?;
    Ok(base64::engine::general_purpose::STANDARD.encode(buf.into_inner()))
}

/// 向量点积（SIMD 自动向量化）
pub fn vector_dot_core(a: &[f32], b: &[f32]) -> Result<f32, String> {
    if a.len() != b.len() {
        return Err(format!("dimension mismatch: {} vs {}", a.len(), b.len()));
    }
    Ok(a.iter().zip(b.iter()).map(|(x, y)| x * y).sum())
}

/// 向量余弦相似度（SIMD 自动向量化）
pub fn vector_cosine_core(a: &[f32], b: &[f32]) -> Result<f32, String> {
    if a.len() != b.len() {
        return Err(format!("dimension mismatch: {} vs {}", a.len(), b.len()));
    }
    let dot: f32 = a.iter().zip(b.iter()).map(|(x, y)| x * y).sum();
    let norm_a: f32 = a.iter().map(|x| x * x).sum::<f32>().sqrt();
    let norm_b: f32 = b.iter().map(|x| x * x).sum::<f32>().sqrt();
    if norm_a > 0.0 && norm_b > 0.0 {
        Ok(dot / (norm_a * norm_b))
    } else {
        Ok(0.0)
    }
}

/// 批量向量余弦相似度 — 一对多（SIMD 自动向量化）
pub fn vector_batch_cosine_core(query: &[f32], candidates: &[Vec<f32>]) -> Vec<f32> {
    candidates
        .iter()
        .map(|c| vector_cosine_core(query, c).unwrap_or(0.0))
        .collect()
}

// ═══════════════════════════════════════════════════════════
// PyO3 Python 绑定
// ═══════════════════════════════════════════════════════════

/// 图像缩放（Python 接口）
#[pyfunction]
fn resize(data: Vec<u8>, width: u32, height: u32, keep_ratio: Option<bool>, fmt: Option<&str>) -> PyResult<HashMap<String, pyo3::PyObject>> {
    Python::with_gil(|py| {
        let b64 = resize_core(&data, width, height, keep_ratio.unwrap_or(true), fmt.unwrap_or("jpeg"))
            .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e))?;
        let mut result = HashMap::new();
        result.insert("data_b64".into(), b64.0.into_pyobject(py).unwrap().into());
        result.insert("size".into(), {
            let list = pyo3::types::PyList::new(
                py,
                &[b64.1[0].into_pyobject(py).unwrap(), b64.1[1].into_pyobject(py).unwrap()],
            ).unwrap();
            list.into()
        });
        Ok(result)
    })
}

/// 图像增强（Python 接口）
#[pyfunction]
fn enhance(data: Vec<u8>, brightness: Option<f32>, contrast: Option<f32>, sharpness: Option<f32>, fmt: Option<&str>) -> PyResult<String> {
    enhance_core(&data, brightness.unwrap_or(1.0), contrast.unwrap_or(1.0), sharpness.unwrap_or(1.0), fmt.unwrap_or("jpeg"))
        .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e))
}

/// OCR 预处理（Python 接口）
#[pyfunction]
fn ocr_preprocess(data: Vec<u8>, fmt: Option<&str>) -> PyResult<String> {
    ocr_preprocess_core(&data, fmt.unwrap_or("png"))
        .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e))
}

/// 向量点积（Python 接口）
#[pyfunction]
fn vector_dot(a: Vec<f32>, b: Vec<f32>) -> PyResult<f32> {
    vector_dot_core(&a, &b)
        .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e))
}

/// 向量余弦相似度（Python 接口）
#[pyfunction]
fn vector_cosine(a: Vec<f32>, b: Vec<f32>) -> PyResult<f32> {
    vector_cosine_core(&a, &b)
        .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e))
}

/// 批量向量余弦相似度（Python 接口）
#[pyfunction]
fn vector_batch_cosine(query: Vec<f32>, candidates: Vec<Vec<f32>>) -> Vec<f32> {
    vector_batch_cosine_core(&query, &candidates)
}

/// Python 模块定义
#[pymodule]
fn galaxyos_native(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(resize, m)?)?;
    m.add_function(wrap_pyfunction!(enhance, m)?)?;
    m.add_function(wrap_pyfunction!(ocr_preprocess, m)?)?;
    m.add_function(wrap_pyfunction!(vector_dot, m)?)?;
    m.add_function(wrap_pyfunction!(vector_cosine, m)?)?;
    m.add_function(wrap_pyfunction!(vector_batch_cosine, m)?)?;
    m.add("__version__", "0.1.0")?;
    m.add("__doc__", "GalaxyOS native extension — PIL replacement + SIMD vector compute")?;
    Ok(())
}
