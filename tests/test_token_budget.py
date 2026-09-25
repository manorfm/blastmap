import json

from orbitkb.generation import token_budget


class _Encoding:
    def encode(self, text: str) -> list[int]:
        return list(range(len(text.split())))


def test_measure_json_tokens_uses_the_available_tokenizer(monkeypatch):
    monkeypatch.setattr(token_budget, "_load_o200k_encoding", lambda: _Encoding())

    measurement = token_budget.measure_json_tokens({"message": "one two three"})

    assert measurement.tokens == 3
    assert measurement.method == "tiktoken:o200k_base"


def test_measure_json_tokens_labels_the_byte_estimate_fallback(monkeypatch):
    payload = {"message": "Olá"}
    monkeypatch.setattr(token_budget, "_load_o200k_encoding", lambda: None)

    measurement = token_budget.measure_json_tokens(payload)

    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    assert measurement.tokens == (len(encoded) + 3) // 4
    assert measurement.method == "byte_estimate"
