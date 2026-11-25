cmake_minimum_required(VERSION 3.22)
project(stalltrace_fixture C)
add_library(stalltrace_fixture SHARED fixtures.c)
target_compile_options(stalltrace_fixture PRIVATE -g -fno-omit-frame-pointer)
