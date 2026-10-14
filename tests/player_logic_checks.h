#pragma once
#include "player_logic.h"

volatile uint32_t playerSelfTestPassed = 0, playerSelfTestFailures = 0;
void playerCheck(bool condition) {
  if (condition) ++playerSelfTestPassed;
  else ++playerSelfTestFailures;
}
void runPlayerLogicChecks() {
  using PlayerLogic::Controls;
  Controls tap;
  tap.percent = 10;
  tap.poll(true, false, 10); tap.poll(false, false, 15);
  tap.poll(true, false, 17); tap.poll(false, false, 21);
  tap.poll(true, false, 25); tap.poll(true, false, 49);
  playerCheck(tap.percent == 10);
  tap.poll(true, false, 50);
  playerCheck(tap.percent == 11);
  tap.poll(false, false, 60); tap.poll(false, false, 85);
  playerCheck(tap.percent == 11);

  Controls hold;
  hold.percent = 10;
  hold.poll(true, false, 100); hold.poll(true, false, 125);
  playerCheck(hold.percent == 11);
  hold.poll(true, false, 474);
  playerCheck(hold.percent == 11);
  hold.poll(true, false, 475);
  playerCheck(hold.percent == 12);
  hold.poll(true, false, 625); hold.poll(true, false, 775);
  playerCheck(hold.percent == 14);

  Controls mute;
  mute.percent = 1;
  mute.poll(false, true, 0); mute.poll(false, true, 25);
  playerCheck(mute.percent == 0);
  mute.poll(false, true, 45);
  playerCheck(mute.gain(32767) == 0);
  mute.poll(false, true, 400);
  playerCheck(mute.percent == 0);

  Controls both;
  both.percent = 10;
  both.poll(true, true, 100); both.poll(true, true, 125); both.poll(true, true, 1000);
  playerCheck(both.percent == 10);

  Controls wrap;
  wrap.percent = 10;
  wrap.poll(true, false, 0xFFFFFFE0U);
  wrap.poll(true, false, 0xFFFFFFF9U);
  playerCheck(wrap.percent == 11);
  wrap.poll(true, false, 342);
  playerCheck(wrap.percent == 11);
  wrap.poll(true, false, 343);
  playerCheck(wrap.percent == 12);
  playerCheck(PlayerLogic::elapsed(0x20, 0xFFFFFFF0, 1000) == 48);
  playerCheck(PlayerLogic::elapsed(0x20, 0xFFFFFFF0, 40) == 40);
  playerCheck(PlayerLogic::progress(500, 1000, 118) == 59);
  playerCheck(PlayerLogic::progress(1001, 1000, 118) == 118);
  playerCheck(PlayerLogic::progress(100, 0, 118) == 0);

  playerCheck(PlayerLogic::volumeGainQ15(0) == 0);
  playerCheck(PlayerLogic::volumeGainQ15(1) > 0);
  playerCheck(PlayerLogic::volumeGainQ15(10) == 65);
  playerCheck(PlayerLogic::volumeGainQ15(100) == 32767);
  playerCheck(PlayerLogic::volumeGainQ15(200) == 32767);
  bool monotonic = true;
  for (uint32_t p = 1; p <= 100; ++p)
    monotonic &= PlayerLogic::volumeGainQ15(p) > PlayerLogic::volumeGainQ15(p - 1);
  playerCheck(monotonic);
  Controls quiet;
  quiet.percent = 10;
  quiet.poll(false, false, 100);
  playerCheck(quiet.gain(32767) == 65);
  playerCheck(quiet.gain(32767) < 3276 / 40);
  quiet.poll(true, false, 110); quiet.poll(true, false, 135);
  const uint32_t beforeRamp = quiet.gain(32767);
  quiet.poll(true, false, 145);
  playerCheck(quiet.gain(32767) > beforeRamp && quiet.gain(32767) < 70);
  quiet.poll(true, false, 155);
  playerCheck(quiet.gain(32767) == 70);
  playerCheck(PlayerLogic::adjustedVolume(0, false) == 0);
  playerCheck(PlayerLogic::adjustedVolume(100, true) == 100);
  playerCheck(PlayerLogic::adjustedVolume(19, true) == 20);
  playerCheck(PlayerLogic::adjustedVolume(20, true) == 25);
  playerCheck(PlayerLogic::adjustedVolume(25, false) == 20);
  playerCheck(PlayerLogic::adjustedVolume(20, false) == 19);
  PlayerLogic::ToggleKey pause;
  playerCheck(!pause.poll(true, 100));
  playerCheck(!pause.poll(false, 110));
  playerCheck(!pause.poll(true, 120));
  playerCheck(!pause.poll(true, 144));
  playerCheck(pause.poll(true, 145));
  playerCheck(!pause.poll(true, 1000));
  playerCheck(!pause.poll(false, 1010));
  playerCheck(!pause.poll(false, 1035));
  playerCheck(!pause.poll(true, 1040));
  playerCheck(pause.poll(true, 1065));
  PlayerLogic::PlaybackClock clock;
  clock.start();
  playerCheck(clock.musicNow(100) == 100);
  clock.toggle(100);
  playerCheck(clock.paused && clock.musicNow(900) == 100);
  clock.toggle(1000);
  playerCheck(!clock.paused && clock.musicNow(1000) == 100);
  playerCheck(clock.musicNow(1300) == 400);
  clock.toggle(1400); clock.toggle(2000);
  playerCheck(clock.musicNow(2100) == 600);
  clock.start(); clock.toggle(0xFFFFFFF0); clock.toggle(0x20);
  playerCheck(clock.musicNow(0x30) == 0);
  clock.start();
  playerCheck(!clock.paused && clock.musicNow(500) == 500);
}
