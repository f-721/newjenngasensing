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



def test_every_watch_must_complete_first_turn_before_reference_switches():
    tracker = motor_controller.TurnReferenceTracker(["watch1", "watch2", "watch3", "watch4"])
    for number, watch in enumerate(["watch1", "watch2", "watch3", "watch4"], 1):
        assert tracker.update(watch, number)
        assert tracker.use_baseline
    assert tracker.update("watch1", 5)
    assert not tracker.use_baseline


def test_repeated_turns_do_not_count_as_other_watches_first_turn():
    tracker = motor_controller.TurnReferenceTracker(["watch1", "watch2", "watch3"])
    for number, watch in enumerate(["watch1", "watch2", "watch1", "watch2", "watch3"], 1):
        tracker.update(watch, number)
        assert tracker.use_baseline
    tracker.update("watch1", 6)
    assert not tracker.use_baseline


def test_polling_does_not_complete_current_watch_turn():
    tracker = motor_controller.TurnReferenceTracker(["watch1"])
    assert tracker.update("watch1", 1)
    assert not tracker.update("watch1", 1)
    assert tracker.use_baseline
    assert tracker.update("watch1", 2)
    assert not tracker.use_baseline


def test_new_game_resets_completed_watches():
    tracker = motor_controller.TurnReferenceTracker(["watch1", "watch2"])
    tracker.update("watch1", 1)
    tracker.update("watch2", 2)
    tracker.update("watch1", 3)
    assert not tracker.use_baseline
    tracker.update("watch1", 1)
    assert tracker.use_baseline
    assert tracker.completed == set()
    tracker.update("watch2", 2)
    assert tracker.use_baseline


def test_watch3_first_turn_uses_average_even_with_large_turn_number():
    tracker = motor_controller.TurnReferenceTracker(["watch1", "watch2", "watch3"])
    for number, watch in enumerate(["watch1", "watch2", "watch1", "watch2", "watch3"], 10):
        tracker.update(watch, number)
    references, source = motor_controller.get_comparison_references(
        tracker.use_baseline, {"watch3": 68}, {"watch3": 82}
    )
    assert source == "baseline"
    assert motor_controller.calculate_rpm_fast(81 - references["watch3"]) == 30
    tracker.update("watch1", 15)
    references, source = motor_controller.get_comparison_references(
        tracker.use_baseline, {"watch3": 68}, {"watch3": 82}
    )
    assert source == "turn_start"
    assert motor_controller.calculate_rpm_fast(81 - references["watch3"]) == 10
