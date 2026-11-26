import pytest

from app.classifier import TransformersClassifier, VaderClassifier, build_classifier


@pytest.fixture(scope="module")
def vader():
    return VaderClassifier()


@pytest.mark.parametrize("text,expected", [
    ("I love this product, it works great!", "positive"),
    ("Terrible experience, the app crashes constantly and support ignored me.", "negative"),
    ("The package arrived on Tuesday.", "neutral"),
    ("This is not good at all.", "negative"),            # negation
    ("AMAZING!!! Best purchase this year.", "positive"),  # emphasis
])
def test_vader_labels(vader, text, expected):
    s = vader.classify(text)
    assert s.label == expected
    assert -1.0 <= s.score <= 1.0 and 0.0 <= s.confidence <= 1.0
    assert s.model == vader.name


class FakePipeline:
    def __init__(self, outputs):
        self.outputs = outputs

    def __call__(self, inputs, **kwargs):
        return [self.outputs[inputs]]


def test_transformers_mapping():
    pipe = FakePipeline({"good": {"label": "POSITIVE", "score": 0.98}, "bad": {"label": "NEGATIVE", "score": 0.91},
                         "meh": {"label": "POSITIVE", "score": 0.55}})
    clf = TransformersClassifier("fake-model", neutral_below=0.60, pipeline=pipe)
    assert clf.name == "transformers:fake-model"
    good, bad, meh = (clf.classify(t) for t in ("good", "bad", "meh"))
    assert (good.label, good.score, good.confidence) == ("positive", 0.98, 0.98)
    assert (bad.label, bad.score, bad.confidence) == ("negative", -0.91, 0.91)
    assert meh.label == "neutral" and meh.confidence == pytest.approx(0.45)


def test_unknown_backend_rejected():
    with pytest.raises(ValueError):
        build_classifier("llm", transformers_model="x", transformers_neutral_below=0.6)
