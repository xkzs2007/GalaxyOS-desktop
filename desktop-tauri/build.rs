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

    let core_lib = if cfg!(target_os = "windows") {
        lib_dir.join("eui_neo.lib")
    } else {
        lib_dir.join("libeui_neo.a")
    };

    if include_dir.exists() && core_lib.exists() {
        println!("cargo:rustc-link-search=native={}", lib_dir.display());
        println!("cargo:rustc-link-lib=static=eui_neo");
        println!("cargo:rustc-link-lib=static=freetype");
        println!("cargo:rustc-link-lib=static=glfw3");
        println!("cargo:rustc-link-lib=static=harfbuzz");
        println!("cargo:rustc-link-lib=static=glad");

        let png_lib = if cfg!(target_os = "windows") {
            "libpng16_static"
        } else {
            let png_a = lib_dir.join("libpng16_static.a");
            if png_a.exists() {
                "png16_static"
            } else {
                "png16"
            }
        };
        println!("cargo:rustc-link-lib=static={}", png_lib);

        println!("cargo:rustc-link-lib=static=eui_zlib");
        println!("cargo:rustc-link-lib=static=eui_md4c");
        println!("cargo:rerun-if-env-changed=EUI_NEO_ROOT");
    } else {
        println!("cargo:warning=EUI-NEO SDK not found or incomplete, skipping native linking");
    }

    tauri_build::build()
}
