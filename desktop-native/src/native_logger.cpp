#include "native_logger.h"

#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <sstream>

#ifdef _WIN32
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>
#include <psapi.h>
#endif

namespace galaxyos {

NativeLogger& NativeLogger::instance() {
    static NativeLogger logger;
    return logger;
}

void NativeLogger::initialize(LogLevel min_level, const std::string& log_dir) {
    std::lock_guard<std::mutex> lock(mutex_);
    min_level_ = min_level;
    log_dir_ = log_dir;

    if (!log_dir_.empty()) {
        log_file_path_ = log_dir_ + "/galaxyos-desktop.log";
#ifdef _WIN32
        CreateDirectoryA(log_dir_.c_str(), nullptr);
#else
        mkdir(log_dir_.c_str(), 0755);
#endif
        log_file_ = std::fopen(log_file_path_.c_str(), "a");
        if (!log_file_) {
            std::fprintf(stderr, "[GalaxyOS] Failed to open log file: %s\n", log_file_path_.c_str());
        }
    }
}

std::string NativeLogger::format_timestamp() const {
    auto now = std::chrono::system_clock::now();
    auto ms = std::chrono::duration_cast<std::chrono::milliseconds>(
        now.time_since_epoch()) % 1000;
    auto time_t_now = std::chrono::system_clock::to_time_t(now);
    std::tm tm_buf{};
#ifdef _WIN32
    localtime_s(&tm_buf, &time_t_now);
#else
    localtime_r(&time_t_now, &tm_buf);
#endif
    char buf[32];
    std::strftime(buf, sizeof(buf), "%Y-%m-%dT%H:%M:%S", &tm_buf);
    std::ostringstream oss;
    oss << buf << "." << std::setfill('0') << std::setw(3) << ms.count();
    return oss.str();
}

std::string NativeLogger::level_to_string(LogLevel level) const {
    switch (level) {
        case LogLevel::Trace: return "TRACE";
        case LogLevel::Debug: return "DEBUG";
        case LogLevel::Info:  return "INFO";
        case LogLevel::Warn:  return "WARN";
        case LogLevel::Error: return "ERROR";
        case LogLevel::Fatal: return "FATAL";
        default: return "UNKNOWN";
    }
}

static std::string escape_json_string(const std::string& s) {
    std::string result;
    result.reserve(s.size() + 4);
    for (char c : s) {
        switch (c) {
            case '"':  result += "\\\""; break;
            case '\\': result += "\\\\"; break;
            case '\b': result += "\\b"; break;
            case '\f': result += "\\f"; break;
            case '\n': result += "\\n"; break;
            case '\r': result += "\\r"; break;
            case '\t': result += "\\t"; break;
            default:
                if (static_cast<unsigned char>(c) < 0x20) {
                    char hex[8];
                    std::snprintf(hex, sizeof(hex), "\\u%04x", static_cast<unsigned char>(c));
                    result += hex;
                } else {
                    result += c;
                }
                break;
        }
    }
    return result;
}

void NativeLogger::write_log(const std::string& json_line) {
    std::fprintf(stderr, "%s\n", json_line.c_str());
    std::fflush(stderr);
    if (log_file_) {
        std::fprintf(log_file_, "%s\n", json_line.c_str());
        std::fflush(log_file_);
    }
}

void NativeLogger::log(LogLevel level, const std::string& module,
                       const std::string& message,
                       const std::unordered_map<std::string, std::string>& context) {
    if (level < min_level_) return;

    std::lock_guard<std::mutex> lock(mutex_);

    std::ostringstream oss;
    oss << "{\"timestamp\":\"" << escape_json_string(format_timestamp()) << "\""
        << ",\"level\":\"" << level_to_string(level) << "\""
        << ",\"module\":\"" << escape_json_string(module) << "\""
        << ",\"message\":\"" << escape_json_string(message) << "\"";

    if (!context.empty()) {
        oss << ",\"context\":{";
        bool first = true;
        for (const auto& kv : context) {
            if (!first) oss << ",";
            oss << "\"" << escape_json_string(kv.first) << "\":\""
                << escape_json_string(kv.second) << "\"";
            first = false;
        }
        oss << "}";
    }

    oss << "}";

    write_log(oss.str());

    if (level == LogLevel::Fatal) {
        std::abort();
    }
}

void NativeLogger::trace(const std::string& module, const std::string& message,
                         const std::unordered_map<std::string, std::string>& context) {
    log(LogLevel::Trace, module, message, context);
}

void NativeLogger::debug(const std::string& module, const std::string& message,
                         const std::unordered_map<std::string, std::string>& context) {
    log(LogLevel::Debug, module, message, context);
}

void NativeLogger::info(const std::string& module, const std::string& message,
                        const std::unordered_map<std::string, std::string>& context) {
    log(LogLevel::Info, module, message, context);
}

void NativeLogger::warn(const std::string& module, const std::string& message,
                        const std::unordered_map<std::string, std::string>& context) {
    log(LogLevel::Warn, module, message, context);
}

void NativeLogger::error(const std::string& module, const std::string& message,
                         const std::unordered_map<std::string, std::string>& context) {
    log(LogLevel::Error, module, message, context);
}

void NativeLogger::fatal(const std::string& module, const std::string& message,
                         const std::unordered_map<std::string, std::string>& context) {
    log(LogLevel::Fatal, module, message, context);
}

DiagnosticsInfo NativeLogger::get_diagnostics() const {
    std::lock_guard<std::mutex> lock(mutex_);
    DiagnosticsInfo info = diagnostics_;

#ifdef _WIN32
    PROCESS_MEMORY_COUNTERS_EX pmc{};
    pmc.cb = sizeof(pmc);
    if (GetProcessMemoryInfo(GetCurrentProcess(),
                             reinterpret_cast<PROCESS_MEMORY_COUNTERS*>(&pmc),
                             sizeof(pmc))) {
        info.memory_usage_mb = pmc.WorkingSetSize / (1024 * 1024);
    }
#endif

    return info;
}

void NativeLogger::update_diagnostics(const DiagnosticsInfo& info) {
    std::lock_guard<std::mutex> lock(mutex_);
    diagnostics_ = info;
}

void NativeLogger::set_min_level(LogLevel level) {
    std::lock_guard<std::mutex> lock(mutex_);
    min_level_ = level;
}

} // namespace galaxyos
