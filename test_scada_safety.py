import datetime
import csv
import json
import os
import tempfile
import unittest

from test_config_sync import scada


class VoltageMonitoringMappingTests(unittest.TestCase):
    def test_three_phase_addresses_match_authoritative_device_map(self):
        expected = {
            "Uab": (6402, 16394),
            "Ubc": (6402, 16395),
            "Uca": (6402, 16396),
        }
        actual = {
            row["Physical_Name"]: (
                row["ASDU_INT"],
                row["IOA_INT"],
            )
            for row in scada.SETTING_REGISTRY
            if (
                row.get("Device_Addr") == "10.100.163.7"
                and row.get("Bay_Name") == "Voltage Monitoring"
                and row.get("Physical_Name") in expected
            )
        }
        self.assertEqual(actual, expected)


class BreakerMappingTests(unittest.TestCase):
    def setUp(self):
        scada._load_status_settings()
        self.original_values = dict(scada.STATUS_VALUE_STORE)
        self.original_online = dict(scada.VINCI_STATUS)
        self.original_data = {
            ip: dict(values) for ip, values in scada.DATA_STORE.items()
        }
        with open(
            scada.MONITOR_TV_CONFIG_PATH, "r", encoding="utf-8-sig",
        ) as handle:
            self.config = json.load(handle)
        with open(
            os.path.join(scada.APP_DIR, "UIRealtimeBindings.json"),
            "r",
            encoding="utf-8-sig",
        ) as handle:
            self.persisted_statuses = json.load(handle)["statuses"]

    def tearDown(self):
        scada.STATUS_VALUE_STORE.clear()
        scada.STATUS_VALUE_STORE.update(self.original_values)
        scada.VINCI_STATUS.clear()
        scada.VINCI_STATUS.update(self.original_online)
        scada.DATA_STORE.clear()
        scada.DATA_STORE.update(self.original_data)

    def test_monitor_breakers_match_authoritative_status_addresses(self):
        expected = {
            ("G1", "j-main"): (258, 2, None, 1),
            ("G1", "j-generator"): (1537, 2, 1, 1),
            ("G1", "j-transformer"): (770, 4, 3, 0),
            ("G1", "j-transformer-bottom"): (770, 6, 5, 0),
            ("G2", "j-main"): (514, 2, None, 1),
            ("G2", "j-generator"): (2049, 2, 1, 1),
            ("G2", "j-transformer"): (1026, 4, 3, 0),
            ("G2", "j-transformer-bottom"): (1026, 6, 5, 0),
        }
        persisted_keys = {
            ("G1", "j-main"): "system:breaker:wire_1",
            ("G1", "j-generator"): "system:breaker:generator_1",
            ("G1", "j-transformer"): "system:breaker:aux_1_hv",
            ("G1", "j-transformer-bottom"): "system:breaker:aux_1_lv",
            ("G2", "j-main"): "system:breaker:wire_2",
            ("G2", "j-generator"): "system:breaker:generator_2",
            ("G2", "j-transformer"): "system:breaker:aux_2_hv",
            ("G2", "j-transformer-bottom"): "system:breaker:aux_2_lv",
        }
        for (panel, rectangle), wanted in expected.items():
            configured = self.config[panel]["rectangles"][rectangle]
            runtime = scada._generator_breaker_defaults(panel)[rectangle]
            for binding in (configured, runtime):
                open_binding = binding.get("open_binding", {})
                actual = (
                    int(binding["ASDU"]),
                    int(binding["IOA"]),
                    (
                        int(open_binding["IOA"])
                        if open_binding else None
                    ),
                    int(binding["Reverse"]),
                )
                self.assertEqual(
                    actual, wanted, f"{panel} {rectangle}",
                )
            persisted = self.persisted_statuses[
                persisted_keys[(panel, rectangle)]
            ]
            self.assertEqual(
                (
                    int(persisted["ASDU"]),
                    int(persisted["IOA"]),
                    int(persisted["Reverse"]),
                ),
                (wanted[0], wanted[1], wanted[3]),
                f"persisted {panel} {rectangle}",
            )

    def test_field_confirmed_active_low_generator_contacts_render_closed(self):
        for panel in ("G1", "G2"):
            binding = self.config[panel]["rectangles"]["j-generator"]
            ip = binding["Device_Addr"]
            scada.VINCI_STATUS[ip] = True
            scada.STATUS_VALUE_STORE[
                (ip, "info", binding["Info_Name"])
            ] = 0
            open_binding = binding["open_binding"]
            scada.STATUS_VALUE_STORE[
                (ip, "info", open_binding["Info_Name"])
            ] = 1

            state = scada._digital_breaker_snapshot(binding)

            self.assertEqual(state["state"], "CLOSED")
            self.assertEqual(state["color"], "#ff2020")
            self.assertEqual(state["quality"], "GOOD")

    def test_run_monitor_uses_same_digital_generator_state(self):
        binding = scada._run_monitor_breaker_binding("brk_g1")
        ip = binding["Device_Addr"]
        open_binding = scada._companion_open_status_binding(binding)
        scada.VINCI_STATUS[ip] = True
        scada.STATUS_VALUE_STORE[(ip, "info", binding["Info_Name"])] = 0
        scada.STATUS_VALUE_STORE[
            (ip, "info", open_binding["Info_Name"])
        ] = 1
        scada.DATA_STORE.setdefault(ip, {})["Ia"] = 0.0

        state_without_current = scada._run_monitor_breaker_snapshot("brk_g1")
        scada.DATA_STORE[ip]["Ia"] = 1000.0
        state_with_current = scada._run_monitor_breaker_snapshot("brk_g1")

        self.assertEqual(state_without_current["state"], "CLOSED")
        self.assertEqual(state_with_current["state"], "CLOSED")
        self.assertEqual(
            state_without_current["effective_yx"],
            state_with_current["effective_yx"],
        )


class BreakerReportTests(unittest.TestCase):
    def test_report_records_digital_state_and_quality(self):
        with tempfile.TemporaryDirectory() as temporary:
            ui = scada.SCADAMainUI.__new__(scada.SCADAMainUI)
            ui.rectangle_report_states = {}
            ui.rectangle_report_file = os.path.join(
                temporary, "Report_Rectangle_Status.csv",
            )
            ui._record_rectangle_transition(
                "run:brk_g1",
                {
                    "page": "Run Monitor",
                    "label": "Generator 1 breaker",
                    "Device_Addr": "10.100.163.2",
                    "Bay_Name": "1# Measure Control Box",
                    "Physical_Name": "1# G circuit breaker close position",
                    "ASDU": 1537,
                    "IOA": 2,
                    "operator": "=",
                    "threshold": 1,
                },
                1,
                True,
                state="CLOSED",
                quality="GOOD",
            )
            with open(
                ui.rectangle_report_file,
                "r",
                encoding="utf-8-sig",
                newline="",
            ) as handle:
                rows = list(csv.DictReader(handle))

        self.assertEqual(rows[0]["State"], "CLOSED")
        self.assertEqual(rows[0]["Quality"], "GOOD")
        self.assertEqual(rows[0]["Physical_Name"], (
            "1# G circuit breaker close position"
        ))


class CredentialSecurityTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.original_path = scada.AUTH_ACCOUNTS_PATH
        scada.AUTH_ACCOUNTS_PATH = os.path.join(
            self.temporary.name, "UserAccounts.json",
        )

    def tearDown(self):
        scada.AUTH_ACCOUNTS_PATH = self.original_path
        self.temporary.cleanup()

    def test_passwords_are_salted_hashed_and_verified(self):
        password = "Plant-Admin-2026"
        scada._auth_set_password("Admin", password)

        with open(
            scada.AUTH_ACCOUNTS_PATH, "r", encoding="utf-8",
        ) as handle:
            payload = json.load(handle)

        self.assertNotIn(password, json.dumps(payload))
        self.assertTrue(scada._auth_verify_password("Admin", password))
        self.assertFalse(
            scada._auth_verify_password("Admin", "Wrong-Admin-2026"),
        )
        self.assertEqual(scada._auth_configured_roles(), {"Admin"})

    def test_weak_password_is_rejected(self):
        with self.assertRaises(ValueError):
            scada._auth_set_password("Admin", "password")


class RelayAuthenticationTests(unittest.TestCase):
    def setUp(self):
        scada.RELAY_SEEN_NONCES.clear()
        self.token = "a" * 64
        self.path = "/RealtimeSnapshot.json"
        self.timestamp = 1_700_000_000
        self.headers = scada._relay_auth_headers(
            self.path,
            self.token,
            timestamp=self.timestamp,
            nonce="b" * 32,
        )

    def test_request_proof_is_path_bound_fresh_and_single_use(self):
        self.assertTrue(scada._relay_auth_valid(
            "GET", self.path, self.token, self.headers,
            now=self.timestamp, consume_nonce=True,
        ))
        self.assertFalse(scada._relay_auth_valid(
            "GET", self.path, self.token, self.headers,
            now=self.timestamp, consume_nonce=True,
        ))
        self.assertFalse(scada._relay_auth_valid(
            "GET", "/ConfigSync.json", self.token, self.headers,
            now=self.timestamp,
        ))
        self.assertFalse(scada._relay_auth_valid(
            "GET", self.path, self.token, self.headers,
            now=self.timestamp + scada.RELAY_AUTH_WINDOW_SECONDS + 1,
        ))

    def test_response_body_is_authenticated(self):
        body = b'{"ok":true}'
        signature = scada._relay_response_signature(
            self.path,
            self.headers["X-SCADA-Auth-Nonce"],
            body,
            self.token,
        )

        class Response:
            headers = {"X-SCADA-Response-Signature": signature}

            @staticmethod
            def read():
                return body

        self.assertEqual(
            scada._relay_decode_json_response(
                Response(), self.path, self.token, self.headers,
            ),
            {"ok": True},
        )


class ReportRetentionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.originals = {
            "APP_DIR": scada.APP_DIR,
            "REPORT_MAX_FILE_BYTES": scada.REPORT_MAX_FILE_BYTES,
            "REPORT_RETENTION_DAYS": scada.REPORT_RETENTION_DAYS,
            "REPORT_LAST_RETENTION_DATE": scada.REPORT_LAST_RETENTION_DATE,
            "_communication_log": getattr(scada, "_communication_log"),
        }
        scada.APP_DIR = self.temporary.name
        scada.REPORT_MAX_FILE_BYTES = 64
        scada.REPORT_RETENTION_DAYS = 2
        scada.REPORT_LAST_RETENTION_DATE = None
        scada._communication_log = lambda *_args, **_kwargs: None

    def tearDown(self):
        for name, value in self.originals.items():
            setattr(scada, name, value)
        self.temporary.cleanup()

    def test_oversized_report_rotates_and_stale_archive_expires(self):
        now = datetime.datetime(2026, 10, 29, 16, 0, 0)
        path = os.path.join(self.temporary.name, "Report_Test.csv")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("Date,Time,Value\n" + ("x" * 128))
        epoch = now.timestamp()
        os.utime(path, (epoch, epoch))

        scada._report_prepare_file(
            path, ["Date", "Time", "Value"], now,
        )

        paths = scada._report_history_paths(path)
        self.assertEqual(paths[0], path)
        self.assertEqual(len(paths), 2)
        with open(path, "r", encoding="utf-8-sig") as handle:
            self.assertEqual(handle.readline().strip(), "Date,Time,Value")

        stale = os.path.join(
            self.temporary.name, "Report_Test.2020-01-01.csv",
        )
        with open(stale, "w", encoding="utf-8") as handle:
            handle.write("Date,Time,Value\n")
        scada.REPORT_LAST_RETENTION_DATE = None
        scada._report_enforce_retention(now)
        self.assertFalse(os.path.exists(stale))


if __name__ == "__main__":
    unittest.main()
