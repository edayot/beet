# <img src="https://github.com/vberlier/beet/blob/master/docs/assets/logo.svg" alt="beet logo" width="30"> beet

[![Build Status](https://travis-ci.com/vberlier/beet.svg?token=HSyYhdxSKy5kTTrkmWq7&branch=master)](https://travis-ci.com/vberlier/beet)
[![PyPI](https://img.shields.io/pypi/v/beet.svg)](https://pypi.org/project/beet/)
[![PyPI - Python Version](https://img.shields.io/pypi/pyversions/beet.svg)](https://pypi.org/project/beet/)
[![Code style: black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/ambv/black)
[![Checked with mypy](http://www.mypy-lang.org/static/mypy_badge.svg)](http://mypy-lang.org/)

> The Minecraft pack development kit.

## Introduction

As Minecraft's vanilla customization capabilities keep growing, it's becoming more and more apparent that [resource packs](https://minecraft.gamepedia.com/Resource_Pack) and [data packs](https://minecraft.gamepedia.com/Data_Pack) can be pretty limiting as an _authoring_ format. Their simple structure allows them to fulfill their initial objective as a _distribution_ format, but without the ability to parametrize or create abstractions over assets and data pack resources, the reusability and interoperability of community-created projects and libraries is greatly limited.

The community is tackling the problem by building independent tooling left and right, from command pre-processors to frameworks of all kinds and full-blown programming languages. However, there's no silver bullet and in situations where a combination of these tools could actually provide the most suited abstractions, the separate toolchains and the poor interoperability make it difficult for them to coexist.

The `beet` project is meant to serve as a platform for building interoperable higher-level frameworks by providing a flexible composition model and a unified, user-friendly development workflow.

### Library

> [Documentation]()

```python
from beet import ResourcePack, Texture

with ResourcePack(path="stone.zip") as assets:
    assets["minecraft:block/stone"] = Texture(source_path="custom.png")
```

The `beet` library provides carefully crafted abstractions for working with Minecraft resource packs and data packs in Python.

- Create, read, edit and merge resource packs and data packs
- Handle zipped and unzipped packs
- Fast and lazy by default, files are transparently loaded when needed
- Statically typed API enabling rich intellisense and autocompletion

### Toolchain

> [Documentation]()

```python
from beet import Context, Function

def greet(ctx: Context):
    ctx.data["greet:hello"] = Function(["say hello"], tags=["minecraft:load"])
```

The `beet` toolchain makes it easy to create configurable resource packs and data packs by composing pack generators.

- Write simple functions that can edit or inspect the generated resource pack and data pack
- Cache expensive computations and heavy files with a versatile caching API
- Automatically rebuild the project on file changes with watch mode
- Link the project to Minecraft to synchronize the generated resource pack and data pack

## Installation

The package can be installed with `pip`.

```bash
$ pip install beet
```

You can make sure that `beet` was successfully installed by trying to use the toolchain from the command-line.

```bash
$ beet --help
Usage: beet [OPTIONS] COMMAND [ARGS]...

  The beet toolchain.

Options:
  -C, --directory DIRECTORY  The project directory.
  --version                  Show the version and exit.
  --help                     Show this message and exit.

Commands:
  build  Build the current project.
  cache  Inspect or clear the cache.
  init   Initialize a new project.
  link   Link the generated resource pack and data pack to Minecraft.
  watch  Watch the project directory and rebuild on file changes.
```

## Contributing

Contributions are welcome. This project uses [`poetry`](https://python-poetry.org).

```bash
$ poetry install
```

You can run the tests with `poetry run pytest`. We use [`pytest-minecraft`](https://github.com/vberlier/pytest-minecraft) to run tests against actual Minecraft releases.

```bash
$ poetry run pytest
$ poetry run pytest --minecraft-latest
```

The project must type-check with [`mypy`](http://mypy-lang.org) and [`pylint`](https://www.pylint.org) shouldn't report any error.

```bash
$ poetry run mypy
$ poetry run pylint beet tests
```

The code follows the [`black`](https://github.com/psf/black) code style.

```bash
$ poetry run black beet tests
$ poetry run black --check beet tests
```

---

License - [MIT](https://github.com/vberlier/beet/blob/master/LICENSE)
