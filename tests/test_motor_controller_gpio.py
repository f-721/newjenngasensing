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

    def test_direction_labels_use_opposite_phase_deltas(self):
        self.assertEqual(motor_controller.CLOCKWISE_PHASE_DELTA, -1)
        self.assertEqual(motor_controller.COUNTERCLOCKWISE_PHASE_DELTA, 1)
        self.assertNotEqual(
            motor_controller.phase_delta_for_direction("c"),
            motor_controller.phase_delta_for_direction("a"),
        )

    def test_step_delay_uses_half_step_count(self):
        for rpm, delay in motor_controller.RPM_STEP_DELAYS.items():
            self.assertEqual(motor_controller.calculate_step_delay(rpm), delay)

    def test_40_rpm_uses_the_stable_30_rpm_step_delay(self):
        delays = [
            motor_controller.calculate_step_delay(rpm)
            for rpm in (10, 20, 30, 40)
        ]
        self.assertEqual(delays, sorted(delays, reverse=True))
        self.assertEqual(delays[-1], delays[-2])

    def test_a_direction_uses_slower_torque_priority_step_delay(self):
        c_delay = motor_controller.step_delay_for_direction("c", 20)
        a_delay = motor_controller.step_delay_for_direction("a", 20)

        self.assertEqual(
            a_delay,
            c_delay * motor_controller.COUNTERCLOCKWISE_DELAY_MULTIPLIER,
        )

    def test_a_direction_uses_minimum_start_speed_at_10_rpm(self):
        self.assertEqual(
            motor_controller.step_delay_for_direction("a", 10),
            motor_controller.step_delay_for_direction("a", 20),
        )

    def test_acceleration_approaches_target_without_jumping(self):
        start = motor_controller.STARTUP_STEP_DELAY
        target = motor_controller.RPM_STEP_DELAYS[40]
        next_delay = motor_controller.approach_step_delay(start, target)
        self.assertLess(next_delay, start)
        self.assertGreater(next_delay, target)

    def test_auto_direction_uses_calculated_direction_without_randomizing(self):
        motor_controller.applied_direction = "c"
        motor_controller.applied_direction_changed_at = 100.0
        direction, immediate = motor_controller.apply_rotation_preferences(
            "c", {"direction": "auto", "hold": False}, now=100.1
        )
        self.assertEqual(direction, "c")
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

    def test_hold_setting_allows_calculated_change_after_five_seconds(self):
        motor_controller.applied_direction = "c"
        motor_controller.applied_direction_changed_at = 100.0
        direction, immediate = motor_controller.apply_rotation_preferences(
            "a", {"direction": "auto", "hold": True}, now=105.0
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
