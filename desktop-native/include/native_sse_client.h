#pragma once

#include <atomic>
#include <functional>
#include <mutex>
#include <string>
#include <thread>
#include <unordered_map>

namespace galaxyos {

struct SSEEvent {
    std::string event_type;
    std::string data;
    std::string id;
};

using SSEEventCallback = std::function<void(const SSEEvent& event)>;

class NativeSSEClient {
public:
    static NativeSSEClient& instance();
    NativeSSEClient() = default;
    ~NativeSSEClient();

    std::string start_stream(const std::string& url);
    void stop_stream(const std::string& stream_id);
    void stop_all_streams();

    void set_event_callback(SSEEventCallback callback);

    void connect(const std::string& url);
    void disconnect();

    bool is_connected() const;

private:
    void stream_thread(const std::string& stream_id, const std::string& url);
    void parse_sse_data(const std::string& stream_id, const std::string& chunk);
    void dispatch_event(const std::string& stream_id, const SSEEvent& event);

    struct StreamState {
        std::string url;
        std::thread thread;
        std::atomic<bool> running{false};
        std::string event_type;
        std::string data_buffer;
    };

    mutable std::mutex mutex_;
    std::unordered_map<std::string, std::unique_ptr<StreamState>> streams_;
    SSEEventCallback event_callback_;
    std::atomic<bool> connected_{false};
    static constexpr int MAX_RETRIES = 3;
};

} // namespace galaxyos
