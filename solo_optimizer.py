"""
Solo Miner Optimizer
Monitoring, error-aware auto-tuning, block-probability calculator, pool management and alerts
for Bitaxe (AxeOS) and NexusQX1 miners on the local network.

Starts a local web server on http://127.0.0.1:8765 and opens the browser.
Python standard library only.

License: MIT (see LICENSE). Use at your own risk – changing frequency/voltage can damage hardware.
"""
import base64
import concurrent.futures
import functools
import ipaddress
import itertools
import json
import locale
import os
import socket
import sys
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

APP_NAME = "SoloMinerOptimizer"
VERSION = "1.1.0"
PORT = int(os.environ.get("SMO_PORT", "8765"))
CURRENCIES = ["EUR", "USD", "CHF", "GBP", "CAD", "AUD", "JPY"]

DEFAULT_CONFIG = {
    "devices": [],
    "language": "auto",        # auto | de | en
    "currency": "auto",        # auto | EUR | USD | CHF | ...
    "poll_seconds": 15,
    "electricity_eur_kwh": 0.30,   # price per kWh in the selected currency (key name kept for compatibility)
    "alarms": {
        "temp_max": 68.0,
        "vr_temp_max": 85.0,
        "hashrate_min_pct": 80.0,
        "pool_latency_max_ms": 2000,
        "offline_after_polls": 3,
        "notify_best_diff": True,
        "cooldown_min": 15,
        "windows_notify": False,  # deprecated, unused
        "telegram_token": "",
        "telegram_chat_id": "",
        "ntfy_url": "",
    },
    "known_best": {},
    "tuning_results": {},
    "pools": [
        {"name": "public-pool.io", "host": "public-pool.io", "port": 21496},
        {"name": "solo.ckpool.org (EU)", "host": "eusolo.ckpool.org", "port": 3333},
        {"name": "solo.ckpool.org", "host": "solo.ckpool.org", "port": 3333},
        {"name": "Blitzpool", "host": "blitzpool.yourdevice.ch", "port": 3333},
    ],
}

# --------------------------------------------------------------------------- i18n
MSG = {
    "de": {
        "alarm_offline_t": "Miner offline", "alarm_offline": "{name} ({ip}) antwortet nicht mehr.",
        "alarm_online_t": "Miner wieder online", "alarm_online": "{name} ({ip}) ist wieder erreichbar.",
        "alarm_temp_t": "Temperatur hoch", "alarm_temp": "{name}: ASIC {v:.1f} °C (Grenze {lim} °C)",
        "alarm_vr_t": "VR-Temperatur hoch", "alarm_vr": "{name}: VR {v:.1f} °C (Grenze {lim} °C)",
        "alarm_hr_t": "Hashrate niedrig", "alarm_hr": "{name}: {v:.0f} GH/s statt erwartet {exp:.0f} GH/s",
        "alarm_lat_t": "Netzwerk langsam",
        "alarm_lat": "{name}: Pool-Antwortzeit {v:.0f} ms (Grenze {lim} ms) – WLAN/Router oder IP-Konflikt prüfen",
        "alarm_fb_t": "Fallback-Pool aktiv", "alarm_fb": "{name} mined auf dem Fallback-Pool.",
        "alarm_block_t": "BLOCK GEFUNDEN!", "alarm_block": "{name} hat einen Block gefunden!",
        "alarm_best_t": "Neuer Best Share", "alarm_best": "{name}: {v} (vorher {prev})",
        "alarm_test_t": "Test-Alarm", "alarm_test": "Benachrichtigungen funktionieren.",
        "alarm_tuned_t": "Auto-Tuning fertig", "alarm_tuned": "{name}: {f} MHz / {v} mV, {hr:.0f} GH/s, {eff} J/TH",
        "alarm_restored_t": "Tuning-Werte zurückgesetzt",
        "alarm_restored": "{ip}: Ein Tuning wurde unterbrochen. Originalwerte {f} MHz / {v} mV wiederhergestellt.",
        "lim_freq": "Frequenz {f} MHz außerhalb {a}–{b} MHz",
        "lim_volt": "Spannung {v} mV außerhalb {a}–{b} mV",
        "err_running": "Tuning läuft bereits für dieses Gerät",
        "err_offline": "Gerät ist offline",
        "err_untunable": "Auto-Tuning wird für dieses Gerät nicht unterstützt",
        "err_minmax": "Min-Werte müssen kleiner als Max-Werte sein",
        "err_busy": "Auf diesem Gerät läuft gerade ein Tuning",
        "err_scan_running": "Scan läuft bereits",
        "err_restoring": "Für dieses Gerät werden gerade die Originalwerte wiederhergestellt – bitte kurz warten",
        "err_too_many": "Zu viele Messpunkte ({n}) – höchstens 200",
        "err_cidr": "Ungültiges Netzwerk. Erlaubt sind private Netze bis /22, z. B. 192.168.1.0/24",
        "err_unknown_dev": "Unbekanntes Gerät",
        "err_original": "Das Gerät meldet keine gültige Frequenz/Spannung – Tuning nicht möglich",
        "err_ip": "Ungültige IP-Adresse (Beispiel: 192.168.1.50)",
        "err_ntfy": "Die ntfy-URL muss mit http:// oder https:// beginnen",
        "err_not_applied": "Gerät hat {f} MHz / {v} mV nicht übernommen",
        "err_apply_tries": "{f} MHz / {v} mV nach {n} Versuchen nicht gesetzt: {e}",
        "err_no_return": "Gerät kommt nach Neustart nicht zurück",
        "err_network": "Netzwerk zu instabil für eine verlässliche Messung ({why})",
        "ph_start": "Start", "ph_set": "Setze {f} MHz / {v} mV", "ph_retry": " (Wiederholung {n})",
        "ph_warm": "Aufwärmen {f} MHz / {v} mV", "ph_measure": "Messen {f} MHz / {v} mV",
        "ph_done": "Fertig", "ph_aborted": "Abgebrochen", "ph_error": "Fehler",
        "rs_reboot": "Gerät hat während der Messung neu gestartet",
        "rs_temp": "ASIC {v:.1f} °C > {lim} °C", "rs_vr": "VR {v:.1f} °C > {lim} °C",
        "rs_power": "Leistung {v:.1f} W nahe Limit {lim} W",
        "rs_net_fail": "{a} von {b} Abfragen ohne Antwort",
        "rs_net_lat": "Fallback-Pool oder hohe Pool-Latenz während der Messung",
        "rs_net_few": "zu wenige Messwerte",
        "rs_hr": "Hashrate nur {v:.0f}% vom Soll", "rs_err": "Fehlerrate {v}%",
        "rs_hw": "{v:.0f} HW-Fehler/min (Grenze {lim:.0f})", "rs_rej": "{v} abgelehnte Shares",
        "msg_net_retry": "Netzwerkproblem bei {f} MHz / {v} mV – warte und wiederhole den Punkt",
        "msg_best": "Bestes Ergebnis übernommen: {f} MHz / {v} mV",
        "msg_restored": "Originalwerte wiederhergestellt ({f} MHz / {v} mV)",
        "msg_apply_later": "Konnte Einstellungen noch nicht setzen ({e}). Die Originalwerte werden automatisch weiter "
                           "versucht, sobald das Gerät antwortet.",
        "pool_only_axeos": "Nur für AxeOS-Geräte – andere Geräte bitte über deren Weboberfläche einstellen",
        "pool_set": "gesetzt ({user}), Neustart ausgelöst",
    },
    "en": {
        "alarm_offline_t": "Miner offline", "alarm_offline": "{name} ({ip}) is not responding.",
        "alarm_online_t": "Miner back online", "alarm_online": "{name} ({ip}) is reachable again.",
        "alarm_temp_t": "High temperature", "alarm_temp": "{name}: ASIC {v:.1f} °C (limit {lim} °C)",
        "alarm_vr_t": "High VR temperature", "alarm_vr": "{name}: VR {v:.1f} °C (limit {lim} °C)",
        "alarm_hr_t": "Low hashrate", "alarm_hr": "{name}: {v:.0f} GH/s instead of expected {exp:.0f} GH/s",
        "alarm_lat_t": "Slow network",
        "alarm_lat": "{name}: pool response time {v:.0f} ms (limit {lim} ms) – check Wi-Fi/router or IP conflict",
        "alarm_fb_t": "Fallback pool active", "alarm_fb": "{name} is mining on the fallback pool.",
        "alarm_block_t": "BLOCK FOUND!", "alarm_block": "{name} found a block!",
        "alarm_best_t": "New best share", "alarm_best": "{name}: {v} (previous {prev})",
        "alarm_test_t": "Test alert", "alarm_test": "Notifications are working.",
        "alarm_tuned_t": "Auto-tuning finished", "alarm_tuned": "{name}: {f} MHz / {v} mV, {hr:.0f} GH/s, {eff} J/TH",
        "alarm_restored_t": "Tuning values restored",
        "alarm_restored": "{ip}: A tuning run was interrupted. Original values {f} MHz / {v} mV restored.",
        "lim_freq": "Frequency {f} MHz outside {a}–{b} MHz",
        "lim_volt": "Voltage {v} mV outside {a}–{b} mV",
        "err_running": "Tuning is already running for this device",
        "err_offline": "Device is offline",
        "err_untunable": "Auto-tuning is not supported for this device",
        "err_minmax": "Min values must be lower than max values",
        "err_busy": "A tuning run is active on this device",
        "err_scan_running": "Scan already running",
        "err_restoring": "Original values are being restored for this device right now – please wait a moment",
        "err_too_many": "Too many test points ({n}) – maximum is 200",
        "err_cidr": "Invalid network. Private networks up to /22 are allowed, e.g. 192.168.1.0/24",
        "err_unknown_dev": "Unknown device",
        "err_original": "The device reports no valid frequency/voltage – tuning not possible",
        "err_ip": "Invalid IP address (example: 192.168.1.50)",
        "err_ntfy": "The ntfy URL must start with http:// or https://",
        "err_not_applied": "Device did not accept {f} MHz / {v} mV",
        "err_apply_tries": "{f} MHz / {v} mV not set after {n} attempts: {e}",
        "err_no_return": "Device did not come back after restart",
        "err_network": "Network too unstable for a reliable measurement ({why})",
        "ph_start": "Starting", "ph_set": "Setting {f} MHz / {v} mV", "ph_retry": " (retry {n})",
        "ph_warm": "Warming up {f} MHz / {v} mV", "ph_measure": "Measuring {f} MHz / {v} mV",
        "ph_done": "Finished", "ph_aborted": "Aborted", "ph_error": "Error",
        "rs_reboot": "Device restarted during measurement",
        "rs_temp": "ASIC {v:.1f} °C > {lim} °C", "rs_vr": "VR {v:.1f} °C > {lim} °C",
        "rs_power": "Power {v:.1f} W near limit {lim} W",
        "rs_net_fail": "{a} of {b} requests unanswered",
        "rs_net_lat": "Fallback pool or high pool latency during measurement",
        "rs_net_few": "too few samples",
        "rs_hr": "Hashrate only {v:.0f}% of expected", "rs_err": "Error rate {v}%",
        "rs_hw": "{v:.0f} HW errors/min (limit {lim:.0f})", "rs_rej": "{v} rejected shares",
        "msg_net_retry": "Network problem at {f} MHz / {v} mV – waiting and repeating the point",
        "msg_best": "Best result applied: {f} MHz / {v} mV",
        "msg_restored": "Original values restored ({f} MHz / {v} mV)",
        "msg_apply_later": "Could not apply settings yet ({e}). The original values will be retried automatically "
                           "as soon as the device responds.",
        "pool_only_axeos": "AxeOS devices only – please configure other devices in their own web interface",
        "pool_set": "set ({user}), restart triggered",
    },
}


@functools.lru_cache(maxsize=1)
def system_language():
    """Windows UI language via ctypes (no process spawning), else locale."""
    try:
        if os.name == "nt":
            import ctypes
            langid = ctypes.windll.kernel32.GetUserDefaultUILanguage()
            return "de" if (langid & 0x3FF) == 0x07 else "en"
    except Exception:  # noqa
        pass
    try:
        loc = (locale.getlocale()[0] or "").lower()
        return "de" if loc.startswith(("de", "german")) else "en"
    except Exception:  # noqa
        return "en"


def lang():
    v = CFG.data.get("language", "auto")
    return v if v in MSG else system_language()


def T(key, **kw):
    s = MSG[lang()].get(key) or MSG["en"].get(key, key)
    try:
        return s.format(**kw)
    except Exception:  # noqa
        return s


def currency():
    v = CFG.data.get("currency", "auto")
    if v in CURRENCIES:
        return v
    return "EUR" if lang() == "de" else "USD"


# --------------------------------------------------------------------------- utils
def num(v, default=None):
    """Device/config data is untrusted: accept only finite numbers (also numeric strings)."""
    if isinstance(v, bool):
        return float(v)
    try:
        f = float(v)
    except (TypeError, ValueError, OverflowError):
        return default
    return f if f == f and abs(f) != float("inf") else default


def txt(v, maxlen=120):
    """Device/config data is untrusted: plain, length-limited strings only."""
    if v is None:
        return ""
    s = str(v) if isinstance(v, (str, int, float)) else ""
    return "".join(ch for ch in s if ch.isprintable())[:maxlen]


def valid_device_addr(s):
    """A device address is an IPv4 address, optionally with :port – never a hostname/URL."""
    host, _, port = s.rpartition(":") if ":" in s else (s, "", "")
    try:
        ipaddress.IPv4Address(host)
    except ValueError:
        return False
    return not port or (port.isdigit() and 0 < int(port) < 65536)


def app_dir():
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    d = os.environ.get("SMO_CONFIG_DIR") or os.path.join(base, APP_NAME)
    os.makedirs(d, exist_ok=True)
    return d


def resource_path(name):
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


def now():
    return time.time()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Never follow redirects – a (compromised) device must not be able to send us elsewhere."""

    def redirect_request(self, *a, **kw):
        return None


# LAN requests to miners: never through a system proxy (would break home-network access on proxied PCs)
LAN_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect)
# internet requests (price, Telegram, ntfy): system proxy allowed
NET_OPENER = urllib.request.build_opener(_NoRedirect)
MAX_RESPONSE = 2 * 1024 * 1024


def http_json(method, url, body=None, timeout=4.0, lan=True):
    data = None
    headers = {"Accept": "application/json", "User-Agent": f"SoloMinerOptimizer/{VERSION}"}
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    with (LAN_OPENER if lan else NET_OPENER).open(req, timeout=timeout) as r:
        raw = r.read(MAX_RESPONSE + 1)
        if len(raw) > MAX_RESPONSE:
            raise ValueError("response too large")
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except ValueError:
            return {"raw": raw[:200].decode(errors="replace")}


def finite(obj):
    """Replace NaN/Infinity (invalid in JSON, would break the web page) by None – recursively."""
    if isinstance(obj, float):
        return obj if obj == obj and abs(obj) != float("inf") else None
    if isinstance(obj, dict):
        return {k: finite(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [finite(v) for v in obj]
    return obj


def jdump(obj):
    return json.dumps(finite(obj), allow_nan=False)


def parse_diff(v):
    """bestDiff can be a number or a string like '15.73G'. Always returns a finite float >= 0."""
    if v is None or isinstance(v, bool):
        return 0.0
    if isinstance(v, (int, float)):
        f = num(v, 0.0)
        return f if f > 0 else 0.0
    s = str(v).strip().upper()[:40]
    mult = {"K": 1e3, "M": 1e6, "G": 1e9, "T": 1e12, "P": 1e15, "E": 1e18}
    if s and s[-1] in mult:
        f = num(s[:-1])
        f = f * mult[s[-1]] if f is not None else None
    else:
        f = num(s)
    f = num(f, 0.0)  # catches overflow to inf
    return f if f > 0 else 0.0


def fmt_diff(v):
    for unit, m in (("T", 1e12), ("G", 1e9), ("M", 1e6), ("K", 1e3)):
        if v >= m:
            return f"{v / m:.2f} {unit}"
    return f"{v:.0f}"


def local_subnet():
    """Guess the LAN /24 of this PC (UDP connect sends no packets)."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("10.255.255.255", 1))
            ip = s.getsockname()[0]
        finally:
            s.close()
        if ip and not ip.startswith("127."):
            return str(ipaddress.ip_network(f"{ip}/24", strict=False))
    except Exception:  # noqa
        pass
    return "192.168.1.0/24"


# --------------------------------------------------------------------------- config
class Config:
    def __init__(self):
        self.path = os.path.join(app_dir(), "config.json")
        self.lock = threading.RLock()
        self.data = json.loads(json.dumps(DEFAULT_CONFIG))
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    stored = json.load(f)
                if not isinstance(stored, dict):
                    raise ValueError("config is not a JSON object")
                self._merge(self.data, stored)
            except Exception as e:  # noqa
                # never silently destroy a damaged config (it holds tuning results) – keep a copy
                self.data = json.loads(json.dumps(DEFAULT_CONFIG))
                backup = self.path + f".broken-{int(time.time())}"
                try:
                    os.replace(self.path, backup)
                    print(f"Could not read config ({e}). Damaged file kept as {backup}")
                except OSError:
                    print("Could not read config:", e)
        self._validate()  # wrong types/values are repaired field by field, the rest is kept
        self.save()

    def _validate(self):
        """A hand-edited config must not crash the program or make it contact arbitrary hosts."""
        d, defaults = self.data, DEFAULT_CONFIG
        d["devices"] = [x for x in d.get("devices", []) if isinstance(x, str) and valid_device_addr(x)] \
            if isinstance(d.get("devices"), list) else []
        if d.get("language") not in ("auto", "de", "en"):
            d["language"] = "auto"
        if d.get("currency") != "auto" and d.get("currency") not in CURRENCIES:
            d["currency"] = "auto"
        for k, lo, hi in (("poll_seconds", 5, 3600), ("electricity_eur_kwh", 0, 1000)):
            v = num(d.get(k))
            d[k] = v if v is not None and lo <= v <= hi else defaults[k]
        d["poll_seconds"] = int(d["poll_seconds"])
        al = d["alarms"] if isinstance(d.get("alarms"), dict) else {}
        for k, dv in defaults["alarms"].items():
            if isinstance(dv, bool):
                al[k] = bool(al.get(k, dv))
            elif isinstance(dv, (int, float)):
                v = num(al.get(k))
                al[k] = v if v is not None and v >= 0 else dv
            else:
                al[k] = txt(al.get(k, dv), 300)
        d["alarms"] = al
        for k in ("known_best", "tuning_results", "tuning_pending"):
            if not isinstance(d.get(k), dict):
                d[k] = {}
        d["tuning_pending"] = {ip: v for ip, v in d["tuning_pending"].items()
                               if isinstance(v, dict) and num(v.get("frequency")) and num(v.get("coreVoltage"))}
        pools = d.get("pools") if isinstance(d.get("pools"), list) else []
        d["pools"] = [p for p in pools if isinstance(p, dict) and isinstance(p.get("host"), str)
                      and num(p.get("port")) and 0 < num(p.get("port")) < 65536]

    def _merge(self, base, new):
        for k, v in new.items():
            if isinstance(v, dict) and isinstance(base.get(k), dict) and k not in ("known_best", "tuning_results"):
                self._merge(base[k], v)
            else:
                base[k] = v

    def save(self):
        with self.lock:
            text = json.dumps(self.data, indent=2)  # serialise under the lock (no concurrent mutation)
            tmp = self.path + ".tmp"
            try:
                with open(tmp, "w", encoding="utf-8") as f:
                    f.write(text)
                os.replace(tmp, self.path)
            except OSError as e:
                print("Could not save config:", e)

    def get(self, k):
        with self.lock:
            return self.data[k]

    def snapshot(self):
        with self.lock:
            return json.loads(json.dumps(self.data))


CFG = Config()


# --------------------------------------------------------------------------- devices
def hw_errors(info):
    """Cumulative hardware error counter: AxeOS hashrateMonitor or Nexus duplicateHWNonces."""
    hm = info.get("hashrateMonitor")
    asics = hm.get("asics") if isinstance(hm, dict) else None
    if isinstance(asics, list) and asics:
        return int(sum(num(a.get("errorCount"), 0) for a in asics if isinstance(a, dict)))
    return int(num(info.get("duplicateHWNonces"), 0))


ASIC_CACHE = {}     # ip -> /api/system/asic ({} if the firmware has no such endpoint)
ASIC_CACHE_TS = {}  # ip -> time of last attempt


def fetch_asic(ip):
    try:
        r = http_json("GET", f"http://{ip}/api/system/asic", timeout=4)
        ASIC_CACHE[ip] = r if isinstance(r, dict) else {}
    except Exception:  # noqa
        ASIC_CACHE.setdefault(ip, {})  # remember the failure – older AxeOS has no /asic endpoint
    ASIC_CACHE_TS[ip] = now()
    return ASIC_CACHE.get(ip)


def device_limits(ip, is_nexus):
    """Allowed ranges. Nexus: absolute limits reported by the device. Bitaxe: fixed safety limits."""
    a = ASIC_CACHE.get(ip) if isinstance(ASIC_CACHE.get(ip), dict) else {}

    def nums(key):
        v = a.get(key)
        return [int(x) for x in (num(i) for i in v) if x] if isinstance(v, list) else []

    fo, vo = nums("frequencyOptions"), nums("voltageOptions")
    if is_nexus:
        # device-reported limits, but never beyond sane absolute caps for a 4-chip BM1370 chain
        max_f = int(num(a.get("absMaxFrequency")) or (max(fo) if fo else 1000))
        max_v = int(num(a.get("absMaxVoltage")) or (max(vo) if vo else 2650))
        return {
            "minFreq": min(fo) if fo else 700, "maxFreq": min(max_f, 1100),
            "minV": min(vo) if vo else 2300, "maxV": min(max_v, 2800),
            "defaultFreq": int(num(a.get("defaultFrequency"), 1000)), "defaultV": int(num(a.get("defaultVoltage"), 2550)),
            "source": "device",
        }
    return {
        "minFreq": 400, "maxFreq": 800, "minV": 1000, "maxV": 1300,
        "defaultFreq": int(num(a.get("defaultFrequency"), 525)), "defaultV": int(num(a.get("defaultVoltage"), 1150)),
        "source": "app",
    }


def normalize(ip, info):
    if not isinstance(info, dict):
        raise ValueError("unexpected response")
    is_nexus = "Nexus" in txt(info.get("deviceModel"))
    hr = num(info.get("hashRate_10m")) or num(info.get("hashRate"), 0.0)
    hr1m = num(info.get("hashRate_1m")) or num(info.get("hashRate"), 0.0)
    asics = int(num(info.get("asicCount"), 1) or 1)
    freq = num(info.get("frequency"), 0.0)
    cores = num(info.get("smallCoreCount"), 0.0)
    expected = num(info.get("expectedHashrate")) or (freq * cores * asics / 1000.0 if freq and cores else 0.0)
    power = num(info.get("power"), 0.0)
    uptime = num(info.get("uptimeSeconds"), 0.0)
    if is_nexus:
        st = info.get("stratum") if isinstance(info.get("stratum"), dict) else {}
        using_fb = bool(st.get("usingFallback"))
        blocks = num(info.get("totalFoundBlocks")) or num(info.get("foundBlocks"), 0.0)
    else:
        using_fb = bool(info.get("isUsingFallbackStratum"))
        blocks = num(info.get("blockFound"), 0.0)
    lat = num(info.get("lastpingrtt") if is_nexus else info.get("responseTime"))
    asic_model = txt(info.get("ASICModel"), 20) or "?"
    hw = hw_errors(info)

    def rnd(v, n):
        return round(v, n) if v is not None else None

    return {
        "ip": ip,
        "online": True,
        "kind": "nexus" if is_nexus else "axeos",
        "model": txt(info.get("deviceModel"), 40) or txt((ASIC_CACHE.get(ip) or {}).get("deviceModel"), 40)
        or ("Gamma" if asic_model == "BM1370" else asic_model),
        "asic": asic_model,
        "asicCount": asics,
        "hostname": txt(info.get("hostname"), 64) or ip,
        "version": txt(info.get("version"), 40) or "?",
        "hashrate": round(hr, 1),
        "hashrate1m": round(hr1m, 1),
        "expected": round(expected, 1),
        "power": round(power, 2),
        "efficiency": round(power / (hr / 1000.0), 2) if hr > 0 else None,
        "temp": rnd(num(info.get("temp")), 2),
        "vrTemp": rnd(num(info.get("vrTemp")), 2),
        "frequency": int(freq) if freq == int(freq) else round(freq, 2),
        "coreVoltage": int(num(info.get("coreVoltage"), 0)),
        "coreVoltageActual": num(info.get("coreVoltageActual")),
        "fanspeed": rnd(num(info.get("fanspeed")), 1),
        "fanrpm": num(info.get("fanrpm")),
        "bestDiff": parse_diff(info.get("bestDiff")),
        "bestSessionDiff": parse_diff(info.get("bestSessionDiff")),
        "sharesAccepted": int(num(info.get("sharesAccepted"), 0)),
        "sharesRejected": int(num(info.get("sharesRejected"), 0)),
        "errorPercentage": num(info.get("errorPercentage")),
        "uptime": uptime,
        "poolURL": txt(info.get("stratumURL"), 120),
        "poolPort": int(num(info.get("stratumPort"), 0)),
        "poolUser": txt(info.get("stratumUser"), 140),
        "poolDifficulty": num(info.get("poolDifficulty")),
        "usingFallback": using_fb,
        "networkDifficulty": num(info.get("networkDifficulty")),
        "rewardSats": num(info.get("coinbaseValueTotalSatoshis")),
        "blocksFound": int(blocks or 0),
        "maxPower": num(info.get("maxPower")),
        "hwErrors": hw,
        "hwErrPerMin": round(hw / uptime * 60, 1) if uptime else None,
        "poolLatency": round(lat) if lat is not None else None,
        "tunable": asic_model in ("BM1370", "BM1368", "BM1366", "BM1397"),
        "smallCoreCount": cores,
        "restartRequired": bool(info.get("restartRequired")),
        "limits": device_limits(ip, is_nexus),
        "lastSeen": now(),
    }


class State:
    def __init__(self):
        self.lock = threading.RLock()
        self.devices = {}       # ip -> normalized dict
        self.fails = {}         # ip -> consecutive failures
        self.history = []       # [{t, hashrate, power}]
        self.alarms = []        # newest first
        self.alarm_last = {}    # key -> ts
        self.network = {"difficulty": None, "rewardSats": 312_500_000, "prices": {}, "priceTs": 0}
        self.scan = {"running": False, "found": [], "progress": 0}
        self.subnet = local_subnet()
        self.blocks_notified = {}  # ip -> block count already alerted
        self.restoring = set()     # ips whose original values are being restored right now


S = State()


def fetch_info(ip, timeout=8.0):
    # /asic is fetched once; if it was missing/failed, retry at most every 10 minutes (not on every poll)
    if ip not in ASIC_CACHE or (not ASIC_CACHE[ip] and now() - ASIC_CACHE_TS.get(ip, 0) > 600):
        fetch_asic(ip)
    return http_json("GET", f"http://{ip}/api/system/info", timeout=timeout)


def poll_once():
    ips = list(CFG.get("devices"))
    results = {}
    if ips:
        with concurrent.futures.ThreadPoolExecutor(max_workers=16) as ex:
            futs = {ex.submit(fetch_info, ip): ip for ip in ips}
            for f in concurrent.futures.as_completed(futs):
                ip = futs[f]
                try:
                    results[ip] = normalize(ip, f.result())
                except Exception:  # noqa
                    results[ip] = None
    with S.lock:
        for ip in list(S.devices):
            if ip not in ips:
                S.devices.pop(ip, None)
        for ip, d in results.items():
            if d is None:
                S.fails[ip] = S.fails.get(ip, 0) + 1
                prev = dict(S.devices.get(ip) or {"ip": ip, "hostname": ip, "kind": "?", "model": "?"})
                if S.fails[ip] >= 2 or "hashrate" not in prev:
                    prev["online"] = False  # ignore single lost polls
                S.devices[ip] = prev
            else:
                S.fails[ip] = 0
                S.devices[ip] = d
                if d.get("networkDifficulty"):
                    S.network["difficulty"] = d["networkDifficulty"]
                if d.get("rewardSats"):
                    S.network["rewardSats"] = d["rewardSats"]
        online = [d for d in S.devices.values() if d.get("online")]
        if ips:
            S.history.append({
                "t": int(now()),
                "hashrate": round(sum(d.get("hashrate") or 0 for d in online), 1),
                "power": round(sum(d.get("power") or 0 for d in online), 1),
            })
        cutoff = now() - 24 * 3600
        S.history = [h for h in S.history if h["t"] >= cutoff]
    check_alarms(results)


def poll_loop():
    while True:
        t0 = now()
        try:
            poll_once()
        except Exception as e:  # noqa
            print("Poll error:", e)
        time.sleep(max(3, CFG.get("poll_seconds") - (now() - t0)))


def price_loop():
    while True:
        wait = 600
        try:
            p = http_json("GET", "https://mempool.space/api/v1/prices", timeout=10, lan=False)
            if not isinstance(p, dict):
                raise ValueError("unexpected price response")
            with S.lock:
                S.network["prices"] = {k: num(v) for k, v in p.items() if k in CURRENCIES and num(v)}
                S.network["priceTs"] = int(now())
        except Exception as e:  # noqa
            print("Price request failed:", e)
            wait = 60  # retry soon instead of leaving the calculator without a price for 10 minutes
        time.sleep(wait)


# --------------------------------------------------------------------------- alarms
def notify(title, msg, level="warn", key=None, force=False):
    a = CFG.get("alarms")
    with S.lock:
        if key and not force:
            last = S.alarm_last.get(key, 0)
            if now() - last < a["cooldown_min"] * 60:
                return
            S.alarm_last[key] = now()
        S.alarms.insert(0, {"t": int(now()), "title": title, "msg": msg, "level": level})
        del S.alarms[200:]
    print(f"[ALERT] {title}: {msg}")
    threading.Thread(target=_deliver, args=(title, msg, a), daemon=True).start()


def _deliver(title, msg, a):
    # Desktop notifications go through the browser (Notification API).
    # Deliberately NO hidden helper processes (PowerShell etc.) – antivirus tools flag that as malware behaviour.
    if a.get("telegram_token") and a.get("telegram_chat_id"):
        try:
            token = urllib.parse.quote(a["telegram_token"], safe=":")
            url = f"https://api.telegram.org/bot{token}/sendMessage"
            http_json("POST", url, {"chat_id": a["telegram_chat_id"], "text": f"{title}\n{msg}"}, timeout=10, lan=False)
        except Exception as e:  # noqa
            print("Telegram failed:", e)
    if a.get("ntfy_url", "").lower().startswith(("http://", "https://")):
        try:
            # RFC 2047 encoded title keeps umlauts (HTTP headers are latin-1 only)
            enc_title = "=?UTF-8?B?" + base64.b64encode(title.encode()).decode() + "?="
            req = urllib.request.Request(a["ntfy_url"], data=msg.encode(), method="POST", headers={"Title": enc_title})
            with NET_OPENER.open(req, timeout=10) as r:
                r.read(4096)
        except Exception as e:  # noqa
            print("ntfy failed:", e)


def check_alarms(results):
    a = CFG.get("alarms")
    known = CFG.get("known_best")
    changed = False
    for ip, d in results.items():
        name = (S.devices.get(ip) or {}).get("hostname", ip)
        if d is None:
            if S.fails.get(ip, 0) == a["offline_after_polls"]:
                notify(T("alarm_offline_t"), T("alarm_offline", name=name, ip=ip), "error", key=f"off:{ip}")
            continue
        if S.fails.get(ip, 0) == 0 and (S.alarm_last.get(f"off:{ip}") or 0) > 0:
            S.alarm_last.pop(f"off:{ip}", None)
            notify(T("alarm_online_t"), T("alarm_online", name=name, ip=ip), "ok", force=True)
        if TUNER.active(ip):
            continue  # no performance alerts while tuning
        if d["temp"] is not None and d["temp"] > a["temp_max"]:
            notify(T("alarm_temp_t"), T("alarm_temp", name=name, v=d["temp"], lim=a["temp_max"]), "error", key=f"t:{ip}")
        if d["vrTemp"] and d["vrTemp"] > a["vr_temp_max"]:
            notify(T("alarm_vr_t"), T("alarm_vr", name=name, v=d["vrTemp"], lim=a["vr_temp_max"]), "error", key=f"vr:{ip}")
        if d["expected"] and d["uptime"] > 900 and d["hashrate"] < d["expected"] * a["hashrate_min_pct"] / 100:
            notify(T("alarm_hr_t"), T("alarm_hr", name=name, v=d["hashrate"], exp=d["expected"]), "warn", key=f"hr:{ip}")
        lat_max = a.get("pool_latency_max_ms") or 0
        if lat_max and d.get("poolLatency") and d["poolLatency"] > lat_max:
            notify(T("alarm_lat_t"), T("alarm_lat", name=name, v=d["poolLatency"], lim=lat_max), "warn", key=f"lat:{ip}")
        if d["usingFallback"]:
            notify(T("alarm_fb_t"), T("alarm_fb", name=name), "warn", key=f"fb:{ip}")
        if d["blocksFound"] and S.blocks_notified.get(ip) != d["blocksFound"]:
            S.blocks_notified[ip] = d["blocksFound"]  # alert once per new block, not every cooldown period
            notify(T("alarm_block_t"), T("alarm_block", name=name), "ok", force=True)
        with CFG.lock:
            prev = known.get(ip)
            if prev is None or d["bestDiff"] > prev:
                known[ip] = d["bestDiff"]
                changed = True
        if prev is not None and d["bestDiff"] > prev and a.get("notify_best_diff"):
            notify(T("alarm_best_t"), T("alarm_best", name=name, v=fmt_diff(d["bestDiff"]), prev=fmt_diff(prev)),
                   "ok", force=True)
    if changed:
        CFG.save()


# --------------------------------------------------------------------------- tuner
class NotApplied(RuntimeError):
    pass


def check_limits(lim, freq, volt):
    if not (lim["minFreq"] <= freq <= lim["maxFreq"]):
        raise ValueError(T("lim_freq", f=freq, a=lim["minFreq"], b=lim["maxFreq"]))
    if not (lim["minV"] <= volt <= lim["maxV"]):
        raise ValueError(T("lim_volt", v=volt, a=lim["minV"], b=lim["maxV"]))


class Tuner:
    """Grid search over voltage x frequency with hard safety limits and error-aware stability checks."""

    def __init__(self):
        self.lock = threading.RLock()
        self.jobs = {}  # ip -> job dict

    def active(self, ip):
        with self.lock:
            j = self.jobs.get(ip)
            return bool(j and j["running"])

    def status(self):
        # jobs are mutated by worker threads – take a JSON snapshot, retry if it changed mid-copy
        for _ in range(5):
            try:
                with self.lock:
                    return json.loads(json.dumps(
                        {ip: {k: v for k, v in j.items() if k != "stop"} for ip, j in self.jobs.items()}))
            except RuntimeError:
                time.sleep(0.01)
        return {}

    def start(self, ip, p):
        with self.lock:
            if self.active(ip):
                raise ValueError(T("err_running"))
            d = S.devices.get(ip)
            if not d or not d.get("online"):
                raise ValueError(T("err_offline"))
            if not d.get("tunable"):
                raise ValueError(T("err_untunable"))
            if ip in S.restoring:
                raise ValueError(T("err_restoring"))
            fetch_asic(ip)
            lim = device_limits(ip, d.get("kind") == "nexus")

            def val(key, default, lo, hi, cast=float):
                v = num(p.get(key), default)
                return cast(min(hi, max(lo, v)))

            p = {
                "mode": "hashrate" if p.get("mode") == "hashrate" else "efficiency",
                "minFreq": val("minFreq", 500, 1, 5000, int), "maxFreq": val("maxFreq", 625, 1, 5000, int),
                "stepFreq": val("stepFreq", 25, 5, 500, int),
                "minV": val("minV", 1100, 1, 5000, int), "maxV": val("maxV", 1200, 1, 5000, int),
                "stepV": val("stepV", 25, 5, 500, int),
                # hard safety caps – cannot be overridden through the API
                "maxTemp": val("maxTemp", 65, 40, 75), "maxVr": val("maxVr", 80, 40, 95),
                # >= 60 s warm-up: the 1-minute hashrate must not contain values from before the change
                "warmupSec": val("warmupSec", 120, 60, 1800, int), "measureSec": val("measureSec", 300, 60, 3600, int),
                "minHrPct": val("minHrPct", 92, 50, 100),
                "maxHwErrPerMin": val("maxHwErrPerMin", 100, 0, 1e6),
                "maxRejected": val("maxRejected", 2, 0, 1000, int),
            }
            check_limits(lim, p["minFreq"], p["minV"])
            check_limits(lim, p["maxFreq"], p["maxV"])
            if p["minFreq"] > p["maxFreq"] or p["minV"] > p["maxV"]:
                raise ValueError(T("err_minmax"))
            volts = list(range(p["minV"], p["maxV"] + 1, p["stepV"]))
            freqs = list(range(p["minFreq"], p["maxFreq"] + 1, p["stepFreq"]))
            if len(volts) * len(freqs) > 200:
                raise ValueError(T("err_too_many", n=len(volts) * len(freqs)))
            # if an earlier run was interrupted, the device still runs test values: the real originals are the backup
            pending = (CFG.data.get("tuning_pending") or {}).get(ip)
            original = ({"frequency": pending["frequency"], "coreVoltage": pending["coreVoltage"]} if pending
                        else {"frequency": d["frequency"], "coreVoltage": d["coreVoltage"]})
            if not (num(original["frequency"], 0) > 0 and num(original["coreVoltage"], 0) > 0):
                raise ValueError(T("err_original"))  # without valid originals we could not restore safely
            job = {
                "running": True, "stop": False, "params": p, "phase": T("ph_start"),
                "original": original,
                "results": [], "best": None, "current": None, "message": "",
                "kind": d.get("kind"), "cores": d.get("smallCoreCount") or 0, "asics": d.get("asicCount") or 1,
                "total": len(volts) * len(freqs), "done": 0,
                "started": int(now()),
                "etaSec": len(volts) * len(freqs) * (p["warmupSec"] + p["measureSec"] + 20),
            }
            self.jobs[ip] = job
            # back up original values so they can be restored after a crash / program exit
            with CFG.lock:
                CFG.data.setdefault("tuning_pending", {})[ip] = dict(job["original"], t=int(now()))
                CFG.save()
        threading.Thread(target=self._run, args=(ip, job, volts, freqs), daemon=True).start()

    def stop(self, ip):
        with self.lock:
            j = self.jobs.get(ip)
            if j and j["running"]:
                j["stop"] = True

    def _sleep(self, job, sec):
        end = now() + sec
        while now() < end:
            if job["stop"]:
                return False
            time.sleep(1)
        return True

    def _apply(self, ip, freq, volt, tries=6, job=None):
        """Sets frequency/voltage. Short network dropouts are bridged with retries.
        With `job`, a click on "Cancel" interrupts the retries immediately."""
        last_err = None
        for attempt in range(tries):
            if job is not None and job["stop"]:
                raise InterruptedError()
            try:
                self._apply_once(ip, freq, volt)
                return
            except NotApplied as e:
                last_err = e
            except RuntimeError:
                raise
            except Exception as e:  # noqa – timeout, connection error
                last_err = e
            if attempt < tries - 1:
                for _ in range(min(60, 10 * (attempt + 1))):
                    if job is not None and job["stop"]:
                        raise InterruptedError()
                    time.sleep(1)
        raise RuntimeError(T("err_apply_tries", f=freq, v=volt, n=tries, e=last_err))

    @staticmethod
    def _limit_hit(info, p):
        """Safety check used during warm-up AND measurement. Returns a reason or None."""
        temp = num(info.get("temp"), 0.0)
        vr = num(info.get("vrTemp"), 0.0)
        power = num(info.get("power"), 0.0)
        maxp = num(info.get("maxPower")) or 40
        if temp > p["maxTemp"]:
            return T("rs_temp", v=temp, lim=p["maxTemp"])
        if vr > p["maxVr"]:
            return T("rs_vr", v=vr, lim=p["maxVr"])
        if power > maxp * 0.95:
            return T("rs_power", v=power, lim=maxp)
        return None

    def _warmup(self, ip, job):
        """Waits the warm-up time while watching temperatures/power every 10 s.
        Returns None, 'abort' or a limit reason."""
        p = job["params"]
        end = now() + p["warmupSec"]
        next_check = now()
        while now() < end:
            if job["stop"]:
                return "abort"
            if now() >= next_check:
                next_check = now() + 10
                try:
                    info = fetch_info(ip)
                    if isinstance(info, dict):
                        hit = self._limit_hit(info, p)
                        if hit:
                            return hit
                except Exception:  # noqa – network hiccups are handled by the measurement
                    pass
            time.sleep(1)
        return None

    @staticmethod
    def _read_state(ip, tries=4):
        """(stored_freq, stored_volt, live_freq, restart_required) – retried, raises if unreachable."""
        last = None
        for i in range(tries):
            try:
                info = fetch_info(ip, timeout=15)
                f = num(info.get("frequency"), 0)
                live = num(info.get("actualFrequency")) or f
                return f, int(num(info.get("coreVoltage"), 0)), live, bool(info.get("restartRequired"))
            except Exception as e:  # noqa
                last = e
                time.sleep(5)
        raise ConnectionError(last)

    def _apply_once(self, ip, freq, volt):
        http_json("PATCH", f"http://{ip}/api/system", {"frequency": freq, "coreVoltage": volt}, timeout=15)
        time.sleep(6)
        # a failed read here must NOT trigger a restart – it raises and _apply simply retries
        f, v, live, restart_req = self._read_state(ip)
        if abs(f - freq) > 1 or v != volt:
            raise NotApplied(T("err_not_applied", f=freq, v=volt))
        if abs(live - freq) <= 5 and not restart_req:
            return
        # stored but not applied live -> restart and verify afterwards
        try:
            http_json("POST", f"http://{ip}/api/system/restart", {}, timeout=8)
        except Exception:  # noqa
            pass
        time.sleep(15)
        for _ in range(30):
            try:
                f, v, live, _ = self._read_state(ip, tries=1)
            except Exception:  # noqa
                time.sleep(4)
                continue
            if abs(f - freq) > 1 or v != volt or abs(live - freq) > 5:
                raise NotApplied(T("err_not_applied", f=freq, v=volt))
            return
        raise RuntimeError(T("err_no_return"))

    def _measure(self, ip, job):
        """Returns (result, reason, kind) with kind in {None, 'abort', 'net', 'fail', 'hot'}."""
        p = job["params"]
        samples = []
        first = last = None  # (time, hw errors, rejected, uptime)
        polls = fails = net_bad = 0
        lat_max = CFG.get("alarms").get("pool_latency_max_ms") or 2000
        end = now() + p["measureSec"]
        while now() < end:
            if job["stop"]:
                return None, "", "abort"
            polls += 1
            try:
                info = fetch_info(ip)
            except Exception:  # noqa
                fails += 1
                time.sleep(5)
                continue
            if not isinstance(info, dict):
                fails += 1
                time.sleep(5)
                continue
            st = info.get("stratum") if isinstance(info.get("stratum"), dict) else {}
            fallback = bool(info.get("isUsingFallbackStratum") or st.get("usingFallback"))
            lat = num(info.get("lastpingrtt")) if info.get("lastpingrtt") is not None else num(info.get("responseTime"))
            if fallback or (lat is not None and lat > lat_max):
                net_bad += 1
            snap = (now(), hw_errors(info), int(num(info.get("sharesRejected"), 0)), int(num(info.get("uptimeSeconds"), 0)))
            if first is None:
                first = snap
            elif snap[3] < last[3]:
                return None, T("rs_reboot"), "fail"
            last = snap
            if last[0] > first[0]:
                job["current"]["hwErrPerMin"] = round((last[1] - first[1]) / (last[0] - first[0]) * 60, 1)
                job["current"]["rejected"] = last[2] - first[2]
            hit = self._limit_hit(info, p)
            if hit:
                return None, hit, "hot"
            temp = num(info.get("temp"), 0.0)
            vr = num(info.get("vrTemp"), 0.0)
            power = num(info.get("power"), 0.0)
            samples.append({
                "hr": num(info.get("hashRate_1m")) or num(info.get("hashRate"), 0.0),
                "power": power, "temp": temp, "vr": vr,
                "err": num(info.get("errorPercentage"), 0.0),
                "expected": num(info.get("expectedHashrate"))
                or job["current"]["frequency"] * job["cores"] * job["asics"] / 1000.0,
            })
            job["current"]["live"] = samples[-1]
            time.sleep(10)
        # network problems are not a chip problem -> the point will be repeated
        if polls and fails / polls > 0.3:
            return None, T("rs_net_fail", a=fails, b=polls), "net"
        if net_bad and net_bad >= max(2, len(samples) * 0.2):
            return None, T("rs_net_lat"), "net"
        if len(samples) < 3 or last[0] - first[0] < 30:
            return None, T("rs_net_few"), "net"
        n = len(samples)
        minutes = (last[0] - first[0]) / 60
        hr = sum(s["hr"] for s in samples) / n
        pw = sum(s["power"] for s in samples) / n
        res = {
            "hashrate": round(hr, 1), "power": round(pw, 2),
            "efficiency": round(pw / (hr / 1000), 2) if hr else None,
            "temp": round(max(s["temp"] for s in samples), 1),
            "vr": round(max(s["vr"] for s in samples), 1),
            "err": round(sum(s["err"] for s in samples) / n, 2),
            "hwErrPerMin": round((last[1] - first[1]) / minutes, 1),
            "rejected": last[2] - first[2],
            "expected": round(samples[-1]["expected"], 1),
        }
        return res, "", None

    def _run(self, ip, job, volts, freqs):
        p = job["params"]
        orig = job["original"]
        status = "error"
        too_hot = False
        try:
            for v in volts:
                if too_hot:
                    break
                for f in freqs:
                    if job["stop"]:
                        raise InterruptedError()
                    for attempt in range(3):  # repeat a point up to 2x on network problems
                        job["current"] = {"frequency": f, "coreVoltage": v, "live": None}
                        job["phase"] = T("ph_set", f=f, v=v) + (T("ph_retry", n=attempt) if attempt else "")
                        self._apply(ip, f, v, job=job)
                        job["phase"] = T("ph_warm", f=f, v=v)
                        w = self._warmup(ip, job)  # temperatures/power are watched during warm-up too
                        if w == "abort":
                            raise InterruptedError()
                        if w:
                            res, reason, kind = None, w, "hot"
                            break
                        job["phase"] = T("ph_measure", f=f, v=v)
                        res, reason, kind = self._measure(ip, job)
                        if kind == "abort":
                            raise InterruptedError()
                        if kind != "net":
                            break
                        job["message"] = T("msg_net_retry", f=f, v=v)
                        if not self._sleep(job, 90):
                            raise InterruptedError()
                    else:
                        raise RuntimeError(T("err_network", why=reason))
                    job["message"] = ""
                    job["done"] += 1
                    entry = {"frequency": f, "coreVoltage": v}
                    stable = False
                    if res:
                        entry.update(res)
                        exp = res["expected"] or 0
                        ratio = res["hashrate"] / exp * 100 if exp else 100
                        entry["hrPct"] = round(ratio, 1)
                        if ratio < p["minHrPct"]:
                            reason = T("rs_hr", v=ratio)
                        elif res["err"] > 3:
                            reason = T("rs_err", v=res["err"])
                        elif res["hwErrPerMin"] > p["maxHwErrPerMin"]:
                            reason = T("rs_hw", v=res["hwErrPerMin"], lim=p["maxHwErrPerMin"])
                        elif res["rejected"] > p["maxRejected"]:
                            reason = T("rs_rej", v=res["rejected"])
                        else:
                            stable = True
                    entry["stable"] = stable
                    entry["reason"] = reason or ""
                    job["results"].append(entry)
                    if stable:
                        self._update_best(job)
                    else:
                        if kind == "hot" and f == freqs[0]:
                            # even the lowest frequency hits a limit – higher voltages would only run hotter
                            too_hot = True
                        # higher frequencies at this voltage won't help and are riskier
                        job["done"] += len([x for x in freqs if x > f])
                        break
            if too_hot:
                job["done"] = job["total"]
            status = "done"
            job["phase"] = T("ph_done")
        except InterruptedError:
            status = "aborted"
            job["phase"] = T("ph_aborted")
        except Exception as e:  # noqa
            job["phase"] = T("ph_error")
            job["message"] = str(e)
        job["status"] = status
        # apply the result or restore the original values
        target = job["best"] if (job["best"] and status == "done") else None
        name = (S.devices.get(ip) or {}).get("hostname", ip)
        try:
            if target:
                self._apply(ip, target["frequency"], target["coreVoltage"])
                job["message"] = T("msg_best", f=target["frequency"], v=target["coreVoltage"])
                with CFG.lock:
                    CFG.data["tuning_results"][ip] = json.loads(json.dumps(
                        {"t": int(now()), "best": target, "results": job["results"], "mode": p["mode"]}))
                    CFG.save()
                notify(T("alarm_tuned_t"), T("alarm_tuned", name=name, f=target["frequency"], v=target["coreVoltage"],
                                             hr=target["hashrate"], eff=target["efficiency"]), "ok", force=True)
            else:
                self._apply(ip, int(orig["frequency"]), int(orig["coreVoltage"]))
                job["message"] = (job["message"] + " – " if job["message"] else "") + \
                    T("msg_restored", f=orig["frequency"], v=orig["coreVoltage"])
            clear_pending(ip)
        except Exception as e:  # noqa
            job["message"] += " – " + T("msg_apply_later", e=e)
        job["running"] = False
        job["current"] = None

    def _update_best(self, job):
        stable = [r for r in job["results"] if r["stable"]]
        if job["params"]["mode"] == "hashrate":
            # candidates within 1 % of the highest hashrate
            top = max(r["hashrate"] for r in stable)
            cand = [r for r in stable if r["hashrate"] >= top * 0.99]
        else:
            cand = stable
        # of those, everything within 1 % of the best efficiency
        best_eff = min(r["efficiency"] or 1e9 for r in cand)
        near = [r for r in cand if (r["efficiency"] or 1e9) <= best_eff * 1.01]
        # drop points with clearly more HW errors (> 25 % above the minimum),
        # then the highest hashrate wins (solo mining: more hashrate = higher block chance)
        min_err = min(r.get("hwErrPerMin") or 0 for r in near)
        clean = [r for r in near if (r.get("hwErrPerMin") or 0) <= max(min_err * 1.25, min_err + 5)]
        job["best"] = max(clean, key=lambda r: (r["hashrate"], -(r["efficiency"] or 1e9)))


def clear_pending(ip):
    with CFG.lock:
        if CFG.data.get("tuning_pending", {}).pop(ip, None) is not None:
            CFG.save()


def restore_pending_loop():
    """If the program was closed during a tuning run, restore the backed-up original values."""
    while True:
        with CFG.lock:
            pending = dict(CFG.data.get("tuning_pending") or {})
        for ip, orig in pending.items():
            with TUNER.lock:
                if TUNER.active(ip) or ip in S.restoring:
                    continue
                S.restoring.add(ip)  # blocks new tuning runs / manual sets on this device meanwhile
            try:
                fetch_info(ip)
                TUNER._apply(ip, int(orig["frequency"]), int(orig["coreVoltage"]))
                # only clear the backup if nobody replaced it in the meantime
                with CFG.lock:
                    if (CFG.data.get("tuning_pending") or {}).get(ip) == orig:
                        clear_pending(ip)
                notify(T("alarm_restored_t"), T("alarm_restored", ip=ip, f=orig["frequency"], v=orig["coreVoltage"]),
                       "warn", force=True)
            except Exception as e:  # noqa
                print("Restore pending:", ip, e)
            finally:
                S.restoring.discard(ip)
        time.sleep(30)


TUNER = Tuner()


# --------------------------------------------------------------------------- scan & pools
CGNAT = ipaddress.ip_network("100.64.0.0/10")  # e.g. Tailscale


def scan_network(cidr):
    """Only private IPv4 networks up to /22 (max 1022 hosts) – never scan the internet by accident."""
    try:
        net = ipaddress.ip_network(str(cidr).strip(), strict=False)
    except ValueError:
        raise ValueError(T("err_cidr"))
    if net.version != 4 or net.prefixlen < 22 or not (net.is_private or net.subnet_of(CGNAT)) or net.is_loopback:
        raise ValueError(T("err_cidr"))
    return net


def scan_subnet(cidr):
    with S.lock:
        S.scan = {"running": True, "found": [], "progress": 0}
    try:
        net = scan_network(cidr)
        hosts = [str(h) for h in itertools.islice(net.hosts(), 1024)]
        total = len(hosts) or 1

        def probe(ip):
            try:
                info = http_json("GET", f"http://{ip}/api/system/info", timeout=1.5)
                if isinstance(info, dict) and ("hashRate" in info or "ASICModel" in info):
                    return ip, txt(info.get("hostname"), 64) or ip
            except Exception:  # noqa
                pass
            return None

        done = 0
        with concurrent.futures.ThreadPoolExecutor(max_workers=64) as ex:
            for r in ex.map(probe, hosts):
                done += 1
                with S.lock:
                    S.scan["progress"] = round(done / total * 100)
                    if r:
                        S.scan["found"].append({"ip": r[0], "hostname": r[1]})
        added = 0
        with CFG.lock:
            for f in S.scan["found"]:
                if f["ip"] not in CFG.data["devices"]:
                    CFG.data["devices"].append(f["ip"])
                    added += 1
            CFG.save()
        S.scan["added"] = added
        if added:
            threading.Thread(target=poll_once, daemon=True).start()
    finally:
        with S.lock:
            S.scan["running"] = False


def tcp_latency(host, port, tries=3):
    times = []
    for _ in range(tries):
        t0 = time.perf_counter()
        try:
            with socket.create_connection((host, int(port)), timeout=3):
                times.append((time.perf_counter() - t0) * 1000)
        except Exception:  # noqa
            pass
    if not times:
        return None
    return round(min(times), 1)


def set_pool(ips, host, port, user_base, password, append_worker, which):
    out = []
    for ip in ips:
        d = S.devices.get(ip) or {}
        if d.get("kind") != "axeos":
            out.append({"ip": ip, "ok": False, "msg": T("pool_only_axeos")})
            continue
        if TUNER.active(ip) or ip in S.restoring:
            out.append({"ip": ip, "ok": False, "msg": T("err_busy")})  # pool change restarts the miner
            continue
        user = user_base.strip()
        if append_worker:
            user = f"{user.split('.')[0]}.{d.get('hostname', ip)}"
        prefix = "fallbackStratum" if which == "fallback" else "stratum"
        body = {
            f"{prefix}URL": host, f"{prefix}Port": int(port), f"{prefix}User": user,
            f"{prefix}Protocol": "SV1",
        }
        if password:
            body[f"{prefix}Password"] = password
        try:
            http_json("PATCH", f"http://{ip}/api/system", body, timeout=8)
            http_json("POST", f"http://{ip}/api/system/restart", {}, timeout=8)
            out.append({"ip": ip, "ok": True, "msg": T("pool_set", user=user)})
        except Exception as e:  # noqa
            out.append({"ip": ip, "ok": False, "msg": str(e)})
    return out


# --------------------------------------------------------------------------- http
class Server(ThreadingHTTPServer):
    # on Windows SO_REUSEADDR would let a second instance bind the same port
    allow_reuse_address = False
    daemon_threads = True


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    MAX_BODY = 256 * 1024

    def _send(self, code, obj, ctype="application/json"):
        body = obj if isinstance(obj, (bytes, bytearray)) else jdump(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        # no framing (clickjacking), no MIME sniffing, no referrer leaks
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "frame-ancestors 'none'")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n < 0 or n > self.MAX_BODY:
            raise ValueError("invalid request size")
        if not n:
            return {}
        b = json.loads(self.rfile.read(n))
        if not isinstance(b, dict):
            raise ValueError("JSON object expected")
        return b

    def _host_ok(self):
        # DNS-rebinding protection: only answer requests addressed to this machine's loopback name
        host = (self.headers.get("Host") or "").lower()
        if host not in (f"127.0.0.1:{PORT}", f"localhost:{PORT}"):
            self._send(403, {"error": "forbidden host"})
            return False
        return True

    def _check_origin(self):
        # CSRF protection: foreign websites may not send commands to the local server.
        origin = self.headers.get("Origin")
        if origin and origin not in (f"http://127.0.0.1:{PORT}", f"http://localhost:{PORT}"):
            self._send(403, {"error": "forbidden"})
            return False
        # JSON content type forces a CORS preflight for cross-site requests, which this server never grants
        if not (self.headers.get("Content-Type") or "").lower().startswith("application/json"):
            self._send(415, {"error": "application/json required"})
            return False
        return True

    def do_GET(self):
        if not self._host_ok():
            return
        try:
            self._get()
        except Exception as e:  # noqa – never leave the browser with a dropped connection
            print("GET error:", e)
            try:
                self._send(500, {"error": str(e)})
            except Exception:  # noqa
                pass

    def _get(self):
        path = urllib.parse.urlparse(self.path).path
        if path in ("/", "/index.html"):
            with open(resource_path("index.html"), "rb") as f:
                return self._send(200, f.read(), "text/html; charset=utf-8")
        if path == "/api/state":
            cfg = CFG.snapshot()
            if cfg["alarms"].get("telegram_token"):
                cfg["alarms"]["telegram_token"] = "••••••"
            tuning = TUNER.status()
            with S.lock:
                payload = jdump({
                    "version": VERSION,
                    "lang": lang(),
                    "currency": currency(),
                    "currencies": CURRENCIES,
                    "subnet": S.subnet,
                    "configPath": CFG.path,
                    "devices": [S.devices[ip] for ip in cfg["devices"] if ip in S.devices],
                    "network": S.network,
                    "alarms": S.alarms[:100],
                    "tuning": tuning,
                    "scan": S.scan,
                    "config": cfg,
                }).encode()
            return self._send(200, payload)
        if path == "/api/history":
            with S.lock:
                h = list(S.history)
            step = max(1, len(h) // 500)  # the chart needs at most ~500 points, not 5760 every 5 s
            pts = h[::step]
            if h and pts[-1] is not h[-1]:
                pts.append(h[-1])
            return self._send(200, jdump(pts).encode())
        self._send(404, {"error": "not found"})

    @staticmethod
    def _known(ip):
        if ip not in CFG.get("devices"):
            raise ValueError(T("err_unknown_dev"))
        return ip

    @staticmethod
    def _clean_alarms(al):
        out = {}
        ranges = {"temp_max": (30, 100), "vr_temp_max": (30, 130), "hashrate_min_pct": (1, 100),
                  "pool_latency_max_ms": (0, 600000), "offline_after_polls": (1, 100), "cooldown_min": (1, 1440)}
        for k, (lo, hi) in ranges.items():
            if k in al:
                v = num(al[k])
                if v is None or not lo <= v <= hi:
                    raise ValueError(f"{k}: {lo}–{hi}")
                out[k] = int(v) if k in ("offline_after_polls", "cooldown_min", "pool_latency_max_ms") else v
        for k in ("notify_best_diff", "windows_notify"):
            if k in al:
                out[k] = bool(al[k])
        for k in ("telegram_token", "telegram_chat_id", "ntfy_url"):
            if k in al:
                out[k] = txt(al[k], 300).strip()
        if out.get("ntfy_url") and not out["ntfy_url"].lower().startswith(("http://", "https://")):
            raise ValueError(T("err_ntfy"))
        if out.get("telegram_token") == "••••••":
            out.pop("telegram_token")
        return out

    @staticmethod
    def _clean_pools(pools):
        out = []
        for p in (pools if isinstance(pools, list) else [])[:30]:
            if not isinstance(p, dict):
                continue
            host = txt(p.get("host"), 253).strip()
            port = int(num(p.get("port"), 0))
            if host and 0 < port < 65536 and all(c.isalnum() or c in ".-_:[]" for c in host):
                out.append({"name": txt(p.get("name"), 60).strip() or host, "host": host, "port": port})
        return out

    def do_POST(self):
        if not self._host_ok() or not self._check_origin():
            return
        path = urllib.parse.urlparse(self.path).path
        try:
            b = self._body()
            if path == "/api/devices/add":
                ip = str(b.get("ip") or "").strip()
                if not valid_device_addr(ip):
                    raise ValueError(T("err_ip"))
                ip = str(ipaddress.IPv4Address(ip.split(":")[0])) + (":" + ip.split(":")[1] if ":" in ip else "")
                with CFG.lock:
                    if ip not in CFG.data["devices"]:
                        CFG.data["devices"].append(ip)
                    CFG.save()
                threading.Thread(target=poll_once, daemon=True).start()
                return self._send(200, {"ok": True})
            if path == "/api/devices/remove":
                with CFG.lock:
                    CFG.data["devices"] = [x for x in CFG.data["devices"] if x != b["ip"]]
                    CFG.save()
                with S.lock:
                    S.devices.pop(b["ip"], None)
                return self._send(200, {"ok": True})
            if path == "/api/scan":
                cidr = str(b.get("cidr") or S.subnet)
                scan_network(cidr)  # validate before starting (clear error instead of a silent abort)
                with S.lock:
                    if S.scan.get("running"):
                        return self._send(409, {"error": T("err_scan_running")})
                    S.scan = {"running": True, "found": [], "progress": 0}
                threading.Thread(target=scan_subnet, args=(cidr,), daemon=True).start()
                return self._send(200, {"ok": True})
            if path == "/api/config":
                upd = {}
                if "poll_seconds" in b:
                    v = num(b["poll_seconds"])
                    if v is None or not 5 <= v <= 3600:
                        raise ValueError("poll_seconds: 5–3600")
                    upd["poll_seconds"] = int(v)
                if "electricity_eur_kwh" in b:
                    v = num(b["electricity_eur_kwh"])
                    if v is None or not 0 <= v <= 1000:
                        raise ValueError("electricity price: 0–1000")
                    upd["electricity_eur_kwh"] = v
                if b.get("language") in ("auto", "de", "en"):
                    upd["language"] = b["language"]
                if b.get("currency") == "auto" or b.get("currency") in CURRENCIES:
                    upd["currency"] = b["currency"]
                alarms = self._clean_alarms(b["alarms"]) if isinstance(b.get("alarms"), dict) else None
                pools = self._clean_pools(b["pools"]) if "pools" in b else None
                with CFG.lock:  # validate everything first, then write – no half-applied configs
                    CFG.data.update(upd)
                    if alarms:
                        CFG.data["alarms"].update(alarms)
                    if pools is not None:
                        CFG.data["pools"] = pools
                    CFG.save()
                return self._send(200, {"ok": True})
            if path == "/api/alarm/test":
                notify(T("alarm_test_t"), T("alarm_test"), "ok", force=True)
                return self._send(200, {"ok": True})
            if path == "/api/alarm/clear":
                with S.lock:
                    S.alarms.clear()
                return self._send(200, {"ok": True})
            if path == "/api/tune/start":
                TUNER.start(self._known(b.get("ip")), b)
                return self._send(200, {"ok": True})
            if path == "/api/tune/stop":
                TUNER.stop(b["ip"])
                return self._send(200, {"ok": True})
            if path == "/api/tune/clear":
                with TUNER.lock:
                    if not TUNER.active(b["ip"]):
                        TUNER.jobs.pop(b["ip"], None)
                return self._send(200, {"ok": True})
            if path == "/api/settings":
                ip = self._known(b.get("ip"))
                d = S.devices.get(ip) or {}
                if not d.get("tunable"):
                    raise ValueError(T("err_untunable"))
                f, v = num(b.get("frequency")), num(b.get("coreVoltage"))
                if f is None or v is None:
                    raise ValueError(T("err_minmax"))
                f, v = int(f), int(v)
                fetch_asic(ip)
                check_limits(device_limits(ip, d.get("kind") == "nexus"), f, v)
                with TUNER.lock:
                    if TUNER.active(ip):
                        raise ValueError(T("err_busy"))
                    if ip in S.restoring:
                        raise ValueError(T("err_restoring"))
                    S.restoring.add(ip)  # also blocks a tuning start while we set values
                try:
                    TUNER._apply(ip, f, v, tries=2)  # interactive: fail fast instead of blocking for minutes
                    clear_pending(ip)  # the user deliberately chose values – never "restore" over them
                finally:
                    S.restoring.discard(ip)
                return self._send(200, {"ok": True})
            if path == "/api/restart":
                ip = self._known(b.get("ip"))
                if TUNER.active(ip) or ip in S.restoring:
                    raise ValueError(T("err_busy"))  # a restart would corrupt the running measurement
                http_json("POST", f"http://{ip}/api/system/restart", {}, timeout=8)
                return self._send(200, {"ok": True})
            if path == "/api/pools/test":
                pools = CFG.get("pools")  # only the configured pools – no arbitrary targets
                if not pools:
                    return self._send(200, [])
                with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
                    lat = list(ex.map(lambda p: tcp_latency(p["host"], p["port"]), pools))
                return self._send(200, [dict(p, latency=l) for p, l in zip(pools, lat)])
            if path == "/api/pools/set":
                ips = [self._known(ip) for ip in (b.get("ips") or [])]
                pool = (self._clean_pools([{"name": "", "host": b.get("host"), "port": b.get("port")}]) or [None])[0]
                user = txt(b.get("user"), 140).strip()
                if not pool or not ips or not user:
                    raise ValueError("pool / devices / user")
                res = set_pool(ips, pool["host"], pool["port"], user, txt(b.get("password"), 100),
                               bool(b.get("appendWorker", True)), "fallback" if b.get("which") == "fallback" else "primary")
                return self._send(200, res)
            self._send(404, {"error": "not found"})
        except Exception as e:  # noqa
            self._send(400, {"error": str(e)})


def main():
    print(f"Solo Miner Optimizer {VERSION}")
    print(f"Config: {CFG.path}")
    try:
        srv = Server(("127.0.0.1", PORT), Handler)
    except OSError:
        print(f"Port {PORT} is in use – is the program already running? Opening browser...")
        webbrowser.open(f"http://127.0.0.1:{PORT}")
        time.sleep(3)
        return
    threading.Thread(target=poll_loop, daemon=True).start()
    threading.Thread(target=price_loop, daemon=True).start()
    threading.Thread(target=restore_pending_loop, daemon=True).start()
    url = f"http://127.0.0.1:{PORT}"
    print(f"Web interface: {url}")
    print("Close this window or press Ctrl+C to quit.")
    if "--no-browser" not in sys.argv:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
