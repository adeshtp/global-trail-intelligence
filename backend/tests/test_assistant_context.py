"""
The assistant must answer with what the page already knows.

Probed against a live Mont Buet payload: asked where to buy poles it said no
product links were loaded (the page never sent them, and the answer only named
categories anyway); asked "is it steep and will it rain?" it answered the rain
and dropped the steepness; asked "how hard is it and what should I bring?" it
answered only the first half; asked about altitude it had nothing, because the
activity was not part of what it was given.
"""

from __future__ import annotations

import unittest

from app.services import assistant

TRAIL = {"name": "Test Route", "osm_type": "relation", "osm_id": 1}

WEATHER = {
    "current": {"temperature": 8.0, "precipitation": 0.0, "wind_speed": 12.0},
    "recent_rain": {"24h_mm": 2.5},
    "forecast": {"precipitation_probability_max": 60},
}

DIFFICULTY = {
    "source": {"sac_scale": "demanding_mountain_hiking"},
    "ml": {"available": False},
}

GEAR = {
    "items": [
        {
            "need": "rain_shell",
            "item": "Waterproof shell",
            "priority": "essential",
            "reason": "Rain is forecast",
            "evidence": ["forecast"],
        },
        {
            "need": "poles",
            "item": "Trekking poles",
            "priority": "recommended",
            "reason": "The route climbs",
            "evidence": ["ascent"],
        },
    ],
    "activity": {
        "type": "high_altitude_trek",
        "label": "High-altitude trek",
        "reasons": ["highest sampled point 3096 m"],
        "max_elevation_m": 3096.0,
    },
}

PRODUCTS = {
    "groups": [
        {
            "item": "Trekking poles",
            "status": "search_link",
            "card": {
                "mode": "shopping_fallback",
                "gear_item": "Trekking poles",
                "name": "Trekking poles",
                "retailer": "Hi tec",
                "url": "https://hi-tec.com/collections/poles",
            },
        },
        {
            "item": "Waterproof shell",
            "status": "ok",
            "card": {
                "mode": "direct_product",
                "gear_item": "Waterproof shell",
                "name": "Acme Storm Shell",
                "retailer": "Rei",
                "url": "https://rei.com/product/storm-shell",
            },
        },
    ]
}


def _intelligence(**overrides) -> dict:
    payload = {
        "analysis": {"distance_km": 9.7},
        "terrain": {
            "metrics": {"elevation_gain_m": 1200.0, "max_slope_percent": 41.0}
        },
        "weather": WEATHER,
        "condition": {"status": "caution"},
        "difficulty": DIFFICULTY,
        "gear": GEAR,
    }
    payload.update(overrides)
    return payload


def _answer(question: str, **overrides) -> str:
    return assistant._compose_local_answer(
        question, TRAIL, _intelligence(**overrides)
    ) or ""


class ProductAnswerTests(unittest.TestCase):
    def test_a_named_item_gets_its_real_destination(self) -> None:
        answer = _answer(
            "Where can I buy trekking poles for this route?",
            products=PRODUCTS,
        )
        self.assertIn("https://hi-tec.com/collections/poles", answer)
        self.assertIn("Hi tec", answer)
        # A category page is not a specific product, and says so.
        self.assertIn("search", answer.lower())
        self.assertNotIn("storm-shell", answer)

    def test_a_direct_product_is_named(self) -> None:
        answer = _answer(
            "Where can I buy a waterproof shell for this trail?",
            products=PRODUCTS,
        )
        self.assertIn("Acme Storm Shell", answer)
        self.assertIn("https://rei.com/product/storm-shell", answer)

    def test_a_general_question_lists_every_item(self) -> None:
        answer = _answer("What can I buy for this route?", products=PRODUCTS)
        self.assertIn("hi-tec.com", answer)
        self.assertIn("rei.com", answer)

    def test_without_products_it_says_none_are_loaded(self) -> None:
        answer = _answer("Where can I buy poles for this route?")
        self.assertIn("don't have any product links", answer)

    def test_a_card_without_a_safe_link_is_not_quoted(self) -> None:
        bad = {
            "groups": [
                {
                    "item": "Trekking poles",
                    "card": {"mode": "shopping_fallback", "url": "javascript:x"},
                }
            ]
        }
        answer = _answer("Where can I buy trekking poles?", products=bad)
        self.assertNotIn("javascript", answer)


class MultipleIntentTests(unittest.TestCase):
    def test_steepness_and_rain_are_both_answered(self) -> None:
        answer = _answer("Is this route steep and will it rain?")
        self.assertIn("2.5 mm", answer)
        self.assertIn("41%", answer)

    def test_difficulty_and_gear_are_both_answered(self) -> None:
        answer = _answer("How hard is this route and what should I bring?")
        self.assertIn("Hard", answer)
        self.assertIn("Waterproof shell", answer)

    def test_a_single_topic_question_stays_a_single_answer(self) -> None:
        answer = _answer("Will it rain on this route?")
        self.assertIn("2.5 mm", answer)
        self.assertNotIn("Waterproof shell", answer)

    def test_answers_are_separated_and_capped(self) -> None:
        intents = assistant._question_intents(
            "How hard is it, will it rain, what should I bring, is it "
            "suitable, and where can I buy poles on this route?"
        )
        self.assertLessEqual(len(intents), 3)
        answer = _answer(
            "How hard is it, will it rain, what should I bring and is it "
            "suitable on this route?"
        )
        self.assertIn("\n\n", answer)

    def test_the_first_intent_is_unchanged_for_existing_callers(self) -> None:
        self.assertEqual(
            assistant._question_intent("Where do I buy a shell?"), "products"
        )
        self.assertEqual(
            assistant._question_intent("How difficult is the route?"),
            "difficulty",
        )


class ActivityAnswerTests(unittest.TestCase):
    def test_altitude_is_answered_from_the_activity(self) -> None:
        answer = _answer("Is altitude a problem on this route?")
        self.assertIn("High-altitude trek", answer)
        self.assertIn("3096", answer)

    def test_what_kind_of_trip_it_is_is_answered(self) -> None:
        answer = _answer("What kind of trip is this route?")
        self.assertIn("High-altitude trek", answer)

    def test_without_an_activity_it_says_so(self) -> None:
        gear = {k: v for k, v in GEAR.items() if k != "activity"}
        answer = _answer("What kind of trip is this route?", gear=gear)
        self.assertIn("don't have", answer)


class CorpusTests(unittest.TestCase):
    def _passages(self, **overrides) -> list[dict]:
        return assistant._build_corpus(TRAIL, _intelligence(**overrides))

    def test_product_cards_reach_the_corpus(self) -> None:
        text = " ".join(p["text"] for p in self._passages(products=PRODUCTS))
        self.assertIn("hi-tec.com/collections/poles", text)
        self.assertIn("Acme Storm Shell", text)

    def test_the_activity_reaches_the_corpus(self) -> None:
        text = " ".join(p["text"] for p in self._passages())
        self.assertIn("High-altitude trek", text)
        self.assertIn("3096", text)


if __name__ == "__main__":
    unittest.main()
