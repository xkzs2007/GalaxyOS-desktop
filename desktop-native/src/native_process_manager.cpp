#include "native_process_manager.h"
#include "native_event_bus.h"
#include "native_logger.h"

#include <httplib.h>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <thread>

#ifdef _WIN32
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>
#else
#include <signal.h>
#include <sys/wait.h>
#include <unistd.h>
#endif

namespace galaxyos {

NativeProcessManager& NativeProcessManager::instance() {
    static NativeProcessManager pm;
    return pm;
}

NativeProcessManager::~NativeProcessManager() {
    stop();
}

std::string NativeProcessManager::find_galaxyos_binary() const {
#ifdef _WIN32
    char exe_path[MAX_PATH] = {};
    GetModuleFileNameA(nullptr, exe_path, MAX_PATH);
    std::string dir(exe_path);
    size_t last_sep = dir.find_last_of("\\/");
    if (last_sep != std::string::npos) {
        dir = dir.substr(0, last_sep);
    }

    std::string bundled = dir + "\\galaxyos-mcp.exe";
    if (GetFileAttributesA(bundled.c_str()) != INVALID_FILE_ATTRIBUTES) {
        return bundled;
    }

    bundled = dir + "\\python\\python.exe";
    if (GetFileAttributesA(bundled.c_str()) != INVALID_FILE_ATTRIBUTES) {
        return bundled;
    }
#endif

    return "python";
}

bool NativeProcessManager::start() {
    if (state_ == ProcessState::Running) return true;

    state_ = ProcessState::Starting;
    restart_count_ = 0;

    if (!launch_process()) {
        state_ = ProcessState::Failed;
        NativeEventBus::instance().publish("galaxyos://startup-status",
            R"({"stage":"Failed","error":"Launch failed"})");
        return false;
    }

    monitor_running_ = true;
    monitor_thread_ = std::thread(&NativeProcessManager::monitor_thread, this);

    return true;
}

bool NativeProcessManager::launch_process() {
    std::string binary = find_galaxyos_binary();

#ifdef _WIN32
    std::string cmdline = binary;
    if (binary.find("python") != std::string::npos ||
        binary.find("Python") != std::string::npos) {
        cmdline += " -m galaxyos.kernel.mcp_server_entry";
    }

    STARTUPINFOA si{};
    si.cb = sizeof(si);
    si.dwFlags = STARTF_USESHOWWINDOW;
    si.wShowWindow = SW_HIDE;

    PROCESS_INFORMATION pi{};

    std::string env = "GALAXYOS_MODE=desktop";

    BOOL ok = CreateProcessA(
        nullptr,
        cmdline.data(),
        nullptr, nullptr, FALSE,
        CREATE_NO_WINDOW | CREATE_UNICODE_ENVIRONMENT,
        nullptr, nullptr, &si, &pi);

    if (!ok) {
        galaxyos::NativeLogger::instance().error("process",
            "CreateProcess failed",
            {{"cmdline", cmdline},
             {"error", std::to_string(GetLastError())}});
        return false;
    }

    process_handle_ = pi.hProcess;
    process_id_ = pi.dwProcessId;
    CloseHandle(pi.hThread);
#else
    process_id_ = fork();
    if (process_id_ == 0) {
        setenv("GALAXYOS_MODE", "desktop", 1);
        execlp(binary.c_str(), binary.c_str(),
               "-m", "galaxyos.kernel.mcp_server_entry", nullptr);
        _exit(1);
    }
    if (process_id_ < 0) return false;
#endif

    galaxyos::NativeLogger::instance().info("process",
        "Process started",
        {{"pid", std::to_string(process_id_)}});
    return true;
}

void NativeProcessManager::stop() {
    monitor_running_ = false;
    state_ = ProcessState::Stopped;

    if (monitor_thread_.joinable()) {
        monitor_thread_.join();
    }

#ifdef _WIN32
    if (process_handle_) {
        TerminateProcess(process_handle_, 0);
        WaitForSingleObject(process_handle_, 10000);
        CloseHandle(process_handle_);
        process_handle_ = nullptr;
    }
#else
    if (process_id_ > 0) {
        kill(process_id_, SIGTERM);
        int status = 0;
        for (int i = 0; i < 10; ++i) {
            if (waitpid(process_id_, &status, WNOHANG) != 0) break;
            usleep(1000000);
        }
        if (waitpid(process_id_, &status, WNOHANG) == 0) {
            kill(process_id_, SIGKILL);
            waitpid(process_id_, &status, 0);
        }
        process_id_ = 0;
    }
#endif

    galaxyos::NativeLogger::instance().info("process", "Process stopped");
}

bool NativeProcessManager::restart() {
    stop();
    restart_count_ = 0;
    return start();
}

bool NativeProcessManager::wait_for_health(int timeout_seconds) {
    for (int i = 0; i < timeout_seconds; ++i) {
        if (check_health_internal()) {
            state_ = ProcessState::Running;
            NativeEventBus::instance().publish("galaxyos://startup-status",
                R"({"stage":"McpReady"})");
            return true;
        }
        std::this_thread::sleep_for(std::chrono::seconds(1));
    }

    state_ = ProcessState::Failed;
    NativeEventBus::instance().publish("galaxyos://startup-status",
        R"({"stage":"Failed","error":"Health check timeout"})");
    return false;
}

bool NativeProcessManager::check_health_internal() const {
    httplib::Client cli("http://127.0.0.1:" + std::to_string(mcp_port_));
    cli.set_connection_timeout(2);
    auto res = cli.Get("/health");
    return res && res->status == 200;
}

void NativeProcessManager::monitor_thread() {
    while (monitor_running_) {
        std::this_thread::sleep_for(std::chrono::seconds(2));

#ifdef _WIN32
        if (process_handle_) {
            DWORD exit_code = 0;
            if (GetExitCodeProcess(process_handle_, &exit_code) &&
                exit_code != STILL_ACTIVE) {
                CloseHandle(process_handle_);
                process_handle_ = nullptr;

                galaxyos::NativeLogger::instance().warn("process",
                    "Process exited unexpectedly",
                    {{"exit_code", std::to_string(exit_code)}});

                check_and_restart();
            }
        }
#endif
    }
}

void NativeProcessManager::check_and_restart() {
    if (restart_count_ >= MAX_RESTARTS) {
        state_ = ProcessState::Degraded;
        NativeEventBus::instance().publish("galaxyos://startup-status",
            R"({"stage":"Failed","error":"Max restarts exceeded"})");
        return;
    }

    restart_count_++;
    galaxyos::NativeLogger::instance().info("process",
        "Restarting process",
        {{"attempt", std::to_string(restart_count_.load())}});

    std::this_thread::sleep_for(std::chrono::seconds(RESTART_INTERVAL_SEC));

    if (launch_process()) {
        wait_for_health(30);
    }
}

void NativeProcessManager::redirect_logs(const std::string& log_dir) {
    log_dir_ = log_dir;
}

} // namespace galaxyos
