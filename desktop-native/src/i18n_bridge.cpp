#include "i18n_bridge.h"
#include "native_logger.h"

#include <fstream>
#include <nlohmann/json.hpp>
#include <regex>

namespace galaxyos {

I18nBridge& I18nBridge::instance() {
    static I18nBridge bridge;
    return bridge;
}

void I18nBridge::load_translations(const std::string& translations_dir) {
    std::vector<std::string> locales = {"zh", "en"};

    for (const auto& locale : locales) {
        std::string path = translations_dir + "/" + locale + ".json";
        std::ifstream file(path);
        if (!file.is_open()) {
            galaxyos::NativeLogger::instance().warn("i18n",
                "Translation file not found",
                {{"path", path}});
            continue;
        }

        try {
            nlohmann::json j = nlohmann::json::parse(file);
            for (auto& [key, val] : j.items()) {
                if (val.is_string()) {
                    translations_[locale][key] = val.get<std::string>();
                }
            }
            galaxyos::NativeLogger::instance().info("i18n",
                "Translations loaded",
                {{"locale", locale},
                 {"count", std::to_string(translations_[locale].size())}});
        } catch (const std::exception& e) {
            galaxyos::NativeLogger::instance().warn("i18n",
                "Failed to parse translation file",
                {{"path", path}, {"error", e.what()}});
        }
    }
}

std::string I18nBridge::translate(const std::string& key) const {
    auto it = translations_.find(current_locale_);
    if (it != translations_.end()) {
        auto kit = it->second.find(key);
        if (kit != it->second.end()) return kit->second;
    }

    auto zh_it = translations_.find("zh");
    if (zh_it != translations_.end()) {
        auto kit = zh_it->second.find(key);
        if (kit != zh_it->second.end()) return kit->second;
    }

    return key;
}

void I18nBridge::set_locale(const std::string& locale) {
    current_locale_ = locale;
}

std::string I18nBridge::get_locale() const {
    return current_locale_;
}

std::string I18nBridge::inject_into_dsl(const std::string& dsl) const {
    std::string result = dsl;
    std::regex pattern(R"(\{\{i18n:(\w+)\}\})");

    std::smatch match;
    while (std::regex_search(result, match, pattern)) {
        std::string key = match[1].str();
        std::string replacement = translate(key);
        result.replace(match.position(), match.length(), replacement);
    }

    return result;
}

} // namespace galaxyos
