"""Replaceable speech policy for spoken conjugation exercises.

The acoustic scorer is Palestinian-Arabic aware at the phoneme level, but it is not
treated as a general dialect ASR.  In particular, an unrelated/low-confidence result
asks for a retry instead of becoming a grammatical error.
"""
from __future__ import annotations

from typing import Any


class SpeechEvaluationService:
    def __init__(self, scorer: Any, pass_score: int = 75):
        self.scorer = scorer
        self.pass_score = pass_score

    def evaluate(self, wav, phrase: dict, diagnostics: bool = False) -> dict:
        result = self.scorer.score(wav, phrase, diagnostics=diagnostics)
        if result.get("error"):
            return {**result, "outcome": "uncertain", "counts_as_attempt": False}
        if result.get("unrelated"):
            return {**result, "outcome": "uncertain", "counts_as_attempt": False,
                    "message": "לא הצלחתי לזהות בביטחון את ההטיה. האזינו להקלטה ונסו שוב."}
        correct = int(result.get("score", 0)) >= self.pass_score
        return {**result, "outcome": "correct" if correct else "needs_work",
                "error_kind": None if correct else "undetermined",
                "correct": correct, "counts_as_attempt": True,
                "message": "נכון" if correct else "לא הצלחתי לאשר את התשובה. הקשיבו להקלטה ולתשובה ונסו שוב."}
