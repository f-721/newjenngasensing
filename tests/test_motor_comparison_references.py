import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import motor_controller


def test_first_turn_uses_baselines_for_rpm_and_display():
    references, source = motor_controller.get_comparison_references(
        True,
        {"watch1": 70, "watch2": 75},
        {"watch1": 90, "watch2": 95},
    )

    assert references == {"watch1": 70, "watch2": 75}
    assert source == "baseline"


def test_later_turn_uses_turn_start_heartbeats_for_rpm_and_display():
    references, source = motor_controller.get_comparison_references(
        False,
        {"watch1": 70, "watch2": 75},
        {"watch1": 90, "watch2": 95},
    )

    assert references == {"watch1": 90, "watch2": 95}
    assert source == "turn_start"


def test_attack_challenge_displays_only_used_device_reference():
    displayed = motor_controller.get_displayed_references(
        "attack_challenge",
        "watch2",
        "watch1",
        {"watch1": 80, "watch2": 91, "watch3": 86},
    )

    assert displayed == {"watch1": 80}


def test_difference_mode_displays_all_candidates_used_for_selection():
    displayed = motor_controller.get_displayed_references(
        "highest_diff",
        "watch2",
        "watch3",
        {"watch1": 80, "watch2": 91, "watch3": 86},
    )

    assert displayed == {"watch1": 80, "watch3": 86}


def test_difference_watch_uses_each_watch_turn_reference(monkeypatch):
    monkeypatch.setattr(motor_controller, "get_watch_ids", lambda: ["watch1", "watch2", "watch3"])
    heart_data = {
        "watch1": {"heartbeat": 100},
        "watch2": {"heartbeat": 90},
        "watch3": {"heartbeat": 85},
    }
    turn_references = {
        "watch1": 100,
        "watch2": 89,
        "watch3": 70,
    }

    target = motor_controller.get_difference_watch(
        "watch1",
        heart_data,
        largest=True,
        reference_heartbeats=turn_references,
    )

    assert target == "watch3"


def test_normal_mode_does_not_replace_heart_rate_rpm_with_fallback():
    assert motor_controller.should_use_no_attack_fallback(
        {"attack_mode": False, "pending_attackers": []},
        [],
    ) is False


def test_attack_challenge_waits_at_fallback_until_challenge_succeeds():
    assert motor_controller.should_use_no_attack_fallback(
        {"attack_mode": True, "pending_attackers": ["watch2"]},
        [],
    ) is True


def test_attack_challenge_uses_attack_effect_after_all_challenges_succeed():
    assert motor_controller.should_use_no_attack_fallback(
        {"attack_mode": True, "pending_attackers": []},
        ["watch2"],
    ) is False
