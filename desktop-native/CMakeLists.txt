cmake_minimum_required(VERSION 3.21)

project(galaxyos-desktop VERSION 0.3.0 LANGUAGES C CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

set(CMAKE_RUNTIME_OUTPUT_DIRECTORY ${CMAKE_BINARY_DIR}/bin)
set(CMAKE_LIBRARY_OUTPUT_DIRECTORY ${CMAKE_BINARY_DIR}/lib)
set(CMAKE_ARCHIVE_OUTPUT_DIRECTORY ${CMAKE_BINARY_DIR}/lib)

option(GALAXYOS_ENABLE_TESTS "Build unit tests" ON)

# ── EUI-NEO (source tree integration) ────────────────────────────────────────
set(EUI_BUILD_APPS OFF CACHE BOOL "" FORCE)
set(EUI_BUILD_USER_APPS OFF CACHE BOOL "" FORCE)
set(EUI_BUILD_TEST_FIXTURES OFF CACHE BOOL "" FORCE)
set(EUI_ENABLE_INSTALL OFF CACHE BOOL "" FORCE)
set(EUI_NEO_SOURCE_DIR "${CMAKE_SOURCE_DIR}/../EUI-NEO")
add_subdirectory(${EUI_NEO_SOURCE_DIR} ${CMAKE_BINARY_DIR}/eui-neo-build)


# ── Header-only: cpp-httplib ────────────────────────────────────────────────
add_library(cpp-httplib INTERFACE)
target_include_directories(cpp-httplib INTERFACE
    $<BUILD_INTERFACE:${CMAKE_SOURCE_DIR}/third_party/cpp-httplib>
)

# ── Header-only: nlohmann/json ──────────────────────────────────────────────
add_library(nlohmann_json INTERFACE)
target_include_directories(nlohmann_json INTERFACE
    $<BUILD_INTERFACE:${CMAKE_SOURCE_DIR}/third_party>
)

# ── GalaxyOS Desktop executable ──────────────────────────────────────────────
add_executable(galaxyos-desktop
    ${EUI_NEO_SOURCE_DIR}/core/app/glfw_app_main.cpp
    src/galaxyos_app.cpp
    src/native_logger.cpp
    src/native_config.cpp
    src/native_event_bus.cpp
    src/native_ipc_channel.cpp
    src/native_sse_client.cpp
    src/native_process_manager.cpp
    src/i18n_bridge.cpp

)

target_include_directories(galaxyos-desktop PRIVATE
    ${CMAKE_SOURCE_DIR}/include
)

# eui_neo_configure_app handles: link eui::neo, compile options, link options, copy assets
eui_neo_configure_app(galaxyos-desktop)

target_link_libraries(galaxyos-desktop PRIVATE
    cpp-httplib
    nlohmann_json
)

if(UNIX AND NOT APPLE)
    find_package(PkgConfig REQUIRED)
    pkg_check_modules(APPINDICATOR QUIET appindicator3-0.1)
    if(APPINDICATOR_FOUND)
        target_compile_definitions(galaxyos-desktop PRIVATE HAS_APPINDICATOR)
        target_include_directories(galaxyos-desktop PRIVATE ${APPINDICATOR_INCLUDE_DIRS})
        target_link_libraries(galaxyos-desktop PRIVATE ${APPINDICATOR_LIBRARIES})
    endif()
endif()

# ── Copy GalaxyOS app assets ────────────────────────────────────────────────
if(EXISTS "${CMAKE_SOURCE_DIR}/assets")
    eui_neo_copy_app_assets(galaxyos-desktop "${CMAKE_SOURCE_DIR}/assets")
endif()

# ── Tests ────────────────────────────────────────────────────────────────────
if(GALAXYOS_ENABLE_TESTS)
    enable_testing()
    add_subdirectory(tests)
endif()

# ── Install rules ────────────────────────────────────────────────────────────
if(WIN32)
    install(TARGETS galaxyos-desktop RUNTIME DESTINATION .)
    install(DIRECTORY ${CMAKE_BINARY_DIR}/../../galaxyos-mcp-dist/
            DESTINATION python OPTIONAL)
    install(DIRECTORY ${CMAKE_BINARY_DIR}/../../models/embeddings/
            DESTINATION models/embeddings OPTIONAL)

else()
    install(TARGETS galaxyos-desktop RUNTIME DESTINATION bin)
    install(DIRECTORY ${CMAKE_BINARY_DIR}/../../galaxyos-mcp-dist/
            DESTINATION share/galaxyos/galaxyos-mcp-dist OPTIONAL)
    install(DIRECTORY ${CMAKE_BINARY_DIR}/../../models/embeddings/
            DESTINATION share/galaxyos/models/embeddings OPTIONAL)
    install(FILES ${CMAKE_SOURCE_DIR}/scripts/galaxyos.desktop
            DESTINATION share/applications)
endif()

# ── CPack ────────────────────────────────────────────────────────────────────
set(CPACK_PACKAGE_NAME "GalaxyOS")
set(CPACK_PACKAGE_VERSION "${PROJECT_VERSION}")
set(CPACK_PACKAGE_VENDOR "GalaxyOS Team")
set(CPACK_PACKAGE_DESCRIPTION_SUMMARY "Cognitive AI Agent Desktop Engine")
set(CPACK_PACKAGE_HOMEPAGE_URL "https://github.com/xkzs2007/GalaxyOS-desktop")
set(CPACK_PACKAGE_INSTALL_DIRECTORY "GalaxyOS")
set(CPACK_PACKAGE_EXECUTABLES "galaxyos-desktop;GalaxyOS")
set(CPACK_PACKAGE_CONTACT "dev@galaxyos.ai")
set(CPACK_RESOURCE_FILE_LICENSE "${CMAKE_SOURCE_DIR}/../LICENSE")
set(CPACK_PACKAGE_CHECKSUM "SHA256")
set(CPACK_THREADS 0)

set(CPACK_PROJECT_CONFIG_FILE "${CMAKE_SOURCE_DIR}/CPackPlatformConfig.cmake")

if(WIN32)
    set(CPACK_GENERATOR "NSIS;ZIP")

    set(CPACK_NSIS_INSTALL_ROOT "$LOCALAPPDATA")
    set(CPACK_NSIS_PACKAGE_NAME "GalaxyOS")
    set(CPACK_NSIS_DISPLAY_NAME "GalaxyOS Desktop")
    set(CPACK_NSIS_HELP_LINK "https://github.com/xkzs2007/GalaxyOS-desktop")
    set(CPACK_NSIS_URL_INFO_ABOUT "https://github.com/xkzs2007/GalaxyOS-desktop")
    set(CPACK_NSIS_CONTACT "dev@galaxyos.ai")
    set(CPACK_NSIS_MODIFY_PATH OFF)
    set(CPACK_NSIS_ENABLE_UNINSTALL_BEFORE_INSTALL ON)
    set(CPACK_NSIS_MUI_FINISHPAGE_RUN "galaxyos-desktop.exe")
    set(CPACK_NSIS_MANIFEST_DPI_AWARE ON)
    set(CPACK_NSIS_BRANDING_TEXT "GalaxyOS Desktop v${PROJECT_VERSION}")
    set(CPACK_NSIS_WELCOME_TITLE "Welcome to GalaxyOS Desktop")
    set(CPACK_NSIS_FINISH_TITLE "GalaxyOS Desktop Installed Successfully")
    set(CPACK_NSIS_CREATE_ICONS_EXTRA
        "CreateShortCut '$DESKTOP\\\\GalaxyOS.lnk' '$INSTDIR\\\\galaxyos-desktop.exe'
         CreateShortCut '$STARTMENU\\\\GalaxyOS.lnk' '$INSTDIR\\\\galaxyos-desktop.exe'"
    )
    set(CPACK_NSIS_DELETE_ICONS_EXTRA
        "Delete '$DESKTOP\\\\GalaxyOS.lnk'
         Delete '$STARTMENU\\\\GalaxyOS.lnk'"
    )
    set(CPACK_NSIS_MENU_LINKS
        "https://github.com/xkzs2007/GalaxyOS-desktop" "GalaxyOS Homepage"
    )
    set(CPACK_NSIS_EXTRA_PREINSTALL_COMMANDS
        "ExecWait 'taskkill /IM galaxyos-desktop.exe /F'"
    )

    set(CPACK_NSIS_EXTRA_UNINSTALL_COMMANDS
        "ExecWait 'taskkill /IM galaxyos-desktop.exe /F'
         RMDir /r '$LOCALAPPDATA\\\\GalaxyOS\\\\logs'"
    )
else()
    set(CPACK_GENERATOR "DEB;TGZ")

    set(CPACK_DEBIAN_PACKAGE_NAME "galaxyos")
    set(CPACK_DEBIAN_PACKAGE_MAINTAINER "GalaxyOS Team <dev@galaxyos.ai>")
    set(CPACK_DEBIAN_PACKAGE_DESCRIPTION "Cognitive AI Agent Desktop Engine
 Powered by EUI-NEO GPU direct rendering and Python MCP Server.")
    set(CPACK_DEBIAN_PACKAGE_SECTION "utils")
    set(CPACK_DEBIAN_PACKAGE_PRIORITY "optional")
    set(CPACK_DEBIAN_PACKAGE_HOMEPAGE "https://github.com/xkzs2007/GalaxyOS-desktop")
    set(CPACK_DEBIAN_PACKAGE_SHLIBDEPS ON)
    set(CPACK_DEBIAN_FILE_NAME "DEB-DEFAULT")
    set(CPACK_DEBIAN_PACKAGE_CONTROL_EXTRA
        "${CMAKE_SOURCE_DIR}/scripts/pre-install.sh;${CMAKE_SOURCE_DIR}/scripts/post-install.sh;${CMAKE_SOURCE_DIR}/scripts/pre-remove.sh;${CMAKE_SOURCE_DIR}/scripts/post-remove.sh"
    )
    set(CPACK_DEBIAN_PACKAGE_RECOMMENDS "libappindicator3-1")
endif()

include(CPack)
