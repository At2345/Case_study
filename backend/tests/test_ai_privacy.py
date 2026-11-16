"""PR-8.2: nothing sensitive reaches a provider, and an undeclared provider is refused."""
import asyncio

import pytest

from app.ai.privacy import (
    PrivacyClient,
    ProviderPolicy,
    Redactor,
    StreamRestorer,
    counters,
    report,
    reset_counters,
    scrub,
    set_active,
)
from app.narrative.client import GlmClient


class RecordingClient:
    """Stands in for a provider and records exactly what it was asked to send."""

    def __init__(self, reply: str = "ok", chunks=None) -> None:
        self.reply = reply
        self.chunks = chunks or []
        self.seen = []

    def generate(self, system: str, user: str) -> str:
        self.seen.append((system, user))
        return self.reply

    async def stream(self, system, history, user):
        self.seen.append((system, history, user))
        for chunk in self.chunks:
            yield chunk


def _collect(client, system, history, user):
    async def run():
        return [chunk async for chunk in client.stream(system, history, user)]
    return asyncio.run(run())


class TestRedactionRules:
    @pytest.mark.parametrize("text,kind", [
        ("email john.smith@acme.co.uk now", "email"),
        ("call 07700 900123 today", "phone_uk"),
        ("call +44 7700 900123 today", "phone_uk"),
        ("ring 020 7946 0958 please", "phone_uk"),
        ("ship to SW1A 1AA", "postcode_uk"),
        ("iban GB82WEST12345698765432", "iban"),
        ("card 4111 1111 1111 1111", "card"),
        ("ni AB123456C", "ni_number"),
        ("host 192.168.1.10", "ipv4"),
    ])
    def test_each_kind_is_redacted(self, text, kind):
        redactor = Redactor()
        out = redactor.redact(text)
        assert out != text
        assert kind in [f["kind"] for f in redactor.findings]

    def test_the_same_value_gets_the_same_placeholder(self):
        redactor = Redactor()
        out = redactor.redact("a@b.co.uk then a@b.co.uk then c@d.co.uk")
        assert out == "[[EMAIL_1]] then [[EMAIL_1]] then [[EMAIL_2]]"

    def test_restore_puts_every_occurrence_back(self):
        redactor = Redactor()
        original = "mail a@b.co.uk and c@d.co.uk, or a@b.co.uk again"
        assert redactor.restore(redactor.redact(original)) == original

    def test_findings_never_contain_the_value(self):
        redactor = Redactor()
        redactor.redact("a@b.co.uk")
        assert "a@b.co.uk" not in str(redactor.findings)
        assert redactor.findings == [{"kind": "email", "count": 1}]


class TestNoFalsePositives:
    def test_business_numbers_are_untouched(self):
        """The mart's own numbers must survive: over-redaction destroys the answer."""
        redactor = Redactor()
        text = ('{"plan": {"month": "2026-03-01", "baseline_units": 1200, "plan_units": 1450, '
                '"change_vs_baseline_pct": 20.8, "value_delta_gbp": 55278}, '
                '"signals": {"oos_rate_28d": 12.5, "on_hand": 180}, '
                '"item": {"channel": "Amazon", "abc_category": "A", "phase": "GROWTH", '
                '"risk_type": "CRITICAL_AMAZON_BUYBOX_ALERT"}}')
        assert redactor.redact(text) == text
        assert redactor.findings == []

    def test_only_the_free_text_comment_is_masked_in_a_real_prompt(self):
        from app.narrative.prompt import build_messages
        telemetry = {
            "item": {"channel": "Amazon", "phase": "GROWTH",
                     "risk_type": "CRITICAL_AMAZON_BUYBOX_ALERT"},
            "plan": {"month": "2026-03-01", "baseline_units": 1200, "plan_units": 1450},
            "prior_decisions": [{"month": "2026-03-01", "submitted_units": 320,
                                 "reason": "EOL_RUNOUT",
                                 "comment": "chase john.smith@acme.co.uk before the lift"}],
        }
        _, user = build_messages(telemetry)
        redactor = Redactor()
        masked = redactor.redact(user)
        assert "john.smith@acme.co.uk" not in masked
        assert "[[EMAIL_1]]" in masked
        assert "CRITICAL_AMAZON_BUYBOX_ALERT" in masked
        assert redactor.restore(masked) == user

    def test_the_shipped_system_prompts_survive_redaction(self):
        from app.api.extended import ASSISTANT_PROMPT, GENERAL_KNOWLEDGE_PROMPT
        from app.narrative.prompt import SYSTEM_PROMPT
        redactor = Redactor()
        for prompt in (SYSTEM_PROMPT, ASSISTANT_PROMPT, GENERAL_KNOWLEDGE_PROMPT):
            assert redactor.redact(prompt) == prompt


class TestPrivacyClient:
    def test_generate_never_sends_the_value_and_restores_the_reply(self):
        inner = RecordingClient(reply="I emailed [[EMAIL_1]] about it.")
        out = PrivacyClient(inner).generate("system", "chase john.smith@acme.co.uk")
        _, user = inner.seen[0]
        assert "john.smith@acme.co.uk" not in user
        assert "[[EMAIL_1]]" in user
        assert out == "I emailed john.smith@acme.co.uk about it."

    def test_stream_restores_a_placeholder_split_across_chunks(self):
        inner = RecordingClient(chunks=["I emailed [[EMA", "IL_1]] about", " it."])
        parts = _collect(PrivacyClient(inner), "s", [("user", "mail john.smith@acme.co.uk")], "q")
        assert "".join(parts) == "I emailed john.smith@acme.co.uk about it."

    def test_history_is_masked_too(self):
        inner = RecordingClient(chunks=["ok"])
        _collect(PrivacyClient(inner), "s", [("user", "my email is a@b.co.uk")], "q")
        _, history, _ = inner.seen[0]
        assert history == [("user", "my email is [[EMAIL_1]]")]

    def test_the_model_attribute_is_passed_through(self):
        inner = RecordingClient()
        inner.model = "some-model"
        assert PrivacyClient(inner).model == "some-model"

    def test_stream_restorer_holds_back_an_unterminated_placeholder(self):
        redactor = Redactor()
        redactor.redact("a@b.co.uk")
        restorer = StreamRestorer(redactor)
        assert restorer.feed("hello [[EMA") == "hello "
        assert restorer.feed("IL_1]]") == "a@b.co.uk"
        assert restorer.flush() == ""


class TestProviderPolicy:
    def test_nothing_is_refused_by_default(self):
        policy = ProviderPolicy()
        assert policy.permits(RecordingClient())

    def test_attested_is_false_when_nothing_is_declared(self):
        assert ProviderPolicy().attested(["GlmClient"]) is False
        assert ProviderPolicy().attested([]) is False

    def test_required_zdr_refuses_an_undeclared_provider(self):
        policy = ProviderPolicy(require_zdr=True, zdr_declared=frozenset({"glmclient"}))
        assert policy.permits(GlmClient(api_key="k"))
        assert not policy.permits(RecordingClient())

    def test_attested_requires_every_provider_declared(self):
        policy = ProviderPolicy(zdr_declared=frozenset({"glmclient"}))
        assert policy.attested(["GlmClient"]) is True
        assert policy.attested(["GlmClient", "GeminiClient"]) is False


class TestClientFactory:
    @pytest.fixture(autouse=True)
    def clean_env(self, monkeypatch):
        for key in ("EMERGENT_LLM_KEY", "GEMINI_API_KEY", "GLM_API_KEY", "LOCAL_MODEL",
                    "AI_REQUIRE_ZDR", "AI_ZDR_PROVIDERS"):
            monkeypatch.delenv(key, raising=False)
        from app.narrative import llm
        monkeypatch.setattr(llm, "PROVIDER_STATUS", {})

    def test_no_key_means_no_client(self):
        from app.narrative.llm import client_from_env
        assert client_from_env() is None

    def test_a_configured_provider_is_wrapped_for_privacy(self, monkeypatch):
        monkeypatch.setenv("GLM_API_KEY", "test-key")
        from app.narrative.llm import client_from_env
        client = client_from_env()
        assert isinstance(client, PrivacyClient)
        assert client.provider == "GlmClient"

    def test_required_zdr_with_nothing_declared_makes_no_external_call(self, monkeypatch):
        monkeypatch.setenv("GLM_API_KEY", "test-key")
        monkeypatch.setenv("AI_REQUIRE_ZDR", "true")
        from app.narrative.llm import PROVIDER_STATUS, client_from_env
        assert client_from_env() is None
        assert PROVIDER_STATUS["GlmClient"]["ok"] is False
        assert "zero-retention" in PROVIDER_STATUS["GlmClient"]["error"]

    def test_a_declared_provider_is_permitted(self, monkeypatch):
        monkeypatch.setenv("GLM_API_KEY", "test-key")
        monkeypatch.setenv("AI_REQUIRE_ZDR", "true")
        monkeypatch.setenv("AI_ZDR_PROVIDERS", "glmclient")
        from app.narrative.llm import client_from_env
        assert isinstance(client_from_env(), PrivacyClient)


class TestScrub:
    def test_scrub_removes_pii_from_an_error_message(self):
        out = scrub("400 Bad Request: user john.smith@acme.co.uk, phone 07700 900123")
        assert "john.smith@acme.co.uk" not in out
        assert "07700 900123" not in out
        assert "400 Bad Request" in out


class TestNarratorIntegration:
    def test_the_narrative_prompt_is_masked_before_it_reaches_the_provider(self, tmp_path):
        from app.narrative.narrator import Narrator
        telemetry = {
            "item": {"channel": "Amazon", "phase": "GROWTH"},
            "plan": {"month": "2026-03-01", "baseline_units": 1200, "plan_units": 1450},
            "prior_decisions": [{"month": "2026-03-01", "submitted_units": 320,
                                 "reason": "EOL_RUNOUT",
                                 "comment": "chase john.smith@acme.co.uk before the lift"}],
        }
        inner = RecordingClient(reply="")
        out = Narrator(PrivacyClient(inner), tmp_path / "narratives.sqlite").narrate(telemetry, "fallback")
        _, user = inner.seen[0]
        assert "john.smith@acme.co.uk" not in user
        assert "[[EMAIL_1]]" in user
        assert out["source"] == "template"  # an empty reply is rejected by the guardrails


class TestReport:
    @pytest.fixture(autouse=True)
    def fresh_state(self):
        reset_counters()
        set_active(ProviderPolicy(), [])
        yield
        reset_counters()
        set_active(ProviderPolicy(), [])

    def test_counters_track_prompts_and_kinds_without_values(self):
        PrivacyClient(RecordingClient()).generate("s", "mail a@b.co.uk or c@d.co.uk")
        body = counters()
        assert body["prompts"] == 1
        assert body["findings"] == {"email": 2}

    def test_report_is_honest_about_zdr(self):
        set_active(ProviderPolicy(), ["GlmClient"])
        body = report()
        assert body["masking"]["enabled"] is True
        assert body["masking"]["rules"]
        assert body["zdr"]["attested"] is False
        assert body["zdr"]["declared"] == []
        assert body["limitations"]

    def test_report_attests_only_when_every_provider_is_declared(self):
        set_active(ProviderPolicy(zdr_declared=frozenset({"glmclient"})), ["GlmClient"])
        assert report()["zdr"]["attested"] is True
