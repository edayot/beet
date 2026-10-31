__all__ = [
    "McdocChecker",
    "MCDOC_ARGUMENT_TYPES",
]


import json
import re
from difflib import get_close_matches
from typing import TYPE_CHECKING, Any, Dict, Iterator, Optional

import vanilla_mcdoc.data.number_provider.FloatNumberProvider
import vanilla_mcdoc.data.number_provider.IntNumberProvider
from pydantic import TypeAdapter, ValidationError
from pydantic_core import ErrorDetails
from tokenstream import explain_patterns, set_location
import vanilla_mcdoc.data.number_provider.IntNumberProvider

from .ast import (
    AstCommand,
    AstNbt,
    AstNbtByteArray,
    AstNbtCompound,
    AstNbtIntArray,
    AstNbtList,
    AstNbtLongArray,
)
from .diagnostic import Diagnostic
from .dispatch import Reducer, rule

if TYPE_CHECKING:
    from .api import Mecha


MCDOC_ARGUMENT_TYPES: Dict[str, Any] = {
    "minecraft:context_float_provider": vanilla_mcdoc.data.number_provider.FloatNumberProvider.FloatNumberProvider,
    "minecraft:context_int_provider": vanilla_mcdoc.data.number_provider.IntNumberProvider.IntNumberProvider,
}

TAG_REGEX = re.compile(r"'([^']*)'")

ValidationIssue = tuple[ErrorDetails, list[bool]]

AstNbtSequence = (AstNbtList, AstNbtByteArray, AstNbtIntArray, AstNbtLongArray)


class McdocChecker(Reducer):
    """Checker that validates nbt command arguments against their mcdoc type."""

    mc: "Mecha"
    adapters: Dict[str, TypeAdapter[Any]]

    def __init__(self, mc: "Mecha"):
        super().__init__()
        self.mc = mc
        self.adapters = {}

    def get_adapter(self, parser: str) -> Optional[TypeAdapter[Any]]:
        if parser not in self.adapters:
            if (validator := MCDOC_ARGUMENT_TYPES.get(parser)) is None:
                return None
            self.adapters[parser] = TypeAdapter(validator)
        return self.adapters[parser]

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
            if not (adapter := self.get_adapter(tree.parser)):
                continue
            value = self.to_python(argument)
            try:
                adapter.validate_python(value)
            except ValidationError as exc:
                yield from self.format_validation_error(tree.parser, exc, argument, value)

    def to_python(self, node: AstNbt) -> Any:
        """Convert the nbt node to plain python values.

        Unlike `AstNbt.evaluate()`, this supports heterogeneous lists, which
        nbtlib can't represent.
        """
        if isinstance(node, AstNbtCompound):
            return {entry.key.value: self.to_python(entry.value) for entry in node.entries}
        if isinstance(node, AstNbtSequence):
            return [self.to_python(element) for element in node.elements]
        return node.evaluate()

    def format_validation_error(
        self,
        parser: str,
        exc: ValidationError,
        argument: AstNbt,
        value: Any,
    ) -> Iterator[Diagnostic]:
        validator = MCDOC_ARGUMENT_TYPES[parser]
        name = getattr(validator, "__name__", str(validator))
        errors = [(details, self.data_flags(value, details)) for details in exc.errors()]

        for error in self.best_errors(errors):
            path, msg = self.format_error(*error)
            path_str = "".join(json.dumps([item]) for item in path)
            note = f"{path_str} => {msg}" if path_str else msg
            diagnostic = Diagnostic("error", f"Invalid {name}.", notes=[note])
            yield set_location(diagnostic, self.find_node(argument, path))

    def find_node(self, node: AstNbt, path: list[Any]) -> AstNbt:
        """Return the node at the given data path, or the deepest one that exists."""
        for item in path:
            if isinstance(node, AstNbtCompound):
                entries = [entry for entry in node.entries if entry.key.value == item]
                if not entries:
                    break
                node = entries[-1].value
            elif isinstance(node, AstNbtSequence) and isinstance(item, int):
                if not 0 <= item < len(node.elements):
                    break
                node = node.elements[item]
            else:
                break
        return node

    def data_flags(self, value: Any, details: ErrorDetails) -> list[bool]:
        """Tell for each item of the error location if it's a key or an index of the data.

        The other items are labels that pydantic adds for union branches, like
        "float", "tagged-union[...]" or the tag of the branch.
        """
        loc = details["loc"]
        flags: list[bool] = []
        for i, item in enumerate(loc):
            if isinstance(value, dict) and item in value:
                value = value[item]
                flags.append(True)
            elif isinstance(value, list) and isinstance(item, int) and 0 <= item < len(value):
                value = value[item]
                flags.append(True)
            else:
                flags.append(details["type"] == "missing" and i == len(loc) - 1)
        return flags

    def best_errors(self, errors: list[ValidationIssue], depth: int = 0) -> list[ValidationIssue]:
        """Only keep the errors of the union branches that match the data the best."""
        result: list[ValidationIssue] = []
        groups: dict[Any, list[ValidationIssue]] = {}
        for details, flags in errors:
            if len(details["loc"]) > depth:
                groups.setdefault(details["loc"][depth], []).append((details, flags))
            else:
                result.append((details, flags))

        branches = list(groups.values())
        if branches and not branches[0][0][1][depth]:
            branches = [max(branches, key=self.branch_score)]

        for branch in branches:
            result.extend(self.best_errors(branch, depth + 1))
        return result

    def branch_score(self, errors: list[ValidationIssue]) -> tuple[int, bool]:
        """Prefer branches that go deeper into the data, then the ones where the type matches."""
        return (
            max(sum(flags) for _, flags in errors),
            not all(self.is_type_mismatch(details) for details, _ in errors),
        )

    def is_type_mismatch(self, details: ErrorDetails) -> bool:
        if details["type"] == "literal_error":
            return not isinstance(details["input"], (str, int, float))
        return details["type"].endswith("_type") or details["type"] == "union_tag_not_found"

    def format_error(
        self,
        details: ErrorDetails,
        flags: list[bool],
    ) -> tuple[list[Any], str]:
        path = [item for item, is_data in zip(details["loc"], flags) if is_data]

        if details["type"] == "union_tag_invalid" and "ctx" in details:
            tag = details["ctx"]["tag"]
            key = self.find_tag_key(details["input"], tag)
            if key is not None:
                path.append(key)
            msg = f"Unknown {key or 'tag'} {json.dumps(tag)}."
            expected = TAG_REGEX.findall(details["ctx"]["expected_tags"])
            if suggestion := self.suggest_tag(tag, expected):
                msg += f" Did you mean {json.dumps(suggestion)}?"
            else:
                patterns = tuple(json.dumps(tag) for tag in expected)
                msg += f" Expected {explain_patterns(patterns)}."
        else:
            msg = details["msg"]
            msg = msg[0].upper() + msg[1:] + "." * (not msg.endswith("."))
            if suggestion := self.suggest_key(details):
                msg += f" Did you misspell {json.dumps(suggestion)}?"

        return path, msg

    def suggest_tag(self, tag: str, expected: list[str]) -> Optional[str]:
        """Return the expected tag that looks like a misspelling of the given tag."""
        # Compare without the namespace, otherwise all the tags look alike
        paths = {name.removeprefix("minecraft:"): name for name in expected}
        matches = get_close_matches(tag.removeprefix("minecraft:"), list(paths), n=1)
        return paths[matches[0]] if matches else None

    def find_tag_key(self, compound: Any, tag: str) -> Optional[str]:
        """Return the key of the compound that holds the tag of the tagged union."""
        if not isinstance(compound, dict):
            return None
        for key, value in compound.items():
            if isinstance(value, str) and tag in (value, f"minecraft:{value}"):
                return key
        return None

    def suggest_key(self, details: ErrorDetails) -> Optional[str]:
        """Return a key of the compound that looks like a misspelling of the missing field."""
        if details["type"] != "missing" or not details["loc"]:
            return None
        field_name = details["loc"][-1]
        compound = details.get("input")
        if not isinstance(field_name, str) or not isinstance(compound, dict):
            return None
        keys = [key for key in compound if isinstance(key, str) and key != field_name]
        matches = get_close_matches(field_name, keys, n=1)
        return matches[0] if matches else None
