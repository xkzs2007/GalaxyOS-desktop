#include "eui_neo.h"

#include "native_config.h"
#include "native_event_bus.h"
#include "native_ipc_channel.h"
#include "native_logger.h"
#include "native_process_manager.h"
#include "native_sse_client.h"
#include "i18n_bridge.h"

#include <algorithm>
#include <cmath>
#include <mutex>
#include <string>

namespace {

constexpr eui::Color kBg{0.06f, 0.06f, 0.08f, 1.0f};
constexpr eui::Color kSurface{0.10f, 0.10f, 0.13f, 1.0f};
constexpr eui::Color kPrimary{0.40f, 0.70f, 0.95f, 1.0f};
constexpr eui::Color kText{0.92f, 0.93f, 0.94f, 1.0f};
constexpr eui::Color kMuted{0.55f, 0.56f, 0.58f, 1.0f};

struct SharedState {
    std::string chatInput;
    std::string chatResponse;
    bool isStreaming = false;
    bool mcpReady = false;
    bool dirty = false;
};

std::mutex g_state_mutex;
SharedState g_shared;
bool g_initialized = false;

void on_sse_event(const galaxyos::SSEEvent& event) {
    auto& bus = galaxyos::NativeEventBus::instance();
    bool need_update = false;
    {
        std::lock_guard<std::mutex> lock(g_state_mutex);
        if (event.event_type == "chat_chunk") {
            g_shared.chatResponse += event.data;
            g_shared.isStreaming = true;
            need_update = true;
        } else if (event.event_type == "chat_done") {
            g_shared.isStreaming = false;
            need_update = true;
        } else if (event.event_type == "chat_error") {
            g_shared.isStreaming = false;
            need_update = true;
        } else if (event.event_type == "startup_status") {
            if (event.data.find("McpReady") != std::string::npos) {
                g_shared.mcpReady = true;
            }
            need_update = true;
        }
        if (need_update) {
            g_shared.dirty = true;
        }
    }
    if (event.event_type == "ask_user") {
        bus.publish("galaxyos://ask-user", event.data);
    }
    if (need_update) {
        app::requestUpdate();
    }
}

void send_chat() {
    std::string message;
    {
        std::lock_guard<std::mutex> lock(g_state_mutex);
        if (g_shared.chatInput.empty() || g_shared.isStreaming) return;
        message = g_shared.chatInput;
        g_shared.chatInput.clear();
        g_shared.chatResponse.clear();
        g_shared.isStreaming = true;
        g_shared.dirty = true;
    }
    app::requestUpdate();

    app::async::restart("chat-send",
        [message]() -> core::async::Result<void> {
            auto& ipc = galaxyos::NativeIPCChannel::instance();
            ipc.chat_send(message);
            return core::async::success();
        },
        [](core::async::Result<void> result) {
            if (!result.ok) {
                galaxyos::NativeLogger::instance().error("chat",
                    "Failed to send chat: " + result.error);
            }
        }
    );
}

struct PageState {
    std::string chatInput;
    std::string chatResponse;
    bool isStreaming = false;
    bool mcpReady = false;
};

void sync_from_shared(PageState& state) {
    std::lock_guard<std::mutex> lock(g_state_mutex);
    state.chatResponse = g_shared.chatResponse;
    state.isStreaming = g_shared.isStreaming;
    state.mcpReady = g_shared.mcpReady;
    g_shared.dirty = false;
}

void ensure_initialized() {
    if (g_initialized) return;
    g_initialized = true;

    galaxyos::NativeLogger::instance().initialize(galaxyos::LogLevel::Info, "");
    galaxyos::NativeLogger::instance().info("app", "GalaxyOS Desktop v0.3.0 starting");

    galaxyos::NativeConfig::instance().load();
    galaxyos::NativeEventBus::instance();

    galaxyos::I18nBridge::instance().load_translations(
        galaxyos::NativeConfig::instance().get_config_dir() + "/i18n");
    galaxyos::I18nBridge::instance().set_locale(
        galaxyos::NativeConfig::instance().get("locale"));

    galaxyos::NativeProcessManager& pm = galaxyos::NativeProcessManager::instance();
    pm.set_mcp_port(galaxyos::NativeConfig::instance().get_int("mcp_port", 8765));
    pm.start();

    galaxyos::NativeIPCChannel::instance().set_base_url(
        "127.0.0.1",
        galaxyos::NativeConfig::instance().get_int("mcp_port", 8765));

    galaxyos::NativeSSEClient& sse = galaxyos::NativeSSEClient::instance();
    sse.set_event_callback(on_sse_event);

    if (!pm.wait_for_health(30)) {
        galaxyos::NativeLogger::instance().warn("app",
            "MCP Server health check failed, continuing in degraded mode");
    } else {
        {
            std::lock_guard<std::mutex> lock(g_state_mutex);
            g_shared.mcpReady = true;
        }
        sse.connect("http://127.0.0.1:" +
            std::to_string(galaxyos::NativeConfig::instance().get_int("mcp_port", 8765)) +
            "/events");
    }

    galaxyos::NativeLogger::instance().info("app", "Initialization complete");
}

void build_sidebar(eui::Ui& ui, float width, float height) {
    ui.column("sidebar")
        .width(width)
        .height(eui::SizeValue::fill())
        .padding(16.0f)
        .gap(8.0f)
        .content([&] {
            ui.text("logo")
                .text("GalaxyOS")
                .color(kPrimary)
                .fontSize(22.0f)
                .fontWeight(700)
                .build();

            ui.rect("divider")
                .size(eui::SizeValue::fill(), eui::SizeValue::fixed(1.0f))
                .color(eui::Color{0.20f, 0.20f, 0.24f, 1.0f})
                .build();

            components::button(ui, "nav-chat")
                .text(galaxyos::I18nBridge::instance().translate("chat"))
                .size(width - 32.0f, 36.0f)
                .fontSize(14.0f)
                .theme(components::theme::dark(), false)
                .build();

            components::button(ui, "nav-tools")
                .text(galaxyos::I18nBridge::instance().translate("tools"))
                .size(width - 32.0f, 36.0f)
                .fontSize(14.0f)
                .theme(components::theme::dark(), false)
                .build();

            components::button(ui, "nav-settings")
                .text(galaxyos::I18nBridge::instance().translate("settings"))
                .size(width - 32.0f, 36.0f)
                .fontSize(14.0f)
                .theme(components::theme::dark(), false)
                .build();
        })
        .build();
}

void build_chat_area(eui::Ui& ui, float width, float height, PageState& state) {
    float padding = std::clamp(width * 0.03f, 12.0f, 24.0f);
    float input_height = 44.0f;
    float chat_height = std::max(100.0f, height - input_height - padding * 3.0f);

    ui.column("chat-area")
        .size(width, height)
        .padding(padding)
        .gap(12.0f)
        .content([&] {
            components::scrollView(ui, "messages-scroll")
                .size(width - padding * 2.0f, chat_height)
                .theme(components::theme::dark())
                .content([&](eui::Ui& scrollUi, float contentWidth, float) {
                    if (state.chatResponse.empty() && !state.isStreaming) {
                        scrollUi.text("placeholder")
                            .text(galaxyos::I18nBridge::instance().translate("chatPlaceholder"))
                            .color(kMuted)
                            .fontSize(15.0f)
                            .build();
                    } else {
#if defined(EUI_HAS_MD4C)
                        components::markdown(scrollUi, "response-md")
                            .markdown(state.chatResponse)
                            .width(contentWidth)
                            .wrapContentHeight()
                            .theme(components::theme::dark())
                            .build();
#else
                        scrollUi.text("response")
                            .text(state.chatResponse)
                            .color(kText)
                            .fontSize(15.0f)
                            .wrap(true)
                            .maxWidth(contentWidth)
                            .build();
#endif
                    }
                })
                .build();

            ui.row("input-row")
                .size(eui::SizeValue::fill(), eui::SizeValue::fixed(input_height))
                .gap(8.0f)
                .alignItems(eui::Align::CENTER)
                .content([&] {
                    eui::Signal<std::string>& inputSignal =
                        ui.state<eui::Signal<std::string>>("chat-input-signal");
                    inputSignal.set(state.chatInput);

                    components::input(ui, "chat-input")
                        .size(width - padding * 2.0f - 52.0f, input_height)
                        .bind(inputSignal)
                        .placeholder(galaxyos::I18nBridge::instance().translate("chatPlaceholder"))
                        .onEnter([&inputSignal]() {
                            {
                                std::lock_guard<std::mutex> lock(g_state_mutex);
                                g_shared.chatInput = inputSignal.get();
                            }
                            send_chat();
                        })
                        .onChange([](const std::string& value) {
                            std::lock_guard<std::mutex> lock(g_state_mutex);
                            g_shared.chatInput = value;
                        })
                        .theme(components::theme::dark())
                        .build();

                    components::button(ui, "send-btn")
                        .text(">")
                        .size(44.0f, 44.0f)
                        .fontSize(18.0f)
                        .theme(components::theme::dark(), true)
                        .onClick([&inputSignal]() {
                            {
                                std::lock_guard<std::mutex> lock(g_state_mutex);
                                g_shared.chatInput = inputSignal.get();
                            }
                            send_chat();
                        })
                        .build();
                })
                .build();
        })
        .build();
}

void build_status_bar(eui::Ui& ui, float width, PageState& state) {
    float bar_height = 32.0f;
    ui.row("status-bar")
        .size(width, bar_height)
        .padding(12.0f, 0.0f)
        .gap(8.0f)
        .alignItems(eui::Align::CENTER)
        .content([&] {
            ui.rect("status-dot")
                .width(8.0f)
                .height(8.0f)
                .color(state.mcpReady
                    ? eui::Color{0.30f, 0.85f, 0.50f, 1.0f}
                    : eui::Color{0.85f, 0.30f, 0.30f, 1.0f})
                .radius(4.0f)
                .build();

            ui.text("status-text")
                .text(state.mcpReady ? "MCP Ready" : "MCP Offline")
                .color(kMuted)
                .fontSize(12.0f)
                .build();
        })
        .build();
}

} // namespace

namespace app {

const DslAppConfig& dslAppConfig() {
    static const DslAppConfig config = DslAppConfig{}
        .title("GalaxyOS Desktop")
        .pageId("galaxyos")
        .clearColor(kBg)
        .windowSize(1280, 800)
        .fps(90.0)
        .tray(true)
        .trayTitle("GalaxyOS");
    return config;
}

void compose(eui::Ui& ui, const eui::Screen& screen) {
    ensure_initialized();

    PageState& state = ui.state<PageState>("page");
    sync_from_shared(state);

    float width = screen.width;
    float height = screen.height;
    float sidebar_width = std::clamp(width * 0.22f, 200.0f, 300.0f);
    float main_width = width - sidebar_width;

    ui.row("root")
        .size(width, height)
        .content([&] {
            build_sidebar(ui, sidebar_width, height);

            ui.column("main")
                .width(main_width)
                .height(eui::SizeValue::fill())
                .content([&] {
                    float chat_height = height - 32.0f;
                    build_chat_area(ui, main_width, chat_height, state);
                    build_status_bar(ui, main_width, state);
                })
                .build();
        })
        .build();
}

} // namespace app
