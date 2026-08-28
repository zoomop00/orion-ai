"""Credentials must not be readable off a `Settings` object.

THE BUG THIS FIXES WAS REAL AND REPRODUCED. With the four provider keys typed
as plain `str`, `repr(settings)`, `str(settings)`, `settings.model_dump()` and
`settings.model_dump_json()` each returned all four in full. `Settings` is
constructed per analysis and per request, so a `Settings` sitting in a
traceback frame -- or an f-string in a future diagnostic -- printed every
credential ORION holds. `SecretStr` closes that by type rather than by
everyone downstream remembering.

The tests below are written against the *rendering* rather than the field
type wherever possible: what matters is that the value cannot be read out, not
which class is doing the masking. Every credential planted here is fake.
"""

from __future__ import annotations

import tokenize
import traceback
from pathlib import Path

import pytest
from pydantic import SecretStr

from orion.config import Settings, get_settings
from orion.interface.app import create_app
from orion.orchestrator.wiring import analysis_available, build_analysis_service

# Fake, and shaped like the real thing so a substring search cannot pass by
# accident. Nothing here is a credential for anything.
ANTHROPIC = "sk-ant-live-supersecret-1234567890"
TWELVE_DATA = "td-live-supersecret-1234567890"
FINNHUB = "fh-live-supersecret-1234567890"
DEEPGRAM = "dg-live-supersecret-1234567890"

ALL_SECRETS = (ANTHROPIC, TWELVE_DATA, FINNHUB, DEEPGRAM)

#: Every credential field, and the fake planted in it.
CREDENTIAL_FIELDS = {
    "anthropic_api_key": ANTHROPIC,
    "twelve_data_api_key": TWELVE_DATA,
    "finnhub_api_key": FINNHUB,
    "deepgram_api_key": DEEPGRAM,
}


def loaded() -> Settings:
    """A fully-credentialled `Settings`, built without touching `.env`."""
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        ANTHROPIC_API_KEY=ANTHROPIC,
        TWELVE_DATA_API_KEY=TWELVE_DATA,
        FINNHUB_API_KEY=FINNHUB,
        DEEPGRAM_API_KEY=DEEPGRAM,
    )


def assert_no_secret_in(text: str, *, where: str) -> None:
    for secret in ALL_SECRETS:
        assert secret not in text, f"a credential leaked through {where}"
    # The distinctive middle of every fake, in case a renderer truncates.
    assert "supersecret" not in text, f"a credential leaked through {where}"


# ------------------------------------------------------ the exposure itself


def test_repr_masks_every_credential() -> None:
    assert_no_secret_in(repr(loaded()), where="repr(Settings)")


def test_str_masks_every_credential() -> None:
    assert_no_secret_in(str(loaded()), where="str(Settings)")


def test_an_f_string_masks_every_credential() -> None:
    """The accident this is really guarding against."""
    settings = loaded()
    assert_no_secret_in(f"settings were {settings}", where="an f-string")


def test_model_dump_masks_every_credential() -> None:
    dumped = loaded().model_dump()
    assert_no_secret_in(str(dumped), where="model_dump()")
    for field in CREDENTIAL_FIELDS:
        assert isinstance(dumped[field], SecretStr)


def test_model_dump_json_masks_every_credential() -> None:
    assert_no_secret_in(loaded().model_dump_json(), where="model_dump_json()")


def test_a_traceback_holding_settings_masks_every_credential() -> None:
    """A `Settings` in a live frame is the realistic leak path, not a `print`."""
    try:
        settings = loaded()
        raise RuntimeError(f"boom while configured as {settings!r}")
    except RuntimeError as exc:
        rendered = "".join(traceback.format_exception(exc))
    assert_no_secret_in(rendered, where="a formatted traceback")


@pytest.mark.parametrize("field", sorted(CREDENTIAL_FIELDS))
def test_each_credential_is_masked_individually(field: str) -> None:
    assert_no_secret_in(repr(getattr(loaded(), field)), where=f"repr({field})")


def test_every_api_key_field_is_a_secret() -> None:
    """Forward-looking: a fifth provider added as a plain `str` fails here.

    Keyed off the naming convention every credential in this file already
    follows, so the guarantee covers fields that do not exist yet.
    """
    settings = get_settings()
    key_fields = [name for name in type(settings).model_fields if name.endswith("_api_key")]
    assert len(key_fields) == len(CREDENTIAL_FIELDS)
    for name in key_fields:
        assert isinstance(getattr(settings, name), SecretStr), f"{name} is not a SecretStr"


# ------------------------------------------- the value is still there to use


@pytest.mark.parametrize(("field", "secret"), sorted(CREDENTIAL_FIELDS.items()))
def test_the_real_value_survives_the_round_trip(field: str, secret: str) -> None:
    assert getattr(loaded(), field).get_secret_value() == secret


def test_absent_credentials_are_falsy_without_being_unwrapped() -> None:
    """ "Is this configured?" must never have to materialise the value."""
    blank = Settings(_env_file=None)  # type: ignore[call-arg]
    for field in CREDENTIAL_FIELDS:
        assert bool(getattr(blank, field)) is False
    for field in CREDENTIAL_FIELDS:
        assert bool(getattr(loaded(), field)) is True


def test_non_credential_settings_are_untouched() -> None:
    settings = loaded()
    assert settings.twelve_data_base_url == "https://api.twelvedata.com"
    assert settings.anthropic_model == "claude-opus-5"
    assert "https://api.twelvedata.com" in repr(settings)


# ------------------------------------------------------------ still loading


def test_environment_variables_still_load(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", ANTHROPIC)
    monkeypatch.setenv("DEEPGRAM_API_KEY", DEEPGRAM)

    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.anthropic_api_key.get_secret_value() == ANTHROPIC
    assert settings.deepgram_api_key.get_secret_value() == DEEPGRAM


def test_an_env_file_still_loads(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text(
        f"ANTHROPIC_API_KEY={ANTHROPIC}\nTWELVE_DATA_API_KEY={TWELVE_DATA}\n", encoding="utf-8"
    )

    settings = Settings(_env_file=env)  # type: ignore[call-arg]

    assert settings.anthropic_api_key.get_secret_value() == ANTHROPIC
    assert settings.twelve_data_api_key.get_secret_value() == TWELVE_DATA
    assert_no_secret_in(repr(settings), where="a Settings loaded from .env")


# --------------------------------------- providers still receive the secret


def test_wiring_hands_the_real_key_to_the_market_data_provider() -> None:
    service = build_analysis_service(loaded())

    assert service is not None
    assert service._market_data._api_key == TWELVE_DATA  # type: ignore[attr-defined]


def test_wiring_hands_the_real_key_to_the_reasoning_sdk() -> None:
    """`ClaudeReasoningProvider` keeps nothing on itself, so this reads the
    SDK client it handed the credential to."""
    service = build_analysis_service(loaded())

    assert service is not None
    assert service._reasoning_provider._client.api_key == ANTHROPIC  # type: ignore[attr-defined]


def test_wiring_still_declines_when_a_credential_is_absent() -> None:
    assert analysis_available(loaded()) is True
    assert build_analysis_service(Settings(_env_file=None)) is None  # type: ignore[call-arg]
    assert analysis_available(Settings(_env_file=None)) is False  # type: ignore[call-arg]


def test_the_app_hands_the_real_deepgram_key_to_the_turn_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DEEPGRAM_API_KEY", DEEPGRAM)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    monkeypatch.setenv("TWELVE_DATA_API_KEY", "")

    app = create_app()

    routes = {route.path for route in app.routes}
    assert {"/health", "/v1/voice/turn", "/v1/voice/turn/text"} <= routes


# ------------------------------------------------- nothing leaks on failure


def test_a_settings_validation_error_never_echoes_a_credential() -> None:
    """An invalid non-credential field must not drag the keys into the error."""
    with pytest.raises(Exception) as caught:  # noqa: PT011 - pydantic's own type
        Settings(
            _env_file=None,  # type: ignore[call-arg]
            ANTHROPIC_API_KEY=ANTHROPIC,
            DEEPGRAM_API_KEY=DEEPGRAM,
            ORION_KILL_SWITCH="maybe",
        )

    assert_no_secret_in(str(caught.value), where="a Settings ValidationError")
    assert_no_secret_in(repr(caught.value), where="repr of a Settings ValidationError")


def _executable_code(source_path: Path) -> str:
    """The file's executable tokens only -- no comments, no string literals."""
    skip = {tokenize.COMMENT, tokenize.STRING, tokenize.NL, tokenize.NEWLINE}
    fstring_middle = getattr(tokenize, "FSTRING_MIDDLE", None)
    if fstring_middle is not None:
        skip.add(fstring_middle)
    with source_path.open("rb") as handle:
        return " ".join(
            token.string for token in tokenize.tokenize(handle.readline) if token.type not in skip
        )


def test_the_source_unwraps_a_secret_only_at_the_two_composition_roots() -> None:
    """`get_secret_value()` is a deliberate, countable act.

    Two call sites, both handing the value straight to the object that needs
    it. A third appearing anywhere in `src/` means a credential started
    travelling as a plain string again, which is the whole thing this change
    prevents.
    """
    src = Path(__file__).resolve().parents[1] / "src" / "orion"
    callers: dict[str, int] = {}
    for path in sorted(src.rglob("*.py")):
        # Comments and docstrings are stripped before counting: several modules
        # here *explain* where the unwrap happens, and a raw substring scan
        # would count the explanation. Same tokenize idiom the interface's
        # import-hygiene suite already uses.
        count = _executable_code(path).count("get_secret_value ( )")
        if count:
            callers[path.relative_to(src).as_posix()] = count

    assert callers == {"interface/app.py": 1, "orchestrator/wiring.py": 2}
