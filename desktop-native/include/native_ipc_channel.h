#pragma once

#include <functional>
#include <string>
#include <thread>
#include <vector>
#include <queue>
#include <mutex>
#include <condition_variable>

namespace galaxyos {

struct IPCResponse {
    int status_code = 0;
    std::string body;
    std::string error;
    bool success = false;
};

class NativeIPCChannel {
public:
    static NativeIPCChannel& instance();
    NativeIPCChannel() = default;
    ~NativeIPCChannel();

    void set_base_url(const std::string& host, int port);

    IPCResponse http_get(const std::string& path,
                         const std::string& params = "");
    IPCResponse http_post(const std::string& path,
                          const std::string& json_body);

    IPCResponse chat_send(const std::string& message,
                          const std::string& workspace_id = "default");
    IPCResponse permission_respond(const std::string& request_id,
                                   bool approved);
    IPCResponse check_health();
    IPCResponse desktop_tools_status();

    void start_backends();
    void stop_backends();

    std::string get_locale();
    void set_locale(const std::string& locale);

    bool is_available() const;

private:
    IPCResponse request_with_retry(const std::string& method,
                                   const std::string& path,
                                   const std::string& body = "",
                                   int max_retries = 3);

    std::string base_url_ = "http://127.0.0.1:8765";
    int mcp_port_ = 8765;
    static constexpr int REQUEST_TIMEOUT_SEC = 30;
};

} // namespace galaxyos
