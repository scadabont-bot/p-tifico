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


class GeneratorFormulaSourceTests(unittest.TestCase):
    EXPECTED_POINTS = {
        "Ia": (16385, 91.5),
        "Ib": (16386, 91.5),
        "Ic": (16387, 91.5),
        "Uab": (16395, 0.219719999994),
        "Ubc": (16396, 0.219719999994),
        "Uca": (16397, 0.219719999994),
        "P": (16398, 951.41999997),
        "Q": (16399, 951.41999997),
        "cos": (16400, 0.036621097),
        "IE": (16417, 18.2367599834),
        "UE": (16418, 10.985999999),
        "f": (16419, 50.073242187497),
    }

    def test_g1_g2_use_local_measure_control_addresses_and_formulas(self):
        generators = (
            ("10.100.163.2", "1# Measure Control Box", 1537),
            ("10.100.163.3", "2# Measure Control Box", 2049),
        )
        for ip, bay, asdu in generators:
            for physical, (ioa, wanted) in self.EXPECTED_POINTS.items():
                with self.subTest(bay=bay, physical=physical):
                    row = next(
                        item
                        for item in scada.SETTING_REGISTRY
                        if item.get("Device_Addr") == ip
                        and item.get("Bay_Name") == bay
                        and item.get("Physical_Name") == physical
                    )
                    self.assertEqual(
                        (row["ASDU_INT"], row["IOA_INT"]),
                        (asdu, ioa),
                    )
                    actual = scada._calculate_formula(1000.0, row, {})
                    self.assertAlmostEqual(actual, wanted, places=9)

    def test_obsolete_generator_cos_aliases_are_not_loaded(self):
        self.assertFalse(any(
            row.get("Bay_Name") in {"1# Generator", "2# Generator"}
            for row in scada.SETTING_REGISTRY
        ))


class MonitorTVFormulaSourceTests(unittest.TestCase):
    def _assert_value_equal(self, actual, wanted, field):
        try:
            self.assertAlmostEqual(
                float(actual), float(wanted), places=12, msg=field,
            )
        except (TypeError, ValueError):
            self.assertEqual(str(actual), str(wanted), field)

    def test_saved_points_take_engineering_metadata_from_settings(self):
        config = scada._monitor_tv_config()
        columns = (
            "Device_Addr", "ASDU", "IOA", "Coefficient", "CTPT",
            "Formula", "Add_Offset", "Unit",
        )
        for panel in ("G1", "G2"):
            for field, binding in config[panel]["variables"].items():
                row = next(
                    item
                    for item in scada.SETTING_REGISTRY
                    if item.get("Bay_Name") == binding.get("Bay_Name")
                    and item.get("Physical_Name")
                    == binding.get("Physical_Name")
                )
                for column in columns:
                    with self.subTest(
                            panel=panel, field=field, column=column):
                        self._assert_value_equal(
                            binding.get(column, ""),
                            row.get(column, ""),
                            column,
                        )

    def test_g1_g2_top_voltages_share_local_voltage_monitor(self):
        config = scada._monitor_tv_config()
        expected = {
            "tk2": (6402, 16394),
            "tk3": (6402, 16395),
            "tk4": (6402, 16396),
        }
        for panel in ("G1", "G2"):
            for field, address in expected.items():
                binding = config[panel]["variables"][field]
                self.assertEqual(binding["Device_Addr"], "10.100.163.7")
                self.assertEqual(binding["Bay_Name"], "Voltage Monitoring")
                self.assertEqual(
                    (int(binding["ASDU"]), int(binding["IOA"])),
                    address,
                )
                self.assertNotIn(
                    "CUSTOM", str(binding["Formula"]).upper(),
                )

    def test_preview_uses_exact_voltage_and_mw_formulas(self):
        config = scada._monitor_tv_config()
        original_raw = dict(scada.RAW_ADDRESS_STORE)
        original_online = dict(scada.VINCI_STATUS)
        try:
            voltage_results = []
            for panel in ("G1", "G2"):
                binding = dict(config[panel]["variables"]["tk2"])
                binding["Display_Field"] = "tk2"
                address = (
                    binding["Device_Addr"],
                    int(binding["ASDU"]),
                    int(binding["IOA"]),
                )
                scada.RAW_ADDRESS_STORE[address] = 10000.0
                scada.VINCI_STATUS[binding["Device_Addr"]] = True
                voltage_results.append(
                    scada._monitor_tv_preview(binding)["actual"]
                )
            self.assertAlmostEqual(
                voltage_results[0], voltage_results[1], places=12,
            )
            self.assertAlmostEqual(
                voltage_results[0],
                10000.0 * 0.00369899941 * 0.034114061388,
                places=12,
            )

            power = dict(config["G1"]["variables"]["ll1"])
            power["Display_Field"] = "ll1"
            power_address = (
                power["Device_Addr"],
                int(power["ASDU"]),
                int(power["IOA"]),
            )
            scada.RAW_ADDRESS_STORE[power_address] = 20000.0
            scada.VINCI_STATUS[power["Device_Addr"]] = True
            self.assertAlmostEqual(
                scada._monitor_tv_preview(power)["actual"],
                20000.0 * 0.031713999999 * 30.0 / 1000.0,
                places=9,
            )
        finally:
            scada.RAW_ADDRESS_STORE.clear()
            scada.RAW_ADDRESS_STORE.update(original_raw)
            scada.VINCI_STATUS.clear()
            scada.VINCI_STATUS.update(original_online)


class GeneratorProtectionSourceTests(unittest.TestCase):
    def test_lamps_use_the_exact_hard_links_from_local_avd_files(self):
        expected = {
            "Generator differential": (
                "02YX05010000A2", "03YX07010000A2",
            ),
            "Stator overload": (
                "02YX05020000BC", "03YX07020000BC",
            ),
            "Negative sequence OC": (
                "02YX05020000BD", "03YX07020000BD",
            ),
            "Complex voltage OC": (
                "02YX05020000B9", "03YX07020000B9",
            ),
            "Over voltage": (
                "02YX05020000B6", "03YX07020000B6",
            ),
            "Stator earthing": (
                "02YX05020000AA", "03YX07020000AA",
            ),
            "Loss of magnetism": (
                "02YX05020000AB", "03YX07020000AB",
            ),
            "Rotor earthing": (
                "02YX05020000B4", "03YX07020000B4",
            ),
        }
        self.assertEqual(
            scada.SCADAMainUI.GENERATOR_PROTECTION_SOURCES,
            expected,
        )
        for info_names in expected.values():
            for ip, info_name in zip(
                    ("10.100.163.2", "10.100.163.3"), info_names):
                matches = [
                    row
                    for row in scada.STATUS_REGISTRY
                    if row.get("Device_Addr") == ip
                    and row.get("Info_Name") == info_name
                ]
                self.assertEqual(len(matches), 1, f"{info_name} {ip}")


class BreakerMappingTests(unittest.TestCase):
    def setUp(self):
        scada._load_status_settings()
        self.original_values = dict(scada.STATUS_VALUE_STORE)
        self.original_online = dict(scada.VINCI_STATUS)
        self.original_data = {
            ip: dict(values) for ip, values in scada.DATA_STORE.items()
        }
        self.config = scada._monitor_tv_config()
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
            ("G1", "j-main"): (258, 2, None, 0),
            ("G1", "j-generator"): (1537, 2, None, 0),
            ("G1", "j-transformer"): (770, 4, 3, 0),
            ("G1", "j-transformer-bottom"): (770, 6, 5, 0),
            ("G2", "j-main"): (514, 2, None, 0),
            ("G2", "j-generator"): (2049, 2, None, 0),
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
                self.assertEqual(
                    binding["true_color"], scada.BREAKER_CLOSED_COLOR,
                )
                self.assertEqual(
                    binding["false_color"], scada.BREAKER_OPEN_COLOR,
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
            self.assertEqual(
                persisted["on_color"], scada.BREAKER_CLOSED_COLOR,
            )
            self.assertEqual(
                persisted["off_color"], scada.BREAKER_OPEN_COLOR,
            )

    def test_authoritative_generator_contact_uses_green_closed_red_open(self):
        for panel in ("G1", "G2"):
            binding = self.config[panel]["rectangles"]["j-generator"]
            ip = binding["Device_Addr"]
            self.assertFalse(scada._companion_open_status_binding(binding))
            scada.VINCI_STATUS[ip] = True

            scada.STATUS_VALUE_STORE[
                (ip, "info", binding["Info_Name"])
            ] = 1
            closed = scada._digital_breaker_snapshot(binding)

            scada.STATUS_VALUE_STORE[
                (ip, "info", binding["Info_Name"])
            ] = 0
            opened = scada._digital_breaker_snapshot(binding)

            self.assertEqual(
                (closed["state"], closed["color"], closed["quality"]),
                ("CLOSED", scada.BREAKER_CLOSED_COLOR, "GOOD"),
            )
            self.assertEqual(
                (opened["state"], opened["color"], opened["quality"]),
                ("OPEN", scada.BREAKER_OPEN_COLOR, "GOOD"),
            )

    def test_run_monitor_uses_same_digital_generator_state(self):
        binding = scada._run_monitor_breaker_binding("brk_g1")
        ip = binding["Device_Addr"]
        self.assertFalse(scada._companion_open_status_binding(binding))
        scada.VINCI_STATUS[ip] = True
        scada.STATUS_VALUE_STORE[(ip, "info", binding["Info_Name"])] = 1
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

    def test_all_breaker_surfaces_use_green_closed_red_open(self):
        surfaces = []
        for display_key in scada.RUN_MONITOR_BREAKER_SOURCE_BY_KEY:
            surfaces.append((
                f"Run Monitor {display_key}",
                scada._run_monitor_breaker_binding(display_key),
            ))
        for binding_key, binding in self.persisted_statuses.items():
            if binding.get("Source_Kind") == "rectangle_status_csv":
                surfaces.append((binding_key, dict(binding)))
        for panel in ("G1", "G2"):
            for rectangle, binding in self.config[panel]["rectangles"].items():
                surfaces.append((f"Monitor TV {panel}/{rectangle}", dict(binding)))

        def raw_for_effective(effective, binding):
            reversed_point = str(
                binding.get("Reverse", 0) or "",
            ).strip().lower() in {"-1", "1", "true", "yes", "on"}
            return int(effective) ^ int(reversed_point)

        for surface, binding in surfaces:
            with self.subTest(surface=surface):
                self.assertTrue(binding, f"{surface} has no digital binding")
                ip = binding["Device_Addr"]
                open_binding = scada._companion_open_status_binding(binding)
                scada.VINCI_STATUS[ip] = True
                scada.STATUS_VALUE_STORE.clear()

                scada.STATUS_VALUE_STORE[
                    (ip, "info", binding["Info_Name"])
                ] = raw_for_effective(1, binding)
                if open_binding:
                    scada.STATUS_VALUE_STORE[
                        (ip, "info", open_binding["Info_Name"])
                    ] = raw_for_effective(0, open_binding)
                closed = scada._digital_breaker_snapshot(binding)

                scada.STATUS_VALUE_STORE[
                    (ip, "info", binding["Info_Name"])
                ] = raw_for_effective(0, binding)
                if open_binding:
                    scada.STATUS_VALUE_STORE[
                        (ip, "info", open_binding["Info_Name"])
                    ] = raw_for_effective(1, open_binding)
                opened = scada._digital_breaker_snapshot(binding)

                self.assertEqual(
                    (closed["state"], closed["color"]),
                    ("CLOSED", scada.BREAKER_CLOSED_COLOR),
                )
                self.assertEqual(
                    (opened["state"], opened["color"]),
                    ("OPEN", scada.BREAKER_OPEN_COLOR),
                )



class Iec104InterrogationTests(unittest.TestCase):
    class RecordingSocket:
        def __init__(self):
            self.frames = []

        def sendall(self, frame):
            self.frames.append(bytes(frame))

    def test_general_interrogation_targets_every_configured_station(self):
        sock = self.RecordingSocket()

        addresses = scada.send_gi(sock, "10.100.163.1")

        self.assertTrue({258, 514, 770, 1026}.issubset(addresses))
        self.assertEqual(len(sock.frames), len(addresses))
        for sequence, (address, frame) in enumerate(
            zip(addresses, sock.frames),
        ):
            self.assertEqual(frame[0], 0x68)
            self.assertEqual(len(frame), frame[1] + 2)
            self.assertEqual(
                ((frame[3] << 8) | frame[2]) >> 1,
                sequence,
            )
            self.assertEqual(frame[6:10], bytes((100, 1, 6, 0)))
            self.assertEqual(frame[10] | (frame[11] << 8), address)
            self.assertEqual(frame[12:15], b"\x00\x00\x00")
            self.assertEqual(frame[15], 20)

    def test_single_point_frame_reaches_digital_status_decoder(self):
        captured = []
        original = scada._set_status_raw_value
        scada._set_status_raw_value = (
            lambda *args, **kwargs: captured.append((args, kwargs))
        )
        try:
            frame = bytes.fromhex(
                "68 0E 00 00 00 00 01 01 03 00 02 01 02 00 00 01"
            )
            scada._parse_iec104_frame(
                "10.100.163.1",
                frame,
                self.RecordingSocket(),
                {
                    "rx_count": 0,
                    "next_rx_seq": 0,
                    "testfr_pending_since": None,
                },
            )
        finally:
            scada._set_status_raw_value = original

        self.assertEqual(len(captured), 1)
        args, kwargs = captured[0]
        self.assertEqual(args[:4], ("10.100.163.1", 258, 2, 1))
        self.assertEqual(kwargs["type_id"], 1)
        self.assertEqual(kwargs["cause"], 3)

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
