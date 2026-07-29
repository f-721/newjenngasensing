import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import motor_controller


class MotorControllerGpioFallbackTest(unittest.TestCase):
    def test_setup_motor_is_safe_when_gpio_is_unavailable(self):
        original_gpio = motor_controller.GPIO
        motor_controller.GPIO = None
        try:
            self.assertFalse(motor_controller.setup_motor())
        finally:
            motor_controller.GPIO = original_gpio

    def test_rotary_skips_when_gpio_is_not_ready(self):
        original_gpio = motor_controller.GPIO
        original_ready = motor_controller.gpio_ready
        motor_controller.GPIO = object()
        motor_controller.gpio_ready = False
        try:
            motor_controller.rotary("c", 0.001)
        finally:
            motor_controller.GPIO = original_gpio
            motor_controller.gpio_ready = original_ready


if __name__ == "__main__":
    unittest.main()
