# conftest.py - pytest configuration for inline prediction tests
# Uses NiceGUI's built-in testing User class for in-process testing

import pytest

# Import the nicegui user fixture and reset globals
pytest_plugins = ["nicegui.testing.plugin"]
