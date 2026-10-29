import contextlib
import importlib.util
import io
import json
import os
import tempfile
import unittest


SOURCE = os.path.join(
    os.path.dirname(__file__),
    "SCADA_BONT_V50_FIELD_MAP (2) - RR FIXNEW01.py",
)
with contextlib.redirect_stdout(io.StringIO()):
    spec = importlib.util.spec_from_file_location(
        "scada_sync_test_module", SOURCE,
    )
    scada = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(scada)


class ConfigSyncTests(unittest.TestCase):
    def setUp(self):
        self.active = {"ip": "10.100.163.150"}
        scada._local_scada_ip = lambda: self.active["ip"]
        scada._communication_config = lambda: {
            "master_ip": "10.100.163.150",
            "peer_ip": "10.100.163.149",
            "relay_port": 5001,
            "relay_token": "test-token",
        }

    def select(self, directory, ip):
        self.active["ip"] = ip
        scada.APP_DIR = directory
        scada.CONFIG_SYNC_STATE_PATH = os.path.join(
            directory, "ConfigSyncState.json",
        )

    @staticmethod
    def write_binding(directory, marker):
        with open(
            os.path.join(directory, "UIRealtimeBindings.json"),
            "w",
            encoding="utf-8",
        ) as handle:
            json.dump(
                {"variables": {"marker": marker}, "statuses": {}},
                handle,
            )

    @staticmethod
    def read_marker(directory):
        with open(
            os.path.join(directory, "UIRealtimeBindings.json"),
            "r",
            encoding="utf-8",
        ) as handle:
            return json.load(handle)["variables"]["marker"]

    def test_bidirectional_persistence_and_conflict_convergence(self):
        with tempfile.TemporaryDirectory() as temp:
            pc1 = os.path.join(temp, "pc1")
            pc2 = os.path.join(temp, "pc2")
            os.mkdir(pc1)
            os.mkdir(pc2)
            self.write_binding(pc1, "pc1-initial")
            self.write_binding(pc2, "pc2-initial")

            self.select(pc1, "10.100.163.150")
            pc1_initial = scada._config_sync_snapshot()
            self.select(pc2, "10.100.163.149")
            scada._config_sync_snapshot()
            self.assertEqual(
                scada._apply_config_sync_snapshot(pc1_initial),
                ["UIRealtimeBindings.json"],
            )
            self.assertEqual(
                self.read_marker(pc2), "pc1-initial",
            )

            self.write_binding(pc2, "edited-on-pc2")
            pc2_update = scada._config_sync_snapshot()
            self.select(pc1, "10.100.163.150")
            self.assertEqual(
                scada._apply_config_sync_snapshot(pc2_update),
                ["UIRealtimeBindings.json"],
            )
            self.assertEqual(
                self.read_marker(pc1), "edited-on-pc2",
            )

            self.write_binding(pc1, "edited-on-pc1")
            pc1_update = scada._config_sync_snapshot()
            self.select(pc2, "10.100.163.149")
            self.assertEqual(
                scada._apply_config_sync_snapshot(pc1_update),
                ["UIRealtimeBindings.json"],
            )
            self.assertEqual(
                self.read_marker(pc2), "edited-on-pc1",
            )

            self.write_binding(pc2, "pc2-conflict")
            pc2_conflict = scada._config_sync_snapshot()
            self.select(pc1, "10.100.163.150")
            self.write_binding(pc1, "pc1-conflict")
            pc1_conflict = scada._config_sync_snapshot()
            self.select(pc2, "10.100.163.149")
            scada._apply_config_sync_snapshot(pc1_conflict)
            self.select(pc1, "10.100.163.150")
            scada._apply_config_sync_snapshot(pc2_conflict)
            self.assertEqual(
                self.read_marker(pc1), "pc1-conflict",
            )
            self.assertEqual(
                self.read_marker(pc2), "pc1-conflict",
            )


if __name__ == "__main__":
    unittest.main()


