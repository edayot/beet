__all__ = [
    "MCDOC_ARGUMENT_TYPES",
    "McdocChecker",
    "ValidationIssue",
    "resolve_mcdoc_type",
]


import json
import re
from collections.abc import Iterator
from dataclasses import dataclass
from difflib import get_close_matches
from importlib import import_module
from typing import TYPE_CHECKING, Any

from pydantic import TypeAdapter, ValidationError
from pydantic_core import ErrorDetails
from tokenstream import explain_patterns, set_location

from .ast import AstCommand, AstJson, AstNbt, AstNode
from .diagnostic import Diagnostic
from .dispatch import Reducer, rule

if TYPE_CHECKING:
    from .api import Mecha


MCDOC_ARGUMENT_TYPES: dict[str, str] = {
    "minecraft:context_float_provider": "::vanilla_mcdoc::java::data::number_provider::FloatNumberProvider",
    "minecraft:context_int_provider": "::vanilla_mcdoc::java::data::number_provider::IntNumberProvider",
}

TAG_REGEX = re.compile(r"'([^']*)'")


def resolve_mcdoc_type(path: str) -> Any:
    """Import the vanilla_mcdoc model of the given mcdoc path.

    For example "::vanilla_mcdoc::java::data::loot::LootTable" resolves to the `LootTable` type
    of the `vanilla_mcdoc.data.loot.LootTable` module.
    """
    _, module_name, path = path.split("::", 2)
    parts = path.removeprefix("java::").split("::")
    module = import_module(".".join([module_name, *parts]))
    return getattr(module, parts[-1])


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
        return self.type.endswith("_type") or self.type == "union_tag_not_found"

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
    """Checker that validates nbt and json values against their mcdoc type."""

    mc: Mecha
    adapters: dict[str, TypeAdapter[Any]]

    def __init__(self, mc: Mecha):
        super().__init__()
        self.mc = mc
        self.adapters = {}

    def get_adapter(self, mcdoc_type: str) -> TypeAdapter[Any]:
        if mcdoc_type not in self.adapters:
            self.adapters[mcdoc_type] = TypeAdapter(resolve_mcdoc_type(mcdoc_type))
        return self.adapters[mcdoc_type]

    @rule(AstCommand)
    def mcdoc_validation(self, node: AstCommand):
        prototype = self.mc.spec.prototypes.get(node.identifier)
        if not prototype:
            return

        for i, argument in enumerate(node.arguments):
            if not isinstance(argument, AstNbt):
                continue
            tree = self.mc.spec.tree.get(prototype.get_argument(i).scope)
            if not tree or not tree.parser:
                continue
            if mcdoc_type := MCDOC_ARGUMENT_TYPES.get(tree.parser):
                yield from self.validate(mcdoc_type, argument)

    def validate(self, mcdoc_type: str, node: AstNbt | AstJson) -> Iterator[Diagnostic]:
        """Validate the nbt or json node and report errors at the invalid nodes."""
        value = node.unpack()
        try:
            self.get_adapter(mcdoc_type).validate_python(value)
        except ValidationError as exc:
            name = mcdoc_type.rsplit("::", 1)[-1]
            issues = [
                ValidationIssue.from_details(details, value) for details in exc.errors()
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
