#pragma once

#include <string>
#include <unordered_map>

namespace galaxyos {

class I18nBridge {
public:
    static I18nBridge& instance();

    void load_translations(const std::string& translations_dir);
    std::string translate(const std::string& key) const;
    void set_locale(const std::string& locale);
    std::string get_locale() const;
    std::string inject_into_dsl(const std::string& dsl) const;

private:
    I18nBridge() = default;
    I18nBridge(const I18nBridge&) = delete;
    I18nBridge& operator=(const I18nBridge&) = delete;

    std::string current_locale_ = "zh";
    std::unordered_map<std::string, std::unordered_map<std::string, std::string>> translations_;
};

} // namespace galaxyos
