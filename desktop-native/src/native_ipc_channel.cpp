#include "native_ipc_channel.h"
#include "native_logger.h"

#include <httplib.h>
#include <nlohmann/json.hpp>
#include <chrono>
#include <sstream>
#include <thread>

namespace galaxyos {

static std::string url_encode(const std::string& value) {
    std::ostringstream escaped;
    escaped.fill('0');
    escaped << std::hex;
    for (char c : value) {
        if (std::isalnum(static_cast<unsigned char>(c)) || c == '-' || c == '_' ||
            c == '.' || c == '~') {
            escaped << c;
        } else {
            escaped << '%' << std::setw(2) << int(static_cast<unsigned char>(c));
        }
    }
    return escaped.str();
}

NativeIPCChannel& NativeIPCChannel::instance() {
    static NativeIPCChannel channel;
    return channel;
}

NativeIPCChannel::~NativeIPCChannel() = default;

void NativeIPCChannel::set_base_url(const std::string& host, int port) {
    mcp_port_ = port;
    base_url_ = "http://" + host + ":" + std::to_string(port);
}

IPCResponse NativeIPCChannel::http_get(const std::string& path,
                                       const std::string& params) {
    return request_with_retry("GET", path + (params.empty() ? "" : "?" + params));
}

IPCResponse NativeIPCChannel::http_post(const std::string& path,
                                        const std::string& json_body) {
    return request_with_retry("POST", path, json_body);
}

IPCResponse NativeIPCChannel::request_with_retry(const std::string& method,
                                                  const std::string& path,
                                                  const std::string& body,
                                                  int max_retries) {
    IPCResponse result;
    httplib::Client cli(base_url_);
    cli.set_connection_timeout(REQUEST_TIMEOUT_SEC);
    cli.set_read_timeout(REQUEST_TIMEOUT_SEC);

    for (int attempt = 0; attempt < max_retries; ++attempt) {
        try {
            httplib::Result res;
            if (method == "GET") {
                res = cli.Get(path);
            } else {
                res = cli.Post(path, body, "application/json");
            }

            if (res) {
                result.status_code = res->status;
                result.body = res->body;
                result.success = (res->status >= 200 && res->status < 300);
                if (!result.success) {
                    result.error = "HTTP " + std::to_string(res->status);
                }
                return result;
            }

            result.error = res.error() == httplib::Error::Connection
                ? "Connection refused" : "Request failed";

        } catch (const std::exception& e) {
            result.error = e.what();
        }

        if (attempt < max_retries - 1) {
            int delay_ms = 1000 * (1 << attempt);
            galaxyos::NativeLogger::instance().warn("ipc",
                "Request failed, retrying",
                {{"path", path},
                 {"attempt", std::to_string(attempt + 1)},
                 {"delay_ms", std::to_string(delay_ms)},
                 {"error", result.error}});
            std::this_thread::sleep_for(std::chrono::milliseconds(delay_ms));
        }
    }

    result.success = false;
    galaxyos::NativeLogger::instance().error("ipc",
        "Request failed after retries",
        {{"path", path}, {"error", result.error}});
    return result;
}

IPCResponse NativeIPCChannel::chat_send(const std::string& message,
                                        const std::string& workspace_id) {
    return http_get("/agent-chat",
                    "message=" + url_encode(message) + "&workspace_id=" + url_encode(workspace_id));
}

IPCResponse NativeIPCChannel::permission_respond(const std::string& request_id,
                                                  bool approved) {
    std::string body = R"({"request_id":")" + request_id +
                       R"(","approved":)" + (approved ? "true" : "false") + "}";
    return http_post("/permission-respond", body);
}

IPCResponse NativeIPCChannel::check_health() {
    return http_get("/health");
}

IPCResponse NativeIPCChannel::desktop_tools_status() {
    auto res = check_health();
    if (res.success) {
        try {
            auto j = nlohmann::json::parse(res.body);
            if (j.contains("desktop_tools")) {
                IPCResponse tools_res;
                tools_res.success = true;
                tools_res.body = j["desktop_tools"].dump();
                return tools_res;
            }
        } catch (...) {}
    }
    return res;
}

void NativeIPCChannel::start_backends() {}
void NativeIPCChannel::stop_backends() {}

std::string NativeIPCChannel::get_locale() {
    auto res = http_get("/locale");
    if (res.success) return res.body;
    return "zh";
}

void NativeIPCChannel::set_locale(const std::string& locale) {
    http_post("/locale", R"({"locale":")" + locale + "\"}");
}

bool NativeIPCChannel::is_available() const {
    httplib::Client cli(base_url_);
    cli.set_connection_timeout(5);
    auto res = cli.Get("/health");
    return res && res->status == 200;
}

} // namespace galaxyos
