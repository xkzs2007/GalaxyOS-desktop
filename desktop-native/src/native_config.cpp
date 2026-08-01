#include "native_config.h"
#include "native_logger.h"

#include <cstdlib>
#include <fstream>
#include <sstream>

#ifdef _WIN32
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>
#include <shlobj.h>
#endif

#include <nlohmann/json.hpp>

namespace {

bool is_pure_integer(const std::string& s) {
    if (s.empty()) return false;
    size_t start = 0;
    if (s[0] == '-' || s[0] == '+') {
        if (s.size() == 1) return false;
        start = 1;
    }
    for (size_t i = start; i < s.size(); ++i) {
        if (!std::isdigit(static_cast<unsigned char>(s[i]))) return false;
    }
    return true;
}

} // namespace

namespace galaxyos {

NativeConfig& NativeConfig::instance() {
    static NativeConfig config;
    return config;
}

std::string NativeConfig::get_config_dir() const {
#ifdef _WIN32
    char path[MAX_PATH] = {};
    if (SUCCEEDED(SHGetFolderPathA(nullptr, CSIDL_LOCAL_APPDATA, nullptr, 0, path))) {
        return std::string(path) + "\\GalaxyOS";
    }
    return std::string(getenv("LOCALAPPDATA")) + "\\GalaxyOS";
#else
    const char* home = getenv("HOME");
    if (home) {
        return std::string(home) + "/.config/galaxyos";
    }
    return "/tmp/galaxyos";
#endif
}

std::string NativeConfig::get_config_path() const {
    if (!config_path_.empty()) return config_path_;
#ifdef _WIN32
    return get_config_dir() + "\\config.json";
#else
    return get_config_dir() + "/config.json";
#endif
}

void NativeConfig::apply_defaults() {
    if (values_.find("window_width") == values_.end())
        values_["window_width"] = std::to_string(ConfigDefaults::window_width);
    if (values_.find("window_height") == values_.end())
        values_["window_height"] = std::to_string(ConfigDefaults::window_height);
    if (values_.find("window_x") == values_.end())
        values_["window_x"] = std::to_string(ConfigDefaults::window_x);
    if (values_.find("window_y") == values_.end())
        values_["window_y"] = std::to_string(ConfigDefaults::window_y);
    if (values_.find("locale") == values_.end())
        values_["locale"] = ConfigDefaults::locale;
    if (values_.find("mcp_port") == values_.end())
        values_["mcp_port"] = std::to_string(ConfigDefaults::mcp_port);
    if (values_.find("fullscreen") == values_.end())
        values_["fullscreen"] = ConfigDefaults::fullscreen ? "true" : "false";
    if (values_.find("monitor_index") == values_.end())
        values_["monitor_index"] = std::to_string(ConfigDefaults::monitor_index);
}

void NativeConfig::load(const std::string& config_path) {
    std::lock_guard<std::mutex> lock(mutex_);

    if (!config_path.empty()) {
        config_path_ = config_path;
    }

    std::string path = get_config_path();
    std::ifstream file(path);

    if (!file.is_open()) {
        apply_defaults();
        galaxyos::NativeLogger::instance().info("config",
            "Config file not found, using defaults",
            {{"path", path}});
        return;
    }

    try {
        nlohmann::json j = nlohmann::json::parse(file);
        for (auto it = j.begin(); it != j.end(); ++it) {
            if (it.value().is_string()) {
                values_[it.key()] = it.value().get<std::string>();
            } else if (it.value().is_number_integer()) {
                values_[it.key()] = std::to_string(it.value().get<int>());
            } else if (it.value().is_boolean()) {
                values_[it.key()] = it.value().get<bool>() ? "true" : "false";
            }
        }
    } catch (const nlohmann::json::exception& e) {
        galaxyos::NativeLogger::instance().warn("config",
            "Failed to parse config file, using defaults",
            {{"error", e.what()}});
    }

    apply_defaults();
    galaxyos::NativeLogger::instance().info("config", "Config loaded",
        {{"path", path}});
}

void NativeConfig::save(const std::string& config_path) {
    std::lock_guard<std::mutex> lock(mutex_);

    if (!config_path.empty()) {
        config_path_ = config_path;
    }

    std::string path = get_config_path();

#ifdef _WIN32
    std::string dir = get_config_dir();
    CreateDirectoryA(dir.c_str(), nullptr);
#endif

    nlohmann::json j;
    for (const auto& kv : values_) {
        if (kv.second == "true" || kv.second == "false") {
            j[kv.first] = (kv.second == "true");
        } else if (is_pure_integer(kv.second)) {
            j[kv.first] = std::stoi(kv.second);
        } else {
            j[kv.first] = kv.second;
        }
    }

    std::ofstream file(path);
    if (file.is_open()) {
        file << j.dump(2);
        galaxyos::NativeLogger::instance().info("config", "Config saved",
            {{"path", path}});
    } else {
        galaxyos::NativeLogger::instance().error("config",
            "Failed to save config file",
            {{"path", path}});
    }
}

std::string NativeConfig::get(const std::string& key) const {
    std::lock_guard<std::mutex> lock(mutex_);
    auto it = values_.find(key);
    if (it != values_.end()) return it->second;
    return "";
}

void NativeConfig::set(const std::string& key, const std::string& value) {
    std::lock_guard<std::mutex> lock(mutex_);
    values_[key] = value;
}

int NativeConfig::get_int(const std::string& key, int default_val) const {
    std::string val = get(key);
    if (val.empty()) return default_val;
    try {
        return std::stoi(val);
    } catch (...) {
        return default_val;
    }
}

void NativeConfig::set_int(const std::string& key, int value) {
    set(key, std::to_string(value));
}

bool NativeConfig::get_bool(const std::string& key, bool default_val) const {
    std::string val = get(key);
    if (val == "true") return true;
    if (val == "false") return false;
    return default_val;
}

void NativeConfig::set_bool(const std::string& key, bool value) {
    set(key, value ? "true" : "false");
}

} // namespace galaxyos
