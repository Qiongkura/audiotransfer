"""PWM voices and controlled comparisons for externally driven buzzer modules."""
from __future__ import annotations

import math
from .events import NoteEvent

MODES = {'soft': 2, 'harmonic': 3, 'pulse': 4}


def generate_module_main(events: list[NoteEvent], pin: str, gap_ms: int,
                         velocities: list[int], mode: str = 'harmonic', *,
                         active_low: bool = True, compare: bool = False,
                         player_ui: bool = False) -> str:
    if mode not in MODES:
        raise ValueError('模块音色必须为soft、harmonic或pulse')
    if len(events) != len(velocities) or any(not isinstance(v, int) or isinstance(v, bool) or not 1 <= v <= 127 for v in velocities):
        raise ValueError('力度数量及范围与音符不匹配')
    if not isinstance(active_low, bool) or gap_ms < 0:
        raise ValueError('控制极性必须是布尔值，停顿不能为负数')
    if player_ui and compare:
        raise ValueError('歌曲屏幕界面不用于四段对比模式')
    if any(not math.isfinite(e.frequency_hz) or not 0 <= e.frequency_hz < 4000
           or not 0 < e.duration_ms <= 0xFFFFFFFF for e in events):
        raise ValueError('模块音色要求0～4000Hz以内的频率和正时长')
    rows = '\n'.join(f'  {{{round(e.frequency_hz)}, {e.duration_ms}UL, {v}, {int(e.legato)}}},'
                     for e,v in zip(events, velocities)) or '  {0, 1UL, 100, 0},'
    lut = [round(math.sin(2 * math.pi * i / 256) * 32767) for i in range(256)]
    table = '\n'.join('  '+', '.join(map(str,lut[i:i+16]))+',' for i in range(0,256,16))
    source = r'''#include <Arduino.h>
#include <HardwareTimer.h>
@UI_INCLUDE@

const uint32_t BUZZER_PIN = @PIN@;
const uint32_t SAMPLE_RATE = 16000;
const uint32_t CARRIER_RATE = 48000;
const uint32_t GAP_MS = @GAP@;
const uint8_t DEFAULT_MODE = @MODE@;
const bool ACTIVE_LOW = @ACTIVE_LOW@;
const bool COMPARISON = @COMPARISON@;
struct MelodyNote { uint16_t frequency_hz; uint32_t duration_ms; uint8_t velocity; uint8_t flags; };
const MelodyNote melody[] = {
@ROWS@
};
const uint32_t MELODY_LENGTH = @LENGTH@;
const int16_t waveTable[256] = {
@TABLE@
};

HardwareTimer *carrier = nullptr, *sampler = nullptr;
TIM_TypeDef *carrierInstance = nullptr;
uint32_t carrierChannel = 0;
volatile uint32_t *compareRegister = nullptr;
volatile uint32_t phase = 0, phaseStep = 0, gainQ15 = 0;
volatile uint32_t harmonic2Q15 = 0, harmonic3Q15 = 0;
volatile uint32_t currentMode = DEFAULT_MODE, comparisonRound = 0;
uint32_t carrierTicks = 0;
bool voiceReady = false;

bool inverted(uint8_t mode) { return mode != 1 && ACTIVE_LOW; }

void audioTick() {
  phase += phaseStep;
  if (currentMode == 4) return;
  const uint32_t index = phase >> 24;
  int32_t sample = waveTable[index];
  if (currentMode == 3) {
    sample += (int32_t(waveTable[(index * 2) & 255]) * int32_t(harmonic2Q15)) >> 15;
    sample += (int32_t(waveTable[(index * 3) & 255]) * int32_t(harmonic3Q15)) >> 15;
    sample = (sample * 22755) >> 15;  // fixed headroom for at most 1+.32+.12
  }
  // Duty=0 corresponds to the inactive control level (PWM2 when inverted).
  // The carrier's DC component is not an analogue speaker output.
  const uint32_t duty = (uint32_t(sample + 32768) * gainQ15) >> 15;
  *compareRegister = (duty * carrierTicks) >> 16;
}

void silence() {
  gainQ15 = 0;
  @UI_SILENCE@
  if (voiceReady) *compareRegister = 0;
  else {
    noTone(BUZZER_PIN);
    digitalWrite(BUZZER_PIN, inverted(currentMode) ? HIGH : LOW);
  }
}

void configureCarrier(uint32_t frequency, uint8_t mode) {
  sampler->pause();
  carrier->pause();
  gainQ15 = 0;
  currentMode = mode;
  carrier->setMode(carrierChannel,
      inverted(mode) ? TIMER_OUTPUT_COMPARE_PWM2 : TIMER_OUTPUT_COMPARE_PWM1,
      digitalPinToPinName(BUZZER_PIN));
  carrier->setOverflow(frequency, HERTZ_FORMAT);
  carrier->setCaptureCompare(carrierChannel, 0, TICK_COMPARE_FORMAT);
  carrierTicks = carrier->getOverflow(TICK_FORMAT);
  carrier->refresh();
  carrier->resume();
  if (mode != 4) sampler->resume();
}

void setup() {
  digitalWrite(BUZZER_PIN, ACTIVE_LOW ? HIGH : LOW);
  pinMode(BUZZER_PIN, OUTPUT);
  @UI_SETUP@
  const PinName pin = digitalPinToPinName(BUZZER_PIN);
  carrierInstance = (TIM_TypeDef *)pinmap_peripheral(pin, PinMap_TIM);
  if (carrierInstance == nullptr || carrierInstance == (TIM_TypeDef *)NP) return;
  carrierChannel = STM_PIN_CHANNEL(pinmap_function(pin, PinMap_TIM));
  carrier = new HardwareTimer(carrierInstance);
  sampler = new HardwareTimer(carrierInstance == TIM2 ? TIM3 : TIM2);
  sampler->setOverflow(SAMPLE_RATE, HERTZ_FORMAT);
  sampler->attachInterrupt(audioTick);
  sampler->setInterruptPriority(2, 0);
  switch (carrierChannel) {
    case 1: compareRegister = &carrierInstance->CCR1; break;
    case 2: compareRegister = &carrierInstance->CCR2; break;
    case 3: compareRegister = &carrierInstance->CCR3; break;
    case 4: compareRegister = &carrierInstance->CCR4; break;
    default: return;
  }
  voiceReady = true;
  configureCarrier(CARRIER_RATE, DEFAULT_MODE);
  silence();
}

uint32_t envelopeGain(uint32_t elapsed, uint32_t sounding, uint8_t velocity,
                      bool continued, bool joinNext, uint32_t initialGain) {
  const uint32_t attack = continued ? 0 : min(12UL, sounding / 3);
  const uint32_t release = joinNext ? 0 : min(24UL, sounding / 3);
  uint32_t gain = 32767;
  if (continued) gain = 26214;
  else if (attack && elapsed < attack) gain = elapsed * 32767 / attack;
  else gain = 32767 - min(120UL, elapsed - attack) * 6553 / 120;
  gain = gain * velocity / 127;
  if (continued && elapsed < 8)
    gain = uint32_t(int32_t(initialGain) + (int32_t(gain) - int32_t(initialGain)) * int32_t(elapsed) / 8);
  if (release && sounding - elapsed < release)
    gain = gain * (sounding - elapsed) / release;
  return gain;
}

void playMelody(uint8_t mode) {
  if (!voiceReady) return;
  configureCarrier(CARRIER_RATE, mode);
  uint32_t deadline = millis(), previousFrequency = 0;
  @UI_START@
  for (uint32_t i = 0; i < MELODY_LENGTH; ++i) {
    const MelodyNote &note = melody[i];
    const bool continued = i && note.frequency_hz && melody[i-1].frequency_hz && (note.flags & 1);
    const bool joinNext = i+1 < MELODY_LENGTH && note.frequency_hz && melody[i+1].frequency_hz && (melody[i+1].flags & 1);
    const uint32_t gap = note.frequency_hz && !joinNext ? min(GAP_MS, note.duration_ms / 5) : 0;
    const uint32_t sounding = note.duration_ms - gap, start = deadline;
    deadline += note.duration_ms;
    const uint32_t initialGain = @INITIAL_GAIN@;
    if (!continued) silence();
    if (mode == 4 && note.frequency_hz && note.frequency_hz != previousFrequency)
      configureCarrier(note.frequency_hz, mode);
    phaseStep = uint32_t((uint64_t(note.frequency_hz) << 32) / SAMPLE_RATE);
    const uint32_t initialColor2 = harmonic2Q15, initialColor3 = harmonic3Q15;
    while (int32_t(@MUSIC_NOW@ - deadline) < 0) {
      @UI_POLL@
      @UI_PAUSE@
      const uint32_t elapsed = @MUSIC_NOW@ - start;
      if (note.frequency_hz && elapsed < sounding) {
        const uint32_t gain = envelopeGain(elapsed, sounding, note.velocity, continued, joinNext, initialGain);
        if (mode == 3) {
          uint32_t color2 = continued ? 2621 : 10485 - min(400UL, elapsed) * 7864 / 400;
          uint32_t color3 = continued ? 655 : 3932 - min(160UL, elapsed) * 3277 / 160;
          if (continued && elapsed < 8) {
            color2 = uint32_t(int32_t(initialColor2) + (int32_t(color2)-int32_t(initialColor2)) * int32_t(elapsed) / 8);
            color3 = uint32_t(int32_t(initialColor3) + (int32_t(color3)-int32_t(initialColor3)) * int32_t(elapsed) / 8);
          }
          // Avoid aliased partials even when the pitch range is extended.
          harmonic2Q15 = note.frequency_hz * 2 < SAMPLE_RATE / 2 ? color2 : 0;
          harmonic3Q15 = note.frequency_hz * 3 < SAMPLE_RATE / 2 ? color3 : 0;
        }
        @UI_RAW_GAIN@
        gainQ15 = @APPLIED_GAIN@;
        if (mode == 4) *compareRegister = uint64_t(carrierTicks) * gainQ15 / 65534;
      } else silence();
      delay(1);
    }
    if (!joinNext) silence();
    previousFrequency = note.frequency_hz;
  }
  silence();
  @UI_FINISH@
}

void marker(uint8_t count) {
  configureCarrier(1200, 4);
  for (uint8_t i=0; i<count; ++i) {
    *compareRegister = carrierTicks / 3;
    delay(90);
    silence();
    delay(120);
  }
  delay(900);
}

void loop() {
  if (COMPARISON) {
    ++comparisonRound;
    for (uint8_t mode=1; mode<=4; ++mode) {
      marker(mode);
      playMelody(mode);
      // Park at the hypothesized module-off level between all versions.
      configureCarrier(CARRIER_RATE, ACTIVE_LOW ? 2 : 1);
      silence();
      delay(1400);
    }
    delay(2500);
  } else {
    playMelody(DEFAULT_MODE);
    @UI_WAIT@
  }
}
'''
    values = {'PIN':pin,'GAP':str(gap_ms),'MODE':str(MODES[mode]),'ACTIVE_LOW':str(active_low).lower(),
              'COMPARISON':str(compare).lower(),'ROWS':rows,'LENGTH':str(len(events)),'TABLE':table}
    values.update({
        'UI_INCLUDE': '#include "player_ui.h"' if player_ui else '',
        'UI_SILENCE': 'envelopeLevelQ15 = 0;' if player_ui else '',
        'UI_SETUP': 'playerUi.begin();' if player_ui else '',
        'UI_START': 'playerUi.start(deadline);' if player_ui else '',
        'INITIAL_GAIN': 'envelopeLevelQ15' if player_ui else 'gainQ15',
        'UI_RAW_GAIN': 'envelopeLevelQ15 = gain;' if player_ui else '',
        'APPLIED_GAIN': 'playerUi.gain(gain)' if player_ui else 'gain',
        'MUSIC_NOW': 'playerUi.musicNow()' if player_ui else 'millis()',
        'UI_POLL': 'const uint32_t uiMusicNow = playerUi.musicNow(); const int32_t uiRemaining = (note.frequency_hz && uint32_t(uiMusicNow - start) < sounding) ? int32_t(start + sounding - uiMusicNow) : int32_t(deadline - uiMusicNow); playerUi.poll(millis(), playerUi.paused() ? 20 : (uiRemaining > 0 ? uint32_t(uiRemaining) : 0));' if player_ui else '',
        'UI_PAUSE': 'if (playerUi.paused()) { gainQ15 = 0; *compareRegister = 0; delay(1); continue; }\n      if (int32_t(playerUi.musicNow() - deadline) >= 0) break;' if player_ui else '',
        'UI_FINISH': 'playerUi.finish();' if player_ui else '',
        'UI_WAIT': 'playerUi.wait(100);' if player_ui else 'delay(100);',
    })
    for key,value in values.items():
        source = source.replace('@'+key+'@',value)
    return source
