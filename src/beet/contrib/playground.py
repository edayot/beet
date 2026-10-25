"""Singleton for managing a background process running a minecraft server."""

__all__ = [
    "Playground",
    "PlaygroundArgs",
    "PlaygroundOptions",
    "clean",
    "link",
    "reload",
    "start",
    "stop",
]


import hashlib
import logging
import re
import subprocess
from collections.abc import Callable, Generator, Iterable
from contextlib import contextmanager
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from io import BytesIO
from pathlib import Path
from queue import Empty, Queue, ShutDown
from tempfile import TemporaryDirectory
from textwrap import dedent
from threading import Condition, Thread
from typing import Self
from uuid import UUID
from zipfile import ZIP_DEFLATED, ZipFile

from beet import Context, PackOverwrite, PluginOptions, ResourcePack, configurable
from beet.contrib.link import LinkManager
from beet.contrib.singleton import singleton
from beet.contrib.vanilla import Vanilla
from beet.core.utils import remove_path

RESOURCE_PACK_ID = UUID("62656574-706c-6179-6772-6f756e640000")

logger = logging.getLogger("playground")


class PlaygroundOptions(PluginOptions):
    server_properties: str = r"""
        white-list=false
        pause-when-empty-seconds=86400
        level-type=minecraft:flat
        generator-settings={"biome":"minecraft:the_void","layers":[{"block":"minecraft:air","height":1}],"features":true}
        difficulty=normal
        gamemode=creative
        motd=§a{{ project_name }} §4(beet)§r\n{{ project_directory | replace("\\", "\\\\") }}
    """
    port: int | None = None
    host: str = "localhost"
    resource_pack: bool = False
    resource_pack_port: int | None = None


@dataclass
class PlaygroundArgs:
    java: Path
    server_jar: Path
    server_properties: str
    world: Path
    port: int | None
    resource_pack_url: str | None = None


def beet_default(ctx: Context):
    ctx.require(start)


@configurable("playground", validator=PlaygroundOptions)
def start(ctx: Context, opts: PlaygroundOptions):
    """Plugin to start the playground."""
    link_manager = ctx.inject(LinkManager)
    vanilla = ctx.inject(Vanilla)
    cache = ctx.cache["playground"]

    args = PlaygroundArgs(
        java=vanilla.shared.java,
        server_jar=vanilla.shared.server_jar.path,
        server_properties=dedent(ctx.template.render_string(opts.server_properties)),
        world=Path(link_manager.world or cache.directory / "world").resolve(),
        port=opts.port,
    )

    playground = ctx.inject(singleton(Playground))
    if opts.resource_pack:
        args.resource_pack_url = playground.serve(opts.host, opts.resource_pack_port)
    playground.start(args)


def stop(ctx: Context):
    """Plugin to stop the playground."""
    playground = ctx.inject(singleton(Playground))
    playground.stop()


def link(ctx: Context):
    """Plugin to copy the data pack to the world opened in the playground."""
    cache = ctx.cache["playground"]
    dirty = cache.json.setdefault("dirty", [])

    playground = ctx.inject(singleton(Playground))
    with playground.cond:
        if playground.args:
            try:
                path = ctx.data.save(playground.args.world / "datapacks")
            except PackOverwrite:
                pass
            else:
                if cache.directory in path.parents:
                    path = "/".join(path.relative_to(cache.directory).parts)
                dirty.append(str(path))

            if playground.args.resource_pack_url:
                playground.resource_pack = _zip_pack(ctx.assets) if ctx.assets else None


def clean(ctx: Context):
    """Plugin to remove the data pack previously linked to the playground."""
    cache = ctx.cache["playground"]
    dirty = cache.json.setdefault("dirty", [])

    playground = ctx.inject(singleton(Playground))
    with playground.cond:
        remove_path(*[cache.directory / path for path in dirty])

    dirty.clear()


def reload(ctx: Context):
    """Plugin to make the playground reload data packs."""
    playground = ctx.inject(singleton(Playground))
    playground.run("reload")

    playground.update_resource_pack()


class Playground:
    cond: Condition
    proc: subprocess.Popen[str] | None
    args: PlaygroundArgs | None
    ready: bool

    listeners: list[Callable[[str], None]]

    http: ThreadingHTTPServer | None
    resource_pack: bytes | None
    resource_pack_hash: str | None

    def __init__(self):
        self.cond = Condition()
        self.proc = None
        self.args = None
        self.ready = False

        self.listeners = []

        self.http = None
        self.resource_pack = None
        self.resource_pack_hash = None

    def start(self, args: PlaygroundArgs):
        with self.cond:
            if self.args == args:
                return

            self.stop()

            level_name = str(args.world).replace("\\", "\\\\")

            server_properties = args.server_properties
            server_properties += f"\nlevel-name={level_name}\n"

            args.server_jar.with_name("eula.txt").write_text("eula=true\n")
            args.server_jar.with_name("server.properties").write_text(
                server_properties,
                encoding="utf-8",
            )

            cmd: list[str | Path] = [args.java]
            if args.resource_pack_url:
                # Enable the serverpack command
                cmd += ["-DMC_DEBUG_ENABLED=true", "-DMC_DEBUG_DEV_COMMANDS=true"]
                if agent := _build_agent(args.java, args.server_jar.parent):
                    cmd += [f"-javaagent:{agent}"]
            cmd += ["-jar", args.server_jar, "--nogui"]
            if args.port is not None:
                cmd += ["--port", str(args.port)]

            self.proc = subprocess.Popen(
                cmd,
                cwd=args.server_jar.parent,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="ignore",
            )
            self.args = args
            self.resource_pack_hash = None

            Thread(target=self._daemon, args=(self.proc,), daemon=True).start()

    def stop(self):
        with self.cond:
            if self.ready:
                self.run("stop")
            elif self.proc:
                self.proc.kill()
            self.cond.wait_for(lambda: self.proc is None)

    def run(self, command: str):
        with self.cond:
            self.cond.wait_for(lambda: self.ready or self.proc is None)
            if self.proc:
                assert self.proc.stdin
                self.proc.stdin.write(command.strip() + "\n")
                self.proc.stdin.flush()

    def serve(self, host: str, port: int | None = None) -> str:
        """Serve the resource pack over http and return its url."""
        with self.cond:
            if not self.http:
                self.http = ThreadingHTTPServer(("", port or 0), _handler(self))
                Thread(target=self.http.serve_forever, daemon=True).start()
            return f"http://{host}:{self.http.server_port}/resource_pack.zip"

    def update_resource_pack(self, force: bool = False):
        """Push the resource pack to connected players if it changed."""
        with self.cond:
            if not self.args or not self.args.resource_pack_url:
                return
            data = self.resource_pack
            sha1 = hashlib.sha1(data).hexdigest() if data is not None else None
            if sha1 == self.resource_pack_hash and not force:
                return
            if sha1:
                url = f"{self.args.resource_pack_url}?{sha1}"
                self.run(f'serverpack push "{url}" {RESOURCE_PACK_ID} {sha1}')
            elif self.resource_pack_hash:
                self.run(f"serverpack pop {RESOURCE_PACK_ID}")
            self.resource_pack_hash = sha1

    @contextmanager
    def log(self, timeout: float | None = None) -> Generator[Iterable[str]]:
        queue = Queue[str]()
        self.listeners.append(queue.put)
        try:
            yield _drain_queue(queue, timeout)
        finally:
            self.listeners.remove(queue.put)

    def _daemon(self, proc: subprocess.Popen[str]):
        game = logging.getLogger("game")
        game.addFilter(_filter_game_log)

        levels = {
            "DEBUG": logging.DEBUG,
            "INFO": logging.INFO,
            "WARN": logging.WARNING,
            "ERROR": logging.ERROR,
            "FATAL": logging.ERROR,
        }

        regex = re.compile(r"\[(.+?)\] \[.+?/(DEBUG|INFO|WARN|ERROR|FATAL)\]: (.+)")
        previous_level = None

        assert proc.stdout
        for line in proc.stdout:
            extra = {}

            if m := regex.match(line):
                level = levels.get(m[2])
                message = m[3]
                if message.startswith("Done ("):
                    with self.cond:
                        if not self.ready:
                            self.ready = True
                            self.cond.notify_all()
                elif message.endswith(" joined the game"):
                    self.update_resource_pack(force=True)
            else:
                level = previous_level
                message = line
                extra["continue"] = True

            if level is not None:
                game.log(level, message, extra=extra)

            previous_level = level

            for listener in self.listeners:
                listener(line)

        with self.cond:
            proc.wait()
            self.proc = None
            self.args = None
            self.ready = False
            self.cond.notify_all()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_):
        self.stop()
        if self.http:
            self.http.shutdown()
            self.http.server_close()
            self.http = None


def _handler(playground: Playground) -> type[BaseHTTPRequestHandler]:
    class ResourcePackHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            with playground.cond:
                data = playground.resource_pack
            if data is None:
                self.send_error(503, "Resource pack not built yet")
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, format: str, *args: object):
            pass

    return ResourcePackHandler


def _zip_pack(pack: ResourcePack) -> bytes:
    buffer = BytesIO()
    with ZipFile(buffer, mode="w", compression=ZIP_DEFLATED) as zip_file:
        pack.dump(zip_file)
    return buffer.getvalue()


def _build_agent(java: Path, directory: Path) -> Path | None:
    """Compile the java agent that fixes the serverpack command.

    See PlaygroundAgent.java for details. The agent is compiled with the javac of
    the minecraft java runtime and cached according to the hash of its source.
    Returns None if it can't be built, in which case the server runs unpatched.
    """
    source = files("beet.resources").joinpath("PlaygroundAgent.java").read_bytes()
    agent = directory / f"beet-agent-{hashlib.sha1(source).hexdigest()[:12]}.jar"
    if agent.is_file():
        return agent

    javac = java.with_name("javac" + java.suffix)
    with TemporaryDirectory() as tmp:
        build = Path(tmp)
        (build / "PlaygroundAgent.java").write_bytes(source)
        result = subprocess.run(
            [javac, "-d", build, build / "PlaygroundAgent.java"],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            logger.warning("Couldn't compile the playground agent.\n%s", result.stderr)
            return None

        tmp_agent = agent.with_suffix(".tmp")
        with ZipFile(tmp_agent, "w", ZIP_DEFLATED) as jar:
            manifest = "Manifest-Version: 1.0\nPremain-Class: PlaygroundAgent\n"
            jar.writestr("META-INF/MANIFEST.MF", manifest)
            for path in build.glob("*.class"):
                jar.write(path, path.name)
        tmp_agent.replace(agent)

    return agent


def _filter_game_log(record: logging.LogRecord) -> bool:
    record.msg = record.getMessage().removeprefix("[Not Secure] ")
    return True


def _drain_queue[T](queue: Queue[T], timeout: float | None = None) -> Iterable[T]:
    while True:
        try:
            yield queue.get(timeout=timeout)
        except ShutDown, Empty:
            break
