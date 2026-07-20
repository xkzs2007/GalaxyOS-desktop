#pragma once

#include <functional>
#include <list>
#include <mutex>
#include <string>
#include <unordered_map>
#include <vector>

namespace galaxyos {

using EventHandler = std::function<void(const std::string& event_type,
                                        const std::string& event_data)>;

struct SubscriptionHandle {
    uint64_t id = 0;
};

class NativeEventBus {
public:
    static NativeEventBus& instance();

    SubscriptionHandle subscribe(const std::string& event_type, EventHandler handler);
    void unsubscribe(const std::string& event_type, SubscriptionHandle handle);

    void publish(const std::string& event_type, const std::string& event_data);
    void publish_sse_event(const std::string& sse_type, const std::string& sse_data);

    void process_pending();

    void clear();

private:
    NativeEventBus() = default;
    NativeEventBus(const NativeEventBus&) = delete;
    NativeEventBus& operator=(const NativeEventBus&) = delete;

    std::string map_sse_type(const std::string& sse_type) const;

    struct HandlerEntry {
        uint64_t id;
        EventHandler handler;
    };

    mutable std::mutex mutex_;
    uint64_t next_id_ = 1;
    std::unordered_map<std::string, std::list<HandlerEntry>> handlers_;

    struct PendingEvent {
        std::string event_type;
        std::string event_data;
    };
    std::vector<PendingEvent> pending_;
    static constexpr size_t MAX_PENDING = 1024;
};

} // namespace galaxyos
