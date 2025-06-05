from beet.core.utils import PathLikeFallback
from typing import Any

@classmethod
def __modify_schema__(cls: Any, field_schema: Any):
    field_schema.update(type='string')
PathLikeFallback.__modify_schema__ = __modify_schema__  # type: ignore

from beet import ProjectConfig

with open("json-schema.json", "w") as f:
    a = ProjectConfig.schema_json(indent=4)
    f.write(a)