"""
CLI-level error handling.

These exist because a mutation leaked: deleting `InvalidMapId` from the
handler in `main()` broke no test, even though the effect on a user is a raw
traceback instead of the message the exception was written to give them.
"""

import io
import unittest
from contextlib import redirect_stderr
from unittest import mock

from caltopo_evidence import cli

_CREDS_ENV = {
    "CALTOPO_CREDENTIAL_ID": "dummy",
    "CALTOPO_CREDENTIAL_SECRET": "ZHVtbXk=",
    "CALTOPO_TEAM_ID": "",
}


def _run(argv):
    """Run the CLI with credentials present, capturing stderr."""
    err = io.StringIO()
    with mock.patch.dict("os.environ", _CREDS_ENV, clear=False):
        with redirect_stderr(err):
            code = cli.main(argv)
    return code, err.getvalue()


class TestInvalidMapIdIsReportedNotRaised(unittest.TestCase):
    def test_pasted_url_exits_cleanly(self):
        code, err = _run(["extract", "https://caltopo.com/m/ABC123"])
        self.assertEqual(code, 2)
        self.assertIn("is not a CalTopo map id", err)
        self.assertNotIn("Traceback", err)

    def test_path_traversal_exits_cleanly(self):
        code, err = _run(["extract", "../../escape"])
        self.assertEqual(code, 2)
        self.assertIn("is not a CalTopo map id", err)
        self.assertNotIn("Traceback", err)

    def test_absolute_path_exits_cleanly(self):
        code, err = _run(["extract", "/tmp/absolute"])
        self.assertEqual(code, 2)
        self.assertNotIn("Traceback", err)

    def test_deprecated_host_reminder_reaches_the_user(self):
        code, err = _run(["extract", "https://sartopo.com/m/ABC123"])
        self.assertEqual(code, 2)
        self.assertIn("sartopo.com is deprecated", err)
        self.assertIn("caltopo.com", err)


if __name__ == "__main__":
    unittest.main()
