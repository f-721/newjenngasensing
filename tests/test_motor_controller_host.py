import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import motor_controller


class MotorControllerHostTest(unittest.TestCase):
    def test_api_host_defaults_to_localhost(self):
        os.environ.pop("API_HOST", None)
        import importlib
        importlib.reload(motor_controller)
        self.assertEqual(motor_controller.API_HOST, "http://127.0.0.1:8080")


if __name__ == "__main__":
    unittest.main()
