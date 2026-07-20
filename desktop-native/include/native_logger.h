#pragma once

#include <cstdio>
#include <ctime>
#include <mutex>
#include <string>
#include <unordered_map>

namespace galaxyos {

enum class LogLevel {
    Trace = 0,
    Debug = 1,
    Info = 2,
    Warn = 3,
    Error = 4,
    Fatal = 5
};

struct DiagnosticsInfo {
    size_t memory_usage_mb = 0;
    double fps = 0.0;
    int active_surfaces = 0;
    bool sse_connected = false;
};

class NativeLogger {
public:
    static NativeLogger& instance();

    void initialize(LogLevel min_level, const std::string& log_dir);

    void log(LogLevel level, const std::string& module, const std::string& message,
             const std::unordered_map<std::string, std::string>& context = {});

    void trace(const std::string& module, const std::string& message,
               const std::unordered_map<std::string, std::string>& context = {});
    void debug(const std::string& module, const std::string& message,
               const std::unordered_map<std::string, std::string>& context = {});
    void info(const std::string& module, const std::string& message,
              const std::unordered_map<std::string, std::string>& context = {});
    void warn(const std::string& module, const std::string& message,
              const std::unordered_map<std::string, std::string>& context = {});
    void error(const std::string& module, const std::string& message,
               const std::unordered_map<std::string, std::string>& context = {});
    void fatal(const std::string& module, const std::string& message,
               const std::unordered_map<std::string, std::string>& context = {});

    DiagnosticsInfo get_diagnostics() const;

    void update_diagnostics(const DiagnosticsInfo& info);

    void set_min_level(LogLevel level);

private:
    NativeLogger() = default;
    NativeLogger(const NativeLogger&) = delete;
    NativeLogger& operator=(const NativeLogger&) = delete;

    std::string format_timestamp() const;
    std::string level_to_string(LogLevel level) const;
    void write_log(const std::string& json_line);

    mutable std::mutex mutex_;
    LogLevel min_level_ = LogLevel::Info;
    std::string log_dir_;
    std::string log_file_path_;
    FILE* log_file_ = nullptr;
    DiagnosticsInfo diagnostics_;
};

} // namespace galaxyos
