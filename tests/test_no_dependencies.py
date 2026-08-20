"""
Pin: this project declares no third-party dependencies.

The rule is stated in CONTRIBUTING.md, pyproject.toml and requirements.txt.
Stating it three times is worth nothing on its own, so it is enforced here.
"""

import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


class TestNoRuntimeDependencies(unittest.TestCase):
    def test_pyproject_declares_no_dependencies(self):
        text = (REPO / "pyproject.toml").read_text()
        match = re.search(r"^dependencies\s*=\s*(\[[^\]]*\])", text, re.MULTILINE)
        self.assertIsNotNone(match, "pyproject.toml must declare `dependencies` explicitly")
        self.assertEqual(match.group(1).strip(), "[]",
                         "A third-party dependency was added. See CONTRIBUTING.md: "
                         "an evidence tool's dependency tree is part of its audit surface.")

    def test_requirements_txt_lists_no_packages(self):
        lines = (REPO / "requirements.txt").read_text().splitlines()
        packages = [l for l in lines if l.strip() and not l.strip().startswith("#")]
        self.assertEqual(packages, [],
                         "requirements.txt must stay free of packages; it exists so a "
                         "scanner can distinguish zero dependencies from a missing manifest.")

    def test_no_module_imports_a_third_party_package(self):
        """
        The manifests could both say zero while an import says otherwise.

        Any module importable on this interpreter without being installed is
        either stdlib or local, so an ImportError here is the signal.
        """
        import importlib
        for path in sorted((REPO / "caltopo_evidence").glob("*.py")):
            with self.subTest(module=path.name):
                importlib.import_module(f"caltopo_evidence.{path.stem}"
                                        if path.stem != "__init__" else "caltopo_evidence")


if __name__ == "__main__":
    unittest.main()
