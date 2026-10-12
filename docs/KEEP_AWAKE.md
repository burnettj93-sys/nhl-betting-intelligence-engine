# Keeping the engine's Mac awake (2026-10-12)

**What the engine does.** Every 15-minute trader cycle calls `operational/keep_awake.py`. When the next window opens within 25 minutes (or is open) it starts one detached `caffeinate -i -s` for the rest of that window.
Windows (Eastern clock): **7:50–8:35 AM** for the morning look, and for every wave of games (puck drops within 90 minutes of the first) from **125 minutes before the wave's first puck drop** (the pregame capture opens 105
minutes before; the hold starts 20 earlier) **to that first puck drop**, which also covers the T-35 moneyline pull and the recording cycles. Overlapping windows are merged. It spends no credits and changes no setting.

**What it cannot do (stated, not hidden).**

1. A **closed lid still sleeps** a MacBook unless it is on power with an external display attached.
2. A Mac that is **already asleep cannot start the hold**: the hold begins at the trader cycle before a window, so the Mac must be awake then. Waking a sleeping Mac needs an administrator command that I cannot run.
3. It prevents idle sleep only; choosing Sleep or low-battery sleep still sleeps.

**Smallest owner steps that make the windows reliable** (pick what suits you; both need your Mac password, so they are yours to run):

* Keep the Mac **plugged in with the lid open** from about 7:50 AM until the last game of the evening starts. The display may sleep; the Mac will not.
* Optional, so a sleeping Mac wakes for the morning look every day: `sudo pmset repeat wakeorpoweron MTWRFSU 07:50:00`. (Undo with `sudo pmset repeat cancel`.) Evening wave times change daily, so a fixed wake is not set for them;
  the hold plus a Mac that is awake on power covers them.
* Optional, so the Mac never idle-sleeps on power: `sudo pmset -c sleep 0`.

**How to check.** `cat operational/runtime/keep_awake.json` shows the running hold (process, until when, and which window); `pgrep caffeinate` shows the process.
