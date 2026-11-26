from app.tagger import CATEGORIES, tag


def test_category_by_dominant_keyword_group():
    assert tag("I was charged twice on my invoice, please refund me").category == "billing"
    assert tag("Package still not delivered, tracking has not moved").category == "shipping"
    assert tag("The support agent never replied to my ticket").category == "support"
    assert tag("The app is slow and crashes on startup").category == "performance"
    assert tag("The new menu layout is confusing to navigate").category == "usability"
    assert tag("Great product quality, the item works as described").category == "product"
    assert tag("hello there").category == "other"


def test_whole_words_only():
    assert tag("the cardboard relationship").category == "other"   # not 'card', not 'ship'


def test_triage_tags_are_independent_of_category():
    r = tag("URGENT: app crashes every time, I want my money back or I will cancel")
    assert sorted(r.tags) == ["bug", "churn", "refund", "urgent"]
    assert tag("lovely").tags == []
    assert tag("It would be great if you could add dark mode").tags == ["feature-request"]


def test_every_category_is_known():
    for text in ["invoice", "delivery", "ticket", "slow", "confusing", "feature", "zzz"]:
        assert tag(text).category in CATEGORIES
