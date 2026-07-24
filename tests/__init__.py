"""Test package setup.

**This file exists to protect the developer's own install.**

``get_data_root()`` is not a pure function: it creates the folder, and since
1.3.0 it also performs the one-time move of an existing install out of
``Documents\\WizardLauncher``. A test that calls it without an override
therefore reaches straight into the real game folder on the machine running
the suite - which is how running the tests once relocated a live install.

Importing this package pins ``WIZARD_LAUNCHER_DATA`` at a throwaway
directory for the whole session, before any test module gets a chance to ask
where the data lives. Tests that specifically care about path resolution use
the pure ``paths._default_data_root()`` instead, and the migration is tested
against temporary directories in :mod:`tests.test_bootstrap_and_paths`.

An externally-set ``WIZARD_LAUNCHER_DATA`` is respected, so a deliberate run
against a specific folder still works.
"""

import atexit
import os
import shutil
import tempfile

if not (os.environ.get("WIZARD_LAUNCHER_DATA") or "").strip():
    _SCRATCH = tempfile.mkdtemp(prefix="wizard-tests-")
    os.environ["WIZARD_LAUNCHER_DATA"] = _SCRATCH
    atexit.register(shutil.rmtree, _SCRATCH, ignore_errors=True)
