"""Repository configuration contract for legacy CYT entry points."""

from dataclasses import dataclass, replace
import json
import math
import os
from pathlib import Path
import re


class ConfigError(ValueError):
    def __init__(self, filename, field, message, *, line=None, column=None):
        self.filename = str(filename)
        self.field = field
        self.line = line
        self.column = column
        self.message = message
        location = self.filename
        if line is not None:
            location += f":{line}:{column}"
        super().__init__(f"{location}: {field}: {message}")


@dataclass(frozen=True)
class ConfigPaths:
    log_dir: Path
    kismet_logs: str
    mac_ignore_list: Path
    ssid_ignore_list: Path


@dataclass(frozen=True)
class PathProvenance:
    source: Path
    log_dir: str
    kismet_logs: str
    mac_ignore_list: str
    ssid_ignore_list: str


@dataclass(frozen=True)
class TrackingWindows:
    recent_minutes: float
    medium_minutes: float
    old_minutes: float
    oldest_minutes: float


@dataclass(frozen=True)
class ConfigTiming:
    poll_interval_seconds: float
    tracking_list_rotation_cycles: int
    time_windows: TrackingWindows


@dataclass(frozen=True)
class BoundingBox:
    lat_min: float
    lat_max: float
    lon_min: float
    lon_max: float


@dataclass(frozen=True)
class RuntimeConfig:
    schema_version: int
    paths: ConfigPaths
    timing: ConfigTiming
    search: BoundingBox | None
    path_provenance: PathProvenance


class _ObjectPairs(list):
    """Keep object members distinct from arrays until duplicates are checked."""


def _member_field(parent, key):
    if isinstance(key, str) and re.fullmatch(r"[a-zA-Z_][a-zA-Z_0-9]*", key):
        return f"{parent}.{key}"
    return f"{parent}[{json.dumps(key)}]"


_JSON_TOKEN = re.compile(r'"(?:[^"\\]|\\.)*"|[{}\[\],:]|[^\s{}\[\],:"]+')


def _locate_error(text, error):
    """Find the field location on the error path after JSON syntax was checked."""
    tokens = iter(_JSON_TOKEN.finditer(text))
    locations = {}

    def visit(token, field, key_pos=None):
        locations[field] = token.start() if key_pos is None else key_pos
        if token.group() == "{":
            key = next(tokens)
            while key.group() != "}":
                member = _member_field(field, json.loads(key.group()))
                next(tokens)  # colon
                visit(next(tokens), member, key.start())
                if next(tokens).group() == "}":
                    break
                key = next(tokens)
        elif token.group() == "[":
            item = next(tokens)
            index = 0
            while item.group() != "]":
                visit(item, f"{field}[{index}]")
                if next(tokens).group() == "]":
                    break
                item = next(tokens)
                index += 1

    try:
        visit(next(tokens), "$")
    except (StopIteration, ValueError, RecursionError):
        return error
    field = error.field
    while field not in locations and "." in field:
        field = field.rsplit(".", 1)[0]
    if field not in locations:
        return error
    offset = locations[field]
    return ConfigError(error.filename, error.field, error.message,
                       line=text.count("\n", 0, offset) + 1,
                       column=offset - text.rfind("\n", 0, offset))


def parse_config(text: str, filename: str | Path) -> dict:
    """Parse JSON and reject unsafe JSON constructs without touching the filesystem."""
    try:
        document = json.loads(text, object_pairs_hook=_ObjectPairs)
    except json.JSONDecodeError as error:
        raise ConfigError(filename, "$", error.msg, line=error.lineno,
                          column=error.colno) from None
    except (ValueError, RecursionError):
        raise ConfigError(filename, "$", "JSON exceeds supported numeric or nesting limits") from None

    def unpack(value, field):
        if isinstance(value, _ObjectPairs):
            result = {}
            for key, member in value:
                member_field = _member_field(field, key)
                if key in result:
                    raise ConfigError(filename, member_field, "duplicate key; keep one definition")
                result[key] = unpack(member, member_field)
            return result
        if isinstance(value, list):
            return [unpack(member, f"{field}[{index}]") for index, member in enumerate(value)]
        if isinstance(value, float) and not math.isfinite(value):
            raise ConfigError(filename, field, "expected a finite JSON number")
        return value

    try:
        result = unpack(document, "$")
    except ConfigError as error:
        raise _locate_error(text, error) from None
    except RecursionError:
        raise ConfigError(filename, "$", "JSON exceeds supported nesting limits") from None
    if not isinstance(result, dict):
        raise ConfigError(filename, "$", "expected a JSON object")
    return result


def validate_config(document: dict, filename: str | Path) -> RuntimeConfig:
    """Validate the schema and return typed settings with file-supplied paths."""
    def fail(field, message):
        raise ConfigError(filename, field, message)

    def fields(value, field, required, optional=()):
        if not isinstance(value, dict):
            fail(field, "expected an object")
        for key in value:
            if field == "$" and key == "api_keys":
                fail("$.api_keys", "credentials belong outside config.json")
            if key not in required and key not in optional:
                fail(_member_field(field, key), "unknown field")
        for key in required:
            if key not in value:
                fail(f"{field}.{key}", "required field is missing")
        return value

    def path(value, field):
        if (not isinstance(value, str) or not value.strip() or
                any(ord(char) < 32 or 0xD800 <= ord(char) <= 0xDFFF for char in value)):
            fail(field, "expected a nonblank path string without control characters")
        result = Path(value)
        if result.anchor and not result.is_absolute():
            fail(field, "use a fully absolute path or a path relative to the configured directory")
        return value

    def number(value, field):
        if type(value) not in (int, float):
            fail(field, "expected a finite number")
        try:
            result = float(value)
        except OverflowError:
            fail(field, "number exceeds supported range")
        if not math.isfinite(result):
            fail(field, "expected a finite number")
        return result

    fields(document, "$", ("paths",), ("schema_version", "timing", "search"))
    version = document.get("schema_version", 1)
    if type(version) is not int or version not in (1, 2):
        fail("$.schema_version", "expected a supported integer version (1 or 2)")

    paths = fields(document["paths"], "$.paths", ("log_dir", "kismet_logs", "ignore_lists"),
                   ("base_dir",) if version == 1 else ())
    if "base_dir" in paths:
        path(paths["base_dir"], "$.paths.base_dir")  # v1 accepted it but never used it
    lists = fields(paths["ignore_lists"], "$.paths.ignore_lists", ("mac", "ssid"))
    provenance = PathProvenance(
        Path(filename),
        path(paths["log_dir"], "$.paths.log_dir"),
        path(paths["kismet_logs"], "$.paths.kismet_logs"),
        path(lists["mac"], "$.paths.ignore_lists.mac"),
        path(lists["ssid"], "$.paths.ignore_lists.ssid"),
    )
    config_paths = ConfigPaths(
        Path(provenance.log_dir),
        provenance.kismet_logs,
        Path(provenance.mac_ignore_list),
        Path(provenance.ssid_ignore_list),
    )

    if version == 1:
        poll_key, rotation_key = "check_interval", "list_update_interval"
        window_keys = ("recent", "medium", "old", "oldest")
    else:
        poll_key, rotation_key = "poll_interval_seconds", "tracking_list_rotation_cycles"
        window_keys = ("recent_minutes", "medium_minutes", "old_minutes", "oldest_minutes")
    timing_keys = (poll_key, rotation_key, "time_windows")
    if version == 2 and "timing" not in document:
        fail("$.timing", "required field is missing")
    timing = fields(document.get("timing", {}), "$.timing",
                    timing_keys if version == 2 else (), timing_keys if version == 1 else ())
    poll = number(timing.get(poll_key, 60), f"$.timing.{poll_key}")
    if poll <= 0:
        fail(f"$.timing.{poll_key}", "expected a positive number of seconds")
    rotation = timing.get(rotation_key, 5)
    if type(rotation) is not int or rotation <= 0:
        fail(f"$.timing.{rotation_key}", "expected a positive integer number of polling cycles")

    windows = fields(timing.get("time_windows", dict(zip(window_keys, (5, 10, 15, 20)))),
                     "$.timing.time_windows", window_keys)
    offsets = []
    for key in window_keys:
        field = f"$.timing.time_windows.{key}"
        offset = number(windows[key], field)
        if offset <= (offsets[-1] if offsets else 0):
            fail(field, "expected positive offsets in strictly increasing order")
        offsets.append(offset)
    config_timing = ConfigTiming(poll, rotation, TrackingWindows(*offsets))

    search = None
    if "search" in document:
        names = ("lat_min", "lat_max", "lon_min", "lon_max")
        box = fields(document["search"], "$.search", names)
        bounds = []
        for key, limit in zip(names, (90, 90, 180, 180)):
            field = f"$.search.{key}"
            degrees = number(box[key], field)
            if not -limit <= degrees <= limit:
                fail(field, f"expected degrees between {-limit} and {limit}")
            bounds.append(degrees)
        search = BoundingBox(*bounds)
        if search.lat_min >= search.lat_max:
            fail("$.search.lat_max", "must be greater than lat_min")
        if search.lon_min >= search.lon_max:
            fail("$.search.lon_max", "must be greater than lon_min")
    return RuntimeConfig(version, config_paths, config_timing, search, provenance)


def normalize_config(config: RuntimeConfig, *, filename: Path, working_dir: Path) -> RuntimeConfig:
    """Anchor paths using explicit inputs without consulting or changing the filesystem."""
    if not working_dir.is_absolute():
        raise ValueError("working_dir must be absolute")
    source = working_dir / filename
    base = source.parent if config.schema_version == 2 else working_dir
    list_base = base / "ignore_lists" if config.schema_version == 1 else base
    original = config.path_provenance
    paths = ConfigPaths(
        base / original.log_dir,
        os.path.join(base, original.kismet_logs),
        list_base / original.mac_ignore_list,
        list_base / original.ssid_ignore_list,
    )
    return replace(config, paths=paths, path_provenance=replace(original, source=source))


def load_config(filename: str | Path = "config.json", *, working_dir: Path | None = None) -> RuntimeConfig:
    """Read a file once, validate it, and return the immutable resolved settings."""
    working_dir = Path.cwd() if working_dir is None else Path(working_dir)
    if not working_dir.is_absolute():
        raise ValueError("working_dir must be absolute")
    source = working_dir / filename
    try:
        text = source.read_bytes().decode("utf-8")
    except UnicodeDecodeError as error:
        prefix = error.object[:error.start].decode("utf-8")
        raise ConfigError(source, "$", "file must be UTF-8",
                          line=prefix.count("\n") + 1,
                          column=len(prefix.rsplit("\n", 1)[-1]) + 1) from None
    except OSError as error:
        raise ConfigError(source, "$", f"cannot read configuration ({error.strerror or type(error).__name__})") from None
    document = parse_config(text, source)
    try:
        validated = validate_config(document, source)
    except ConfigError as error:
        raise _locate_error(text, error) from None
    return normalize_config(validated, filename=source, working_dir=working_dir)


def load_legacy_config(filename: str | Path = "config.json", *, credential_migration=False) -> dict:
    """Read a v1 dictionary for legacy callers that own their path semantics."""
    source = Path(filename)
    text = source.read_text(encoding="utf-8")
    document = parse_config(text, source)
    version = document.get("schema_version", 1)
    if type(version) is not int or version != 1:
        error = ConfigError(source, "$.schema_version",
                            "this command requires schema v1; use a v1 configuration")
        raise _locate_error(text, error)
    if "api_keys" in document and not credential_migration:
        error = ConfigError(source, "$.api_keys",
                            "credentials belong outside config.json; use migrate_credentials.py explicitly")
        raise _locate_error(text, error)
    return document
