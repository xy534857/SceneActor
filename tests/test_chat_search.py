"""Chat retrieval and uploaded persona-pack contracts."""

from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sceneactor.chat import validate_record
from sceneactor.search import SearchHit, _google_news, briefing, parse_triage, web_search


def test_triage_parses_fenced_json_and_rejects_non_json():
    assert parse_triage('```json\n{"need":true,"query":"OpenAI July 2026"}\n```') == (
        True,
        "OpenAI July 2026",
    )
    assert parse_triage('I think we should search') == (False, "")
    assert parse_triage('{"need":false,"query":"unused"}') == (False, "unused")


def test_google_news_parser_preserves_date_source_and_url(monkeypatch):
    rss = """<rss><channel><item>
      <title>Model ships - Example</title>
      <link>https://news.example/item</link>
      <pubDate>Sat, 18 Jul 2026 09:00:00 GMT</pubDate>
      <source url="https://example.com">Example News</source>
    </item></channel></rss>"""
    monkeypatch.setattr("sceneactor.search._fetch", lambda url, timeout: rss)

    hits = _google_news("new model", 5, 1)

    assert hits == [
        SearchHit(
            "Model ships - Example",
            "Example News · Sat, 18 Jul 2026 09:00:00 GMT",
            "https://news.example/item",
        )
    ]


def test_web_search_falls_back_when_news_backend_fails(monkeypatch):
    monkeypatch.setattr("sceneactor.search._google_news", lambda *args: (_ for _ in ()).throw(OSError("down")))
    monkeypatch.setattr(
        "sceneactor.search._bing",
        lambda *args: [SearchHit("Fallback", "snippet", "https://example.com")],
    )

    assert web_search("query", count=1)[0].title == "Fallback"


def test_briefing_is_actor_instruction_not_verbatim_source_list():
    text = briefing(
        "latest release",
        [SearchHit("A release happened", "Example · today", "https://example.com")],
        "en-US",
    )
    assert "Fresh information" in text
    assert "Digest this in your own words" in text
    assert "https://example.com" not in text


def test_uploaded_persona_pack_requires_runtime_genome_fields():
    with pytest.raises(ValueError, match="record.person_id"):
        validate_record({"genome": {}})
    with pytest.raises(ValueError, match="trait_axes"):
        validate_record({"person_id": "custom", "genome": {}})

    validate_record(
        {
            "person_id": "custom",
            "genome": {"trait_axes": {}, "core_models": [], "blind_spots": []},
        }
    )
