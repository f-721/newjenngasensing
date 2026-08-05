import os
import sys
import unittest
from unittest.mock import patch

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

    def test_high_torque_sequence_always_energizes_two_coils(self):
        self.assertEqual(
            len(motor_controller.HIGH_TORQUE_SEQUENCE),
            len(motor_controller.HALF_STEP_SEQUENCE),
        )
        self.assertTrue(
            all(
                sum(pattern) == 2
                for pattern in motor_controller.HIGH_TORQUE_SEQUENCE
            )
        )
        self.assertTrue(motor_controller.USE_HIGH_TORQUE_DRIVE)

    def test_rotary_uses_high_torque_sequence_when_requested(self):
        class FakeGPIO:
            def __init__(self):
                self.values = []

            def output(self, pin, value):
                self.values.append(value)

        original_gpio = motor_controller.GPIO
        original_ready = motor_controller.gpio_ready
        original_phase = motor_controller.motor_phase
        original_deadline = motor_controller.next_step_deadline
        fake_gpio = FakeGPIO()
        motor_controller.GPIO = fake_gpio
        motor_controller.gpio_ready = True
        motor_controller.motor_phase = 0
        motor_controller.next_step_deadline = None
        try:
            with patch.object(motor_controller.time, "sleep"):
                motor_controller.rotary(
                    "c", 0.001, steps=1, high_torque=True
                )
            self.assertEqual(sum(fake_gpio.values), 2)
        finally:
            motor_controller.GPIO = original_gpio
            motor_controller.gpio_ready = original_ready
            motor_controller.motor_phase = original_phase
            motor_controller.next_step_deadline = original_deadline

    def test_reversal_pause_releases_all_coils(self):
        class FakeGPIO:
            def __init__(self):
                self.outputs = []

            def output(self, pin, value):
                self.outputs.append((pin, value))

        original_gpio = motor_controller.GPIO
        original_ready = motor_controller.gpio_ready
        original_deadline = motor_controller.next_step_deadline
        fake_gpio = FakeGPIO()
        motor_controller.GPIO = fake_gpio
        motor_controller.gpio_ready = True
        motor_controller.next_step_deadline = 123.0
        try:
            with patch.object(motor_controller.time, "sleep") as sleep:
                motor_controller.pause_motor_for_reversal()
            self.assertEqual(
                fake_gpio.outputs,
                [(pin, 0) for pin in motor_controller.motorPins],
            )
            self.assertIsNone(motor_controller.next_step_deadline)
            sleep.assert_called_once_with(
                motor_controller.REVERSAL_PAUSE_SECONDS
            )
        finally:
            motor_controller.GPIO = original_gpio
            motor_controller.gpio_ready = original_ready
            motor_controller.next_step_deadline = original_deadline

    def test_step_delay_uses_half_step_count(self):
        for rpm, delay in motor_controller.RPM_STEP_DELAYS.items():
            self.assertEqual(motor_controller.calculate_step_delay(rpm), delay)

    def test_supported_rpms_produce_different_speeds(self):
        delays = [
            motor_controller.calculate_step_delay(rpm)
            for rpm in (10, 20, 30, 40)
        ]
        self.assertEqual(delays, sorted(delays, reverse=True))
        self.assertEqual(len(set(delays)), 4)

    def test_acceleration_approaches_target_without_jumping(self):
        start = motor_controller.STARTUP_STEP_DELAY
        target = motor_controller.RPM_STEP_DELAYS[40]
        next_delay = motor_controller.approach_step_delay(start, target)
        self.assertLess(next_delay, start)
        self.assertGreater(next_delay, target)

    def test_immediate_direction_setting_changes_without_hold(self):
        motor_controller.applied_direction = "c"
        motor_controller.applied_direction_changed_at = 100.0
        with patch.object(motor_controller.random, "choice", return_value="a"):
            direction, immediate = motor_controller.apply_rotation_preferences(
                "c", {"direction": "auto", "hold": False}, now=100.1
            )
        self.assertEqual(direction, "a")
        self.assertTrue(immediate)

    def test_hold_setting_keeps_direction_for_five_seconds(self):
        motor_controller.applied_direction = "c"
        motor_controller.applied_direction_changed_at = 100.0
        with patch.object(motor_controller.random, "choice", return_value="a"):
            direction, immediate = motor_controller.apply_rotation_preferences(
                "c", {"direction": "auto", "hold": True}, now=102.0
            )
        self.assertEqual(direction, "c")
        self.assertFalse(immediate)

    def test_hold_setting_allows_random_change_after_five_seconds(self):
        motor_controller.applied_direction = "c"
        motor_controller.applied_direction_changed_at = 100.0
        with patch.object(motor_controller.random, "choice", return_value="a"):
            direction, immediate = motor_controller.apply_rotation_preferences(
                "c", {"direction": "auto", "hold": True}, now=105.0
            )
        self.assertEqual(direction, "a")
        self.assertEqual(motor_controller.applied_direction_changed_at, 105.0)
        self.assertFalse(immediate)

    def test_step_delay_is_clamped_for_stability(self):
        self.assertEqual(
            motor_controller.calculate_step_delay(1000),
            motor_controller.MIN_STEP_DELAY,
        )
        self.assertIsNone(motor_controller.calculate_step_delay(0))


if __name__ == "__main__":
    unittest.main()
