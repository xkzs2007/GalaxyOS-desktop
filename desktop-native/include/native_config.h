#pragma once

#include <string>
#include <unordered_map>
#include <mutex>

namespace galaxyos {

struct ConfigDefaults {
    static constexpr int window_width = 1280;
    static constexpr int window_height = 800;
    static constexpr int window_x = -1;
    static constexpr int window_y = -1;
    static constexpr const char* locale = "zh";
    static constexpr int mcp_port = 8765;
    static constexpr bool fullscreen = false;
    static constexpr int monitor_index = 0;
};

class NativeConfig {
public:
    static NativeConfig& instance();

    void load(const std::string& config_path = "");
    void save(const std::string& config_path = "");

    std::string get(const std::string& key) const;
    void set(const std::string& key, const std::string& value);

    int get_int(const std::string& key, int default_val = 0) const;
    void set_int(const std::string& key, int value);

    bool get_bool(const std::string& key, bool default_val = false) const;
    void set_bool(const std::string& key, bool value);

    std::string get_config_dir() const;
    std::string get_config_path() const;

private:
    NativeConfig() = default;
    NativeConfig(const NativeConfig&) = delete;
    NativeConfig& operator=(const NativeConfig&) = delete;

    void apply_defaults();

    mutable std::mutex mutex_;
    std::string config_path_;
    std::unordered_map<std::string, std::string> values_;
};

} // namespace galaxyos
