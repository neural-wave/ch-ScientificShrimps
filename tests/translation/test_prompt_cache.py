"""The prompt's fixed head, which vLLM serves from its prefix cache in whole 800-token blocks: NL2Q_FIXED_CORE moves the
core schema blocks into SYSTEM (the same blocks, another place). No model."""
import importlib
from nl2q import settings as S
from nl2q.translation import prompt

Q = "How many patients have a billed diagnosis of type 2 diabetes?"


def test_fixed_core_moves_the_core_blocks(monkeypatch):
    off_card = prompt.card(Q)
    monkeypatch.setattr(S, "FIXED_CORE", True)
    try:
        on = importlib.reload(prompt)
        on_card = on.card(Q)
        for n in on.CORE:
            assert on.BLOCKS[n] in on.SYSTEM and on.BLOCKS[n] not in on_card
        for b in on.BLOCKS.values():  # nothing lost: every block of the old card is in the new card or in SYSTEM
            if b in off_card:
                assert b in on_card or b in on.SYSTEM
    finally:
        monkeypatch.setattr(S, "FIXED_CORE", False)
        importlib.reload(prompt)
    assert all(prompt.BLOCKS[n] not in prompt.SYSTEM for n in prompt.CORE)  # off by default


def test_warm_prefix_sends_the_fixed_head(monkeypatch):
    """NL2Q_WARM_PREFIX: warm_up() sends SYSTEM and the `always` examples once, 1 token, so request 1 finds them cached."""
    G = importlib.import_module("nl2q.translation.generate")  # the package exports a function of the same name
    sent = []

    class Completions:
        @staticmethod
        def create(**kw):
            sent.append(kw)

    client = type("Client", (), {"chat": type("Chat", (), {"completions": Completions})})
    monkeypatch.setattr(G, "_client", lambda: client)
    monkeypatch.setattr(G, "_awake", lambda url: None)
    G.warm_up()
    assert not sent  # off by default
    monkeypatch.setattr(S, "WARM_PREFIX", True)
    G.warm_up()
    m, head = sent[0]["messages"], 1 + 2 * len(prompt.EXAMPLES)
    assert sent[0]["max_tokens"] == 1 and len(m) == head + 1
    assert m[:head] == prompt.messages(Q)[:head]  # the head every request starts with


def test_card_stats_cues_only_classes_the_data_has(monkeypatch, tmp_path):
    """NL2Q_CARD_STATS: a cue under each block whose class the data has; none (never "0") for one it lacks."""
    sphn = "https://biomedit.ch/rdf/sphn-schema/sphn#"
    stats = tmp_path / "cardinality.json"
    stats.write_text('{"patients": 100000, "classes": {"%sBilledDiagnosis": [103632, 48026], "%sAge": [0, 0]}, '
                     '"class_max": {"%sBilledDiagnosis": 37}}' % (sphn, sphn, sphn))
    monkeypatch.setattr(S, "CARD_STATS", True)
    monkeypatch.setattr(S, "SHAPE_RULE", False)
    monkeypatch.setattr(S, "OPT_STATS", stats)
    try:
        on = importlib.reload(prompt)
        assert on.block("sphn:BilledDiagnosis").split("\n")[1] == "    in 48% of patients, up to 37 per patient"
        assert on.block("sphn:Age") == on.BLOCKS["sphn:Age"] and "rarest type" in on.SYSTEM and "EXISTS, not" not in on.SYSTEM
    finally:
        monkeypatch.undo()
        importlib.reload(prompt)
    assert not prompt.CUES and "rarest type" not in prompt.SYSTEM  # off by default


def test_shape_rule_is_its_own_switch(monkeypatch):
    """NL2Q_SHAPE_RULE: the static OPTIONAL/EXISTS rule, without the data's cues."""
    monkeypatch.setattr(S, "SHAPE_RULE", False)
    try:
        assert "to flag a group, use EXISTS" not in importlib.reload(prompt).SYSTEM
        monkeypatch.setattr(S, "SHAPE_RULE", True)
        on = importlib.reload(prompt)
        assert "to flag a group, use EXISTS" in on.SYSTEM and "rarest type" not in on.SYSTEM and not on.CUES
    finally:
        monkeypatch.undo()
        importlib.reload(prompt)
    assert "to flag a group, use EXISTS" in prompt.SYSTEM  # on by default (O02: no OPTIONAL double count)
