"""Generate a timer-driven PWM sine voice for the STM32 Arduino core."""
from __future__ import annotations

import math
from .events import NoteEvent


def generate_soft_main(events: list[NoteEvent], pin: str, gap_ms: int, velocities: list[int]) -> str:
    if any(e.frequency_hz >= 4000 for e in events):
        raise ValueError("柔和发声模式的音符应低于 4000 Hz；更高音域请选 classic")
    rows = [f"  {{{round(e.frequency_hz)}, {e.duration_ms}UL, {v}, {int(e.legato)}}}," for e, v in zip(events, velocities)]
    table = [round((1 + math.sin(2 * math.pi * i / 256)) * 32767.5) for i in range(256)]
    lut = "\n".join("  " + ", ".join(str(n) for n in table[i:i + 16]) + "," for i in range(0, 256, 16))
    template = r'''#include <Arduino.h>
#include <HardwareTimer.h>

const uint32_t BUZZER_PIN = @PIN@;
const uint32_t MELODY_GAP_MS = @GAP@UL;
const uint32_t SAMPLE_RATE = 16000;
const uint32_t CARRIER_RATE = 48000;

struct MelodyNote { uint16_t frequency_hz; uint32_t duration_ms; uint8_t velocity; uint8_t flags; };
const MelodyNote melody[] = {
@ROWS@
};
const uint32_t MELODY_LENGTH = @LENGTH@UL;
const uint16_t sineTable[256] = {
@LUT@
};

HardwareTimer *carrier = nullptr;
HardwareTimer *sampler = nullptr;
volatile uint32_t *compareRegister = nullptr;
volatile uint32_t phase = 0, phaseStep = 0;
volatile uint32_t gainQ15 = 0;
uint32_t carrierTicks = 0;
bool softReady = false;

void audioTick() {
  phase += phaseStep;
  // Both DC level and AC amplitude fall to zero on a rest. The pin is LOW
  // throughout silence rather than emitting a continuous 50% carrier.
  const uint32_t sample = (uint32_t(sineTable[phase >> 24]) * gainQ15) >> 15;
  *compareRegister = (sample * carrierTicks) >> 16;
}

void silence() {
  gainQ15 = 0;
  if (softReady) {
    *compareRegister = 0;
  } else {
    noTone(BUZZER_PIN);
    digitalWrite(BUZZER_PIN, LOW);
  }
}

void setup() {
  pinMode(BUZZER_PIN, OUTPUT);
  digitalWrite(BUZZER_PIN, LOW);
  const PinName pin = digitalPinToPinName(BUZZER_PIN);
  TIM_TypeDef *instance = (TIM_TypeDef *)pinmap_peripheral(pin, PinMap_TIM);
  if (instance == nullptr || instance == (TIM_TypeDef *)NP) return;
  const uint32_t channel = STM_PIN_CHANNEL(pinmap_function(pin, PinMap_TIM));
  carrier = new HardwareTimer(instance);
  carrier->setMode(channel, TIMER_OUTPUT_COMPARE_PWM1, pin);
  carrier->setOverflow(CARRIER_RATE, HERTZ_FORMAT);
  carrier->setCaptureCompare(channel, 0, TICK_COMPARE_FORMAT);
  carrierTicks = carrier->getOverflow(TICK_FORMAT);
  switch (channel) {
    case 1: compareRegister = &instance->CCR1; break;
    case 2: compareRegister = &instance->CCR2; break;
    case 3: compareRegister = &instance->CCR3; break;
    case 4: compareRegister = &instance->CCR4; break;
    default: return;
  }
  carrier->resume();
  // Keep the sample clock separate from the selected PWM timer.
  sampler = new HardwareTimer(instance == TIM2 ? TIM3 : TIM2);
  sampler->setOverflow(SAMPLE_RATE, HERTZ_FORMAT);
  sampler->attachInterrupt(audioTick);
  sampler->setInterruptPriority(2, 0);
  softReady = true;
  sampler->resume();
}

uint32_t envelopeGain(uint32_t elapsed, uint32_t sounding, uint8_t velocity,
                      bool continued, bool joinNext, uint32_t initialGain) {
  const uint32_t attack = continued ? 0 : min(12UL, sounding / 3);
  const uint32_t release = joinNext ? 0 : min(24UL, sounding / 3);
  // Twelve-ms attack, modest decay, and a 24-ms release. Strength follows
  // phrase and beat accents, not noisy frame-level amplitude estimates.
  uint32_t gain = 32767;
  if (continued) gain = 26214;
  else if (attack > 0 && elapsed < attack) gain = elapsed * 32767 / attack;
  else gain = 32767 - min(120UL, elapsed - attack) * 6553 / 120;
  gain = gain * velocity / 127;
  // Eight-ms gain smoothing at connected pitch changes; never restart the
  // attack or reset the oscillator inside a legato phrase.
  if (continued && elapsed < 8)
    gain = uint32_t(int32_t(initialGain) + (int32_t(gain) - int32_t(initialGain)) * int32_t(elapsed) / 8);
  if (release > 0 && sounding - elapsed < release)
    gain = gain * (sounding - elapsed) / release;
  return gain;
}

void loop() {
  uint32_t deadline = millis();
  for (uint32_t i = 0; i < MELODY_LENGTH; ++i) {
    const MelodyNote &note = melody[i];
    const bool continued = i > 0 && note.frequency_hz && melody[i - 1].frequency_hz && (note.flags & 1);
    const bool joinNext = i + 1 < MELODY_LENGTH && note.frequency_hz && melody[i + 1].frequency_hz && (melody[i + 1].flags & 1);
    const uint32_t gap = note.frequency_hz && !joinNext ? min(MELODY_GAP_MS, note.duration_ms / 5) : 0;
    const uint32_t sounding = note.duration_ms - gap;
    const uint32_t start = deadline;
    deadline += note.duration_ms;
    const uint32_t initialGain = gainQ15;
    if (!continued) silence();
    if (softReady) {
      phaseStep = uint32_t((uint64_t(note.frequency_hz) << 32) / SAMPLE_RATE);
    } else if (note.frequency_hz) {
      tone(BUZZER_PIN, note.frequency_hz);
    }
    // Absolute deadlines prevent ISR/loop overhead accumulating into tempo
    // drift over the five-minute song. uint32 subtraction handles wraparound.
    while (int32_t(millis() - deadline) < 0) {
      const uint32_t elapsed = millis() - start;
      if (note.frequency_hz && elapsed < sounding) {
        if (softReady) gainQ15 = envelopeGain(elapsed, sounding, note.velocity, continued, joinNext, initialGain);
      } else silence();
      delay(1);
    }
    if (!joinNext) silence();
  }
  delay(100);
}
'''
    return (template.replace("@PIN@", pin).replace("@GAP@", str(gap_ms))
            .replace("@ROWS@", "\n".join(rows) if rows else "  {0, 0UL, 0, 0},")
            .replace("@LENGTH@", str(len(events))).replace("@LUT@", lut))
