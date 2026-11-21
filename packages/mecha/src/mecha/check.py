__all__ = [
    "McdocChecker",
    "ValidationIssue",
]


import json
import logging
import re
from collections.abc import Iterator
from dataclasses import dataclass
from difflib import get_close_matches
from pathlib import Path
from typing import TYPE_CHECKING, Any

from nbtlib import Byte, Double, Float, Int, Long, Short, String
from pydantic import TypeAdapter, ValidationError
from pydantic_core import ErrorDetails
from tokenstream import explain_patterns, set_location
from vanilla_mcdoc import VersionModels, load, mcdoc_export

from beet.core.utils import VersionNumber, split_version
from beet.toolchain.context import ErrorMessage

from .ast import (
    AstBlock,
    AstCommand,
    AstItemComponent,
    AstItemPredicate,
    AstItemPredicateTestComponent,
    AstItemPredicateTestPredicate,
    AstItemStack,
    AstJson,
    AstNbt,
    AstNbtCompound,
    AstNode,
    AstResourceLocation,
    AstSelector,
    NbtArrayValue,
)
from .diagnostic import Diagnostic
from .dispatch import Reducer, rule

if TYPE_CHECKING:
    from .api import Mecha


logger = logging.getLogger("mecha")

TAG_REGEX = re.compile(r"'([^']*)'")

NBT_TAGS: dict[type, str] = {
    Byte: "byte",
    Short: "short",
    Int: "int",
    Long: "long",
    Float: "float",
    Double: "double",
    String: "string",
}

# The commands whose nbt is the data of something a dispatcher has the type of: the
# dispatcher, the index of the argument that says which entry (None if no argument
# does), and whether it's only a part of the data (merged, or with defaults).
NBT_TARGETS: dict[str, tuple[str, int | None, bool]] = {
    "summon:entity:pos:nbt": ("minecraft:entity", 0, True),
    "data:merge:entity:target:nbt": ("minecraft:entity", 0, True),
    "data:merge:block:target:nbt": ("minecraft:block", None, True),
    "data:merge:storage:target:nbt": ("minecraft:storage", 0, True),
    "function:name:arguments": ("minecraft:macro_function", 0, False),
}


def nbt_kind(value: Any) -> str | None:
    """The tag of a value from `AstNbt.unpack_tagged()`, for vanilla_mcdoc's NBT checks."""
    if isinstance(value, NbtArrayValue):
        return value.kind
    if kind := NBT_TAGS.get(type(value)):
        return kind
    if isinstance(value, dict):
        return "compound"
    if isinstance(value, list):
        return "list"
    return None


def dispatch_key(node: AstNode) -> str | None:
    """The dispatcher entry an argument says, e.g. "minecraft:zombie" for `zombie` or
    `@e[type=zombie]`, and "minecraft:player" for `@a`. None if it can be anything."""
    if isinstance(node, AstResourceLocation):
        return None if node.is_tag else node.get_canonical_value()
    if isinstance(node, AstSelector):
        if node.variable in ("a", "p", "r"):
            return "minecraft:player"
        types = [
            argument.value
            for argument in node.arguments
            if argument.key.value == "type" and not argument.inverted
        ]
        if len(types) == 1:
            return dispatch_key(types[0]) if types[0] else None
    return None


def quote(value: Any) -> str:
    return json.dumps(value)


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    """Pydantic error, with what each item of its location refers to in the data."""

    details: ErrorDetails
    flags: list[bool]

    @classmethod
    def from_details(cls, details: ErrorDetails, value: Any) -> ValidationIssue:
        """Tell for each item of the error location if it's a key or an index of the data.

        The other items are labels that pydantic adds for union branches, like
        "float", "tagged-union[...]" or the tag of the branch.
        """
        loc = details["loc"]
        flags: list[bool] = []
        for i, item in enumerate(loc):
            if (
                isinstance(value, dict)
                and item in value
                or isinstance(value, list)
                and isinstance(item, int)
                and 0 <= item < len(value)
            ):
                value = value[item]
                flags.append(True)
            else:
                flags.append(details["type"] == "missing" and i == len(loc) - 1)
        return cls(details, flags)

    @property
    def type(self) -> str:
        return self.details["type"]

    @property
    def loc(self) -> tuple[str | int, ...]:
        return self.details["loc"]

    @property
    def path(self) -> list[str | int]:
        """Location of the error in the data, without the union labels."""
        return [item for item, is_data in zip(self.loc, self.flags) if is_data]

    @property
    def depth(self) -> int:
        """How deep in the data the error is."""
        return sum(self.flags)

    @property
    def is_type_mismatch(self) -> bool:
        """Whether the data isn't even the right type for the union branch."""
        if self.type == "literal_error":
            return not isinstance(self.details["input"], (str, int, float))
        return self.type.endswith("_type") or self.type in (
            "union_tag_not_found",
            "nbt_tag",
        )

    def is_data(self, index: int) -> bool:
        return self.flags[index]

    def format(self) -> tuple[list[str | int], str]:
        """Return the path of the invalid node and the message."""
        path = self.path

        if self.type == "union_tag_invalid" and "ctx" in self.details:
            tag = self.details["ctx"]["tag"]
            key = self.find_tag_key(tag)
            if key is not None:
                path.append(key)
            msg = f"Unknown {key or 'tag'} {quote(tag)}."
            expected = TAG_REGEX.findall(self.details["ctx"]["expected_tags"])
            if suggestion := self.suggest_tag(tag, expected):
                msg += f" Did you mean {quote(suggestion)}?"
            else:
                patterns = tuple(quote(tag) for tag in expected)
                msg += f" Expected {explain_patterns(patterns)}."
        else:
            msg = self.details["msg"]
            msg = msg[0].upper() + msg[1:] + "." * (not msg.endswith("."))
            if suggestion := self.suggest_key():
                msg += f" Did you misspell {quote(suggestion)}?"

        return path, msg

    def find_tag_key(self, tag: str) -> str | None:
        """Return the key of the compound that holds the tag of the tagged union."""
        compound = self.details["input"]
        if not isinstance(compound, dict):
            return None
        for key, value in compound.items():
            if isinstance(value, str) and tag in (value, f"minecraft:{value}"):
                return key
        return None

    def suggest_tag(self, tag: str, expected: list[str]) -> str | None:
        """Return the expected tag that looks like a misspelling of the given tag."""
        # Compare without the namespace, otherwise all the tags look alike
        paths = {name.removeprefix("minecraft:"): name for name in expected}
        matches = get_close_matches(tag.removeprefix("minecraft:"), list(paths), n=1)
        return paths[matches[0]] if matches else None

    def suggest_key(self) -> str | None:
        """Return a key of the compound that looks like a misspelling of the missing field."""
        if self.type != "missing" or not self.loc:
            return None
        field_name = self.loc[-1]
        compound = self.details.get("input")
        if not isinstance(field_name, str) or not isinstance(compound, dict):
            return None
        keys = [key for key in compound if isinstance(key, str) and key != field_name]
        matches = get_close_matches(field_name, keys, n=1)
        return matches[0] if matches else None


class McdocChecker(Reducer):
    """Checker that validates nbt and json values against their mcdoc type.

    The types come from vanilla_mcdoc: the models of the minecraft version, installed
    (`vanilla-mcdoc[26.3]`) or generated in `cache_dir` the first time they're needed.
    With the project's own mcdoc (`sources`, or the already exported `symbols`), they're
    generated with its types too. Plugins can add sources before the checks run.
    """

    mc: Mecha
    minecraft_version: VersionNumber
    cache_dir: Path | None
    sources: list[Path]
    symbols: Path | None
    adapters: dict[str, TypeAdapter[Any]]
    _models: VersionModels | None | bool

    def __init__(
        self, mc: Mecha, minecraft_version: VersionNumber, cache_dir: Path | None = None
    ):
        super().__init__()
        self.mc = mc
        self.minecraft_version = minecraft_version
        self.cache_dir = cache_dir
        self.sources = []
        self.symbols = None
        self.adapters = {}
        self._models = False

    def get_models(self) -> VersionModels | None:
        """The models of the minecraft version, or None if there are none (then nothing is validated).

        Loaded the first time, as a version that isn't installed is generated then.
        """
        if self._models is False:
            try:
                self._models = load(
                    self.minecraft_version
                    if isinstance(self.minecraft_version, str)
                    else split_version(self.minecraft_version),
                    symbols=self.get_symbols(),
                    cache_dir=self.cache_dir,
                )
            except LookupError as exc:
                logger.warning("No mcdoc validation: %s", exc)
                self._models = None
        return self._models  # pyright: ignore[reportReturnType]

    def get_symbols(self) -> Path | None:
        """The symbols.json of the project's own mcdoc, exported once (it's cached)."""
        if not self.sources:
            return self.symbols
        if not self.cache_dir:
            raise ErrorMessage("Exporting the project's mcdoc needs a cache directory.")
        try:
            return mcdoc_export.export(self.sources, self.cache_dir)
        except RuntimeError as exc:
            raise ErrorMessage(str(exc)) from exc

    def get_adapter(self, mcdoc_type: str) -> TypeAdapter[Any] | None:
        if mcdoc_type not in self.adapters:
            if not (models := self.get_models()):
                return None
            try:
                self.adapters[mcdoc_type] = TypeAdapter(models.load_symbol(mcdoc_type))
            except LookupError:
                return None
        return self.adapters[mcdoc_type]

    @rule(AstCommand)
    def mcdoc_validation(self, node: AstCommand):
        prototype = self.mc.spec.prototypes.get(node.identifier)
        if not prototype or not (models := self.get_models()):
            return

        for i, argument in enumerate(node.arguments):
            tree = self.mc.spec.tree.get(prototype.get_argument(i).scope)
            parser = tree.parser if tree else None

            if isinstance(argument, AstSelector):
                yield from self.validate_selector(models, argument)
            elif isinstance(argument, AstBlock):
                predicate = parser == "minecraft:block_predicate"
                yield from self.validate_block(models, argument, predicate)
            elif isinstance(argument, AstItemStack):
                yield from self.validate_item_stack(models, argument)
            elif isinstance(argument, AstItemPredicate):
                yield from self.validate_item_predicate(models, argument)
            elif isinstance(argument, AstNbt):
                if parser and (mcdoc_type := models.argument_symbol(parser)):
                    yield from self.validate(mcdoc_type, argument)
                elif target := NBT_TARGETS.get(node.identifier):
                    registry, index, partial = target
                    key = None if index is None else dispatch_key(node.arguments[index])
                    if key and (mcdoc_type := models.dispatcher_symbol(registry, key)):
                        yield from self.validate(mcdoc_type, argument, partial=partial)

    def validate_selector(
        self, models: VersionModels, selector: AstSelector
    ) -> Iterator[Diagnostic]:
        """The nbt of a selector is compared tag for tag with the entity's, which has more."""
        key = dispatch_key(selector)
        for argument in selector.arguments:
            if argument.key.value == "nbt" and isinstance(
                argument.value, AstNbtCompound
            ):
                if key and (
                    mcdoc_type := models.dispatcher_symbol("minecraft:entity", key)
                ):
                    yield from self.validate(
                        mcdoc_type, argument.value, exact=True, partial=True
                    )

    def validate_block(
        self, models: VersionModels, block: AstBlock, predicate: bool
    ) -> Iterator[Diagnostic]:
        """The nbt of a block is its block entity's, which has defaults (or more, in a predicate)."""
        key = dispatch_key(block.identifier)
        if block.data_tags and key:
            if mcdoc_type := models.dispatcher_symbol("minecraft:block", key):
                yield from self.validate(
                    mcdoc_type, block.data_tags, exact=predicate, partial=True
                )

    def validate_item_stack(
        self, models: VersionModels, item_stack: AstItemStack
    ) -> Iterator[Diagnostic]:
        for component in item_stack.arguments:
            if isinstance(component, AstItemComponent) and (
                key := dispatch_key(component.key)
            ):
                if mcdoc_type := models.dispatcher_symbol(
                    "minecraft:data_component", key
                ):
                    yield from self.validate(mcdoc_type, component.value)

    def validate_item_predicate(
        self, models: VersionModels, item_predicate: AstItemPredicate
    ) -> Iterator[Diagnostic]:
        """`[damage=3]` tests a component's value, `[damage~{...}]` uses its predicate."""
        for alternatives in item_predicate.arguments:
            for test in alternatives.alternatives:
                if not test.value or not (key := dispatch_key(test.key)):
                    continue
                registry = (
                    "minecraft:data_component_predicate"
                    if isinstance(test, AstItemPredicateTestPredicate)
                    else "minecraft:data_component"
                )
                if isinstance(
                    test, (AstItemPredicateTestComponent, AstItemPredicateTestPredicate)
                ) and (mcdoc_type := models.dispatcher_symbol(registry, key)):
                    yield from self.validate(mcdoc_type, test.value)

    def validate(
        self,
        mcdoc_type: str,
        node: AstNbt | AstJson,
        exact: bool = False,
        partial: bool = False,
    ) -> Iterator[Diagnostic]:
        """Validate the nbt or json node against the mcdoc type (e.g. "::java::data::loot::LootTable")
        and report errors at the invalid nodes.

        The tags of nbt are checked too, and with `exact` (in a predicate) they have to be the
        field's own. With `partial` (e.g. data merge), the fields of compounds can be missing.
        """
        if not (adapter := self.get_adapter(mcdoc_type)):
            return
        if isinstance(node, AstNbt):
            value = node.unpack_tagged()
            context = {"nbt_kind": nbt_kind, "nbt_exact": exact}
        else:
            value = node.unpack()
            context = None
        try:
            adapter.validate_python(value, context=context)
        except ValidationError as exc:
            name = mcdoc_type.rsplit("::", 1)[-1]
            issues = [
                ValidationIssue.from_details(details, value) for details in exc.errors()
            ]
            if partial:
                # In a list, the items are whole, as lists are replaced rather than merged
                issues = [
                    issue
                    for issue in issues
                    if issue.type != "missing"
                    or any(isinstance(item, int) for item in issue.path)
                ]

            for issue in self.best_issues(issues):
                path, msg = issue.format()
                path_str = "".join(quote([item]) for item in path)
                note = f"{path_str} => {msg}" if path_str else msg
                diagnostic = Diagnostic("error", f"Invalid {name}.", notes=[note])
                yield set_location(diagnostic, self.find_node(node, path))

    def find_node(self, node: AstNbt | AstJson, path: list[str | int]) -> AstNode:
        """Return the node at the given data path, or the deepest one that exists."""
        for item in path:
            if (child := node.child(item)) is None:
                break
            node = child
        return node

    def best_issues(
        self,
        issues: list[ValidationIssue],
        depth: int = 0,
    ) -> list[ValidationIssue]:
        """Only keep the issues of the union branches that match the data the best."""
        result: list[ValidationIssue] = []
        groups: dict[Any, list[ValidationIssue]] = {}
        for issue in issues:
            if len(issue.loc) > depth:
                groups.setdefault(issue.loc[depth], []).append(issue)
            else:
                result.append(issue)

        branches = list(groups.values())
        if branches and not branches[0][0].is_data(depth):
            branches = [max(branches, key=self.branch_score)]

        for branch in branches:
            result.extend(self.best_issues(branch, depth + 1))
        return result

    def branch_score(self, issues: list[ValidationIssue]) -> tuple[int, bool]:
        """Prefer branches that go deeper into the data, then the ones where the type matches."""
        return (
            max(issue.depth for issue in issues),
            not all(issue.is_type_mismatch for issue in issues),
        )
