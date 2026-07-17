fn main() {
    let eui_neo_root = std::env::var("EUI_NEO_ROOT")
        .map(std::path::PathBuf::from)
        .unwrap_or_else(|_| {
            let exe_dir = std::env::current_exe()
                .ok()
                .and_then(|p| p.parent().map(|p| p.to_path_buf()))
                .unwrap_or_else(|| std::path::PathBuf::from("."));

            let candidates = vec![
                exe_dir.join("vendor").join("eui-neo"),
                std::path::PathBuf::from("vendor/eui-neo"),
                std::path::PathBuf::from("../vendor/eui-neo"),
            ];

            candidates
                .into_iter()
                .find(|p| p.join("sdk").exists())
                .unwrap_or_else(|| std::path::PathBuf::from("vendor/eui-neo"))
        });

    let sdk_dir = eui_neo_root.join("sdk");
    let include_dir = sdk_dir.join("include");
    let lib_dir = sdk_dir.join("lib");

    if include_dir.exists() {
        println!("cargo:rustc-link-search=native={}", lib_dir.display());
        println!("cargo:rustc-link-lib=static=eui_neo");
        println!("cargo:rustc-link-lib=static=freetype");
        println!("cargo:rustc-link-lib=static=glfw3");
        println!("cargo:rustc-link-lib=static=harfbuzz");
        println!("cargo:rustc-link-lib=static=glad");
        if cfg!(target_os = "windows") {
            println!("cargo:rustc-link-lib=static=libpng16_static");
        } else {
            println!("cargo:rustc-link-lib=static=png16_static");
        }
        println!("cargo:rustc-link-lib=static=eui_zlib");
        println!("cargo:rustc-link-lib=static=eui_md4c");
        println!("cargo:rerun-if-env-changed=EUI_NEO_ROOT");
    }

    tauri_build::build()
}