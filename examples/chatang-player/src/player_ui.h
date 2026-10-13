#pragma once
#include <Arduino.h>
#include <Wire.h>
#include <stdio.h>
#include <string.h>
#include "player_logic.h"
#include "player_config.h"

volatile uint32_t volumePercent = 50;
volatile uint32_t playbackElapsedMs = 0;
volatile uint32_t displayFrames = 0, displayFailures = 0;
volatile uint32_t oledAddress = 0, oledReady = 0;
uint32_t envelopeLevelQ15 = 0;

class PlayerUi {
  PlayerLogic::Controls controls;
  uint8_t frame[1024] = {};
  uint16_t sent = 1024;
  bool addressPending = true, playing = false;
  uint32_t epoch = 0, nextFrame = 0, retryAt = 0;

  bool transfer(uint8_t control, const uint8_t *bytes, size_t count) {
    Wire.beginTransmission(uint8_t(oledAddress));
    Wire.write(control);
    Wire.write(bytes, count);
    if (!Wire.endTransmission()) return true;
    ++displayFailures;
    oledReady = 0;
    oledAddress = 0;
    sent = 1024;
    retryAt = millis() + 3000;
    return false;
  }

  bool connect(uint32_t now) {
    for (uint8_t address : {uint8_t(0x3C), uint8_t(0x3D)}) {
      Wire.beginTransmission(address);
      if (!Wire.endTransmission()) { oledAddress = address; break; }
    }
    retryAt = now + 3000;
    if (!oledAddress) return false;
    const uint8_t common[] = {0xAE, 0xD5, 0x80, 0xA8, 0x3F, 0xD3, 0x00,
      0x40, 0xA1, 0xC8, 0xDA, 0x12, 0x81, 0x7F, 0xD9,
      uint8_t(PLAYER_SH1106 ? 0x22 : 0xF1), 0xDB, 0x40, 0xA4, 0xA6};
    if (!transfer(0, common, sizeof(common))) return false;
    const uint8_t ssd[] = {0x20, 0x02, 0x8D, 0x14, 0xAF};
    const uint8_t sh[] = {0xAD, 0x8B, 0xAF};
    if (!transfer(0, PLAYER_SH1106 ? sh : ssd, PLAYER_SH1106 ? sizeof(sh) : sizeof(ssd))) return false;
    oledReady = 1;
    sent = 1024;
    nextFrame = now;
    return true;
  }

  void pixel(int x, int y) {
    if (x >= 0 && x < 128 && y >= 0 && y < 64)
      frame[(y / 8) * 128 + x] |= 1U << (y & 7);
  }
  void rectangle(int x, int y, int w, int h, bool filled) {
    for (int row = 0; row < h; ++row)
      for (int col = 0; col < w; ++col)
        if (filled || !row || row == h - 1 || !col || col == w - 1)
          pixel(x + col, y + row);
  }
  void text(int x, int y, const char *value, uint8_t scale = 1) {
    for (; *value; ++value, x += 6 * scale) {
      const char *found = strchr(PLAYER_ASCII_CHARS, *value);
      if (!found) continue;
      const uint8_t *glyph = PLAYER_ASCII + (found - PLAYER_ASCII_CHARS) * 5;
      for (uint8_t col = 0; col < 5; ++col)
        for (uint8_t row = 0; row < 7; ++row)
          if (glyph[col] & (1U << row))
            rectangle(x + col * scale, y + row * scale, scale, scale, true);
    }
  }
  void draw() {
    memcpy(frame, PLAYER_BASE, sizeof(frame));
    char value[28];
    snprintf(value, sizeof(value), "%lu%%", (unsigned long)volumePercent);
    text(102, 25, value);
    const uint32_t seconds = playbackElapsedMs / 1000, total = PLAYER_DURATION_MS / 1000;
    snprintf(value, sizeof(value), "%lu:%02lu/%lu:%02lu", (unsigned long)(seconds / 60),
      (unsigned long)(seconds % 60), (unsigned long)(total / 60), (unsigned long)(total % 60));
    const uint8_t scale = strlen(value) <= 10 ? 2 : 1;
    text((128 - int(strlen(value)) * 6 * scale + scale) / 2, 38, value, scale);
    rectangle(3, 56, 122, 7, false);
    const uint16_t width = PlayerLogic::progress(playbackElapsedMs, PLAYER_DURATION_MS, 118);
    rectangle(5, 58, width, 3, true);
    if (playing) {
      for (int col = 0; col < 6; ++col) rectangle(116 + col, 4 + col, 1, 11 - 2 * col, true);
    }
  }

public:
  void begin() {
    pinMode(PLAYER_VOLUME_UP, INPUT_PULLUP);
    pinMode(PLAYER_VOLUME_DOWN, INPUT_PULLUP);
    Wire.setSCL(PLAYER_SCL);
    Wire.setSDA(PLAYER_SDA);
    Wire.begin();
    Wire.setClock(400000);
    connect(millis());
  }
  void start(uint32_t now) {
    epoch = now; playing = true; playbackElapsedMs = 0;
    nextFrame = now;
  }
  void finish() { playing = false; playbackElapsedMs = PLAYER_DURATION_MS; }
  uint32_t gain(uint32_t rawGain) const { return controls.gain(rawGain); }
  void poll(uint32_t now, uint32_t budgetMs = 20) {
    controls.poll(digitalRead(PLAYER_VOLUME_UP) == LOW, digitalRead(PLAYER_VOLUME_DOWN) == LOW, now);
    volumePercent = controls.percent;
    if (playing) playbackElapsedMs = PlayerLogic::elapsed(now, epoch, PLAYER_DURATION_MS);
    if (!oledReady) {
      if (PlayerLogic::due(now, nextFrame)) { draw(); nextFrame = now + 250; }
      // Probe only when there is enough time before the next audio boundary.
      if (budgetMs >= 25 && PlayerLogic::due(now, retryAt)) connect(now);
      return;
    }
    if (budgetMs < 3) return;
    if (sent == 1024) {
      if (!PlayerLogic::due(now, nextFrame)) return;
      draw();
      nextFrame = now + 250;
      sent = 0; addressPending = true;
    }
    if (addressPending) {
      const uint8_t offset = PLAYER_SH1106 ? 2 : 0;
      const uint8_t position[] = {uint8_t(0xB0 | (sent / 128)),
                                 uint8_t(offset & 15), uint8_t(0x10 | (offset >> 4))};
      if (transfer(0, position, sizeof(position))) addressPending = false;
    } else {
      // One small transfer per audio-loop iteration; never send a whole frame here.
      if (transfer(0x40, frame + sent, 16)) {
        sent += 16;
        addressPending = !(sent % 128);
        if (sent == 1024) ++displayFrames;
      }
    }
  }
  void wait(uint32_t duration) {
    const uint32_t deadline = millis() + duration;
    while (!PlayerLogic::due(millis(), deadline)) { poll(millis()); delay(1); }
  }
};
PlayerUi playerUi;
