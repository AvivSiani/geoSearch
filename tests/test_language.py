import pytest

from geosearch.request.language import detect_language


@pytest.mark.parametrize(
    ("prompt", "expected"),
    [
        ("Find me a good Asian restaurant that is not too expensive.", "en"),
        ("מצא לי מסעדה אסייתית טובה שלא יקרה מדי.", "he"),
        ("מסעדה ליד Starbucks", "he"),  # mixed: any Hebrew letter wins
        ("Restaurants near קפה", "he"),
        ("123 4.5 !?", "en"),  # digits and punctuation only
        ("Café près d'ici", "en"),  # unsupported languages fall back to English
        ("", "en"),
    ],
)
def test_detect_language(prompt: str, expected: str) -> None:
    assert detect_language(prompt) == expected
