#pragma once
#include <stdint.h>

namespace PlayerLogic {
// Electrical gain taper spanning about 60 dB; not a calibrated SPL reading.
inline uint32_t volumeGainQ15(uint32_t percent) {
  static const uint16_t gains[] = {
  0, 35, 38, 40, 43, 46, 50, 53, 57, 61,
  65, 70, 75, 80, 86, 92, 99, 106, 114, 122,
  130, 140, 150, 160, 172, 184, 197, 212, 227, 243,
  260, 279, 299, 320, 343, 368, 394, 422, 452, 485,
  519, 556, 596, 639, 685, 734, 786, 842, 902, 967,
  1036, 1110, 1190, 1275, 1366, 1464, 1568, 1680, 1801, 1929,
  2067, 2215, 2374, 2544, 2725, 2920, 3129, 3353, 3593, 3850,
  4125, 4420, 4736, 5075, 5438, 5827, 6244, 6690, 7169, 7681,
  8231, 8819, 9450, 10126, 10850, 11626, 12458, 13349, 14303, 15326,
  16422, 17597, 18855, 20204, 21649, 23197, 24856, 26634, 28539, 30580,
  32767,
  };
  return gains[percent > 100 ? 100 : percent];
}

inline uint32_t adjustedVolume(uint32_t percent, bool increase) {
  if (percent > 100) percent = 100;
  const uint32_t step = (increase ? percent < 20 : percent <= 20) ? 1 : 5;
  if (increase) return percent + step > 100 ? 100 : percent + step;
  return percent < step ? 0 : percent - step;
}

inline bool due(uint32_t now, uint32_t deadline) {
  return int32_t(now - deadline) >= 0;
}

struct Key {
  bool raw = false, pressed = false;
  uint32_t changedAt = 0, repeatAt = 0;
  bool poll(bool level, uint32_t now) {
    if (level != raw) { raw = level; changedAt = now; }
    if (pressed != raw && uint32_t(now - changedAt) >= 25) {
      pressed = raw;
      if (pressed) { repeatAt = now + 350; return true; }
    }
    if (pressed && raw && due(now, repeatAt)) {
      repeatAt = now + 150;
      return true;
    }
    return false;
  }
};

// A transport key fires once per debounced press, without volume-key repeat.
struct ToggleKey {
  bool raw = false, pressed = false;
  uint32_t changedAt = 0;
  bool poll(bool level, uint32_t now) {
    if (level != raw) { raw = level; changedAt = now; }
    if (pressed != raw && uint32_t(now - changedAt) >= 25) {
      pressed = raw;
      return pressed;
    }
    return false;
  }
};

struct PlaybackClock {
  bool paused = false;
  uint32_t pausedAt = 0, pausedTotal = 0;
  void start() { paused = false; pausedAt = pausedTotal = 0; }
  uint32_t musicNow(uint32_t wallNow) const {
    return (paused ? pausedAt : wallNow) - pausedTotal;
  }
  void toggle(uint32_t now) {
    if (paused) pausedTotal += uint32_t(now - pausedAt);
    else pausedAt = now;
    paused = !paused;
  }
};

struct Controls {
  Key up, down;
  uint32_t percent = 50, scaleQ15 = 1036;
  uint32_t rampFrom = 1036, rampAt = 0;
  void poll(bool plus, bool minus, uint32_t now) {
    const uint32_t target = volumeGainQ15(percent);
    const uint32_t age = uint32_t(now - rampAt);
    scaleQ15 = age >= 20 ? target : uint32_t(int32_t(rampFrom) +
        (int32_t(target) - int32_t(rampFrom)) * int32_t(age) / 20);
    const bool increase = up.poll(plus, now), decrease = down.poll(minus, now);
    if (plus && minus) return;
    uint32_t next = percent;
    if (increase && plus) next = adjustedVolume(percent, true);
    if (decrease && minus) next = adjustedVolume(percent, false);
    if (next != percent) { rampFrom = scaleQ15; rampAt = now; percent = next; }
  }
  uint32_t gain(uint32_t rawGain) const {
    return uint32_t(uint64_t(rawGain) * scaleQ15 / 32767);
  }
};

inline uint32_t elapsed(uint32_t now, uint32_t start, uint32_t duration) {
  const uint32_t delta = uint32_t(now - start);
  return delta < duration ? delta : duration;
}
inline uint16_t progress(uint32_t elapsedMs, uint32_t durationMs, uint16_t width) {
  if (!durationMs) return 0;
  if (elapsedMs >= durationMs) return width;
  return uint16_t(uint64_t(elapsedMs) * width / durationMs);
}
}
