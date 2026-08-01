#pragma once

#include <atomic>
#include <string>
#include <thread>

namespace galaxyos {

enum class ProcessState {
    Stopped,
    Starting,
    Running,
    Degraded,
    Failed
};

class NativeProcessManager {
public:
    static NativeProcessManager& instance();
    NativeProcessManager() = default;
    ~NativeProcessManager();

    bool start();
    void stop();
    bool restart();

    bool wait_for_health(int timeout_seconds = 30);

    ProcessState state() const { return state_.load(); }
    bool is_running() const { return state_ == ProcessState::Running; }

    void set_mcp_port(int port) { mcp_port_ = port; }
    void redirect_logs(const std::string& log_dir);

private:
    std::string find_galaxyos_binary() const;
    bool launch_process();
    void monitor_thread();
    void check_and_restart();
    bool check_health_internal() const;

#ifdef _WIN32
    void* process_handle_ = nullptr;
    unsigned long process_id_ = 0;
#else
    int process_id_ = 0;
#endif

    std::string log_dir_;
    int mcp_port_ = 8765;
    std::atomic<ProcessState> state_{ProcessState::Stopped};
    std::atomic<int> restart_count_{0};
    std::atomic<bool> monitor_running_{false};
    std::thread monitor_thread_;
    static constexpr int MAX_RESTARTS = 3;
    static constexpr int RESTART_INTERVAL_SEC = 5;
};

} // namespace galaxyos
