# ⛏️ Solo Miner Optimizer

**Monitoring, error-aware auto-tuning and block-chance calculator for Bitaxe and NexusQX1 solo miners – in one app with a web interface.**

*🇩🇪 Deutsche Kurzbeschreibung weiter unten.*

> ⚠️ **Use at your own risk.** Changing frequency and voltage can damage hardware and may void your warranty. The software is provided without any warranty (see [LICENSE](LICENSE)).

---

## Why another tool?

Most Bitaxe tools do *one* thing: either a command-line benchmark for a single device, or a dashboard. Solo Miner Optimizer combines both – and its tuning looks at more than just hashrate and temperature:

| | Solo Miner Optimizer |
|---|---|
| **Error-aware tuning** | A test point only counts as stable if hashrate ≥ target **and** the error rate, hardware errors per minute and rejected shares stay within limits. A setting that looks efficient on paper but produces 17 % errors is rejected. |
| **Network-aware** | If Wi-Fi drops or the miner switches to its fallback pool during a measurement, the point is **repeated** instead of being blamed on the chip. |
| **Safe** | Hard limits, abort on ASIC/VR temperature or power limit, original values are backed up and **restored automatically** – even if the program is closed mid-run. |
| **Whole swarm** | All miners in one view, several devices can be tuned in parallel. |
| **Multi-ASIC** | Bitaxe (AxeOS) and NexusQX1 (4× BM1370) – for the Nexus the device-reported limits are used. |
| **Made for solo mining** | Block-chance calculator (per day/month/year, cost per 1 % chance), best-share tracking and alerts. |
| **GUI, no setup** | Windows .exe or plain Python (standard library only). Finds your miners automatically. |

## Features

- **Overview** – hashrate, power, J/TH, temperatures, HW errors/min, pool latency, best shares, uptime for every device; 24 h chart
- **Auto-tuning** – grid search over frequency × voltage with presets per device type, live progress, result table; apply any stable point with one click
- **Calculator** – probability of finding a block, electricity cost vs. expected value, per-device breakdown (uses live network difficulty and BTC price)
- **Pools** – compare solo pools by latency, set primary/fallback pool on several AxeOS devices at once
- **Alerts** – offline, temperature, low hashrate, slow network, fallback pool, new best share, block found; via browser, Telegram or ntfy
- **German & English**, currency selectable (EUR, USD, CHF, GBP, CAD, AUD, JPY)

## Quick start

### Option A – Windows .exe
1. Download the latest release and unzip it.
2. Run `SoloMinerOptimizer.exe`. Your browser opens at `http://127.0.0.1:8765`.
3. Click **Find miners** – done.

> Unsigned .exe files are sometimes flagged by antivirus software (false positive). The program starts no hidden processes and only talks to your miners and the services listed under *Privacy*. If in doubt, use option B – the source code is fully readable.

### Option B – Python
Requires Python 3.9+ (no extra packages).
```bash
python solo_optimizer.py
```
Or double-click `Start Solo Miner Optimizer.bat` on Windows.

### Build the .exe yourself
```bash
build.bat
```

## Tuning – how it works

1. Pick a device and a profile (*Efficiency*, *Balanced*, *Maximum hashrate* or custom).
2. For each voltage the program steps through the frequencies: set → warm up (default 2 min, min. 1 min) → measure (default 5 min). Temperatures and power are checked every 10 s in **both** phases.
3. A point is **unstable** if hashrate < x % of target, error rate > 3 %, HW errors/min > limit, too many rejected shares, temperature/power limit reached, or the device restarted. Higher frequencies at that voltage are then skipped; if even the lowest frequency is too hot, higher voltages are skipped too.
4. At the end the best point is applied: in *efficiency* mode the best J/TH, in *hashrate* mode the highest hashrate – ties go to the higher hashrate unless a point has clearly more HW errors.
5. You can apply any other stable point from the result table.

**Real-world example (two Bitaxe Gamma, same model):** one chip reached 1.52 TH/s at 14.3 J/TH, the other needed ≥ 1200 mV to run cleanly and ended at 18.8 J/TH – a 30 % difference ("silicon lottery"). Without the error check, the weaker chip would have been set to a point with 17 % errors.

## Supported devices

| Device | Firmware | Status |
|---|---|---|
| Bitaxe Gamma (BM1370) | AxeOS v2.15.x | ✅ tested |
| NexusQX1 (4× BM1370) | nexus.v1.0.4.x | ✅ tested |
| Bitaxe Supra / Ultra / Max (BM1368 / BM1366 / BM1397) | AxeOS | ⚠️ should work, untested |
| NerdQaxe, Hex and others with AxeOS-compatible API | – | ❓ untested – feedback welcome |

## Privacy & security

- Runs **locally** on `127.0.0.1`; no account, no telemetry.
- Outgoing connections: your miners (LAN), `mempool.space` (BTC price), and – only if you configure them – Telegram / ntfy.
- Settings are stored in `%APPDATA%\SoloMinerOptimizer\config.json` (a damaged file is kept as a backup, never silently overwritten).
- The local web server rejects requests from other websites (Host/Origin checks against DNS rebinding and CSRF, JSON-only API, no framing).
- Data reported by miners is treated as untrusted and type-checked before it is shown or used.
- Network scans are limited to private networks up to /22; tuning safety limits (max. 75 °C ASIC, 95 °C VR) cannot be raised through the API.

## Tests

```bash
python -m unittest discover -s tests -v
```
The suite uses a **simulated miner** and a fast-forward clock – a full tuning run takes seconds and no real device is touched. It covers tuning end-to-end (error-heavy points rejected, best point applied, abort/crash recovery), network dropouts, devices that need a restart, malicious device data and the HTTP security checks.

## Known limitations

- Setting pools via the app uses the classic Stratum V1 fields and has not been verified on every AxeOS version – check the result in the device's own web UI.
- Desktop notifications only appear while the browser tab is open (use Telegram/ntfy for phone alerts).
- Hardware error counters differ between firmwares; the NexusQX1 only reports duplicate nonces, so its tuning relies mainly on the hashrate check.

## Roadmap

- **Swarm power budget** – distribute a watt or cost budget across all miners based on each chip's measured efficiency curve (watt cap, monthly budget, day/night tariff, dynamic tariffs, quiet/heating mode, optional solar surplus)
- Faster adaptive tuning (search for the lowest stable voltage per frequency)
- Docker image and Umbrel/Start9 app
- Long-term history and best-share leaderboard

## ☕ Support the project

Solo Miner Optimizer is free and open source – and it will stay that way.
I build it in my spare time, test every feature on my own swarm and pay for the electricity myself.

If the tool saved you time, a few watts or a fried chip, a small donation helps keep it going:

**Bitcoin:** `bc1qmqhcqyz906fw8djwmt68pe09ynfwjpauy5j8en`

Every sat goes into new features (swarm power budget, Docker/Umbrel app) and into testing on more devices.
Not in a position to donate? A ⭐ on GitHub, a bug report or a share in the Bitaxe community helps just as much.

*Thank you – and good luck finding that block!* 🍀

> 🇩🇪 **Unterstützen:** Das Programm ist kostenlos und bleibt Open Source. Wenn es dir Zeit, Strom oder einen Chip gespart hat, freue ich mich über eine kleine Spende an die Adresse oben. Jeder Sat fließt in neue Funktionen und Tests mit weiteren Geräten. Ein ⭐ auf GitHub, ein Fehlerbericht oder eine Empfehlung in der Community helfen genauso. Danke – und viel Glück beim Block! 🍀

---

## 🇩🇪 Kurzbeschreibung

Der **Solo Miner Optimizer** überwacht deine Bitaxe- und NexusQX1-Miner, findet per **Auto-Tuning** die beste Kombination aus Frequenz und Spannung und berechnet deine **Chance auf einen Block**.

Das Besondere: Das Tuning prüft nicht nur Hashrate und Temperatur, sondern auch **Fehlerrate, Hardware-Fehler pro Minute und abgelehnte Shares**. Netzwerkaussetzer werden erkannt, und die ursprünglichen Werte werden bei Abbruch automatisch wiederhergestellt.

**Start:** `SoloMinerOptimizer.exe` ausführen (oder `python solo_optimizer.py`), im Browser auf **„Miner suchen“** klicken – fertig. Die Oberfläche gibt es auf Deutsch und Englisch.

**Benutzung auf eigene Gefahr** – das Ändern von Frequenz und Spannung kann Hardware beschädigen. Open Source unter MIT-Lizenz, ohne Gewähr.

## License

[MIT](LICENSE) © 2026 Grischa-Miner
