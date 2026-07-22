from __future__ import annotations

import random
from typing import Optional


class TurnConditionLogic:
    """各ターンの心拍条件をランダムに決め、状態遷移に応じて基準を切り替える。"""

    def __init__(self) -> None:
        self.previous_condition: Optional[str] = None
        self.reference_bpm: Optional[float] = None
        self.current_condition: Optional[str] = None

    def choose_condition(self) -> str:
        if self.current_condition is None:
            self.current_condition = random.choice(["up", "down"])
            return self.current_condition

        if random.random() < 0.25:
            self.current_condition = "down" if self.current_condition == "up" else "up"

        return self.current_condition

    def update(self, current_bpm: float, baseline_bpm: float, condition: Optional[str] = None) -> tuple[float, float, str]:
        if condition is None:
            condition = self.choose_condition()

        previous_condition = self.previous_condition
        stored_reference = self.reference_bpm

        if previous_condition is None:
            reference_bpm = baseline_bpm
            diff = current_bpm - reference_bpm
            source = "baseline"
        elif previous_condition == condition:
            reference_bpm = stored_reference if stored_reference is not None else baseline_bpm
            diff = current_bpm - reference_bpm
            source = "state"
        else:
            reference_bpm = current_bpm
            diff = 0.0
            source = "switch"

        self.previous_condition = condition
        self.reference_bpm = reference_bpm
        self.current_condition = condition
        return reference_bpm, diff, source


def evaluate_turn_condition(current_bpm: float, baseline_bpm: float, current_condition: str, previous_condition: Optional[str], stored_reference_bpm: Optional[float]) -> tuple[float, float, str]:
    if previous_condition is None:
        reference_bpm = baseline_bpm
        diff = current_bpm - reference_bpm
        source = "baseline"
    elif previous_condition == current_condition:
        reference_bpm = stored_reference_bpm if stored_reference_bpm is not None else baseline_bpm
        diff = current_bpm - reference_bpm
        source = "state"
    else:
        reference_bpm = current_bpm
        diff = 0.0
        source = "switch"

    return reference_bpm, diff, source


def compute_condition_diff(current_bpm: float, reference_bpm: float, condition: str) -> float:
    if condition == "up":
        return current_bpm - reference_bpm
    return reference_bpm - current_bpm
