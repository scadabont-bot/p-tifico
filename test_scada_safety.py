import datetime
import json
import os
import tempfile
import unittest

from test_config_sync import scada


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
