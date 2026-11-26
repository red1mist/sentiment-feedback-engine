"""Sentiment classifiers behind one small interface.

Every backend returns a `Sentiment` with:
  label       positive | negative | neutral
  score       polarity in [-1, 1]  (negative .. positive)
  confidence  how sure the model is about `label`, in [0, 1]
  model       identifier stored with each feedback record, so a later model
              swap can be told apart in the data
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

Label = Literal["positive", "negative", "neutral"]


@dataclass(frozen=True)
class Sentiment:
    label: Label
    score: float
    confidence: float
    model: str


class Classifier(Protocol):
    name: str

    def classify(self, text: str) -> Sentiment: ...


class VaderClassifier:
    """Lexicon + rule based (VADER). No model download, ~0.1 ms per short text.

    Good at short, informal customer feedback (handles negation, intensifiers,
    punctuation and capitalisation emphasis). The standard thresholds on the
    compound score are used: >= 0.05 positive, <= -0.05 negative, else neutral.
    """

    name = "vader-3.3.2"
    POS_THRESHOLD = 0.05
    NEG_THRESHOLD = -0.05

    def __init__(self) -> None:
        from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

        self._analyzer = SentimentIntensityAnalyzer()

    def classify(self, text: str) -> Sentiment:
        scores = self._analyzer.polarity_scores(text)
        compound = float(scores["compound"])
        if compound >= self.POS_THRESHOLD:
            label: Label = "positive"
            confidence = min(1.0, abs(compound))
        elif compound <= self.NEG_THRESHOLD:
            label = "negative"
            confidence = min(1.0, abs(compound))
        else:
            label = "neutral"
            confidence = float(scores["neu"])  # share of neutral tokens: a sensible proxy
        return Sentiment(label=label, score=round(compound, 4), confidence=round(confidence, 4), model=self.name)


class TransformersClassifier:
    """Optional backend: a Hugging Face `sentiment-analysis` pipeline.

    Heavier (hundreds of MB, needs `requirements-transformers.txt`) but more
    accurate on long or sarcastic text. SST-2 models are binary, so low
    probability answers are reported as neutral.
    """

    def __init__(self, model_name: str, neutral_below: float, pipeline=None) -> None:
        if pipeline is None:
            from transformers import pipeline as hf_pipeline  # type: ignore

            pipeline = hf_pipeline("sentiment-analysis", model=model_name)
        self._pipe = pipeline
        self._neutral_below = neutral_below
        self.name = f"transformers:{model_name}"

    def classify(self, text: str) -> Sentiment:
        out = self._pipe(text, truncation=True)[0]
        prob = float(out["score"])
        positive = str(out["label"]).upper().startswith("POS")
        signed = prob if positive else -prob
        if prob < self._neutral_below:
            return Sentiment("neutral", round(signed, 4), round(1 - prob, 4), self.name)
        return Sentiment("positive" if positive else "negative", round(signed, 4), round(prob, 4), self.name)


def build_classifier(backend: str, *, transformers_model: str, transformers_neutral_below: float) -> Classifier:
    if backend == "vader":
        return VaderClassifier()
    if backend == "transformers":
        return TransformersClassifier(transformers_model, transformers_neutral_below)
    raise ValueError(f"unknown SENTIMENT_BACKEND {backend!r} (expected vader|transformers)")
