#pragma once
#include <Arduino.h>

constexpr uint8_t KEYBOARD_INSTANCE = 0;
constexpr uint8_t MOUSE_INSTANCE = 1;

bool beginBootHid();
bool sendBootHid(uint8_t instance, const void *data);
