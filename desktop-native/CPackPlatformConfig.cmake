# CPackPlatformConfig.cmake — Per-generator overrides
# Included once per generator when CPACK_GENERATOR is a list.
# CPACK_GENERATOR is set to the current generator being iterated.

# Normalize CMAKE_SYSTEM_PROCESSOR for file naming (AMD64 -> x64 on Windows MSVC)
if(CMAKE_SYSTEM_PROCESSOR STREQUAL "AMD64")
    set(_GALAXYOS_ARCH "x64")
else()
    set(_GALAXYOS_ARCH "${CMAKE_SYSTEM_PROCESSOR}")
endif()

if(CPACK_GENERATOR STREQUAL "NSIS")
    # Windows NSIS-specific overrides at cpack time
    # CPACK_PACKAGE_FILE_NAME already includes platform info from CMake
elseif(CPACK_GENERATOR STREQUAL "ZIP")
    # Portable ZIP archive for Windows
    set(CPACK_PACKAGE_FILE_NAME "GalaxyOS-${CPACK_PACKAGE_VERSION}-windows-${_GALAXYOS_ARCH}-portable")
elseif(CPACK_GENERATOR STREQUAL "DEB")
    # Debian package — architecture auto-detected by dpkg
    # SHLIBDEPS will auto-populate CPACK_DEBIAN_PACKAGE_DEPENDS
elseif(CPACK_GENERATOR STREQUAL "TGZ")
    # Portable tar.gz for Linux
    set(CPACK_PACKAGE_FILE_NAME "GalaxyOS-${CPACK_PACKAGE_VERSION}-linux-${_GALAXYOS_ARCH}-portable")
endif()
