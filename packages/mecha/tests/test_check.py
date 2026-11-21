"""The mcdoc checker validates nbt and json against their types, which come from vanilla_mcdoc: the installed version's
models, or ones it generates in the cache for a version that isn't installed."""

import json
import logging
from pathlib import Path

import pytest

from beet import Function, run_beet

from mecha import DiagnosticCollection, Mecha


@pytest.fixture(scope="module")
def mcdoc_cache(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Shared, so each version is only generated once."""
    return tmp_path_factory.mktemp("mcdoc")


@pytest.fixture
def mc(mcdoc_cache: Path) -> Mecha:
    mc = Mecha(version="26.3")
    mc.mcdoc.cache_dir = mcdoc_cache
    return mc


def check(mc: Mecha, source: str) -> list[str]:
    diagnostics = DiagnosticCollection()
    mc.compile(Function(source), report=diagnostics)
    return [f"{d.message} {' '.join(d.notes)}" for d in diagnostics.exceptions]


def test_valid_argument(mc: Mecha):
    assert check(mc, 'compute default float {type: "max", inputs: [1.2, 2.3]}') == []


def test_invalid_argument(mc: Mecha):
    errors = check(mc, 'compute default float {type: "maxx", inputs: [1.2]}')
    assert len(errors) == 1
    assert errors[0].startswith("Invalid ContextFloatProvider.")


def test_the_diagnostic_is_a_rule(mc: Mecha):
    """So `rules: {mcdoc_validation: warn}` can make them warnings (vanilla-mcdoc isn't always right)."""
    diagnostics = DiagnosticCollection()
    mc.compile(Function('compute default float {type: "maxx"}'), report=diagnostics)
    assert [d.rule for d in diagnostics.exceptions] == ["mcdoc_validation"]


def test_models_are_for_the_mecha_version(mc: Mecha):
    models = mc.mcdoc.get_models()
    assert models is not None and models.version == "26.3"


def test_unknown_version_is_not_validated(
    mcdoc_cache: Path, caplog: pytest.LogCaptureFixture
):
    mc = Mecha(version="26.3")
    mc.mcdoc.cache_dir = mcdoc_cache
    mc.mcdoc.minecraft_version = "0.1"
    with caplog.at_level(logging.WARNING, logger="mecha"):
        assert check(mc, 'compute default float {type: "maxx"}') == []
        assert check(mc, 'compute default float {type: "maxx"}') == []
    assert len([r for r in caplog.records if "0.1" in r.getMessage()]) == 1  # Said once


def test_json_files_are_validated_by_their_folder(tmp_path: Path):
    """Every json resource is validated against the type of its folder, e.g. a loot table, without declaring it."""
    pack = tmp_path / "pack"
    (pack / "data" / "demo" / "loot_table").mkdir(parents=True)
    (pack / "data" / "demo" / "loot_table" / "broken.json").write_text(
        json.dumps({"pools": 3})
    )
    (pack / "data" / "demo" / "loot_table" / "fine.json").write_text(
        json.dumps({"pools": []})
    )
    config = {
        "data_pack": {"load": str(pack)},
        "require": ["mecha.contrib.json_files"],
        "pipeline": ["mecha"],
        "meta": {"mecha": {"rules": {"mcdoc_validation": "warn"}}},
    }
    with run_beet(config, directory=tmp_path) as ctx:
        diagnostics = ctx.inject(Mecha).diagnostics.exceptions
    assert [(d.level, d.message, Path(d.filename or "").name) for d in diagnostics] == [
        ("warn", "Invalid LootTable.", "broken.json"),
    ]
