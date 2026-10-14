#include <Arduino.h>
#include <HardwareTimer.h>
#include "player_ui.h"

const uint32_t BUZZER_PIN = PA8;
const uint32_t SAMPLE_RATE = 16000;
const uint32_t CARRIER_RATE = 48000;
const uint32_t GAP_MS = 12;
const uint8_t DEFAULT_MODE = 4;
const bool ACTIVE_LOW = true;
const bool COMPARISON = false;
struct MelodyNote { uint16_t frequency_hz; uint32_t duration_ms; uint8_t velocity; uint8_t flags; };
const MelodyNote melody[] = {
  {0, 6510UL, 88, 0},
  {0, 1851UL, 88, 0},
  {1480, 133UL, 85, 0},
  {1760, 132UL, 85, 1},
  {1480, 1587UL, 98, 1},
  {1109, 529UL, 91, 1},
  {1319, 4232UL, 98, 1},
  {0, 1851UL, 88, 0},
  {1480, 133UL, 85, 0},
  {1760, 132UL, 85, 1},
  {1480, 1587UL, 98, 1},
  {1109, 529UL, 91, 1},
  {988, 4232UL, 98, 1},
  {0, 2262UL, 88, 0},
  {0, 535UL, 88, 0},
  {440, 268UL, 91, 0},
  {440, 267UL, 85, 0},
  {440, 535UL, 91, 0},
  {370, 268UL, 91, 0},
  {330, 267UL, 85, 0},
  {440, 535UL, 98, 0},
  {494, 268UL, 91, 0},
  {554, 535UL, 85, 0},
  {659, 802UL, 85, 0},
  {0, 535UL, 88, 0},
  {494, 268UL, 91, 0},
  {494, 267UL, 85, 0},
  {494, 268UL, 91, 0},
  {370, 535UL, 85, 0},
  {554, 267UL, 85, 0},
  {494, 1873UL, 98, 0},
  {554, 267UL, 85, 0},
  {659, 268UL, 98, 0},
  {554, 267UL, 85, 0},
  {659, 268UL, 91, 0},
  {740, 1070UL, 85, 0},
  {659, 267UL, 85, 0},
  {740, 268UL, 98, 0},
  {659, 267UL, 85, 0},
  {740, 268UL, 91, 0},
  {880, 535UL, 85, 0},
  {740, 267UL, 85, 0},
  {659, 268UL, 91, 0},
  {554, 267UL, 85, 0},
  {494, 268UL, 98, 0},
  {554, 535UL, 85, 0},
  {370, 1070UL, 85, 0},
  {370, 267UL, 85, 0},
  {330, 268UL, 98, 0},
  {554, 535UL, 85, 0},
  {494, 1337UL, 85, 0},
  {0, 535UL, 88, 0},
  {440, 268UL, 91, 0},
  {440, 267UL, 85, 0},
  {440, 535UL, 91, 0},
  {370, 268UL, 91, 0},
  {330, 267UL, 85, 0},
  {440, 535UL, 98, 0},
  {494, 268UL, 91, 0},
  {554, 535UL, 85, 0},
  {659, 802UL, 85, 0},
  {0, 535UL, 88, 0},
  {494, 268UL, 91, 0},
  {494, 267UL, 85, 0},
  {494, 268UL, 91, 0},
  {370, 535UL, 85, 0},
  {554, 267UL, 85, 0},
  {494, 1873UL, 98, 0},
  {554, 267UL, 85, 0},
  {659, 268UL, 98, 0},
  {554, 267UL, 85, 0},
  {659, 268UL, 91, 0},
  {740, 1070UL, 85, 0},
  {659, 267UL, 85, 0},
  {740, 268UL, 98, 0},
  {659, 267UL, 85, 0},
  {740, 268UL, 91, 0},
  {880, 535UL, 85, 0},
  {740, 267UL, 85, 0},
  {659, 268UL, 91, 0},
  {554, 267UL, 85, 0},
  {494, 268UL, 98, 0},
  {494, 535UL, 85, 0},
  {554, 535UL, 85, 0},
  {370, 802UL, 85, 0},
  {0, 1070UL, 88, 0},
  {494, 535UL, 91, 0},
  {440, 535UL, 91, 0},
  {659, 2140UL, 98, 0},
  {0, 40UL, 88, 0},
  {0, 268UL, 88, 0},
  {659, 267UL, 85, 0},
  {659, 268UL, 91, 0},
  {659, 268UL, 85, 0},
  {740, 268UL, 91, 0},
  {659, 267UL, 85, 0},
  {740, 268UL, 91, 0},
  {1109, 268UL, 85, 0},
  {988, 268UL, 98, 0},
  {988, 535UL, 85, 0},
  {740, 268UL, 85, 0},
  {988, 1071UL, 91, 0},
  {0, 268UL, 88, 0},
  {988, 267UL, 85, 0},
  {988, 268UL, 91, 0},
  {988, 268UL, 85, 0},
  {988, 268UL, 91, 0},
  {880, 267UL, 85, 0},
  {740, 268UL, 91, 0},
  {659, 268UL, 85, 0},
  {740, 803UL, 98, 0},
  {554, 268UL, 85, 0},
  {659, 1071UL, 91, 0},
  {0, 268UL, 88, 0},
  {659, 267UL, 85, 0},
  {659, 268UL, 91, 0},
  {659, 268UL, 85, 0},
  {740, 268UL, 91, 0},
  {659, 267UL, 85, 0},
  {740, 268UL, 91, 0},
  {1109, 268UL, 85, 0},
  {988, 268UL, 98, 0},
  {988, 535UL, 85, 0},
  {740, 268UL, 85, 0},
  {988, 1071UL, 91, 0},
  {0, 268UL, 88, 0},
  {988, 267UL, 85, 0},
  {988, 268UL, 91, 0},
  {988, 268UL, 85, 0},
  {988, 268UL, 91, 0},
  {880, 267UL, 85, 0},
  {988, 268UL, 91, 0},
  {1319, 536UL, 85, 0},
  {1109, 1874UL, 85, 0},
  {0, 268UL, 88, 0},
  {659, 267UL, 85, 0},
  {659, 268UL, 91, 0},
  {659, 268UL, 85, 0},
  {740, 268UL, 91, 0},
  {659, 267UL, 85, 0},
  {740, 268UL, 91, 0},
  {1109, 268UL, 85, 0},
  {988, 268UL, 98, 0},
  {988, 535UL, 85, 0},
  {740, 268UL, 85, 0},
  {988, 1071UL, 91, 0},
  {0, 268UL, 88, 0},
  {988, 267UL, 85, 0},
  {988, 268UL, 91, 0},
  {988, 268UL, 85, 0},
  {988, 268UL, 91, 0},
  {880, 267UL, 85, 0},
  {740, 268UL, 91, 0},
  {659, 268UL, 85, 0},
  {740, 803UL, 98, 0},
  {554, 268UL, 85, 0},
  {659, 1071UL, 91, 0},
  {0, 268UL, 88, 0},
  {659, 267UL, 85, 0},
  {659, 268UL, 91, 0},
  {659, 268UL, 85, 0},
  {740, 268UL, 91, 0},
  {659, 267UL, 85, 0},
  {740, 268UL, 91, 0},
  {1109, 268UL, 85, 0},
  {988, 268UL, 98, 0},
  {988, 535UL, 85, 0},
  {740, 268UL, 85, 0},
  {988, 1071UL, 91, 0},
  {0, 268UL, 88, 0},
  {988, 267UL, 85, 0},
  {988, 268UL, 91, 0},
  {988, 268UL, 85, 0},
  {988, 268UL, 91, 0},
  {880, 267UL, 85, 0},
  {740, 268UL, 91, 0},
  {880, 803UL, 85, 0},
  {740, 1655UL, 91, 0},
  {0, 2100UL, 88, 0},
  {0, 536UL, 88, 0},
  {440, 267UL, 91, 0},
  {440, 268UL, 85, 0},
  {440, 536UL, 91, 0},
  {370, 267UL, 91, 0},
  {330, 268UL, 85, 0},
  {440, 536UL, 98, 0},
  {494, 267UL, 91, 0},
  {554, 536UL, 85, 0},
  {659, 803UL, 85, 0},
  {0, 536UL, 88, 0},
  {494, 267UL, 91, 0},
  {494, 268UL, 85, 0},
  {494, 268UL, 91, 0},
  {370, 535UL, 85, 0},
  {554, 268UL, 85, 0},
  {494, 1874UL, 98, 0},
  {554, 268UL, 85, 0},
  {659, 268UL, 98, 0},
  {554, 268UL, 85, 0},
  {659, 267UL, 91, 0},
  {740, 1071UL, 85, 0},
  {659, 268UL, 85, 0},
  {740, 268UL, 98, 0},
  {659, 268UL, 85, 0},
  {740, 267UL, 91, 0},
  {880, 536UL, 85, 0},
  {740, 268UL, 85, 0},
  {659, 267UL, 91, 0},
  {554, 268UL, 85, 0},
  {494, 268UL, 98, 0},
  {554, 535UL, 85, 0},
  {370, 1071UL, 85, 0},
  {370, 268UL, 85, 0},
  {330, 268UL, 98, 0},
  {554, 535UL, 85, 0},
  {494, 1339UL, 85, 0},
  {0, 536UL, 88, 0},
  {440, 267UL, 91, 0},
  {440, 268UL, 85, 0},
  {440, 536UL, 91, 0},
  {370, 267UL, 91, 0},
  {330, 268UL, 85, 0},
  {440, 536UL, 98, 0},
  {494, 267UL, 91, 0},
  {554, 536UL, 85, 0},
  {659, 803UL, 85, 0},
  {0, 536UL, 88, 0},
  {494, 267UL, 91, 0},
  {494, 268UL, 85, 0},
  {494, 268UL, 91, 0},
  {370, 535UL, 85, 0},
  {554, 268UL, 85, 0},
  {494, 1874UL, 98, 0},
  {554, 268UL, 85, 0},
  {659, 268UL, 98, 0},
  {554, 268UL, 85, 0},
  {659, 267UL, 91, 0},
  {740, 1071UL, 85, 0},
  {659, 268UL, 85, 0},
  {740, 268UL, 98, 0},
  {659, 268UL, 85, 0},
  {740, 267UL, 91, 0},
  {880, 536UL, 85, 0},
  {740, 268UL, 85, 0},
  {659, 267UL, 91, 0},
  {554, 268UL, 85, 0},
  {494, 268UL, 98, 0},
  {494, 535UL, 85, 0},
  {554, 536UL, 85, 0},
  {370, 803UL, 85, 0},
  {0, 1071UL, 88, 0},
  {494, 536UL, 91, 0},
  {440, 535UL, 91, 0},
  {659, 2142UL, 98, 0},
  {0, 36UL, 88, 0},
  {0, 267UL, 88, 0},
  {659, 267UL, 85, 0},
  {659, 268UL, 91, 0},
  {659, 267UL, 85, 0},
  {740, 267UL, 91, 0},
  {659, 267UL, 85, 0},
  {740, 268UL, 91, 0},
  {1109, 267UL, 85, 0},
  {988, 267UL, 98, 0},
  {988, 535UL, 85, 0},
  {740, 267UL, 85, 0},
  {988, 1069UL, 91, 0},
  {0, 267UL, 88, 0},
  {988, 267UL, 85, 0},
  {988, 268UL, 91, 0},
  {988, 267UL, 85, 0},
  {988, 267UL, 91, 0},
  {880, 267UL, 85, 0},
  {740, 268UL, 91, 0},
  {659, 267UL, 85, 0},
  {740, 802UL, 98, 0},
  {554, 267UL, 85, 0},
  {659, 1069UL, 91, 0},
  {0, 267UL, 88, 0},
  {659, 267UL, 85, 0},
  {659, 268UL, 91, 0},
  {659, 267UL, 85, 0},
  {740, 267UL, 91, 0},
  {659, 267UL, 85, 0},
  {740, 268UL, 91, 0},
  {1109, 267UL, 85, 0},
  {988, 267UL, 98, 0},
  {988, 535UL, 85, 0},
  {740, 267UL, 85, 0},
  {988, 1069UL, 91, 0},
  {0, 267UL, 88, 0},
  {988, 267UL, 85, 0},
  {988, 268UL, 91, 0},
  {988, 267UL, 85, 0},
  {988, 267UL, 91, 0},
  {880, 267UL, 85, 0},
  {988, 268UL, 91, 0},
  {1319, 534UL, 85, 0},
  {1109, 1871UL, 85, 0},
  {0, 267UL, 88, 0},
  {659, 267UL, 85, 0},
  {659, 268UL, 91, 0},
  {659, 267UL, 85, 0},
  {740, 267UL, 91, 0},
  {659, 267UL, 85, 0},
  {740, 268UL, 91, 0},
  {1109, 267UL, 85, 0},
  {988, 267UL, 98, 0},
  {988, 535UL, 85, 0},
  {740, 267UL, 85, 0},
  {988, 1069UL, 91, 0},
  {0, 267UL, 88, 0},
  {988, 267UL, 85, 0},
  {988, 268UL, 91, 0},
  {988, 267UL, 85, 0},
  {988, 267UL, 91, 0},
  {880, 267UL, 85, 0},
  {740, 268UL, 91, 0},
  {659, 267UL, 85, 0},
  {740, 802UL, 98, 0},
  {554, 267UL, 85, 0},
  {659, 1069UL, 91, 0},
  {0, 267UL, 88, 0},
  {659, 267UL, 85, 0},
  {659, 268UL, 91, 0},
  {659, 267UL, 85, 0},
  {740, 267UL, 91, 0},
  {659, 267UL, 85, 0},
  {740, 268UL, 91, 0},
  {1109, 267UL, 85, 0},
  {988, 267UL, 98, 0},
  {988, 535UL, 85, 0},
  {740, 267UL, 85, 0},
  {988, 1069UL, 91, 0},
  {0, 267UL, 88, 0},
  {988, 267UL, 85, 0},
  {988, 268UL, 91, 0},
  {988, 267UL, 85, 0},
  {988, 267UL, 91, 0},
  {880, 267UL, 85, 0},
  {740, 268UL, 91, 0},
  {880, 801UL, 85, 0},
  {740, 2246UL, 91, 0},
  {0, 5800UL, 88, 0},
  {554, 1071UL, 98, 0},
  {554, 535UL, 91, 0},
  {554, 536UL, 91, 0},
  {494, 268UL, 98, 1},
  {554, 268UL, 85, 1},
  {554, 267UL, 91, 0},
  {494, 268UL, 85, 1},
  {494, 268UL, 91, 0},
  {440, 267UL, 85, 1},
  {440, 268UL, 91, 0},
  {494, 268UL, 85, 1},
  {554, 1071UL, 98, 1},
  {554, 535UL, 91, 0},
  {554, 536UL, 91, 0},
  {494, 268UL, 98, 1},
  {554, 268UL, 85, 1},
  {554, 267UL, 91, 0},
  {494, 268UL, 85, 1},
  {494, 268UL, 91, 0},
  {440, 267UL, 85, 1},
  {440, 268UL, 91, 0},
  {494, 268UL, 85, 1},
  {554, 1071UL, 98, 1},
  {554, 535UL, 91, 0},
  {554, 536UL, 91, 0},
  {494, 268UL, 98, 1},
  {554, 267UL, 85, 1},
  {554, 268UL, 91, 0},
  {494, 268UL, 85, 1},
  {494, 268UL, 91, 0},
  {440, 267UL, 85, 1},
  {440, 268UL, 91, 0},
  {494, 268UL, 85, 1},
  {554, 1071UL, 98, 1},
  {554, 535UL, 91, 0},
  {554, 536UL, 91, 0},
  {494, 268UL, 98, 1},
  {554, 267UL, 85, 1},
  {554, 268UL, 91, 0},
  {494, 268UL, 85, 1},
  {494, 1071UL, 91, 0},
  {0, 44UL, 88, 0},
  {0, 536UL, 88, 0},
  {440, 269UL, 91, 0},
  {440, 268UL, 85, 0},
  {440, 536UL, 91, 0},
  {370, 269UL, 91, 0},
  {330, 268UL, 85, 0},
  {440, 536UL, 98, 0},
  {494, 269UL, 91, 0},
  {554, 536UL, 85, 0},
  {659, 805UL, 85, 0},
  {0, 536UL, 88, 0},
  {494, 269UL, 91, 0},
  {494, 268UL, 85, 0},
  {494, 268UL, 91, 0},
  {370, 537UL, 85, 0},
  {554, 268UL, 85, 0},
  {494, 1878UL, 98, 0},
  {554, 268UL, 85, 0},
  {659, 268UL, 98, 0},
  {554, 268UL, 85, 0},
  {659, 269UL, 91, 0},
  {740, 1073UL, 85, 0},
  {659, 268UL, 85, 0},
  {740, 268UL, 98, 0},
  {659, 268UL, 85, 0},
  {740, 269UL, 91, 0},
  {880, 536UL, 85, 0},
  {740, 268UL, 85, 0},
  {659, 269UL, 91, 0},
  {554, 268UL, 85, 0},
  {494, 268UL, 98, 0},
  {494, 537UL, 85, 0},
  {554, 536UL, 85, 0},
  {370, 805UL, 85, 0},
  {0, 1073UL, 88, 0},
  {494, 536UL, 91, 0},
  {440, 537UL, 91, 0},
  {659, 2146UL, 98, 0},
  {0, 224UL, 88, 0},
  {659, 268UL, 85, 0},
  {659, 268UL, 91, 0},
  {659, 268UL, 85, 0},
  {740, 268UL, 91, 0},
  {659, 268UL, 85, 0},
  {740, 268UL, 91, 0},
  {1109, 268UL, 85, 0},
  {988, 268UL, 98, 0},
  {988, 536UL, 85, 0},
  {740, 268UL, 85, 0},
  {988, 1072UL, 91, 0},
  {0, 268UL, 88, 0},
  {988, 268UL, 85, 0},
  {988, 268UL, 91, 0},
  {988, 268UL, 85, 0},
  {988, 268UL, 91, 0},
  {880, 268UL, 85, 0},
  {740, 268UL, 91, 0},
  {659, 268UL, 85, 0},
  {740, 804UL, 98, 0},
  {554, 268UL, 85, 0},
  {659, 1072UL, 91, 0},
  {0, 268UL, 88, 0},
  {659, 268UL, 85, 0},
  {659, 268UL, 91, 0},
  {659, 268UL, 85, 0},
  {740, 268UL, 91, 0},
  {659, 268UL, 85, 0},
  {740, 268UL, 91, 0},
  {1109, 268UL, 85, 0},
  {988, 268UL, 98, 0},
  {988, 536UL, 85, 0},
  {740, 268UL, 85, 0},
  {988, 1072UL, 91, 0},
  {0, 268UL, 88, 0},
  {988, 268UL, 85, 0},
  {988, 268UL, 91, 0},
  {988, 268UL, 85, 0},
  {988, 268UL, 91, 0},
  {880, 268UL, 85, 0},
  {988, 268UL, 91, 0},
  {1319, 536UL, 85, 0},
  {1109, 1876UL, 85, 0},
  {0, 268UL, 88, 0},
  {659, 268UL, 85, 0},
  {659, 268UL, 91, 0},
  {659, 268UL, 85, 0},
  {740, 268UL, 91, 0},
  {659, 268UL, 85, 0},
  {740, 268UL, 91, 0},
  {1109, 268UL, 85, 0},
  {988, 268UL, 98, 0},
  {988, 536UL, 85, 0},
  {740, 268UL, 85, 0},
  {988, 1072UL, 91, 0},
  {0, 268UL, 88, 0},
  {988, 268UL, 85, 0},
  {988, 268UL, 91, 0},
  {988, 268UL, 85, 0},
  {988, 268UL, 91, 0},
  {880, 268UL, 85, 0},
  {740, 268UL, 91, 0},
  {659, 268UL, 85, 0},
  {740, 804UL, 98, 0},
  {554, 268UL, 85, 0},
  {659, 1072UL, 91, 0},
  {0, 268UL, 88, 0},
  {659, 268UL, 85, 0},
  {659, 268UL, 91, 0},
  {659, 268UL, 85, 0},
  {740, 268UL, 91, 0},
  {659, 268UL, 85, 0},
  {740, 268UL, 91, 0},
  {1109, 268UL, 85, 0},
  {988, 268UL, 98, 0},
  {988, 536UL, 85, 0},
  {740, 268UL, 85, 0},
  {988, 1072UL, 91, 0},
  {0, 268UL, 88, 0},
  {988, 268UL, 85, 0},
  {988, 268UL, 91, 0},
  {988, 268UL, 85, 0},
  {988, 268UL, 91, 0},
  {880, 268UL, 85, 0},
  {740, 268UL, 91, 0},
  {880, 804UL, 85, 0},
  {740, 1608UL, 91, 0},
  {0, 254UL, 88, 0},
  {659, 267UL, 85, 0},
  {659, 268UL, 91, 0},
  {659, 268UL, 85, 0},
  {740, 268UL, 91, 0},
  {659, 267UL, 85, 0},
  {740, 268UL, 91, 0},
  {1109, 268UL, 85, 0},
  {988, 268UL, 98, 0},
  {988, 535UL, 85, 0},
  {740, 268UL, 85, 0},
  {988, 1071UL, 91, 0},
  {0, 268UL, 88, 0},
  {988, 267UL, 85, 0},
  {988, 268UL, 91, 0},
  {988, 268UL, 85, 0},
  {988, 268UL, 91, 0},
  {880, 267UL, 85, 0},
  {740, 268UL, 91, 0},
  {659, 268UL, 85, 0},
  {740, 803UL, 98, 0},
  {554, 268UL, 85, 0},
  {659, 1071UL, 91, 0},
  {0, 268UL, 88, 0},
  {659, 267UL, 85, 0},
  {659, 268UL, 91, 0},
  {659, 268UL, 85, 0},
  {740, 268UL, 91, 0},
  {659, 267UL, 85, 0},
  {740, 268UL, 91, 0},
  {1109, 268UL, 85, 0},
  {988, 268UL, 98, 0},
  {988, 535UL, 85, 0},
  {740, 268UL, 85, 0},
  {988, 1071UL, 91, 0},
  {0, 268UL, 88, 0},
  {988, 267UL, 85, 0},
  {988, 268UL, 91, 0},
  {988, 268UL, 85, 0},
  {988, 268UL, 91, 0},
  {880, 267UL, 85, 0},
  {988, 268UL, 91, 0},
  {1319, 536UL, 85, 0},
  {1109, 1874UL, 85, 0},
  {0, 268UL, 88, 0},
  {659, 267UL, 85, 0},
  {659, 268UL, 91, 0},
  {659, 268UL, 85, 0},
  {740, 268UL, 91, 0},
  {659, 267UL, 85, 0},
  {740, 268UL, 91, 0},
  {1109, 268UL, 85, 0},
  {988, 268UL, 98, 0},
  {988, 535UL, 85, 0},
  {740, 268UL, 85, 0},
  {988, 1071UL, 91, 0},
  {0, 268UL, 88, 0},
  {988, 267UL, 85, 0},
  {988, 268UL, 91, 0},
  {988, 268UL, 85, 0},
  {988, 268UL, 91, 0},
  {880, 267UL, 85, 0},
  {740, 268UL, 91, 0},
  {659, 268UL, 85, 0},
  {740, 803UL, 98, 0},
  {554, 268UL, 85, 0},
  {659, 1071UL, 91, 0},
  {0, 268UL, 88, 0},
  {659, 267UL, 85, 0},
  {659, 268UL, 91, 0},
  {659, 268UL, 85, 0},
  {740, 268UL, 91, 0},
  {659, 267UL, 85, 0},
  {740, 268UL, 91, 0},
  {1109, 268UL, 85, 0},
  {988, 268UL, 98, 0},
  {988, 535UL, 85, 0},
  {740, 268UL, 85, 0},
  {988, 1071UL, 91, 0},
  {0, 268UL, 88, 0},
  {988, 267UL, 85, 0},
  {988, 268UL, 91, 0},
  {988, 268UL, 85, 0},
  {988, 268UL, 91, 0},
  {880, 267UL, 85, 0},
  {740, 268UL, 91, 0},
  {880, 803UL, 85, 0},
  {740, 2955UL, 91, 0},
  {0, 790UL, 88, 0},
  {0, 1876UL, 88, 0},
  {1480, 134UL, 85, 0},
  {1760, 134UL, 85, 1},
  {1480, 1608UL, 98, 1},
  {1109, 536UL, 91, 1},
  {1319, 4288UL, 98, 1},
  {0, 1876UL, 88, 0},
  {1480, 134UL, 85, 0},
  {1760, 134UL, 85, 1},
  {1480, 1608UL, 98, 1},
  {1109, 536UL, 91, 1},
  {988, 4288UL, 98, 1},
  {0, 8831UL, 88, 0},
};
const uint32_t MELODY_LENGTH = 614;
const int16_t waveTable[256] = {
  0, 804, 1608, 2410, 3212, 4011, 4808, 5602, 6393, 7179, 7962, 8739, 9512, 10278, 11039, 11793,
  12539, 13279, 14010, 14732, 15446, 16151, 16846, 17530, 18204, 18868, 19519, 20159, 20787, 21403, 22005, 22594,
  23170, 23731, 24279, 24811, 25329, 25832, 26319, 26790, 27245, 27683, 28105, 28510, 28898, 29268, 29621, 29956,
  30273, 30571, 30852, 31113, 31356, 31580, 31785, 31971, 32137, 32285, 32412, 32521, 32609, 32678, 32728, 32757,
  32767, 32757, 32728, 32678, 32609, 32521, 32412, 32285, 32137, 31971, 31785, 31580, 31356, 31113, 30852, 30571,
  30273, 29956, 29621, 29268, 28898, 28510, 28105, 27683, 27245, 26790, 26319, 25832, 25329, 24811, 24279, 23731,
  23170, 22594, 22005, 21403, 20787, 20159, 19519, 18868, 18204, 17530, 16846, 16151, 15446, 14732, 14010, 13279,
  12539, 11793, 11039, 10278, 9512, 8739, 7962, 7179, 6393, 5602, 4808, 4011, 3212, 2410, 1608, 804,
  0, -804, -1608, -2410, -3212, -4011, -4808, -5602, -6393, -7179, -7962, -8739, -9512, -10278, -11039, -11793,
  -12539, -13279, -14010, -14732, -15446, -16151, -16846, -17530, -18204, -18868, -19519, -20159, -20787, -21403, -22005, -22594,
  -23170, -23731, -24279, -24811, -25329, -25832, -26319, -26790, -27245, -27683, -28105, -28510, -28898, -29268, -29621, -29956,
  -30273, -30571, -30852, -31113, -31356, -31580, -31785, -31971, -32137, -32285, -32412, -32521, -32609, -32678, -32728, -32757,
  -32767, -32757, -32728, -32678, -32609, -32521, -32412, -32285, -32137, -31971, -31785, -31580, -31356, -31113, -30852, -30571,
  -30273, -29956, -29621, -29268, -28898, -28510, -28105, -27683, -27245, -26790, -26319, -25832, -25329, -24811, -24279, -23731,
  -23170, -22594, -22005, -21403, -20787, -20159, -19519, -18868, -18204, -17530, -16846, -16151, -15446, -14732, -14010, -13279,
  -12539, -11793, -11039, -10278, -9512, -8739, -7962, -7179, -6393, -5602, -4808, -4011, -3212, -2410, -1608, -804,
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
  envelopeLevelQ15 = 0;
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
  playerUi.begin();
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
  playerUi.start(deadline);
  for (uint32_t i = 0; i < MELODY_LENGTH; ++i) {
    const MelodyNote &note = melody[i];
    const bool continued = i && note.frequency_hz && melody[i-1].frequency_hz && (note.flags & 1);
    const bool joinNext = i+1 < MELODY_LENGTH && note.frequency_hz && melody[i+1].frequency_hz && (melody[i+1].flags & 1);
    const uint32_t gap = note.frequency_hz && !joinNext ? min(GAP_MS, note.duration_ms / 5) : 0;
    const uint32_t sounding = note.duration_ms - gap, start = deadline;
    deadline += note.duration_ms;
    const uint32_t initialGain = envelopeLevelQ15;
    if (!continued) silence();
    if (mode == 4 && note.frequency_hz && note.frequency_hz != previousFrequency)
      configureCarrier(note.frequency_hz, mode);
    phaseStep = uint32_t((uint64_t(note.frequency_hz) << 32) / SAMPLE_RATE);
    const uint32_t initialColor2 = harmonic2Q15, initialColor3 = harmonic3Q15;
    while (int32_t(playerUi.musicNow() - deadline) < 0) {
      const uint32_t uiMusicNow = playerUi.musicNow(); const int32_t uiRemaining = (note.frequency_hz && uint32_t(uiMusicNow - start) < sounding) ? int32_t(start + sounding - uiMusicNow) : int32_t(deadline - uiMusicNow); playerUi.poll(millis(), playerUi.paused() ? 20 : (uiRemaining > 0 ? uint32_t(uiRemaining) : 0));
      if (playerUi.paused()) { gainQ15 = 0; *compareRegister = 0; delay(1); continue; }
      if (int32_t(playerUi.musicNow() - deadline) >= 0) break;
      const uint32_t elapsed = playerUi.musicNow() - start;
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
        envelopeLevelQ15 = gain;
        gainQ15 = playerUi.gain(gain);
        if (mode == 4) *compareRegister = uint64_t(carrierTicks) * gainQ15 / 65534;
      } else silence();
      delay(1);
    }
    if (!joinNext) silence();
    previousFrequency = note.frequency_hz;
  }
  silence();
  playerUi.finish();
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
    playerUi.wait(100);
  }
}
