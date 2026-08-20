"""Tests for the read-only CalTopo client's input validation."""

import unittest

from caltopo_evidence import client

CREDS = client.Credentials(credential_id="id", credential_secret="c2VjcmV0")


class TestBaseUrlValidation(unittest.TestCase):
    def test_https_origin_is_accepted(self):
        c = client.CalTopoReadOnlyClient(CREDS, base_url="https://caltopo.com")
        self.assertEqual(c.base_url, "https://caltopo.com")

    def test_default_is_https(self):
        self.assertTrue(client.DEFAULT_BASE_URL.startswith("https://"))
        c = client.CalTopoReadOnlyClient(CREDS)
        self.assertEqual(c.base_url, client.DEFAULT_BASE_URL.rstrip("/"))

    def test_trailing_slashes_are_stripped(self):
        c = client.CalTopoReadOnlyClient(CREDS, base_url="https://caltopo.com///")
        self.assertEqual(c.base_url, "https://caltopo.com")

    def test_file_scheme_is_refused(self):
        """urlopen would happily read a local path and report it as a response."""
        with self.assertRaises(client.CalTopoError):
            client.CalTopoReadOnlyClient(CREDS, base_url="file:///etc/passwd")

    def test_plain_http_is_refused(self):
        """Requests carry a signed credential; cleartext is not an option."""
        with self.assertRaises(client.CalTopoError):
            client.CalTopoReadOnlyClient(CREDS, base_url="http://caltopo.com")

    def test_other_schemes_are_refused(self):
        for bad in ("ftp://caltopo.com", "gopher://caltopo.com",
                    "caltopo.com", "", "//caltopo.com"):
            with self.subTest(base_url=bad):
                with self.assertRaises(client.CalTopoError):
                    client.CalTopoReadOnlyClient(CREDS, base_url=bad)

    def test_scheme_check_is_case_insensitive(self):
        """A scheme is case-insensitive per RFC 3986, so HTTPS:// must pass."""
        c = client.CalTopoReadOnlyClient(CREDS, base_url="HTTPS://caltopo.com")
        self.assertEqual(c.base_url, "HTTPS://caltopo.com")

    def test_https_must_be_the_scheme_not_merely_present(self):
        """A bare substring check would let this through."""
        with self.assertRaises(client.CalTopoError):
            client.CalTopoReadOnlyClient(CREDS, base_url="file:///tmp/https://x")


class TestReadOnlyByConstruction(unittest.TestCase):
    def test_no_write_verbs_anywhere_in_the_module(self):
        """
        The read-only guarantee in SECURITY.md is structural, so it is pinned
        against the source rather than described in a docstring.
        """
        import inspect
        src = inspect.getsource(client)
        stripped = "\n".join(l.split("#")[0] for l in src.splitlines())
        for verb in ('"POST"', '"PUT"', '"DELETE"', '"PATCH"',
                     "method='POST'", 'method="POST"'):
            self.assertNotIn(verb, stripped, f"write verb {verb} appeared in client.py")


if __name__ == "__main__":
    unittest.main()
