"""The technical knowledge registry: complete, closed, and honest about limits.

WHAT THESE TESTS PROTECT. The registry is the only place ORION's technical
prose lives, so three properties have to hold mechanically rather than by
review: every concept ORION can name has a definition, every definition covers
every bias a detector can assign it, and no definition describes a technique
this build cannot actually execute. The third is the one that decays quietly —
a definition is easy to write and a detector is not — so `UNSUPPORTED_CONCEPTS`
is tested as carefully as the supported list.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from orion.analysts.knowledge import (
    KNOWLEDGE_VERSION,
    TECHNICAL_KNOWLEDGE,
    UNSUPPORTED_CONCEPTS,
    ConceptDefinition,
    StructureBias,
    TechnicalConcept,
)

#: Vocabulary that would turn a definition into an instruction. The same
#: blocklist idea `test_schemas.py` applies to the Macro Analyst's field names,
#: applied here to prose, because prose is where it would actually appear.
_INSTRUCTION_WORDS = (
    " buy ",
    " sell ",
    " go long",
    " go short",
    "you should",
    "we should",
    "enter at",
    "enter now",
    "entry signal",
    "take profit at",
    "stop loss at",
    "position size",
    "recommend",
    "guaranteed",
    "will reverse",
    "will continue",
    "always works",
)

#: `not_claimed` is scanned separately and more permissively, because a
#: disclaimer legitimately names the thing it is disclaiming -- "says nothing
#: about whether entering is sensible" is the sentence doing its job. What must
#: never appear there is an actual instruction or a promise.
_PROMISE_WORDS = (
    " buy ",
    " sell ",
    "you should",
    "we should",
    "recommend",
    "guaranteed",
    "always works",
)


def test_every_concept_has_a_definition() -> None:
    """A concept ORION can name is a concept ORION can explain."""
    assert set(TECHNICAL_KNOWLEDGE) == set(TechnicalConcept)


def test_every_definition_is_filed_under_its_own_concept() -> None:
    for concept, definition in TECHNICAL_KNOWLEDGE.items():
        assert definition.concept is concept


def test_the_registry_cannot_be_mutated_at_runtime() -> None:
    """A finding's interpretation must be the committed sentence, not one an
    earlier caller edited into the mapping."""
    with pytest.raises(TypeError):
        TECHNICAL_KNOWLEDGE[TechnicalConcept.TREND_REGIME] = None  # type: ignore[index]


@pytest.mark.parametrize("concept", list(TechnicalConcept))
def test_every_definition_states_a_rule_a_requirement_and_a_limit(
    concept: TechnicalConcept,
) -> None:
    definition = TECHNICAL_KNOWLEDGE[concept]

    assert definition.definition.strip()
    assert definition.rule.strip()
    assert definition.requires
    assert definition.not_claimed.strip()


@pytest.mark.parametrize("concept", list(TechnicalConcept))
def test_every_rule_names_the_function_that_implements_it(concept: TechnicalConcept) -> None:
    """THE ANTI-DRIFT CHECK. A rule that does not name real code is a
    description of an intention. Every rule here points at the module that
    actually runs it, so a reader can go and check the two agree.
    """
    rule = TECHNICAL_KNOWLEDGE[concept].rule
    assert "orion.analysis" in rule, f"{concept.value} does not name the code that runs it"


@pytest.mark.parametrize("concept", list(TechnicalConcept))
def test_no_definition_reads_as_an_instruction(concept: TechnicalConcept) -> None:
    definition = TECHNICAL_KNOWLEDGE[concept]
    prose = " ".join(
        [definition.definition, definition.rule, *definition.interpretations.values()]
    ).lower()
    found = [word for word in _INSTRUCTION_WORDS if word in prose]
    assert not found, f"{concept.value} reads as an instruction: {found}"

    # The disclaimer is held to the narrower bar: it may name what it refuses
    # to claim, but it may not itself instruct or promise.
    disclaimer = definition.not_claimed.lower()
    promised = [word for word in _PROMISE_WORDS if word in disclaimer]
    assert not promised, f"{concept.value}'s disclaimer promises something: {promised}"


@pytest.mark.parametrize("concept", list(TechnicalConcept))
def test_no_interpretation_contains_a_number(concept: TechnicalConcept) -> None:
    """THE SPLIT, ENFORCED. Numbers belong to a finding's `observation`, which
    is computed from real values. A number baked into a canonical sentence
    would be a measurement nobody measured — and would be printed identically
    whatever the bars said.
    """
    for bias, sentence in TECHNICAL_KNOWLEDGE[concept].interpretations.items():
        assert not any(character.isdigit() for character in sentence), (
            f"{concept.value}/{bias.value} has a digit in its interpretation"
        )


@pytest.mark.parametrize("concept", list(TechnicalConcept))
def test_every_interpretation_is_reachable_and_distinct(concept: TechnicalConcept) -> None:
    definition = TECHNICAL_KNOWLEDGE[concept]

    assert definition.interpretations, f"{concept.value} has no interpretations at all"
    assert set(definition.interpretations) <= set(StructureBias)
    sentences = list(definition.interpretations.values())
    assert len(set(sentences)) == len(sentences), (
        f"{concept.value} repeats an interpretation across biases, so two different "
        "readings would print the same sentence"
    )


def test_a_missing_interpretation_raises_rather_than_defaulting() -> None:
    """`interpretation_for` must never answer a bias nobody wrote words for
    with a generic sentence — that would hide a real gap in the registry."""
    definition = TECHNICAL_KNOWLEDGE[TechnicalConcept.VOLATILITY_ATR]
    assert StructureBias.BULLISH not in definition.interpretations

    with pytest.raises(KeyError):
        definition.interpretation_for(StructureBias.BULLISH)


def test_a_definition_is_frozen_and_closed() -> None:
    definition = TECHNICAL_KNOWLEDGE[TechnicalConcept.TREND_REGIME]

    with pytest.raises(ValidationError):
        definition.title = "something else"  # type: ignore[misc]

    with pytest.raises(ValidationError):
        ConceptDefinition(
            **definition.model_dump(),
            surprise_field="no",  # type: ignore[call-arg]
        )


# ------------------------------------------------- what ORION cannot do


def test_no_supported_concept_depends_on_volume() -> None:
    """THE HONESTY CHECK THIS FILE EXISTS FOR.

    `OHLCVBar.volume` is optional and routinely absent for spot metals, and
    `bars_to_dataframe` substitutes 0.0 when it is. So no concept in the
    supported registry may name volume as an input, and none may be a
    volume-derived construction.
    """
    for concept, definition in TECHNICAL_KNOWLEDGE.items():
        requirements = " ".join(definition.requires).lower()
        assert "volume" not in requirements, f"{concept.value} requires volume"
        assert "vwap" not in definition.rule.lower()


@pytest.mark.parametrize("name", ["vwap", "macd", "obv", "order block", "elliott"])
def test_no_concept_is_named_for_something_orion_cannot_compute(name: str) -> None:
    named = {concept.value.replace("_", " ") for concept in TechnicalConcept}
    assert not any(name in value for value in named)


def test_the_unsupported_list_names_the_obvious_omissions() -> None:
    """A registry of eleven concepts reads as a claim of completeness unless
    the omissions are stated. These are the ones a trader would ask about
    first."""
    listed = " ".join(item.name.lower() for item in UNSUPPORTED_CONCEPTS)
    for expected in ("vwap", "macd", "volume", "order block", "elliott"):
        assert expected in listed, f"{expected!r} is neither supported nor declared unsupported"


@pytest.mark.parametrize("item", UNSUPPORTED_CONCEPTS, ids=lambda item: item.name)
def test_every_unsupported_concept_gives_a_concrete_reason(item: object) -> None:
    """ "Not implemented yet" is not a reason. Each entry has to point at the
    actual obstacle in this build, so a reader can tell a missing feature from
    a missing input."""
    reason = item.reason
    assert len(reason) > 60, f"{item.name} has a reason too short to be concrete"
    assert any(
        marker in reason
        for marker in ("orion.", "OHLCVBar", "provider", "data", "deterministic", "build")
    ), f"{item.name} does not point at anything real"


def test_supported_and_unsupported_do_not_overlap() -> None:
    supported = {concept.value.replace("_", " ") for concept in TechnicalConcept}
    for item in UNSUPPORTED_CONCEPTS:
        assert item.name.lower() not in supported


def test_the_knowledge_version_is_stated() -> None:
    """A stored finding names the wording that produced it, so the wording has
    to be versioned. Mirrors `ReasoningOutput.prompt_version`'s role."""
    assert KNOWLEDGE_VERSION
    assert KNOWLEDGE_VERSION.strip() == KNOWLEDGE_VERSION
