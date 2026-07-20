#include "native_event_bus.h"
#include "native_logger.h"

namespace galaxyos {

NativeEventBus& NativeEventBus::instance() {
    static NativeEventBus bus;
    return bus;
}

SubscriptionHandle NativeEventBus::subscribe(const std::string& event_type,
                                             EventHandler handler) {
    std::lock_guard<std::mutex> lock(mutex_);
    uint64_t id = next_id_++;
    handlers_[event_type].push_back({id, std::move(handler)});
    return SubscriptionHandle{id};
}

void NativeEventBus::unsubscribe(const std::string& event_type,
                                 SubscriptionHandle handle) {
    std::lock_guard<std::mutex> lock(mutex_);
    auto it = handlers_.find(event_type);
    if (it != handlers_.end()) {
        it->second.remove_if([&](const HandlerEntry& entry) {
            return entry.id == handle.id;
        });
    }
}

void NativeEventBus::publish(const std::string& event_type,
                              const std::string& event_data) {
    std::list<HandlerEntry> handlers_copy;
    {
        std::lock_guard<std::mutex> lock(mutex_);
        auto it = handlers_.find(event_type);
        if (it != handlers_.end()) {
            handlers_copy = it->second;
        }
    }

    for (const auto& entry : handlers_copy) {
        try {
            entry.handler(event_type, event_data);
        } catch (const std::exception& e) {
            galaxyos::NativeLogger::instance().error("event_bus",
                "Handler threw exception",
                {{"event_type", event_type}, {"error", e.what()}});
        } catch (...) {
            galaxyos::NativeLogger::instance().error("event_bus",
                "Handler threw unknown exception",
                {{"event_type", event_type}});
        }
    }
}

void NativeEventBus::process_pending() {
    std::vector<PendingEvent> to_process;
    {
        std::lock_guard<std::mutex> lock(mutex_);
        to_process = std::move(pending_);
        pending_.clear();
    }

    for (const auto& event : to_process) {
        std::list<HandlerEntry> handlers_copy;
        {
            std::lock_guard<std::mutex> lock(mutex_);
            auto it = handlers_.find(event.event_type);
            if (it != handlers_.end()) {
                handlers_copy = it->second;
            }
        }

        for (const auto& entry : handlers_copy) {
            try {
                entry.handler(event.event_type, event.event_data);
            } catch (const std::exception& e) {
                galaxyos::NativeLogger::instance().error("event_bus",
                    "Handler threw exception",
                    {{"event_type", event.event_type},
                     {"error", e.what()}});
            } catch (...) {
                galaxyos::NativeLogger::instance().error("event_bus",
                    "Handler threw unknown exception",
                    {{"event_type", event.event_type}});
            }
        }
    }
}

std::string NativeEventBus::map_sse_type(const std::string& sse_type) const {
    if (sse_type == "text" || sse_type == "tokui_dsl" ||
        sse_type == "tokui_chunk" || sse_type == "agent_chunk" ||
        sse_type == "agent_token") {
        return "galaxyos://chat-chunk";
    }
    if (sse_type == "ask_user") {
        return "galaxyos://ask-user";
    }
    if (sse_type == "tool_result") {
        return "galaxyos://chat-chunk";
    }
    if (sse_type == "agent_done") {
        return "galaxyos://chat-done";
    }
    if (sse_type == "error") {
        return "galaxyos://chat-error";
    }
    return "galaxyos://" + sse_type;
}

void NativeEventBus::publish_sse_event(const std::string& sse_type,
                                       const std::string& sse_data) {
    std::string mapped_type = map_sse_type(sse_type);
    publish(mapped_type, sse_data);
}

void NativeEventBus::clear() {
    std::lock_guard<std::mutex> lock(mutex_);
    handlers_.clear();
    pending_.clear();
}

} // namespace galaxyos
