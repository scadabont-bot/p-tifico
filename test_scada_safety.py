import copy
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


class MonitorTVFormulaSourceTests(unittest.TestCase):
    def setUp(self):
        self.temp_directory = tempfile.TemporaryDirectory()
        self.original_config_path = scada.MONITOR_TV_CONFIG_PATH
        self.registry_snapshot = copy.deepcopy(scada.SETTING_REGISTRY)
        self.store_snapshots = {
            name: copy.deepcopy(getattr(scada, name))
            for name in (
                "DATA_STORE", "BAY_VALUE_STORE", "RAW_ADDRESS_STORE",
                "RAW_ADDRESS_TIMESTAMP", "VINCI_STATUS",
            )
        }
        scada.MONITOR_TV_CONFIG_PATH = os.path.join(
            self.temp_directory.name, "MonitorTVConfig.json",
        )

    def tearDown(self):
        scada.MONITOR_TV_CONFIG_PATH = self.original_config_path
        with scada.SETTING_LOCK:
            scada.SETTING_REGISTRY[:] = self.registry_snapshot
        with scada.DATA_LOCK:
            for name, snapshot in self.store_snapshots.items():
                store = getattr(scada, name)
                store.clear()
                store.update(snapshot)
        self.temp_directory.cleanup()

    def _assert_value_equal(self, actual, wanted, field):
        try:
            self.assertAlmostEqual(
                float(actual), float(wanted), places=12, msg=field,
            )
        except (TypeError, ValueError):
            self.assertEqual(str(actual), str(wanted), field)


    def test_numeric_save_accepts_real_breaker_default_whitespace(self):
        config = scada._monitor_tv_config()
        self.assertEqual(
            config["G1"]["rectangles"]["j-transformer"][
                "Physical_Name"
            ],
            "breaker  position",
        )
        rectangles_before = {
            panel: copy.deepcopy(config[panel]["rectangles"])
            for panel in ("G1", "G2")
        }

        scada._save_monitor_tv_config(config)

        with open(
            scada.MONITOR_TV_CONFIG_PATH, "r", encoding="utf-8",
        ) as handle:
            saved = json.load(handle)
        self.assertEqual(
            saved["G1"]["rectangles"]["j-transformer"][
                "Physical_Name"
            ],
            "breaker  position",
        )
        for panel in ("G1", "G2"):
            self.assertEqual(
                saved[panel]["rectangles"], rectangles_before[panel],
            )


    def test_manual_points_survive_two_save_load_cycles_and_drive_payload(self):
        config = scada._monitor_tv_config()
        overrides = {
            ("G1", "tk2"): {
                "mode": "manual",
                "source": "voltage",
                "key": "Uab",
                "Bay_Name": "Voltage Monitoring",
                "Physical_Name": "Uab",
                "Info_Name": "Uab",
                "Device_Addr": "10.100.163.2",
                "ASDU": 9101,
                "IOA": 9201,
                "Coefficient": 2.0,
                "CTPT": 3.0,
                "Formula": (
                    "CUSTOM: RAW*COEFFICIENT*CTPT+ADD_OFFSET"
                ),
                "Add_Offset": 4.0,
                "Unit": "manual-kV",
                "Decimals": 4,
            },
            ("G2", "ll1"): {
                "mode": "manual",
                "source": "measure_control2",
                "key": "P",
                "Bay_Name": "2# Measure Control Box",
                "Physical_Name": "P",
                "Info_Name": "P",
                "Device_Addr": "10.100.163.1",
                "ASDU": 9102,
                "IOA": 9202,
                "Coefficient": 4.0,
                "CTPT": 5.0,
                "Formula": (
                    "CUSTOM: RAW*COEFFICIENT*CTPT+ADD_OFFSET"
                ),
                "Add_Offset": 6.0,
                "Unit": "manual-kW",
                "Decimals": 5,
            },
        }
        untouched_variables = {
            panel: {
                field: copy.deepcopy(binding)
                for field, binding in config[panel]["variables"].items()
                if (panel, field) not in overrides
            }
            for panel in ("G1", "G2")
        }
        rectangles_before = {
            panel: copy.deepcopy(config[panel]["rectangles"])
            for panel in ("G1", "G2")
        }
        for (panel, field), values in overrides.items():
            config[panel]["variables"][field].update(values)

        scada._save_monitor_tv_config(config)
        with open(
            scada.MONITOR_TV_CONFIG_PATH, "r", encoding="utf-8",
        ) as handle:
            first_saved = json.load(handle)
        for panel in ("G1", "G2"):
            self.assertEqual(
                first_saved[panel]["rectangles"], rectangles_before[panel],
            )
            self.assertEqual(
                {
                    field: binding
                    for field, binding in first_saved[panel]["variables"].items()
                    if (panel, field) not in overrides
                },
                untouched_variables[panel],
            )
        # Simulate ListSetting/option metadata changing immediately after Save.
        # A manual popup edit owns every displayed field and must not be
        # replaced by a later registry refresh.
        registry_conflicts = {
            ("Voltage Monitoring", "Uab"): {
                "Device_Addr": "198.51.100.41",
                "ASDU": 9411,
                "IOA": 9412,
                "Coefficient": 41.0,
                "CTPT": 42.0,
                "Formula": "MULTIPLY",
                "Add_Offset": 43.0,
                "Unit": "stale-voltage-option",
            },
            ("2# Measure Control Box", "P"): {
                "Device_Addr": "198.51.100.51",
                "ASDU": 9511,
                "IOA": 9512,
                "Coefficient": 51.0,
                "CTPT": 52.0,
                "Formula": "MULTIPLY",
                "Add_Offset": 53.0,
                "Unit": "stale-power-option",
            },
        }
        with scada.SETTING_LOCK:
            for point, conflicting_values in registry_conflicts.items():
                row = next(
                    item for item in scada.SETTING_REGISTRY
                    if (
                        item.get("Bay_Name"),
                        item.get("Physical_Name"),
                    ) == point
                )
                row.update(conflicting_values)
        first_load = scada._monitor_tv_config()
        scada._save_monitor_tv_config(first_load)
        second_load = scada._monitor_tv_config()
        with open(
            scada.MONITOR_TV_CONFIG_PATH, "r", encoding="utf-8",
        ) as handle:
            on_disk = json.load(handle)

        for (panel, field), expected in overrides.items():
            for source_name, source in (
                    ("first load", first_load),
                    ("second load", second_load),
                    ("disk", on_disk)):
                binding = source[panel]["variables"][field]
                for column, wanted in expected.items():
                    with self.subTest(
                            panel=panel, field=field, source=source_name,
                            column=column):
                        self._assert_value_equal(
                            binding.get(column), wanted, column,
                        )

        with scada.DATA_LOCK:
            scada.RAW_ADDRESS_STORE.clear()
            scada.RAW_ADDRESS_TIMESTAMP.clear()
            scada.VINCI_STATUS.clear()
            scada.RAW_ADDRESS_STORE.update({
                ("10.100.163.2", 9101, 9201): 10.0,
                ("10.100.163.1", 9102, 9202): 100.0,
            })
            scada.VINCI_STATUS.update({
                "10.100.163.1": True,
                "10.100.163.2": True,
                # The old source groups are deliberately unavailable.
                "10.100.163.3": False,
                "10.100.163.7": False,
            })
            scada.BAY_VALUE_STORE.setdefault(
                "Voltage Monitoring", {},
            )["Uab"] = 99999.0
            scada.BAY_VALUE_STORE.setdefault(
                "2# Measure Control Box", {},
            )["P"] = 99999.0

        cases = (
            ("G1", "tk2", "10.100.163.2", "W1", 64.0,
             {"decimals": 4, "unit": "manual-kV"}),
            # Power displays have an established fixed kW -> MW conversion.
            # The saved unit round-trips above; the effective payload format
            # deliberately remains MW and is explained by the popup.
            ("G2", "ll1", "10.100.163.3", "W2", 2.006,
             {"decimals": 5, "unit": "MW"}),
        )
        for panel, field, source_ip, prefix, wanted, wanted_format in cases:
            binding = dict(second_load[panel]["variables"][field])
            binding["Display_Field"] = field
            preview = scada._monitor_tv_preview(binding)
            payload = scada._monitor_tv_generator_payload(
                source_ip, prefix, panel,
            )
            with self.subTest(panel=panel, field=field):
                self.assertEqual(preview["raw"], (
                    10.0 if panel == "G1" else 100.0
                ))
                self.assertNotEqual(preview.get("source"), "PC1 relay")
                self.assertAlmostEqual(preview["actual"], wanted, places=12)
                self.assertAlmostEqual(payload[field], wanted, places=12)
                self.assertEqual(
                    payload["variable_formats"][field], wanted_format,
                )

    def test_nonmanual_points_continue_following_registry_metadata(self):
        config = scada._monitor_tv_config()
        config["G1"]["variables"]["tk2"]["mode"] = "selected"
        config["G2"]["variables"]["tk2"]["mode"] = "default"
        scada._save_monitor_tv_config(config)

        with scada.SETTING_LOCK:
            row = next(
                item for item in scada.SETTING_REGISTRY
                if item.get("Bay_Name") == "Voltage Monitoring"
                and item.get("Physical_Name") == "Uab"
            )
            changed = {
                "Device_Addr": "198.51.100.20",
                "ASDU": "9301",
                "IOA": "9302",
                "Coefficient": "7.25",
                "CTPT": "8.5",
                "Formula": "MULTIPLY_CTPT_ADD",
                "Add_Offset": "9.75",
                "Unit": "registry-unit",
            }
            row.update(changed)

        with scada.DATA_LOCK:
            scada.RAW_ADDRESS_STORE.clear()
            scada.RAW_ADDRESS_STORE[
                ("198.51.100.20", 9301, 9302)
            ] = 10.0
            scada.VINCI_STATUS["198.51.100.20"] = True

        loaded = scada._monitor_tv_config()
        for panel, ip, prefix in (
                ("G1", "10.100.163.2", "W1"),
                ("G2", "10.100.163.3", "W2")):
            binding = dict(loaded[panel]["variables"]["tk2"])
            binding["Display_Field"] = "tk2"
            preview = scada._monitor_tv_preview(binding)
            payload = scada._monitor_tv_generator_payload(ip, prefix, panel)
            with self.subTest(panel=panel):
                self.assertEqual(preview["raw"], 10.0)
                self.assertEqual(preview["actual"], 626.0)
                self.assertEqual(payload["tk2"], 626.0)

    def test_manual_missing_raw_never_reuses_old_engineered_alias(self):
        config = scada._monitor_tv_config()
        binding = config["G1"]["variables"]["tk2"]
        binding.update({
            "mode": "manual",
            "Device_Addr": "198.51.100.30",
            "ASDU": 9401,
            "IOA": 9402,
            "Coefficient": 12.0,
            "CTPT": 13.0,
            "Formula": "MULTIPLY_CTPT",
            "Add_Offset": 14.0,
        })
        scada._save_monitor_tv_config(config)
        loaded = scada._monitor_tv_config()
        manual = dict(loaded["G1"]["variables"]["tk2"])
        manual["Display_Field"] = "tk2"

        with scada.DATA_LOCK:
            scada.RAW_ADDRESS_STORE.clear()
            scada.BAY_VALUE_STORE.setdefault(
                "Voltage Monitoring", {},
            )["Uab"] = 54321.0
            scada.DATA_STORE.setdefault(
                "198.51.100.30", {},
            )["Uab"] = 12345.0

        legacy = dict(manual)
        legacy["mode"] = "selected"
        self.assertEqual(
            scada._monitor_tv_relay_engineered_value(legacy, "tk2"),
            54321.0,
        )
        self.assertIsNone(
            scada._monitor_tv_relay_engineered_value(manual, "tk2"),
        )
        for online in (True, False):
            scada.VINCI_STATUS["198.51.100.30"] = online
            preview = scada._monitor_tv_preview(manual)
            with self.subTest(online=online):
                self.assertFalse(preview["available"])
                self.assertIsNone(preview["raw"])
                self.assertIsNone(preview["actual"])
                self.assertNotEqual(preview.get("source"), "PC1 relay")

        payload = scada._monitor_tv_generator_payload(
            "10.100.163.2", "W1", "G1",
        )
        self.assertEqual(payload["tk2"], 0.0)

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
            ("G1", "j-generator"): (1537, 2, 1, 0),
            ("G1", "j-transformer"): (770, 2, None, 0),
            ("G1", "j-transformer-bottom"): (770, 6, 5, 0),
            ("G2", "j-main"): (514, 2, None, 0),
            ("G2", "j-generator"): (2049, 2, 1, 0),
            ("G2", "j-transformer"): (1026, 2, None, 0),
            ("G2", "j-transformer-bottom"): (1026, 6, 5, 0),
        }
        persisted_keys = {
            ("G1", "j-main"): (
                "system:breaker:wire_1", "aux:breaker:wire_1",
            ),
            ("G1", "j-generator"): ("system:breaker:generator_1",),
            ("G1", "j-transformer"): (
                "system:breaker:aux_1_hv", "aux:breaker:aux_1_hv",
            ),
            ("G1", "j-transformer-bottom"): (
                "system:breaker:aux_1_lv", "aux:breaker:aux_1_lv",
            ),
            ("G2", "j-main"): (
                "system:breaker:wire_2", "aux:breaker:wire_2",
            ),
            ("G2", "j-generator"): ("system:breaker:generator_2",),
            ("G2", "j-transformer"): (
                "system:breaker:aux_2_hv", "aux:breaker:aux_2_hv",
            ),
            ("G2", "j-transformer-bottom"): (
                "system:breaker:aux_2_lv", "aux:breaker:aux_2_lv",
            ),
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
                self.assertEqual(
                    binding["contact_mode"],
                    "paired" if wanted[2] is not None else "single",
                )
            persisted_wanted = (
                wanted[0],
                4 if rectangle == "j-transformer" else wanted[1],
                wanted[2],
                wanted[3],
            )
            for persisted_key in persisted_keys[(panel, rectangle)]:
                persisted = self.persisted_statuses[persisted_key]
                self.assertEqual(
                    (
                        int(persisted["ASDU"]),
                        int(persisted["IOA"]),
                        int(persisted["Reverse"]),
                    ),
                    (
                        persisted_wanted[0],
                        persisted_wanted[1],
                        persisted_wanted[3],
                    ),
                    f"persisted {persisted_key}",
                )
                persisted_open = persisted.get("open_binding", {})
                self.assertEqual(
                    int(persisted_open["IOA"]) if persisted_open else None,
                    persisted_wanted[2],
                    f"persisted open contact {persisted_key}",
                )
                self.assertEqual(
                    persisted["contact_mode"],
                    "paired" if persisted_wanted[2] is not None else "single",
                )
                self.assertEqual(
                    persisted["on_color"], scada.BREAKER_CLOSED_COLOR,
                )
                self.assertEqual(
                    persisted["off_color"], scada.BREAKER_OPEN_COLOR,
                )

    def test_paired_breaker_contacts_cover_all_four_combinations(self):
        binding = self.config["G1"]["rectangles"]["j-generator"]
        open_binding = scada._companion_open_status_binding(binding)
        self.assertEqual(binding["contact_mode"], "paired")
        self.assertEqual(open_binding["Info_Name"], "02YX0601000001")
        ip = binding["Device_Addr"]
        close_key = (ip, "info", binding["Info_Name"])
        open_key = (ip, "info", open_binding["Info_Name"])
        scada.VINCI_STATUS[ip] = True

        cases = {
            (1, 0): ("CLOSED", True, scada.BREAKER_CLOSED_COLOR, "GOOD"),
            (0, 1): ("OPEN", False, scada.BREAKER_OPEN_COLOR, "GOOD"),
            (0, 0): (
                "UNKNOWN", None, scada.BREAKER_UNKNOWN_COLOR,
                "CONTACT INDETERMINATE",
            ),
            (1, 1): (
                "INVALID", None, scada.BREAKER_INVALID_COLOR,
                "CONTACT CONFLICT",
            ),
        }
        for contacts, wanted in cases.items():
            with self.subTest(close=contacts[0], opened=contacts[1]):
                scada.STATUS_VALUE_STORE[close_key] = contacts[0]
                scada.STATUS_VALUE_STORE[open_key] = contacts[1]
                state = scada._digital_breaker_snapshot(binding)
                self.assertEqual(
                    (
                        state["state"], state["active"],
                        state["color"], state["quality"],
                    ),
                    wanted,
                )

    def test_main_and_high_voltage_breakers_remain_single_contact(self):
        for panel in ("G1", "G2"):
            for rectangle in ("j-main", "j-transformer"):
                binding = self.config[panel]["rectangles"][rectangle]
                with self.subTest(panel=panel, rectangle=rectangle):
                    self.assertEqual(binding["contact_mode"], "single")
                    self.assertFalse(
                        scada._companion_open_status_binding(binding)
                    )

    def test_textual_close_and_open_values_drive_actual_breaker_state(self):
        binding = self.config["G1"]["rectangles"]["j-main"]
        ip = binding["Device_Addr"]
        key = (ip, "info", binding["Info_Name"])
        scada.VINCI_STATUS[ip] = True

        scada.STATUS_VALUE_STORE[key] = "Close"
        closed = scada._digital_breaker_snapshot(binding)
        self.assertEqual(
            (closed["raw_yx"], closed["state"], closed["color"]),
            ("Close", "CLOSED", scada.BREAKER_CLOSED_COLOR),
        )

        scada.STATUS_VALUE_STORE[key] = "Open"
        opened = scada._digital_breaker_snapshot(binding)
        self.assertEqual(
            (opened["raw_yx"], opened["state"], opened["color"]),
            ("Open", "OPEN", scada.BREAKER_OPEN_COLOR),
        )

    def test_unrecognized_text_is_unknown_not_open(self):
        binding = self.config["G1"]["rectangles"]["j-main"]
        ip = binding["Device_Addr"]
        scada.VINCI_STATUS[ip] = True
        scada.STATUS_VALUE_STORE[
            (ip, "info", binding["Info_Name"])
        ] = "Not Available"
        state = scada._digital_breaker_snapshot(binding)
        self.assertEqual(
            (
                state["state"],
                state["active"],
                state["color"],
                state["quality"],
            ),
            ("UNKNOWN", None, scada.BREAKER_UNKNOWN_COLOR, "INVALID VALUE"),
        )

    def test_offline_breaker_is_unknown_not_open(self):
        binding = self.config["G1"]["rectangles"]["j-main"]
        scada.VINCI_STATUS[binding["Device_Addr"]] = False
        state = scada._digital_breaker_snapshot(binding)
        self.assertEqual(
            (
                state["state"],
                state["active"],
                state["color"],
                state["quality"],
            ),
            ("UNKNOWN", None, scada.BREAKER_UNKNOWN_COLOR, "RTU OFFLINE"),
        )

    def test_run_monitor_current_rule_uses_displayed_ampere_boundary(self):
        cases = (
            (5.87, (5.87, True, "#ff2020", "1")),
            (0.006, (0.01, True, "#ff2020", "1")),
            (0.004, (0.0, False, "#14d84a", "0")),
            (0.0, (0.0, False, "#14d84a", "0")),
            (-1.0, (-1.0, False, "#14d84a", "0")),
            (None, (0.0, False, "#14d84a", "0")),
            (float("nan"), (0.0, False, "#14d84a", "0")),
        )
        for current, wanted in cases:
            with self.subTest(current=current):
                self.assertEqual(scada._feeder_breaker_visual(current), wanted)

    def test_digital_breaker_surfaces_use_green_closed_red_open(self):
        self.assertEqual(scada.BREAKER_CLOSED_COLOR, "#14d84a")
        self.assertEqual(scada.BREAKER_OPEN_COLOR, "#ff2020")
        surfaces = []
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
    def test_run_monitor_reports_current_transitions_as_on_off(self):
        with tempfile.TemporaryDirectory() as temporary:
            ui = scada.SCADAMainUI.__new__(scada.SCADAMainUI)
            ui.status_indicators = {}
            ui.status_indicator_value_items = {}
            ui.run_canvas = object()
            ui.rectangle_report_states = {}
            ui.rectangle_report_file = os.path.join(
                temporary, "Report_Rectangle_Status.csv",
            )
            for current in (0.0, 0.004, 0.006, 0.0):
                ui._set_breaker_current("brk_g1", current)
            with open(
                ui.rectangle_report_file,
                "r",
                encoding="utf-8-sig",
                newline="",
            ) as handle:
                rows = list(csv.DictReader(handle))
        self.assertEqual([row["State"] for row in rows], ["OFF", "ON", "OFF"])
        self.assertEqual([float(row["Value"]) for row in rows], [0.0, 0.01, 0.0])
        for row in rows:
            self.assertEqual(row["Physical_Name"], "Ia")
            self.assertEqual(row["Operator"], ">")
            self.assertEqual(float(row["Threshold"]), 0.0)

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
        self.original_seed_path = scada.AUTH_CREDENTIAL_SEED_PATH
        scada.AUTH_ACCOUNTS_PATH = os.path.join(
            self.temporary.name, "UserAccounts.json",
        )
        scada.AUTH_CREDENTIAL_SEED_PATH = os.path.join(
            self.temporary.name, "UserAccounts.seed.json",
        )
        with open(
            os.path.join(scada.APP_DIR, "UserAccounts.seed.json"),
            "r",
            encoding="utf-8",
        ) as handle:
            seed = json.load(handle)
        with open(
            scada.AUTH_CREDENTIAL_SEED_PATH, "w", encoding="utf-8",
        ) as handle:
            json.dump(seed, handle)

    def tearDown(self):
        scada.AUTH_ACCOUNTS_PATH = self.original_path
        scada.AUTH_CREDENTIAL_SEED_PATH = self.original_seed_path
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

    def test_password_shorter_than_six_characters_is_rejected(self):
        with self.assertRaises(ValueError):
            scada._auth_set_password("Admin", "12345")

    def test_six_digit_password_is_allowed_for_every_role(self):
        for role in scada.AUTH_ROLES:
            scada._auth_set_password(role, "123456")
            self.assertTrue(scada._auth_verify_password(role, "123456"))
        self.assertEqual(
            scada._auth_configured_roles(),
            set(scada.AUTH_ROLES),
        )

    def test_versioned_seed_replaces_legacy_passwords_exactly_once(self):
        legacy_password = "Legacy-Password-2026"
        for role in scada.AUTH_ROLES:
            scada._auth_set_password(role, legacy_password)
        self.assertTrue(scada._auth_apply_credential_seed())
        for role in scada.AUTH_ROLES:
            self.assertTrue(scada._auth_verify_password(role, "123456"))
            self.assertFalse(
                scada._auth_verify_password(role, legacy_password)
            )

        replacement = "654321"
        scada._auth_set_password("Operator", replacement)
        self.assertFalse(scada._auth_apply_credential_seed())
        self.assertTrue(
            scada._auth_verify_password("Operator", replacement)
        )
        self.assertFalse(
            scada._auth_verify_password("Operator", "123456")
        )


    def test_packaged_credentials_reset_every_role_to_requested_password(self):
        temporary_path = scada.AUTH_ACCOUNTS_PATH
        scada.AUTH_ACCOUNTS_PATH = os.path.join(
            scada.APP_DIR, "UserAccounts.json",
        )
        try:
            self.assertEqual(
                scada._auth_configured_roles(),
                set(scada.AUTH_ROLES),
            )
            for role in scada.AUTH_ROLES:
                self.assertTrue(
                    scada._auth_verify_password(role, "123456"),
                    role,
                )
            with open(
                scada.AUTH_ACCOUNTS_PATH, "r", encoding="utf-8",
            ) as handle:
                payload = json.load(handle)
            self.assertNotIn("123456", json.dumps(payload))
            with open(
                scada.AUTH_CREDENTIAL_SEED_PATH,
                "r",
                encoding="utf-8",
            ) as handle:
                seed = json.load(handle)
            self.assertNotIn("123456", json.dumps(seed))
            self.assertEqual(
                payload[scada.AUTH_APPLIED_SEED_FIELD],
                seed["revision"],
            )
        finally:
            scada.AUTH_ACCOUNTS_PATH = temporary_path


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
