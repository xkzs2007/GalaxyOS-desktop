cmake_minimum_required(VERSION 3.21)

add_executable(galaxyos-desktop-test
    test_main.cpp
)

target_include_directories(galaxyos-desktop-test PRIVATE
    ${CMAKE_SOURCE_DIR}/include
)

target_link_libraries(galaxyos-desktop-test PRIVATE

    cpp-httplib
    nlohmann_json
)

add_test(NAME galaxyos-desktop-test COMMAND galaxyos-desktop-test)
