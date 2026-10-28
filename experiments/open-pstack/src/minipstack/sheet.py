"""The model sheet and the setup flow that writes it.

Mirrors `setup-pstack/SKILL.md`: one editable sheet of provider-qualified descriptors per role,
wired into the harness instructions file by one import line. A versioned Claude pin of a rolling
family is normalized in memory. Setup probes every assigned family's exact pair before it writes
anything, snapshots both targets, reads them back, and restores the snapshot if readback differs.
A rerun that changes nothing produces byte-identical files.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

from .adapters import ADAPTERS
from .registry import EFFORTS, ROLE_DEFAULTS, WRITER_ROLES, descriptor, family_named, resolve_model

HEADER = "# pstack model configuration\n\n"
DESCRIPTOR = re.compile(r"^(?P<provider>[a-z]+):(?P<model>[A-Za-z0-9._/-]+)@(?P<effort>[a-z]+)$")
ALIASES = ("inherit-parent", "auto")
IMPORT_LINE = "@./pstack-models.md"


@dataclass
class Sheet:
    roles: dict[str, list[str]] = field(default_factory=dict)
    setting: str | None = None
    migrations: list[tuple[str, str]] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


def default_roles() -> dict[str, list[str]]:
    return {role: [lane if lane in ALIASES else descriptor(family_named(lane)) for lane in lanes] for role, lanes in ROLE_DEFAULTS}


def normalize(value: str) -> str:
    match = DESCRIPTOR.match(value)
    if not match:
        return value
    kind, family, _ = resolve_model(match["provider"], match["model"])
    return f"{match['provider']}:{family.model}@{match['effort']}" if kind == "stale-revision" else value


def parse(text: str) -> Sheet:
    sheet = Sheet()
    for line in text.splitlines():
        if ":" not in line or line.startswith("#"):
            continue
        role, _, values = line.partition(":")
        role, values = role.strip(), values.strip()
        if role == "codex writer sandbox":
            if sheet.setting is not None:
                sheet.problems.append("a second setting line")
            sheet.setting = values
            continue
        if role in sheet.roles:
            sheet.problems.append(f"duplicate role {role}")
        lanes = []
        for value in (v.strip() for v in values.split(",")):
            normalized = normalize(value)
            if normalized != value:
                sheet.migrations.append((value, normalized))
            lanes.append(normalized)
        sheet.roles[role] = lanes
    return sheet


def validate(sheet: Sheet) -> list[str]:
    problems = list(sheet.problems)
    known = {role for role, _ in ROLE_DEFAULTS}
    for role, lanes in sheet.roles.items():
        if role not in known:
            continue  # a retired role is dropped at render time, not an error
        for value in lanes:
            if value in ALIASES:
                continue
            match = DESCRIPTOR.match(value)
            if not match:
                problems.append(f"{role}: {value} is not provider:model@effort")
                continue
            if match["provider"] not in ADAPTERS:
                problems.append(f"{role}: unknown provider {match['provider']}")
                continue
            kind, family, kept = resolve_model(match["provider"], match["model"])
            if kind == "unregistered":
                problems.append(f"{role}: {match['provider']}:{match['model']} is not in the model matrix")
            elif match["effort"] not in (kept or family.offered()) or match["effort"] not in EFFORTS:
                problems.append(f"{role}: effort {match['effort']} is not offered for {family.name}")
            if role in WRITER_ROLES and "isolated-write" not in ADAPTERS[match["provider"]].supports:
                problems.append(f"{role}: provider {match['provider']} runs read-only lanes only, and this role writes files")
    return problems


def render(roles: dict[str, list[str]], setting: str | None) -> str:
    known = [role for role, _ in ROLE_DEFAULTS]
    lines = [f"{role}: {', '.join(roles[role])}" for role in known if role in roles]
    if setting is not None:
        lines.append(f"codex writer sandbox: {setting}")
    return HEADER + "\n".join(lines) + "\n"


def families_to_probe(roles: dict[str, list[str]]) -> list[str]:
    seen: dict[str, str] = {}
    for lanes in roles.values():
        for value in lanes:
            match = DESCRIPTOR.match(value)
            if match:
                _, family, _ = resolve_model(match["provider"], match["model"])
                seen.setdefault(family.name if family else value, value)
    return list(seen.values())


def _read(path: str) -> bytes | None:
    return open(path, "rb").read() if os.path.exists(path) else None


def _restore(path: str, snapshot: bytes | None) -> None:
    if snapshot is None:
        if os.path.exists(path):
            os.unlink(path)
    else:
        with open(path, "wb") as f:
            f.write(snapshot)


def wire_import(instructions: str) -> str:
    lines = instructions.splitlines()
    matches = [i for i, line in enumerate(lines) if line.startswith("@") and line.rstrip().endswith("pstack-models.md")]
    if len(matches) > 1:
        raise ValueError("more than one pstack-models.md import line; refusing to guess which one to replace")
    if matches:
        lines[matches[0]] = IMPORT_LINE
    else:
        lines.append(IMPORT_LINE)
    return "\n".join(lines) + "\n"


def setup(config_home: str, probe, *, changes: dict[str, list[str]] | None = None, write=None) -> dict:
    """Probe every assigned family, then write the sheet and the import line, or write nothing."""
    sheet_path = os.path.join(config_home, "pstack-models.md")
    instructions_path = os.path.join(config_home, "CLAUDE.md")
    loaded = _read(sheet_path)
    sheet = parse(loaded.decode()) if loaded is not None else Sheet(roles={})
    roles = {**default_roles(), **{r: v for r, v in sheet.roles.items() if r in default_roles()}, **(changes or {})}
    candidate = Sheet(roles=roles, setting=sheet.setting or "bounded", problems=sheet.problems)
    problems = validate(candidate)
    if problems:
        return {"written": False, "reason": "inconsistent state", "problems": problems}
    probes = {pair: bool(probe(pair)) for pair in families_to_probe(roles)}
    if not all(probes.values()):
        return {"written": False, "reason": "probe failed", "probes": probes}
    new_sheet = render(roles, candidate.setting)
    snapshots = {sheet_path: loaded, instructions_path: _read(instructions_path)}
    new_instructions = wire_import((snapshots[instructions_path] or b"").decode())
    writer = write or (lambda path, data: open(path, "w").write(data))
    try:
        writer(sheet_path, new_sheet)
        writer(instructions_path, new_instructions)
        if _read(sheet_path) != new_sheet.encode() or _read(instructions_path) != new_instructions.encode():
            raise OSError("readback differs from the render")
    except OSError as error:
        for path, snapshot in snapshots.items():
            _restore(path, snapshot)
        return {"written": False, "reason": f"restored after failed write: {error}", "probes": probes}
    return {"written": True, "probes": probes, "migrations": sheet.migrations,
            "unchanged": loaded == new_sheet.encode() and snapshots[instructions_path] == new_instructions.encode()}
