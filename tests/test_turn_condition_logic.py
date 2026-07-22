import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from turn_condition_logic import evaluate_turn_condition


def test_uses_personal_baseline_when_condition_continues():
    reference_bpm, diff, source = evaluate_turn_condition(
        current_bpm=78.0,
        baseline_bpm=72.0,
        current_condition="up",
        previous_condition="up",
        stored_reference_bpm=80.0,
    )

    assert reference_bpm == 80.0
    assert diff == -2.0
    assert source == "state"


def test_resets_reference_when_condition_switches():
    reference_bpm, diff, source = evaluate_turn_condition(
        current_bpm=77.0,
        baseline_bpm=72.0,
        current_condition="down",
        previous_condition="up",
        stored_reference_bpm=80.0,
    )

    assert reference_bpm == 77.0
    assert diff == 0.0
    assert source == "switch"


def test_falls_back_to_personal_baseline_when_no_state_exists():
    reference_bpm, diff, source = evaluate_turn_condition(
        current_bpm=75.0,
        baseline_bpm=70.0,
        current_condition="up",
        previous_condition=None,
        stored_reference_bpm=None,
    )

    assert reference_bpm == 70.0
    assert diff == 5.0
    assert source == "baseline"
