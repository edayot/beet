__all__ = [
    "McdocChecker",
    "MCDOC_ARGUMENT_TYPES",
]


import json
import re
from difflib import get_close_matches
from typing import TYPE_CHECKING, Any, Dict, Optional

import vanilla_mcdoc.data.number_provider.FloatNumberProvider
from pydantic import TypeAdapter, ValidationError
from pydantic_core import ErrorDetails
from tokenstream import set_location

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
}

UNION_LABEL_REGEX = re.compile(r"^[\w-]+\[.*\]$")


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
            try:
                adapter.validate_python(self.to_python(argument))
            except ValidationError as exc:
                yield set_location(self.format_validation_error(tree.parser, exc), argument)

    def to_python(self, node: AstNbt) -> Any:
        """Convert the nbt node to plain python values.

        Unlike `AstNbt.evaluate()`, this supports heterogeneous lists, which
        nbtlib can't represent.
        """
        if isinstance(node, AstNbtCompound):
            return {entry.key.value: self.to_python(entry.value) for entry in node.entries}
        if isinstance(
            node, (AstNbtList, AstNbtByteArray, AstNbtIntArray, AstNbtLongArray)
        ):
            return [self.to_python(element) for element in node.elements]
        return node.evaluate()

    def format_validation_error(self, parser: str, exc: ValidationError) -> Diagnostic:
        validator = MCDOC_ARGUMENT_TYPES[parser]
        name = getattr(validator, "__name__", str(validator))
        notes: list[str] = []

        for details in exc.errors():
            # Drop union labels like "tagged-union[...]" that pydantic adds to the path
            path = "".join(
                json.dumps([item])
                for item in details["loc"]
                if not (isinstance(item, str) and UNION_LABEL_REGEX.match(item))
            )
            msg = details["msg"]
            msg = msg[0].upper() + msg[1:] + "." * (not msg.endswith("."))
            if suggestion := self.suggest_key(details):
                msg += f" Did you misspell {json.dumps(suggestion)}?"
            notes.append(f"{path} => {msg}" if path else msg)

        return Diagnostic("error", f"Invalid {name}.", notes=notes)

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
