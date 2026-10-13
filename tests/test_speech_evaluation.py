from app.speech_evaluation import SpeechEvaluationService


class FakeScorer:
    def __init__(self, result):
        self.result = result

    def score(self, *_args, **_kwargs):
        return dict(self.result)


def test_uncertain_recognition_is_not_counted_as_a_mistake():
    service = SpeechEvaluationService(FakeScorer({"score": 20, "unrelated": True}))
    result = service.evaluate([], {"variants": []})
    assert result["outcome"] == "uncertain"
    assert result["counts_as_attempt"] is False


def test_low_score_is_not_mislabelled_as_grammar_or_pronunciation():
    weak = SpeechEvaluationService(FakeScorer({"score": 60, "unrelated": False})).evaluate([], {})
    good = SpeechEvaluationService(FakeScorer({"score": 90, "unrelated": False})).evaluate([], {})
    assert weak["outcome"] == "needs_work" and weak["error_kind"] == "undetermined"
    assert good["outcome"] == "correct" and good["correct"]
