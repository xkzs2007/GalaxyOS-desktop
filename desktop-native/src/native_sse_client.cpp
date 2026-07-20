#include "native_sse_client.h"
#include "native_event_bus.h"
#include "native_logger.h"

#include <httplib.h>
#include <chrono>
#include <cstdio>
#include <sstream>
#include <thread>

namespace galaxyos {

NativeSSEClient& NativeSSEClient::instance() {
    static NativeSSEClient client;
    return client;
}

NativeSSEClient::~NativeSSEClient() {
    stop_all_streams();
}

std::string NativeSSEClient::start_stream(const std::string& url) {
    auto stream_id = "stream-" + std::to_string(
        std::chrono::steady_clock::now().time_since_epoch().count());

    auto state = std::make_unique<StreamState>();
    state->url = url;
    state->running = true;

    std::thread t(&NativeSSEClient::stream_thread, this, stream_id, url);
    state->thread = std::move(t);

    {
        std::lock_guard<std::mutex> lock(mutex_);
        streams_[stream_id] = std::move(state);
    }

    galaxyos::NativeLogger::instance().info("sse",
        "Stream started", {{"stream_id", stream_id}, {"url", url}});
    return stream_id;
}

void NativeSSEClient::stop_stream(const std::string& stream_id) {
    std::unique_ptr<StreamState> state;
    {
        std::lock_guard<std::mutex> lock(mutex_);
        auto it = streams_.find(stream_id);
        if (it != streams_.end()) {
            state = std::move(it->second);
            streams_.erase(it);
        }
    }

    if (state) {
        state->running = false;
        if (state->thread.joinable()) {
            state->thread.join();
        }
        galaxyos::NativeLogger::instance().info("sse",
            "Stream stopped", {{"stream_id", stream_id}});
    }
}

void NativeSSEClient::stop_all_streams() {
    std::unordered_map<std::string, std::unique_ptr<StreamState>> to_stop;
    {
        std::lock_guard<std::mutex> lock(mutex_);
        to_stop = std::move(streams_);
        streams_.clear();
    }

    for (auto& [id, state] : to_stop) {
        state->running = false;
        if (state->thread.joinable()) {
            state->thread.join();
        }
    }
}

void NativeSSEClient::set_event_callback(SSEEventCallback callback) {
    std::lock_guard<std::mutex> lock(mutex_);
    event_callback_ = std::move(callback);
}

void NativeSSEClient::connect(const std::string& url) {
    start_stream(url);
}

void NativeSSEClient::disconnect() {
    stop_all_streams();
}

bool NativeSSEClient::is_connected() const {
    return connected_.load();
}

void NativeSSEClient::stream_thread(const std::string& stream_id,
                                     const std::string& url) {
    int retry_count = 0;

    while (true) {
        {
            std::lock_guard<std::mutex> lock(mutex_);
            auto it = streams_.find(stream_id);
            if (it == streams_.end() || !it->second->running.load()) break;
        }

        try {
            size_t scheme_end = url.find("://");
            std::string host_port = url;
            std::string path = "/";
            if (scheme_end != std::string::npos) {
                size_t path_start = url.find('/', scheme_end + 3);
                if (path_start != std::string::npos) {
                    host_port = url.substr(scheme_end + 3, path_start - scheme_end - 3);
                    path = url.substr(path_start);
                } else {
                    host_port = url.substr(scheme_end + 3);
                }
            }

            httplib::Client cli("http://" + host_port);
            cli.set_read_timeout(120);

            connected_.store(true);
            retry_count = 0;

            cli.Get(path, [&](const char* data, size_t len) -> bool {
                {
                    std::lock_guard<std::mutex> lock(mutex_);
                    auto it = streams_.find(stream_id);
                    if (it == streams_.end() || !it->second->running.load()) return false;
                }
                parse_sse_data(stream_id, std::string(data, len));
                return true;
            });

            connected_.store(false);

        } catch (const std::exception& e) {
            connected_.store(false);
            galaxyos::NativeLogger::instance().error("sse",
                "Stream error",
                {{"stream_id", stream_id}, {"error", e.what()}});
        }

        retry_count++;
        if (retry_count > MAX_RETRIES) {
            galaxyos::NativeLogger::instance().error("sse",
                "Max retries exceeded",
                {{"stream_id", stream_id}});
            break;
        }

        int delay_s = 2 * (1 << (retry_count - 1));
        if (delay_s > 8) delay_s = 8;
        galaxyos::NativeLogger::instance().warn("sse",
            "Reconnecting",
            {{"stream_id", stream_id},
             {"retry", std::to_string(retry_count)},
             {"delay_s", std::to_string(delay_s)}});

        std::this_thread::sleep_for(std::chrono::seconds(delay_s));
    }

    connected_.store(false);
}

void NativeSSEClient::parse_sse_data(const std::string& stream_id,
                                       const std::string& chunk) {
    std::vector<SSEEvent> events_to_dispatch;
    {
        std::lock_guard<std::mutex> lock(mutex_);
        auto it = streams_.find(stream_id);
        if (it == streams_.end()) return;
        auto& state = it->second;

        std::istringstream stream(chunk);
        std::string line;
        while (std::getline(stream, line)) {
            if (!line.empty() && line.back() == '\r') {
                line.pop_back();
            }

            if (line.empty()) {
                if (!state->data_buffer.empty()) {
                    SSEEvent event;
                    event.event_type = state->event_type.empty() ? "message" : state->event_type;
                    event.data = state->data_buffer;
                    events_to_dispatch.push_back(std::move(event));

                    if (state->event_type == "agent_done") {
                        state->running = false;
                    }

                    state->event_type.clear();
                    state->data_buffer.clear();
                }
                continue;
            }

            if (line.substr(0, 6) == "event:") {
                state->event_type = line.substr(6);
                size_t start = state->event_type.find_first_not_of(' ');
                if (start != std::string::npos) {
                    state->event_type = state->event_type.substr(start);
                }
            } else if (line.substr(0, 5) == "data:") {
                std::string data_part = line.substr(5);
                size_t start = data_part.find_first_not_of(' ');
                if (start != std::string::npos) {
                    data_part = data_part.substr(start);
                }
                if (!state->data_buffer.empty()) {
                    state->data_buffer += "\n";
                }
                state->data_buffer += data_part;
            } else if (line.substr(0, 3) == "id:") {
            }
        }
    }

    for (const auto& event : events_to_dispatch) {
        dispatch_event(stream_id, event);
    }
}

void NativeSSEClient::dispatch_event(const std::string& stream_id,
                                      const SSEEvent& event) {
    if (event_callback_) {
        try {
            event_callback_(event);
        } catch (const std::exception& e) {
            galaxyos::NativeLogger::instance().error("sse",
                "Event callback error",
                {{"stream_id", stream_id}, {"error", e.what()}});
        }
    }

    galaxyos::NativeEventBus::instance().publish_sse_event(
        event.event_type, event.data);
}

} // namespace galaxyos
