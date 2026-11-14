"""
Automated tests for Solo Miner Optimizer – run with:  python -m unittest discover -s tests -v

Uses a simulated AxeOS miner (local HTTP server) and a fast-forward clock, so a full
tuning run takes seconds. No real miner is contacted.
"""
import http.client
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TMP = tempfile.mkdtemp(prefix="smo-test-")
APP_PORT = 18765
os.environ["SMO_CONFIG_DIR"] = TMP
os.environ["SMO_PORT"] = str(APP_PORT)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import solo_optimizer as so  # noqa: E402

REAL_SLEEP = time.sleep


# --------------------------------------------------------------------------- fast clock
class Clock:
    def __init__(self):
        self.t = 1_000_000.0
        self.lock = threading.Lock()

    def now(self):
        with self.lock:
            return self.t

    def sleep(self, s):
        with self.lock:
            self.t += max(0.0, s)
        REAL_SLEEP(0.0005)


CLOCK = Clock()
so.now = CLOCK.now
so.time.sleep = CLOCK.sleep  # module-level patch (affects only code paths that call time.sleep)


# --------------------------------------------------------------------------- simulated miner
class FakeMiner:
    """Behaves like a Bitaxe Gamma: low voltage -> more errors, high frequency -> warmer."""

    def __init__(self, live_apply=True, malicious=False, temp_offset=0.0, no_asic=False, extra=None):
        self.freq, self.volt = 550, 1200
        self.live_freq = 550
        self.live_apply = live_apply
        self.malicious = malicious
        self.temp_offset = temp_offset
        self.no_asic = no_asic
        self.extra = extra or {}
        self.asic_hits = 0
        self.info_hits = 0
        self.restarts = 0
        self.patches = 0
        self.fail_next_gets = 0
        self.boot = CLOCK.now()
        self.lock = threading.Lock()
        miner = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _json(self, obj):
                body = json.dumps(obj).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                with miner.lock:
                    if miner.fail_next_gets > 0:
                        miner.fail_next_gets -= 1
                        self.close_connection = True
                        self.connection.close()
                        return
                if self.path.startswith("/api/system/asic"):
                    with miner.lock:
                        miner.asic_hits += 1
                    if miner.no_asic:
                        self.send_response(404)
                        self.send_header("Content-Length", "0")
                        self.end_headers()
                        return
                    return self._json({"ASICModel": "BM1370", "deviceModel": "Gamma", "frequencyOptions": [400, 525, 600],
                                       "voltageOptions": [1000, 1150, 1250], "defaultFrequency": 525, "defaultVoltage": 1150})
                with miner.lock:
                    miner.info_hits += 1
                return self._json(miner.info())

            def do_PATCH(self):
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n) or b"{}")
                with miner.lock:
                    miner.patches += 1
                    miner.freq = body.get("frequency", miner.freq)
                    miner.volt = body.get("coreVoltage", miner.volt)
                    if miner.live_apply:
                        miner.live_freq = miner.freq
                self._json({})

            def do_POST(self):
                if self.path.startswith("/api/system/restart"):
                    with miner.lock:
                        miner.restarts += 1
                        miner.live_freq = miner.freq
                        miner.boot = CLOCK.now()
                return self._json({})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.addr = f"127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def info(self):
        with self.lock:
            f, v, live = self.freq, self.volt, self.live_freq
            up = CLOCK.now() - self.boot
        expected = live * 2040 / 1000
        err_per_min = max(0, (1200 - v)) * 8 + max(0, live - 600) * 4  # errors rise at low voltage / high freq
        temp = 45 + (live - 400) * 0.06 + (v - 1000) * 0.03 + self.temp_offset
        info = {
            "ASICModel": "BM1370", "hostname": "fakeaxe", "version": "v2.15.4",
            "frequency": f, "actualFrequency": live, "coreVoltage": v, "coreVoltageActual": v - 5,
            "hashRate": expected, "hashRate_1m": expected, "hashRate_10m": expected, "expectedHashrate": expected,
            "power": 8 + live * 0.02 + (v - 1000) * 0.03, "maxPower": 40, "temp": temp, "vrTemp": temp - 2,
            "uptimeSeconds": int(up), "sharesRejected": 0, "errorPercentage": round(err_per_min / 200, 2),
            "hashrateMonitor": {"asics": [{"errorCount": int(err_per_min * up / 60)}]},
            "responseTime": 40, "isUsingFallbackStratum": 0, "smallCoreCount": 2040,
            "stratumURL": "pool.example", "stratumPort": 3333, "stratumUser": "bc1qexample.fakeaxe",
            "bestDiff": 1000, "restartRequired": 0 if self.live_apply or live == f else 1,
        }
        info.update(self.extra)
        if self.malicious:
            info.update({"frequency": "<img src=x onerror=alert(1)>", "hostname": "<script>alert(1)</script>" + "x" * 500,
                         "temp": "hot", "power": None, "hashRate_10m": "NaN", "stratumURL": {"evil": 1},
                         "hashrateMonitor": {"asics": ["bad", {"errorCount": "lots"}]}})
        return info

    def close(self):
        self.server.shutdown()


def register(miner):
    with so.CFG.lock:
        so.CFG.data["devices"] = [miner.addr]
        so.CFG.data["tuning_pending"] = {}
    so.ASIC_CACHE.pop(miner.addr, None)
    so.poll_once()
    return so.S.devices[miner.addr]


def wait_job(ip, limit=60):
    end = time.monotonic() + limit
    while time.monotonic() < end:
        if not so.TUNER.active(ip):
            return so.TUNER.jobs[ip]
        REAL_SLEEP(0.02)
    raise AssertionError("tuning did not finish")


FAST = {"minFreq": 500, "maxFreq": 600, "stepFreq": 50, "minV": 1150, "maxV": 1250, "stepV": 50,
        "warmupSec": 30, "measureSec": 60, "maxHwErrPerMin": 300, "minHrPct": 92}


# --------------------------------------------------------------------------- tests
class TestSanitizing(unittest.TestCase):
    def test_malicious_device_data_is_neutralised(self):
        m = FakeMiner(malicious=True)
        try:
            d = so.normalize(m.addr, m.info())
            self.assertIsInstance(d["frequency"], (int, float))      # no HTML can reach the page as a number field
            self.assertLessEqual(len(d["hostname"]), 64)
            self.assertIsNone(d["temp"])
            self.assertEqual(d["poolURL"], "")                        # dict instead of string -> dropped
            self.assertEqual(d["hwErrors"], 0)
            json.dumps(d)                                             # must stay serialisable
        finally:
            m.close()

    def test_num_rejects_nan_and_inf(self):
        self.assertIsNone(so.num("NaN"))
        self.assertIsNone(so.num(float("inf")))
        self.assertEqual(so.num("12.5"), 12.5)
        self.assertEqual(so.num(None, 3), 3)

    def test_parse_diff(self):
        self.assertEqual(so.parse_diff("15.73G"), 15.73e9)
        self.assertEqual(so.parse_diff(1234), 1234.0)
        self.assertEqual(so.parse_diff("garbage"), 0.0)


class TestScanLimits(unittest.TestCase):
    def test_rejects_huge_or_public_networks(self):
        for bad in ("10.0.0.0/8", "8.8.8.0/24", "192.168.0.0/16", "127.0.0.0/24", "nonsense", "::1/128"):
            with self.assertRaises(ValueError, msg=bad):
                so.scan_network(bad)

    def test_accepts_home_networks(self):
        for good in ("192.168.1.0/24", "10.0.5.0/23", "172.16.0.0/22", "100.100.1.0/24"):
            so.scan_network(good)


class TestBestPointSelection(unittest.TestCase):
    def best(self, rows, mode="efficiency"):
        job = {"params": {"mode": mode}, "results": [dict(r, stable=True) for r in rows]}
        so.TUNER._update_best(job)
        return job["best"]["frequency"]

    def test_near_equal_efficiency_prefers_hashrate(self):
        self.assertEqual(self.best([{"frequency": 500, "hashrate": 1020, "efficiency": 18.97, "hwErrPerMin": 402},
                                    {"frequency": 525, "hashrate": 1070, "efficiency": 18.79, "hwErrPerMin": 419}]), 525)

    def test_clearly_more_errors_loses(self):
        self.assertEqual(self.best([{"frequency": 500, "hashrate": 1020, "efficiency": 18.80, "hwErrPerMin": 300},
                                    {"frequency": 525, "hashrate": 1070, "efficiency": 18.79, "hwErrPerMin": 600}]), 500)

    def test_clearly_more_efficient_wins(self):
        self.assertEqual(self.best([{"frequency": 500, "hashrate": 1020, "efficiency": 18.0, "hwErrPerMin": 400},
                                    {"frequency": 525, "hashrate": 1070, "efficiency": 18.79, "hwErrPerMin": 400}]), 500)


class TestTuningEndToEnd(unittest.TestCase):
    def test_full_run_rejects_error_heavy_points_and_applies_best(self):
        m = FakeMiner()
        try:
            register(m)
            so.TUNER.start(m.addr, dict(FAST))
            job = wait_job(m.addr)
            self.assertEqual(job["status"], "done", job.get("message"))
            low_v = [r for r in job["results"] if r["coreVoltage"] == 1150]
            self.assertTrue(low_v and not low_v[0]["stable"], "1150 mV produces 400 errors/min > 300 -> unstable")
            self.assertIn("HW", low_v[0]["reason"])
            self.assertTrue(job["best"]["stable"])
            self.assertEqual((m.freq, m.volt), (job["best"]["frequency"], job["best"]["coreVoltage"]))
            self.assertEqual(m.restarts, 0, "values apply live -> no restart needed")
            self.assertEqual(so.CFG.data["tuning_pending"], {}, "backup cleared after success")
        finally:
            m.close()

    def test_transient_read_failure_does_not_restart_miner(self):
        m = FakeMiner()
        try:
            register(m)
            m.fail_next_gets = 2  # the read right after PATCH fails twice
            so.TUNER._apply(m.addr, 575, 1225)
            self.assertEqual((m.freq, m.volt), (575, 1225))
            self.assertEqual(m.restarts, 0)
        finally:
            m.close()

    def test_restart_required_device_is_restarted_and_verified(self):
        m = FakeMiner(live_apply=False)
        try:
            register(m)
            so.TUNER._apply(m.addr, 600, 1250)
            self.assertEqual(m.restarts, 1)
            self.assertEqual(m.live_freq, 600)
        finally:
            m.close()

    def test_abort_restores_original_values(self):
        m = FakeMiner()
        try:
            register(m)
            so.TUNER.start(m.addr, dict(FAST))
            REAL_SLEEP(0.05)
            so.TUNER.stop(m.addr)
            job = wait_job(m.addr)
            self.assertEqual(job["status"], "aborted")
            self.assertEqual((m.freq, m.volt), (550, 1200))
        finally:
            m.close()

    def test_crash_recovery_uses_backed_up_originals(self):
        m = FakeMiner()
        try:
            register(m)
            m.freq, m.live_freq, m.volt = 600, 600, 1150  # device still on test values after a "crash"
            with so.CFG.lock:
                so.CFG.data["tuning_pending"] = {m.addr: {"frequency": 550, "coreVoltage": 1200, "t": 1}}
            so.poll_once()
            so.TUNER.start(m.addr, dict(FAST))
            job = wait_job(m.addr)
            self.assertEqual(job["original"], {"frequency": 550, "coreVoltage": 1200})
        finally:
            m.close()

    def test_api_cannot_override_safety_caps(self):
        m = FakeMiner()
        try:
            register(m)
            so.TUNER.start(m.addr, dict(FAST, maxTemp=95, maxVr=150, measureSec=0, warmupSec=0))
            p = so.TUNER.jobs[m.addr]["params"]
            self.assertEqual((p["maxTemp"], p["maxVr"], p["measureSec"], p["warmupSec"]), (75, 95, 60, 60))
            so.TUNER.stop(m.addr)
            wait_job(m.addr)
        finally:
            m.close()

    def test_out_of_range_rejected_before_anything_is_written(self):
        m = FakeMiner()
        try:
            register(m)
            with self.assertRaises(ValueError):
                so.TUNER.start(m.addr, dict(FAST, maxV=1400))
            self.assertEqual(m.patches, 0)
        finally:
            m.close()


class TestHttpSecurity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = so.Server(("127.0.0.1", APP_PORT), so.Handler)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def req(self, method, path, body=None, headers=None):
        c = http.client.HTTPConnection("127.0.0.1", APP_PORT, timeout=10)
        h = {"Host": f"127.0.0.1:{APP_PORT}"}
        if body is not None:
            h["Content-Type"] = "application/json"
        h.update(headers or {})
        c.request(method, path, body=json.dumps(body) if body is not None else None, headers=h)
        r = c.getresponse()
        data = r.read()
        return r.status, r.headers, data

    def test_dns_rebinding_host_is_rejected(self):
        st, _, _ = self.req("GET", "/api/state", headers={"Host": f"evil.example:{APP_PORT}"})
        self.assertEqual(st, 403)

    def test_cross_site_post_is_rejected(self):
        st, _, _ = self.req("POST", "/api/alarm/test", {}, headers={"Origin": "https://evil.example"})
        self.assertEqual(st, 403)

    def test_post_without_json_content_type_is_rejected(self):
        st, _, _ = self.req("POST", "/api/alarm/test", headers={"Content-Type": "text/plain"})
        self.assertEqual(st, 415)

    def test_security_headers_and_token_masking(self):
        with so.CFG.lock:
            so.CFG.data["alarms"]["telegram_token"] = "123:SECRET"
        st, h, data = self.req("GET", "/api/state")
        self.assertEqual(st, 200)
        self.assertEqual(h["X-Frame-Options"], "DENY")
        self.assertNotIn(b"SECRET", data)
        with so.CFG.lock:
            so.CFG.data["alarms"]["telegram_token"] = ""

    def test_restart_only_for_known_devices(self):
        st, _, data = self.req("POST", "/api/restart", {"ip": "192.0.2.10"})
        self.assertEqual(st, 400)

    def test_invalid_alarm_values_rejected_and_config_unchanged(self):
        before = so.CFG.snapshot()["alarms"]["temp_max"]
        st, _, _ = self.req("POST", "/api/config", {"alarms": {"temp_max": 0}})
        self.assertEqual(st, 400)
        self.assertEqual(so.CFG.snapshot()["alarms"]["temp_max"], before)

    def test_ntfy_file_url_rejected(self):
        st, _, _ = self.req("POST", "/api/config", {"alarms": {"ntfy_url": "file:///C:/Windows/win.ini"}})
        self.assertEqual(st, 400)

    def test_scan_of_public_network_rejected(self):
        st, _, _ = self.req("POST", "/api/scan", {"cidr": "8.8.8.0/24"})
        self.assertEqual(st, 400)

    def test_pool_list_is_sanitised(self):
        st, _, _ = self.req("POST", "/api/config", {"pools": [{"name": "ok", "host": "pool.example", "port": 3333},
                                                               {"name": "x", "host": "<script>", "port": 1},
                                                               {"name": "y", "host": "a.b", "port": 99999}]})
        self.assertEqual(st, 200)
        self.assertEqual([p["host"] for p in so.CFG.get("pools")], ["pool.example"])

    def test_oversized_body_rejected(self):
        # the server refuses before reading the body; depending on timing the client sees 400 or a reset connection
        before = so.CFG.snapshot()
        try:
            st, _, _ = self.req("POST", "/api/config", {"pad": "x" * 300_000})
            self.assertEqual(st, 400)
        except ConnectionError:
            pass
        self.assertEqual(so.CFG.snapshot(), before)


class TestConfigSafety(unittest.TestCase):
    def test_broken_config_is_backed_up_not_lost(self):
        d = tempfile.mkdtemp(prefix="smo-cfg-")
        try:
            with open(os.path.join(d, "config.json"), "w", encoding="utf-8") as f:
                f.write("{ this is not json")
            old = os.environ["SMO_CONFIG_DIR"]
            os.environ["SMO_CONFIG_DIR"] = d
            try:
                so.Config()
            finally:
                os.environ["SMO_CONFIG_DIR"] = old
            names = os.listdir(d)
            self.assertTrue(any(n.startswith("config.json.broken-") for n in names), names)
            self.assertIn("config.json", names)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_concurrent_saves_do_not_crash(self):
        errors = []

        def writer(i):
            try:
                for j in range(200):
                    with so.CFG.lock:
                        so.CFG.data["known_best"][f"10.0.{i}.{j}"] = j
                    so.CFG.save()
            except Exception as e:  # noqa
                errors.append(e)

        ts = [threading.Thread(target=writer, args=(i,)) for i in range(4)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        self.assertEqual(errors, [])
        with so.CFG.lock:
            so.CFG.data["known_best"] = {}


class TestRound2(unittest.TestCase):
    """Findings of the second review round."""

    def test_overheat_during_warmup_aborts_and_skips_higher_voltages(self):
        m = FakeMiner(temp_offset=40)  # every setting is > 75 °C
        try:
            register(m)
            so.TUNER.start(m.addr, dict(FAST, warmupSec=300))
            job = wait_job(m.addr)
            self.assertEqual(len(job["results"]), 1, "first point too hot at lowest frequency -> stop")
            self.assertFalse(job["results"][0]["stable"])
            self.assertIn("ASIC", job["results"][0]["reason"])
            self.assertIsNone(job["results"][0].get("hashrate"), "aborted during warm-up, before measuring")
            self.assertEqual((m.freq, m.volt), (550, 1200), "original values restored")
        finally:
            m.close()

    def test_cancel_interrupts_apply_retries_immediately(self):
        m = FakeMiner()
        try:
            register(m)
            with self.assertRaises(InterruptedError):
                so.TUNER._apply(m.addr, 600, 1200, job={"stop": True})
            self.assertEqual(m.patches, 0)
        finally:
            m.close()

    def test_nan_and_infinity_never_break_the_json(self):
        m = FakeMiner(extra={"bestDiff": "1e400", "networkDifficulty": float("nan"), "temp": float("inf")})
        try:
            d = so.normalize(m.addr, m.info())
            self.assertEqual(d["bestDiff"], 0.0)
            text = so.jdump({"d": d, "x": float("nan")})
            json.loads(text, parse_constant=lambda c: self.fail(f"invalid JSON constant {c}"))
        finally:
            m.close()

    def test_redirects_are_not_followed(self):
        target = FakeMiner()
        hits = {"n": 0}

        class R(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                hits["n"] += 1
                self.send_response(302)
                self.send_header("Location", f"http://{target.addr}/api/system/info")
                self.send_header("Content-Length", "0")
                self.end_headers()

        srv = ThreadingHTTPServer(("127.0.0.1", 0), R)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            with self.assertRaises(Exception):
                so.http_json("GET", f"http://127.0.0.1:{srv.server_address[1]}/api/system/info")
            self.assertEqual(hits["n"], 1)
            self.assertEqual(target.info_hits, 0, "the redirect target must never be contacted")
        finally:
            srv.shutdown()
            target.close()

    def test_oversized_device_response_rejected(self):
        class Big(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                body = b"[" + b"1," * 1_600_000 + b"1]"
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        srv = ThreadingHTTPServer(("127.0.0.1", 0), Big)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            with self.assertRaises(ValueError):
                so.http_json("GET", f"http://127.0.0.1:{srv.server_address[1]}/")
        finally:
            srv.shutdown()

    def test_hand_edited_config_is_repaired_not_discarded(self):
        d = tempfile.mkdtemp(prefix="smo-cfg2-")
        try:
            bad = {"devices": ["192.168.1.5", "evil.com", "http://x/", "fe80::1", 5], "poll_seconds": -5,
                   "language": "xx", "alarms": {"temp_max": "hot", "notify_best_diff": 1},
                   "pools": [{"host": 3}, {"name": "ok", "host": "pool.example", "port": 3333}],
                   "tuning_results": {"192.168.1.5": {"best": {}}}, "tuning_pending": {"1.2.3.4": {"frequency": 0}}}
            with open(os.path.join(d, "config.json"), "w", encoding="utf-8") as f:
                json.dump(bad, f)
            old = os.environ["SMO_CONFIG_DIR"]
            os.environ["SMO_CONFIG_DIR"] = d
            try:
                c = so.Config()
            finally:
                os.environ["SMO_CONFIG_DIR"] = old
            self.assertEqual(c.data["devices"], ["192.168.1.5"])
            self.assertEqual(c.data["poll_seconds"], 15)
            self.assertEqual(c.data["language"], "auto")
            self.assertEqual(c.data["alarms"]["temp_max"], 68.0)
            self.assertEqual([p["host"] for p in c.data["pools"]], ["pool.example"])
            self.assertIn("192.168.1.5", c.data["tuning_results"], "valid data is kept")
            self.assertEqual(c.data["tuning_pending"], {})
            self.assertFalse(any("broken" in n for n in os.listdir(d)), "valid JSON is repaired, not backed up")
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_missing_asic_endpoint_is_not_requested_every_poll(self):
        m = FakeMiner(no_asic=True)
        try:
            so.ASIC_CACHE.pop(m.addr, None)
            for _ in range(5):
                so.fetch_info(m.addr)
            self.assertEqual(m.asic_hits, 1)
        finally:
            m.close()

    def test_tuning_refused_without_valid_original_values(self):
        m = FakeMiner(extra={"frequency": 0, "actualFrequency": 0})
        try:
            register(m)
            with self.assertRaises(ValueError):
                so.TUNER.start(m.addr, dict(FAST))
        finally:
            m.close()


class TestHttpRound2(unittest.TestCase):
    PORT = APP_PORT

    @classmethod
    def setUpClass(cls):
        cls.srv = so.Server(("127.0.0.1", APP_PORT), so.Handler)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def post(self, path, body):
        c = http.client.HTTPConnection("127.0.0.1", APP_PORT, timeout=10)
        c.request("POST", path, body=json.dumps(body),
                  headers={"Host": f"127.0.0.1:{APP_PORT}", "Content-Type": "application/json"})
        r = c.getresponse()
        return r.status, json.loads(r.read() or b"{}")

    def test_restart_blocked_while_tuning(self):
        m = FakeMiner()
        try:
            register(m)
            so.TUNER.start(m.addr, dict(FAST, warmupSec=1800))
            st, j = self.post("/api/restart", {"ip": m.addr})
            self.assertEqual(st, 400)
            self.assertEqual(m.restarts, 0)
            so.TUNER.stop(m.addr)
            wait_job(m.addr)
        finally:
            m.close()

    def test_negative_content_length_does_not_hang(self):
        c = http.client.HTTPConnection("127.0.0.1", APP_PORT, timeout=5)
        c.putrequest("POST", "/api/alarm/test", skip_host=True)
        c.putheader("Host", f"127.0.0.1:{APP_PORT}")
        c.putheader("Content-Type", "application/json")
        c.putheader("Content-Length", "-1")
        c.endheaders()
        self.assertEqual(c.getresponse().status, 400)

    def test_history_is_downsampled(self):
        with so.S.lock:
            so.S.history = [{"t": i, "hashrate": i, "power": 1} for i in range(6000)]
        c = http.client.HTTPConnection("127.0.0.1", APP_PORT, timeout=10)
        c.request("GET", "/api/history", headers={"Host": f"127.0.0.1:{APP_PORT}"})
        pts = json.loads(c.getresponse().read())
        self.assertLessEqual(len(pts), 501)
        self.assertEqual(pts[-1]["t"], 5999)
        with so.S.lock:
            so.S.history = []

    def test_device_add_rejects_hostnames_and_ipv6(self):
        for bad in ("evil.com", "http://192.168.1.5", "fe80::1", "192.168.1.5/x"):
            st, _ = self.post("/api/devices/add", {"ip": bad})
            self.assertEqual(st, 400, bad)


if __name__ == "__main__":
    unittest.main()
