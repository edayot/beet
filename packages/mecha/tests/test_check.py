"""The mcdoc checker validates nbt and json against their types, which come from vanilla_mcdoc: the installed version's
models, or ones it generates in the cache for a version that isn't installed."""

import json
import logging
import shutil
from pathlib import Path
from typing import Any

import pytest
import vanilla_mcdoc
from pytest_insta import SnapshotFixture
from vanilla_mcdoc import mcdoc_export

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


# NBT: the type comes from a dispatcher (the entity of summon, the block of setblock...), and
# the tags are checked too (1.5f isn't an int, "yes" isn't a boolean)


def errors_of(mc: Mecha, source: str) -> list[str]:
    return [error.split(" ", 2)[1] for error in check(mc, source)]


def test_summon(mc: Mecha):
    assert check(mc, "summon zombie ~ ~ ~ {CanBreakDoors: 1b, Health: 20}") == []
    assert errors_of(mc, 'summon zombie ~ ~ ~ {CanBreakDoors: "yes"}') == ["Zombie."]
    assert errors_of(mc, "summon zombie ~ ~ ~ {CanBreakDoors: 2b}") == ["Zombie."]


def test_data_merge(mc: Mecha):
    """Only part of the data, so missing fields are fine."""
    target = "@e[type=zombie,limit=1]"
    assert check(mc, f"data merge entity {target} {{CanBreakDoors: 1b}}") == []
    assert errors_of(mc, f'data merge entity {target} {{CanBreakDoors: "yes"}}') == [
        "Zombie."
    ]
    assert check(mc, 'data merge entity @s {CanBreakDoors: "yes"}') == []  # Any entity


def test_block_nbt(mc: Mecha):
    assert check(mc, "setblock ~ ~ ~ chest{Items: []}") == []
    assert len(check(mc, "setblock ~ ~ ~ chest{Items: 3}")) == 1


def test_selector_nbt_is_compared_tag_for_tag(mc: Mecha):
    """In a predicate, 1 doesn't match OnGround:1b, so it's an error, unlike in summon."""
    assert check(mc, "kill @e[type=zombie,nbt={CanBreakDoors: 1b}]") == []
    assert len(check(mc, "kill @e[type=zombie,nbt={CanBreakDoors: 1}]")) == 1
    assert check(mc, "kill @a[nbt={XpLevel: 3}]") == []  # @a is players
    assert len(check(mc, "kill @a[nbt={XpLevel: 3b}]")) == 1


def test_item_components(mc: Mecha):
    assert check(mc, "give @s diamond_sword[damage=3]") == []
    assert len(check(mc, 'give @s diamond_sword[damage="a lot"]')) == 1


# The user's own mcdoc: `meta.mecha.mcdoc.sources` (.mcdoc files, exported by mcdoc-cli,
# with Node.js) or `meta.mecha.mcdoc.symbols` (an already exported symbols.json)

USER_FUNCTION = """\
data merge storage my_pack:data {count: COUNT}
function my_pack:greet {name: NAME}
"""


def user_symbols(path: Path) -> Path:
    """What mcdoc-cli exports for vanilla's types and these, without needing Node.js:

    dispatch minecraft:storage[my_pack:data] to struct Data { count: int @ 0.. }
    dispatch minecraft:macro_function[my_pack:greet] to struct Greet { name: string }
    """
    symbols = json.loads(
        (
            Path(vanilla_mcdoc.__file__).parent / "codegen" / "data" / "symbols.json"
        ).read_text()
    )
    symbols["mcdoc"]["::my_pack::Data"] = {
        "kind": "struct",
        "fields": [
            {
                "kind": "pair",
                "key": "count",
                "type": {"kind": "int", "valueRange": {"kind": 0, "min": 0}},
            }
        ],
    }
    symbols["mcdoc"]["::my_pack::Greet"] = {
        "kind": "struct",
        "fields": [{"kind": "pair", "key": "name", "type": {"kind": "string"}}],
    }
    dispatchers = symbols["mcdoc/dispatcher"]
    dispatchers["minecraft:storage"]["my_pack:data"] = {
        "kind": "reference",
        "path": "::my_pack::Data",
    }
    dispatchers["minecraft:macro_function"] = {
        "my_pack:greet": {"kind": "reference", "path": "::my_pack::Greet"}
    }
    path.write_text(json.dumps(symbols))
    return path


def build_with_user_mcdoc(tmp_path: Path, mcdoc: dict[str, Any], count: str, name: str):
    function = tmp_path / "pack" / "data" / "my_pack" / "function"
    function.mkdir(parents=True)
    source = USER_FUNCTION.replace("COUNT", count).replace("NAME", name)
    (function / "test.mcfunction").write_text(source)
    (function / "greet.mcfunction").write_text("$say hello $(name)\n")
    config = {
        "data_pack": {"load": str(tmp_path / "pack")},
        "pipeline": ["mecha"],
        "meta": {"mecha": {"mcdoc": mcdoc, "rules": {"mcdoc_validation": "warn"}}},
    }
    with run_beet(config, directory=tmp_path) as ctx:
        return [d.message for d in ctx.inject(Mecha).diagnostics.exceptions]


def test_user_symbols(tmp_path: Path):
    symbols = user_symbols(tmp_path / "symbols.json")
    assert (
        build_with_user_mcdoc(tmp_path / "a", {"symbols": str(symbols)}, "3", '"Steve"')
        == []
    )
    assert build_with_user_mcdoc(
        tmp_path / "b", {"symbols": str(symbols)}, "-1", "3"
    ) == [
        "Invalid Data.",
        "Invalid Greet.",
    ]


def test_user_sources_are_exported(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """The .mcdoc files are exported once (see vanilla_mcdoc.mcdoc_export), relative to the project."""
    symbols = user_symbols(tmp_path / "symbols.json")
    exported: list[list[Path]] = []

    def export(sources: list[Path], cache_dir: Path) -> Path:
        exported.append(sources)
        return symbols

    monkeypatch.setattr(mcdoc_export, "export", export)
    messages = build_with_user_mcdoc(tmp_path, {"sources": ["mcdoc"]}, "-1", '"Steve"')
    assert messages == ["Invalid Data."]
    assert exported == [[tmp_path / "mcdoc"]]


@pytest.mark.skipif(shutil.which("npx") is None, reason="Needs Node.js")
def test_user_sources_for_real(tmp_path: Path):
    (tmp_path / "mcdoc" / "my_pack").mkdir(parents=True)
    (tmp_path / "mcdoc" / "my_pack" / "storage.mcdoc").write_text(
        "dispatch minecraft:storage[my_pack:data] to struct Data {\n\tcount: int @ 0..,\n}\n"
        "dispatch minecraft:macro_function[my_pack:greet] to struct Greet {\n\tname: string,\n}\n"
    )
    messages = build_with_user_mcdoc(tmp_path, {"sources": ["mcdoc"]}, "-1", "3")
    assert messages == ["Invalid Data.", "Invalid Greet."]


# The commands of tests/resources/mcdoc_invalid_commands.mcfunction (one per paragraph),
# checked with examples/mcdoc_custom's own mcdoc: each one's errors, as mecha reports them

INVALID_COMMANDS = [
    command.strip()
    for command in Path("tests/resources/mcdoc_invalid_commands.mcfunction")
    .read_text()
    .split("\n\n")
    if command.strip()
]


@pytest.fixture(scope="module")
def custom_mcdoc_mc():
    with run_beet(directory="examples/mcdoc_custom") as ctx:
        yield ctx.inject(Mecha)


@pytest.mark.skipif(shutil.which("npx") is None, reason="Needs Node.js")
@pytest.mark.parametrize("source", INVALID_COMMANDS, ids=range(len(INVALID_COMMANDS)))
def test_invalid_commands(
    snapshot: SnapshotFixture, custom_mcdoc_mc: Mecha, source: str
):
    diagnostics = DiagnosticCollection()
    custom_mcdoc_mc.compile(Function(source), report=diagnostics)
    assert diagnostics.exceptions, "It should be invalid"
    report = [
        "\n".join(
            filter(None, [d.format_message(), d.format_code(source), d.format_notes()])
        )
        for d in diagnostics.exceptions
    ]
    assert snapshot() == "\n---\n".join([source, *report]) + "\n"
