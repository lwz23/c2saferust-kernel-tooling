#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import intake
import workflow

try:
    import lz4.block as lz4_block
except ModuleNotFoundError:  # pragma: no cover - exercised by runtime env detection.
    lz4_block = None


REPO_ROOT = Path(__file__).resolve().parents[2]
LOCAL_LLVM_ROOT = Path("/tmp/llvm-15-local/usr")
ANDROID_RAMDISK_LEGACY_LZ4_MAGIC = b"\x02\x21\x4c\x18"
RUNNERS: dict[str, object] = {}


def register_runner(runner_id: str, handler: object) -> None:
    RUNNERS[runner_id] = handler


def available_runner_ids() -> list[str]:
    return sorted(RUNNERS)


def run_runner(runner_id: str, **kwargs) -> dict:
    if runner_id not in RUNNERS:
        raise ValueError(
            f"Unknown oracle runner {runner_id!r}; available runners: {', '.join(available_runner_ids()) or '<none>'}."
        )
    return RUNNERS[runner_id](**kwargs)


def _artifact_output(
    repo_root: Path,
    module_id: str,
    filename: str,
    output: str | Path | None = None,
    *,
    artifact_root: str | Path | None = None,
) -> Path:
    if output is not None:
        return Path(output)
    return intake._path_from_repo(
        repo_root,
        intake._artifact_path_for(
            module_id,
            filename,
            repo_root=repo_root,
            artifact_root=artifact_root,
        ),
    )


def _environment_missing_step(step_id: str, details: list[str]) -> dict:
    return {
        "step_id": step_id,
        "status": "blocked",
        "details": details,
    }


def _blocked_runner_payload(
    runner_id: str,
    *,
    blocker_kind: str,
    blockers: list[str],
    steps: list[dict],
    evidence_tier: str,
    environment: dict | None = None,
    log_path: str | None = None,
) -> dict:
    return {
        "runner": {
            "id": runner_id,
            "status": "blocked",
            "log_path": log_path,
        },
        "pass": False,
        "ready_for_oracle": False,
        "blocked": True,
        "evidence_tier": evidence_tier,
        "blocker_kind": blocker_kind,
        "blockers": blockers,
        "smoke_summary": {
            "result": "BLOCKED",
        },
        "steps": steps,
        "environment": environment or {},
    }


def _run_command(
    command: list[str],
    *,
    timeout_sec: int = 30,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout_sec,
        env=env,
    )


def _adb_command(adb_path: str, *args: str, timeout_sec: int = 30) -> subprocess.CompletedProcess[str]:
    return _run_command([adb_path, *args], timeout_sec=timeout_sec)


def _adb_device_command(
    adb_path: str,
    serial: str | None,
    *args: str,
    timeout_sec: int = 30,
) -> subprocess.CompletedProcess[str]:
    command = [adb_path]
    if serial:
        command.extend(["-s", serial])
    command.extend(args)
    return _run_command(command, timeout_sec=timeout_sec)


def _parse_adb_devices(output: str) -> list[dict]:
    devices = []
    for line in output.splitlines():
        line = line.strip()
        if not line or line.startswith("List of devices attached"):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        devices.append(
            {
                "serial": parts[0],
                "state": parts[1],
                "details": parts[2:],
            }
        )
    return devices


def _first_ready_adb_device(devices: list[dict]) -> dict | None:
    for device in devices:
        if device["state"] == "device":
            return device
    return None


def _find_summary_value(output: str, prefix: str, key: str) -> int | None:
    match = re.search(rf"\[{re.escape(prefix)}\]\s+{re.escape(key)}=(-?\d+)", output)
    return int(match.group(1)) if match else None


def _extract_scenario_summary(output: str, scenario: dict) -> dict:
    prefix = scenario["log_prefix"]
    summary = {
        "result": "PASS" if scenario["result_success_marker"] in output else "FAIL",
    }
    for field in scenario["summary_fields"]:
        summary[field] = _find_summary_value(output, prefix, field)
    return summary


def _detect_android_emulator_env(
    scenario: dict,
    *,
    required_tools: list[str] | None = None,
    required_env: list[str] | None = None,
) -> dict:
    runtime_contract = scenario.get("runtime_contract", {})
    tool_paths = {
        tool: _resolve_tool_path(tool)
        for tool in (required_tools if required_tools is not None else runtime_contract.get("required_tools", ["emulator", "adb"]))
    }
    env_values = {
        name: os.environ.get(name)
        for name in (required_env if required_env is not None else runtime_contract.get("required_env", []))
    }
    missing_tools = [tool for tool, path in tool_paths.items() if path is None]
    missing_env = [name for name, value in env_values.items() if not value]
    return {
        "tool_paths": tool_paths,
        "env": env_values,
        "missing_tools": missing_tools,
        "missing_env": missing_env,
        "available": not missing_tools and not missing_env,
    }


def _resolve_tool_path(tool_name: str) -> str | None:
    path = shutil.which(tool_name)
    if path is not None:
        return path

    home = Path.home()
    fallbacks = {
        "adb": [home / "android-sdk" / "platform-tools" / "adb"],
        "emulator": [home / "android-sdk" / "emulator" / "emulator"],
        "sdkmanager": [home / "android-sdk" / "cmdline-tools" / "latest" / "bin" / "sdkmanager"],
    }
    for candidate in fallbacks.get(tool_name, []):
        if candidate.exists():
            return str(candidate)
    return None


def _expand_path(path_like: str | Path | None) -> Path | None:
    if path_like is None:
        return None
    return Path(os.path.expandvars(str(path_like))).expanduser()


def _align4(value: int) -> int:
    return (value + 3) & ~3


def _decompress_lz4_block_unknown_size(block: bytes) -> bytes:
    if lz4_block is None:
        raise RuntimeError("python `lz4` module is required to decode Android legacy LZ4 ramdisks")

    limit = max(len(block) * 8, 4096)
    while limit <= 64 * 1024 * 1024:
        try:
            return lz4_block.decompress(block, uncompressed_size=limit)
        except Exception:
            limit *= 2
    raise RuntimeError("unable to decompress legacy LZ4 block within 64 MiB limit")


def _decode_legacy_lz4_ramdisk(payload: bytes) -> bytes:
    cursor = 0
    decoded = bytearray()

    while cursor < len(payload):
        if payload[cursor: cursor + 4] != ANDROID_RAMDISK_LEGACY_LZ4_MAGIC:
            raise RuntimeError(f"unexpected legacy LZ4 magic at offset {cursor}")
        cursor += 4
        if cursor + 4 > len(payload):
            raise RuntimeError("truncated legacy LZ4 ramdisk frame header")
        frame_size = int.from_bytes(payload[cursor: cursor + 4], "little")
        cursor += 4
        if cursor + frame_size > len(payload):
            raise RuntimeError("truncated legacy LZ4 ramdisk block payload")
        block = payload[cursor: cursor + frame_size]
        cursor += frame_size
        decoded.extend(_decompress_lz4_block_unknown_size(block))

    return bytes(decoded)


def _parse_concatenated_newc_archives(payload: bytes) -> list[list[dict]]:
    cursor = 0
    archives: list[list[dict]] = []

    while cursor < len(payload):
        while cursor < len(payload) and payload[cursor] == 0:
            cursor += 1
        if cursor >= len(payload):
            break
        if payload[cursor: cursor + 6] != b"070701":
            raise RuntimeError(f"unsupported cpio magic at offset {cursor}")

        archive: list[dict] = []
        while True:
            if cursor + 110 > len(payload):
                raise RuntimeError("truncated newc header")
            header = payload[cursor: cursor + 110].decode("ascii")
            if not header.startswith("070701"):
                raise RuntimeError(f"unexpected cpio header at offset {cursor}")

            namesize = int(header[94:102], 16)
            filesize = int(header[54:62], 16)
            name_start = cursor + 110
            name_end = name_start + namesize
            if name_end > len(payload):
                raise RuntimeError("truncated cpio filename")
            raw_name = payload[name_start:name_end]
            if not raw_name.endswith(b"\x00"):
                raise RuntimeError("cpio filename is not NUL-terminated")
            name = raw_name[:-1].decode("utf-8", errors="surrogateescape")
            data_start = _align4(name_end)
            data_end = data_start + filesize
            if data_end > len(payload):
                raise RuntimeError("truncated cpio payload")

            entry = {
                "name": name,
                "ino": int(header[6:14], 16),
                "mode": int(header[14:22], 16),
                "uid": int(header[22:30], 16),
                "gid": int(header[30:38], 16),
                "nlink": int(header[38:46], 16),
                "mtime": int(header[46:54], 16),
                "devmajor": int(header[62:70], 16),
                "devminor": int(header[70:78], 16),
                "rdevmajor": int(header[78:86], 16),
                "rdevminor": int(header[86:94], 16),
                "check": int(header[102:110], 16),
                "data": payload[data_start:data_end],
            }
            archive.append(entry)
            cursor = _align4(data_end)

            if name == "TRAILER!!!":
                break

        archives.append(archive)

    if not archives:
        raise RuntimeError("no newc archive found in decoded ramdisk payload")
    return archives


def _encode_newc_entry(entry: dict, ino: int) -> bytes:
    name = entry["name"]
    data = entry.get("data", b"")
    if name == "TRAILER!!!":
        data = b""
    name_bytes = name.encode("utf-8", errors="surrogateescape") + b"\x00"
    fields = [
        "070701",
        f"{ino:08x}",
        f"{int(entry.get('mode', 0)):08x}",
        f"{int(entry.get('uid', 0)):08x}",
        f"{int(entry.get('gid', 0)):08x}",
        f"{int(entry.get('nlink', 1)):08x}",
        f"{int(entry.get('mtime', 0)):08x}",
        f"{len(data):08x}",
        f"{int(entry.get('devmajor', 0)):08x}",
        f"{int(entry.get('devminor', 0)):08x}",
        f"{int(entry.get('rdevmajor', 0)):08x}",
        f"{int(entry.get('rdevminor', 0)):08x}",
        f"{len(name_bytes):08x}",
        f"{int(entry.get('check', 0)):08x}",
    ]
    encoded = bytearray("".join(fields).encode("ascii"))
    encoded.extend(name_bytes)
    encoded.extend(b"\x00" * (_align4(len(encoded)) - len(encoded)))
    encoded.extend(data)
    encoded.extend(b"\x00" * (_align4(len(encoded)) - len(encoded)))
    return bytes(encoded)


def _write_concatenated_newc_archives(archives: list[list[dict]]) -> bytes:
    encoded = bytearray()
    next_ino = 1

    for archive in archives:
        trailer = None
        for entry in archive:
            if entry["name"] == "TRAILER!!!":
                trailer = entry
                continue
            encoded.extend(_encode_newc_entry(entry, next_ino))
            next_ino += 1

        encoded.extend(
            _encode_newc_entry(
                trailer or {"name": "TRAILER!!!", "mode": 0o755, "nlink": 1, "mtime": 0},
                next_ino,
            )
        )
        next_ino += 1

    return bytes(encoded)


def _find_archive_entry(
    archives: list[list[dict]],
    path: str,
    *,
    archive_index: int | None = None,
) -> tuple[int, int, dict] | None:
    wanted = path.lstrip("/")
    if archive_index is not None:
        indexes = [archive_index - 1]
    else:
        indexes = list(range(len(archives)))
    for archive_pos in indexes:
        if archive_pos < 0 or archive_pos >= len(archives):
            continue
        archive = archives[archive_pos]
        for entry_pos, entry in enumerate(archive):
            if entry["name"] == wanted:
                return archive_pos, entry_pos, entry
    return None


def _ensure_archive_directories(archive: list[dict], path: str) -> None:
    parts = Path(path).parts[:-1]
    partial = []
    for component in parts:
        partial.append(component)
        dir_name = "/".join(partial)
        if any(entry["name"] == dir_name for entry in archive):
            continue
        trailer_pos = next(
            (idx for idx, entry in enumerate(archive) if entry["name"] == "TRAILER!!!"),
            len(archive),
        )
        archive.insert(
            trailer_pos,
            {
                "name": dir_name,
                "mode": 0o040755,
                "uid": 0,
                "gid": 0,
                "nlink": 1,
                "mtime": 0,
                "devmajor": 0,
                "devminor": 0,
                "rdevmajor": 0,
                "rdevminor": 0,
                "check": 0,
                "data": b"",
            },
        )


def _apply_ramdisk_patch(archives: list[list[dict]], patch_spec: dict) -> list[dict]:
    operations = patch_spec.get("mutations", [])
    applied = []

    for index, operation in enumerate(operations):
        action = operation.get("action")
        if action != "write_text":
            raise RuntimeError(f"unsupported ramdisk patch action `{action}`")

        target_path = operation["path"].lstrip("/")
        archive_index = operation.get("archive_index")
        text = operation.get("text", "")
        encoding = operation.get("encoding", "utf-8")
        payload = text.encode(encoding)

        found = _find_archive_entry(archives, target_path, archive_index=archive_index)
        if found is None:
            target_archive = int(archive_index or len(archives))
            if target_archive < 1 or target_archive > len(archives):
                raise RuntimeError(f"archive_index out of range for `{target_path}`")
            archive = archives[target_archive - 1]
            _ensure_archive_directories(archive, target_path)
            trailer_pos = next(
                (entry_pos for entry_pos, entry in enumerate(archive) if entry["name"] == "TRAILER!!!"),
                len(archive),
            )
            archive.insert(
                trailer_pos,
                {
                    "name": target_path,
                    "mode": 0o100644,
                    "uid": 0,
                    "gid": 0,
                    "nlink": 1,
                    "mtime": 0,
                    "devmajor": 0,
                    "devminor": 0,
                    "rdevmajor": 0,
                    "rdevminor": 0,
                    "check": 0,
                    "data": payload,
                },
            )
            applied.append(
                {
                    "index": index,
                    "action": action,
                    "path": target_path,
                    "archive_index": target_archive,
                    "status": "created",
                    "size": len(payload),
                }
            )
            continue

        archive_pos, _entry_pos, entry = found
        entry["data"] = payload
        applied.append(
            {
                "index": index,
                "action": action,
                "path": target_path,
                "archive_index": archive_pos + 1,
                "status": "updated",
                "size": len(payload),
            }
        )

    return applied


def _prepare_managed_guest_ramdisk(
    *,
    repo_root: Path,
    module_id: str,
    managed_guest: dict,
    ramdisk_path: Path,
    artifact_root: str | Path | None = None,
) -> dict:
    patch_spec = managed_guest.get("ramdisk_patch")
    if not patch_spec or not patch_spec.get("enabled", True):
        return {
            "status": "pass",
            "patched": False,
            "path": str(ramdisk_path),
        }

    try:
        raw_payload = ramdisk_path.read_bytes()
        source_format = "legacy-lz4-concat" if raw_payload.startswith(ANDROID_RAMDISK_LEGACY_LZ4_MAGIC) else "plain-cpio"
        decoded_payload = (
            _decode_legacy_lz4_ramdisk(raw_payload)
            if source_format == "legacy-lz4-concat"
            else raw_payload
        )
        archives = _parse_concatenated_newc_archives(decoded_payload)
        applied = _apply_ramdisk_patch(archives, patch_spec)
        output_path = _artifact_output(
            repo_root,
            module_id,
            patch_spec.get(
                "artifact_filename",
                f"android-emulator-{managed_guest.get('label', 'managed')}-ramdisk.cpio",
            ),
            artifact_root=artifact_root,
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(_write_concatenated_newc_archives(archives))
        return {
            "status": "pass",
            "patched": True,
            "path": str(output_path),
            "source_path": str(ramdisk_path),
            "source_format": source_format,
            "archive_count": len(archives),
            "operations": applied,
        }
    except Exception as exc:
        return {
            "status": "blocked",
            "blocker_kind": "android-emulator-ramdisk-patch-failed",
            "details": [str(exc)],
            "source_path": str(ramdisk_path),
        }


def _require_external_tool(tool_name: str) -> str:
    resolved = shutil.which(tool_name)
    if resolved is None:
        raise RuntimeError(f"required tool `{tool_name}` is not available in PATH")
    return resolved


def _parse_parted_sector(value: str) -> int:
    if not value.endswith("s"):
        raise RuntimeError(f"expected sector-valued field, got `{value}`")
    return int(value[:-1])


def _read_disk_partitions(image_path: Path) -> dict:
    parted_path = _require_external_tool("parted")
    completed = _run_command([parted_path, "-sm", str(image_path), "unit", "s", "print"], timeout_sec=30)
    if completed.returncode != 0:
        details = "\n".join(
            line for line in [completed.stdout.strip(), completed.stderr.strip()] if line
        )
        raise RuntimeError(f"failed to inspect partition table for `{image_path}`: {details or 'unknown error'}")

    lines = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    if len(lines) < 2 or lines[0] != "BYT;":
        raise RuntimeError(f"unexpected parted machine output for `{image_path}`")

    header = lines[1].rstrip(";").split(":")
    if len(header) < 6:
        raise RuntimeError(f"incomplete partition table header for `{image_path}`")
    sector_size = int(header[3])

    partitions = []
    for raw_line in lines[2:]:
        fields = raw_line.rstrip(";").split(":")
        if len(fields) < 6:
            continue
        partitions.append(
            {
                "index": int(fields[0]),
                "start_sector": _parse_parted_sector(fields[1]),
                "end_sector": _parse_parted_sector(fields[2]),
                "sector_count": _parse_parted_sector(fields[3]),
                "fs_type": fields[4],
                "name": fields[5],
                "sector_size": sector_size,
            }
        )

    return {
        "sector_size": sector_size,
        "partitions": partitions,
    }


def _extract_disk_partition(image_path: Path, *, partition: dict, output_path: Path) -> None:
    byte_offset = partition["start_sector"] * partition["sector_size"]
    byte_count = partition["sector_count"] * partition["sector_size"]
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with image_path.open("rb") as source_file, output_path.open("wb") as output_file:
        source_file.seek(byte_offset)
        remaining = byte_count
        while remaining > 0:
            chunk = source_file.read(min(1024 * 1024, remaining))
            if not chunk:
                raise RuntimeError(f"unexpected EOF while extracting partition from `{image_path}`")
            output_file.write(chunk)
            remaining -= len(chunk)


def _replace_disk_partition(image_path: Path, *, partition: dict, partition_path: Path) -> None:
    expected_size = partition["sector_count"] * partition["sector_size"]
    payload = partition_path.read_bytes()
    if len(payload) != expected_size:
        raise RuntimeError(
            f"patched partition size mismatch for `{image_path}`: expected {expected_size} bytes, got {len(payload)}"
        )

    byte_offset = partition["start_sector"] * partition["sector_size"]
    with image_path.open("r+b") as image_file:
        image_file.seek(byte_offset)
        image_file.write(payload)


def _debugfs_path_exists(fs_image_path: Path, target_path: str) -> bool:
    debugfs_path = _require_external_tool("debugfs")
    completed = _run_command([debugfs_path, "-R", f"stat {target_path}", str(fs_image_path)], timeout_sec=30)
    output = "\n".join([completed.stdout, completed.stderr]).lower()
    return "not found" not in output and "ext2_lookup" not in output and "file not found" not in output


def _apply_ext_filesystem_patch(fs_image_path: Path, patch_spec: dict, *, staging_dir: Path) -> list[dict]:
    debugfs_path = _require_external_tool("debugfs")
    filesystem = patch_spec.get("filesystem", "ext4")
    if filesystem not in ("ext2", "ext3", "ext4"):
        raise RuntimeError(f"unsupported filesystem patch target `{filesystem}`")

    staging_dir.mkdir(parents=True, exist_ok=True)
    command_lines = []
    applied = []

    for index, operation in enumerate(patch_spec.get("mutations", [])):
        action = operation.get("action")
        if action != "write_text":
            raise RuntimeError(f"unsupported image patch action `{action}`")

        target_path = "/" + operation["path"].lstrip("/")
        encoding = operation.get("encoding", "utf-8")
        payload = operation.get("text", "").encode(encoding)
        payload_path = staging_dir / f"payload-{index}"
        payload_path.write_bytes(payload)

        existed = _debugfs_path_exists(fs_image_path, target_path)
        if existed:
            command_lines.append(f"rm {target_path}")
        command_lines.append(f"write {payload_path} {target_path}")
        applied.append(
            {
                "index": index,
                "action": action,
                "path": target_path.lstrip("/"),
                "status": "updated" if existed else "created",
                "size": len(payload),
            }
        )

    command_file = staging_dir / "debugfs.cmd"
    command_file.write_text("\n".join([*command_lines, "close", ""]), encoding="utf-8")
    completed = _run_command([debugfs_path, "-w", "-f", str(command_file), str(fs_image_path)], timeout_sec=60)
    if completed.returncode != 0:
        details = "\n".join(line for line in [completed.stdout.strip(), completed.stderr.strip()] if line)
        raise RuntimeError(f"failed to apply filesystem patch to `{fs_image_path}`: {details or 'unknown error'}")

    return applied


def _prepare_managed_guest_image_patches(
    *,
    repo_root: Path,
    module_id: str,
    managed_guest: dict,
    assets: dict,
    artifact_root: str | Path | None = None,
) -> dict:
    patch_specs = managed_guest.get("image_patches", [])
    resolved_assets = {key: Path(value) if value is not None else None for key, value in assets.items() if key != "image_dir"}
    if not patch_specs:
        return {
            "status": "pass",
            "patched": False,
            "assets": {key: str(value) for key, value in resolved_assets.items() if value is not None},
            "operations": [],
        }

    label = managed_guest.get("label", "managed")
    operations = []
    try:
        with tempfile.TemporaryDirectory(prefix=f"c2saferust-{module_id}-{label}-image-patch-") as temp_dir:
            temp_root = Path(temp_dir)
            for patch_index, patch_spec in enumerate(patch_specs):
                asset_name = patch_spec.get("asset")
                if not asset_name:
                    raise RuntimeError("image patch is missing required `asset` field")
                source_path = resolved_assets.get(asset_name)
                if source_path is None:
                    raise RuntimeError(f"image patch references unknown asset `{asset_name}`")
                if not source_path.exists():
                    raise RuntimeError(f"image patch source asset does not exist: `{source_path}`")

                output_path = _artifact_output(
                    repo_root,
                    module_id,
                    patch_spec.get("artifact_filename", f"android-emulator-{label}-{asset_name}.img"),
                    artifact_root=artifact_root,
                )
                output_path.parent.mkdir(parents=True, exist_ok=True)
                if source_path != output_path:
                    shutil.copy2(source_path, output_path)

                partition_info = None
                patch_target = output_path
                partition_index = patch_spec.get("partition_index")
                if partition_index is not None:
                    layout = _read_disk_partitions(output_path)
                    partition_info = next(
                        (
                            item
                            for item in layout["partitions"]
                            if item["index"] == int(partition_index)
                        ),
                        None,
                    )
                    if partition_info is None:
                        raise RuntimeError(
                            f"partition_index `{partition_index}` not present in `{output_path}`"
                        )
                    patch_target = temp_root / f"{asset_name}-partition-{partition_index}-{patch_index}.img"
                    _extract_disk_partition(output_path, partition=partition_info, output_path=patch_target)

                applied = _apply_ext_filesystem_patch(
                    patch_target,
                    patch_spec,
                    staging_dir=temp_root / f"staging-{patch_index}",
                )

                if partition_info is not None:
                    _replace_disk_partition(output_path, partition=partition_info, partition_path=patch_target)

                resolved_assets[asset_name] = output_path
                operations.append(
                    {
                        "asset": asset_name,
                        "path": str(output_path),
                        "source_path": str(source_path),
                        "partition_index": int(partition_index) if partition_index is not None else None,
                        "filesystem": patch_spec.get("filesystem", "ext4"),
                        "operations": applied,
                    }
                )
    except Exception as exc:
        return {
            "status": "blocked",
            "blocker_kind": "android-emulator-image-patch-failed",
            "details": [str(exc)],
        }

    return {
        "status": "pass",
        "patched": True,
        "assets": {key: str(value) for key, value in resolved_assets.items() if value is not None},
        "operations": operations,
    }


def _synthesize_ext4_image(
    image_path: Path,
    *,
    size_mb: int,
    label: str | None = None,
) -> None:
    mkfs_ext4 = _require_external_tool("mkfs.ext4")
    image_path.parent.mkdir(parents=True, exist_ok=True)
    with image_path.open("wb") as image_file:
        image_file.truncate(size_mb * 1024 * 1024)

    command = [mkfs_ext4, "-F", "-q"]
    if label:
        command.extend(["-L", label])
    command.append(str(image_path))
    completed = _run_command(command, timeout_sec=120)
    if completed.returncode != 0:
        details = "\n".join(line for line in [completed.stdout.strip(), completed.stderr.strip()] if line)
        raise RuntimeError(
            f"failed to create ext4 image `{image_path}`: {details or 'unknown error'}"
        )


def _tail_lines(path: Path, *, limit: int = 80) -> list[str]:
    if not path.exists():
        return []
    return path.read_text(errors="replace").splitlines()[-limit:]


def _classify_android_boot_failure(log_path: Path) -> dict:
    if not log_path.exists():
        return {
            "blocker_kind": None,
            "markers": [],
            "log_tail": [],
        }

    text = log_path.read_text(errors="replace")
    markers: list[str] = []
    blocker_kind = None
    if (
        "Failed to insmod '/lib/modules/virtio_blk.ko'" in text
        and "section size must match the kernel's built struct module size" in text
    ):
        blocker_kind = "android-emulator-ramdisk-module-abi-mismatch"
        markers.append("ramdisk-module-abi-mismatch")
    if "CONFIG_BOOT_CONFIG is not set" in text:
        blocker_kind = blocker_kind or "android-emulator-bootconfig-missing"
        markers.append("missing-boot-config")
    if "verity: unknown target type" in text or "Failed to setup verity for '/system': Invalid argument" in text:
        blocker_kind = blocker_kind or "android-emulator-dm-verity-missing"
        markers.append("missing-dm-verity")
    if "type=erofs)=-1: No such device" in text or "Failed to mount /system_dlkm: No such device" in text:
        blocker_kind = blocker_kind or "android-emulator-erofs-missing"
        markers.append("missing-erofs")
    if "/dev/binder' could not be opened" in text or "/dev/hwbinder' could not be opened" in text:
        blocker_kind = blocker_kind or "android-emulator-binder-missing"
        markers.append("missing-binder")
    if "default-key: unknown target type" in text or "Failed to populate default-key device userdata" in text:
        blocker_kind = blocker_kind or "android-emulator-default-key-missing"
        markers.append("missing-default-key")
    if "No AVD specified" in text or "did not provide the name of an Android Virtual Device" in text:
        blocker_kind = blocker_kind or "android-emulator-raw-image-contract-mismatch"
        markers.append("raw-image-contract-mismatch")
    if "missing the 'vendor.img' image file" in text:
        blocker_kind = blocker_kind or "android-emulator-vendor-image-missing"
        markers.append("missing-vendor-image")
    if "Failed to insmod '/lib/modules/virtio_blk.ko'" in text:
        markers.append("virtio-blk-ramdisk-module-load-failed")
    if "dlkm_loader: Failed to insmod" in text and "section size must match the kernel's built struct module size" in text:
        markers.append("second-stage-module-abi-mismatch")
    if "Failed to mount required partitions early" in text:
        markers.append("first-stage-mount-failure")
    if "InitFatalReboot" in text or "Restarting system with command 'bootloader'" in text:
        markers.append("first-stage-reboot")
    if "reboot,vold-failed" in text:
        markers.append("vold-reboot")
    return {
        "blocker_kind": blocker_kind,
        "markers": markers,
        "log_tail": text.splitlines()[-120:],
    }


def _candidate_android_ndk_roots(tester: dict) -> list[Path]:
    candidates: list[Path] = []
    seen: set[str] = set()

    def add(path_like: str | Path | None) -> None:
        if path_like is None:
            return
        path = Path(path_like).expanduser()
        key = str(path.resolve()) if path.exists() else str(path)
        if key in seen:
            return
        seen.add(key)
        candidates.append(path)

    add(tester.get("ndk_root"))
    for env_name in tester.get("ndk_env_vars", ["ANDROID_NDK_ROOT", "ANDROID_NDK_HOME"]):
        add(os.environ.get(env_name))

    sdk_roots = []
    for env_name in ["ANDROID_SDK_ROOT", "ANDROID_HOME"]:
        if os.environ.get(env_name):
            sdk_roots.append(Path(os.environ[env_name]).expanduser())
    sdk_roots.append(Path.home() / "android-sdk")

    for sdk_root in sdk_roots:
        ndk_root = sdk_root / "ndk"
        if not ndk_root.exists():
            continue
        version_dirs = sorted((entry for entry in ndk_root.iterdir() if entry.is_dir()), reverse=True)
        for version_dir in version_dirs:
            add(version_dir)

    return candidates


def _resolve_android_guest_compiler(tester: dict) -> dict:
    target_triple = tester["target_triple"]
    api_level = int(tester.get("api_level", 21))
    compiler_name = tester.get("compiler_name", f"{target_triple}{api_level}-clang")

    searched = []
    for ndk_root in _candidate_android_ndk_roots(tester):
        compiler = ndk_root / "toolchains" / "llvm" / "prebuilt" / "linux-x86_64" / "bin" / compiler_name
        searched.append(str(compiler))
        if compiler.exists():
            return {
                "available": True,
                "ndk_root": str(ndk_root),
                "compiler": str(compiler),
                "compiler_name": compiler_name,
                "searched": searched,
            }

    return {
        "available": False,
        "ndk_root": None,
        "compiler": None,
        "compiler_name": compiler_name,
        "searched": searched,
    }


def _resolve_managed_guest_config(scenario: dict, runner_context: dict | None) -> dict | None:
    merged = dict(scenario.get("managed_guest_defaults", {}))
    requested = (runner_context or {}).get("managed_guest")
    if requested:
        merged.update(requested)
    if not merged or not merged.get("enabled", False):
        return None
    if merged.get("avd_name") is None and (not requested or "emulator_args" not in requested):
        merged["emulator_args"] = [
            "-no-window",
            "-no-snapshot-load",
            "-no-snapshot-save",
            "-wipe-data",
            "-gpu",
            "off",
            "-show-kernel",
        ]
    return merged


def _managed_guest_boot_mode(managed_guest: dict | None) -> str:
    if managed_guest is None:
        return "android-full"
    return str(managed_guest.get("boot_mode", "android-full"))


def _wait_for_adb_device(
    adb_path: str,
    serial: str,
    *,
    timeout_sec: int,
    process: subprocess.Popen[str] | None = None,
) -> dict:
    deadline = time.monotonic() + timeout_sec
    polls: list[str] = []
    while time.monotonic() < deadline:
        if process is not None and process.poll() is not None:
            return {
                "status": "fail",
                "details": [f"emulator-exited={process.returncode}"],
            }
        try:
            devices = _adb_command(adb_path, "devices", "-l", timeout_sec=10)
        except subprocess.TimeoutExpired:
            polls.append("adb-devices-timeout")
            time.sleep(2)
            continue

        parsed = _parse_adb_devices(devices.stdout)
        for device in parsed:
            polls.append(f"{device['serial']}:{device['state']}")
            if device["serial"] == serial and device["state"] == "device":
                return {
                    "status": "pass",
                    "details": polls[-20:],
                    "devices": parsed,
                }
        time.sleep(2)

    return {
        "status": "fail",
        "details": polls[-20:] or ["adb-device-timeout"],
    }


def _wait_for_android_boot(
    adb_path: str,
    serial: str,
    *,
    timeout_sec: int,
    boot_props: list[str],
    process: subprocess.Popen[str] | None = None,
) -> dict:
    deadline = time.monotonic() + timeout_sec
    polls: list[str] = []
    while time.monotonic() < deadline:
        if process is not None and process.poll() is not None:
            return {
                "status": "fail",
                "details": [f"emulator-exited={process.returncode}"],
            }
        for prop in boot_props:
            try:
                completed = _adb_device_command(
                    adb_path,
                    serial,
                    "shell",
                    "getprop",
                    prop,
                    timeout_sec=10,
                )
            except subprocess.TimeoutExpired:
                polls.append(f"{prop}=timeout")
                continue
            value = completed.stdout.strip()
            polls.append(f"{prop}={value or '<empty>'}")
            if completed.returncode == 0 and value == "1":
                return {
                    "status": "pass",
                    "details": polls[-20:],
                }
        time.sleep(2)

    return {
        "status": "fail",
        "details": polls[-20:] or ["android-boot-timeout"],
    }


def _wait_for_process_exit(
    process: subprocess.Popen[str],
    *,
    timeout_sec: int,
) -> dict:
    try:
        process.wait(timeout=timeout_sec)
    except subprocess.TimeoutExpired:
        return {
            "status": "fail",
            "details": [f"process-timeout>{timeout_sec}s"],
            "exit_code": None,
        }

    return {
        "status": "pass",
        "details": [f"process-exited={process.returncode}"],
        "exit_code": process.returncode,
    }


def _stop_managed_android_guest(
    *,
    adb_path: str | None,
    serial: str | None,
    process: subprocess.Popen[str] | None,
    timeout_sec: int,
    state_dir: Path | None,
) -> dict:
    details: list[str] = []
    adb_exit_code = None
    if process is None:
        if state_dir is not None:
            shutil.rmtree(state_dir, ignore_errors=True)
        return {"status": "skipped", "details": ["no-managed-emulator-process"]}

    if adb_path is not None and serial is not None:
        try:
            completed = _adb_device_command(adb_path, serial, "emu", "kill", timeout_sec=10)
            adb_exit_code = completed.returncode
            details.extend(completed.stdout.splitlines()[-10:] or completed.stderr.splitlines()[-10:])
        except Exception as exc:
            details.append(f"adb-emu-kill-exception={exc}")

    try:
        process.wait(timeout=timeout_sec)
    except subprocess.TimeoutExpired:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)

    if state_dir is not None:
        shutil.rmtree(state_dir, ignore_errors=True)

    return {
        "status": "pass",
        "adb_exit_code": adb_exit_code,
        "process_exit_code": process.returncode,
        "details": details[-20:],
    }


def _start_managed_android_guest(
    *,
    repo_root: Path,
    module_id: str,
    scenario: dict,
    scenario_inputs: dict | None = None,
    env_report: dict,
    managed_guest: dict,
    artifact_root: str | Path | None = None,
    build_dir: str | Path | None = None,
    make_llvm: str | None = None,
) -> dict:
    emulator_path = env_report["tool_paths"]["emulator"]
    label = managed_guest.get("label", "managed")
    boot_mode = _managed_guest_boot_mode(managed_guest)
    ramdisk_patch = {"status": "skipped", "patched": False, "operations": []}
    image_patches = {"status": "skipped", "patched": False, "assets": {}, "operations": []}

    if boot_mode == "minimal-linux":
        minimal_guest = _prepare_minimal_linux_guest(
            repo_root=repo_root,
            module_id=module_id,
            scenario=scenario,
            scenario_inputs=scenario_inputs or {},
            managed_guest=managed_guest,
            artifact_root=artifact_root,
            build_dir=build_dir,
            make_llvm=make_llvm,
        )
        if minimal_guest["status"] != "pass":
            return minimal_guest

        image_dir = Path(minimal_guest["assets"]["image_dir"])
        assets = {key: Path(value) for key, value in minimal_guest["assets"].items() if key != "image_dir"}
        assets["image_dir"] = image_dir
        kernel_source = minimal_guest["kernel_source"]
        resolved_build_dir = (
            Path(minimal_guest["build"]["build_dir"])
            if minimal_guest["build"]["build_dir"] is not None
            else None
        )
        build_dir_source = minimal_guest["build"]["build_dir_source"]
        kernel_build = minimal_guest["build"]["kernel_build"]
        launch_mode = "minimal-linux"
        synthetic_assets = minimal_guest["synthetic_assets"]
        avd_name = None
    else:
        image_dir = _expand_path(managed_guest.get("image_dir"))
        if image_dir is None:
            return {
                "status": "blocked",
                "blocker_kind": "android-emulator-image-dir-missing",
                "details": ["managed_guest.image_dir is required for Android emulator boot"],
            }

        assets = {
            "image_dir": image_dir,
            "ramdisk": _expand_path(managed_guest.get("ramdisk_path")) or (image_dir / "ramdisk.img"),
            "system": _expand_path(managed_guest.get("system_path")) or (image_dir / "system.img"),
            "vendor": _expand_path(managed_guest.get("vendor_path")) or (image_dir / "vendor.img"),
            "userdata": _expand_path(managed_guest.get("userdata_path")) or (image_dir / "userdata.img"),
        }
        kernel_source = managed_guest.get("kernel_source", "sdk-stock")
        resolved_build_dir = None
        build_dir_source = None
        kernel_build = None
        if kernel_source == "sdk-stock":
            assets["kernel"] = _expand_path(managed_guest.get("kernel_path")) or (image_dir / "kernel-ranchu")
        elif kernel_source == "build-tree":
            resolved_build_dir, build_dir_source = _resolve_build_dir(repo_root, module_id, build_dir)
            try:
                kernel_image, kernel_build = _ensure_kernel_image(
                    repo_root,
                    resolved_build_dir,
                    make_llvm=make_llvm,
                )
            except RuntimeError as exc:
                return {
                    "status": "blocked",
                    "blocker_kind": "android-emulator-kernel-build-failed",
                    "details": [str(exc), f"build_dir={resolved_build_dir}"],
                }
            assets["kernel"] = kernel_image
        else:
            return {
                "status": "blocked",
                "blocker_kind": "android-emulator-kernel-source-unsupported",
                "details": [f"kernel_source={kernel_source}"],
            }

        optional_assets = {"image_dir", *managed_guest.get("optional_assets", ["vendor"])}
        missing_assets = [
            f"{name}={path}"
            for name, path in assets.items()
            if name not in optional_assets and (path is None or not Path(path).exists())
        ]
        if missing_assets:
            return {
                "status": "blocked",
                "blocker_kind": "android-emulator-asset-missing",
                "details": missing_assets,
            }

        ramdisk_patch = _prepare_managed_guest_ramdisk(
            repo_root=repo_root,
            module_id=module_id,
            managed_guest=managed_guest,
            ramdisk_path=Path(assets["ramdisk"]),
            artifact_root=artifact_root,
        )
        if ramdisk_patch["status"] != "pass":
            return ramdisk_patch
        assets["ramdisk"] = Path(ramdisk_patch["path"])

        image_patches = _prepare_managed_guest_image_patches(
            repo_root=repo_root,
            module_id=module_id,
            managed_guest=managed_guest,
            assets=assets,
            artifact_root=artifact_root,
        )
        if image_patches["status"] != "pass":
            return image_patches
        for asset_name, asset_path in image_patches["assets"].items():
            if asset_name in assets:
                assets[asset_name] = Path(asset_path)

        avd_name = managed_guest.get("avd_name") or None
        launch_mode = "avd" if avd_name else "raw-image"
        synthetic_assets = {}
        if launch_mode == "raw-image" and not Path(assets["vendor"]).exists():
            synthetic_vendor_path = _artifact_output(
                repo_root,
                module_id,
                managed_guest.get("synthetic_vendor_filename", f"android-emulator-{label}-vendor.img"),
                artifact_root=artifact_root,
            )
            synthetic_vendor_size_mb = int(managed_guest.get("synthetic_vendor_size_mb", 64))
            try:
                _synthesize_ext4_image(
                    synthetic_vendor_path,
                    size_mb=synthetic_vendor_size_mb,
                    label=managed_guest.get("synthetic_vendor_label", "vendor"),
                )
            except Exception as exc:
                return {
                    "status": "blocked",
                    "blocker_kind": "android-emulator-synthetic-vendor-failed",
                    "details": [str(exc)],
                }
            assets["vendor"] = synthetic_vendor_path
            synthetic_assets["vendor"] = {
                "path": str(synthetic_vendor_path),
                "size_mb": synthetic_vendor_size_mb,
                "label": managed_guest.get("synthetic_vendor_label", "vendor"),
            }

    port_base = int(managed_guest.get("serial_port_base", 5560))
    serial = f"emulator-{port_base}"
    state_dir = Path(tempfile.mkdtemp(prefix=f"c2saferust-{module_id}-{label}-android-"))

    log_filename = managed_guest.get("log_filename", f"android-emulator-{label}.log")
    emulator_log_path = _artifact_output(
        repo_root,
        module_id,
        log_filename,
        artifact_root=artifact_root,
    )
    emulator_log_path.parent.mkdir(parents=True, exist_ok=True)

    command = [emulator_path]
    popen_env = os.environ.copy()
    if avd_name:
        command.append(f"@{avd_name}")
    else:
        command.extend(["-sysdir", str(image_dir)])
        popen_env["ANDROID_PRODUCT_OUT"] = str(image_dir)
    command.extend(
        managed_guest.get(
            "emulator_args",
            [
                "-no-window",
                "-no-snapshot-load",
                "-no-snapshot-save",
                "-wipe-data",
                "-gpu",
                "swiftshader_indirect",
                "-show-kernel",
            ],
        )
    )
    command.extend(["-ports", f"{port_base},{port_base + 1}"])
    command.extend(["-kernel", str(assets["kernel"])])
    command.extend(["-ramdisk", str(assets["ramdisk"])])
    command.extend(["-system", str(assets["system"])])
    if Path(assets["vendor"]).exists():
        command.extend(["-vendor", str(assets["vendor"])])
    if launch_mode == "avd":
        userdata_dst = state_dir / "userdata.img"
        shutil.copy2(Path(assets["userdata"]), userdata_dst)
        command.extend(["-data", str(userdata_dst)])
    else:
        userdata_runtime = state_dir / managed_guest.get("raw_userdata_filename", "userdata-qemu.img")
        command.extend(["-initdata", str(assets["userdata"])])
        command.extend(["-data", str(userdata_runtime)])
    if boot_mode == "minimal-linux":
        command.extend(
            [
                "-qemu",
                "-append",
                managed_guest.get(
                    "kernel_cmdline",
                    "console=ttyS0 rdinit=/init nokaslr panic=-1",
                ),
            ]
        )

    log_file = emulator_log_path.open("w")
    process = subprocess.Popen(
        command,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        text=True,
        env=popen_env,
    )
    log_file.close()
    time.sleep(float(managed_guest.get("launch_settle_sec", 5)))
    if process.poll() is not None:
        analysis = (
            _classify_minimal_guest_failure(emulator_log_path, scenario)
            if boot_mode == "minimal-linux"
            else _classify_android_boot_failure(emulator_log_path)
        )
        return {
            "status": "blocked",
            "blocker_kind": analysis["blocker_kind"] or (
                "minimal-guest-emulator-contract-failure"
                if boot_mode == "minimal-linux"
                else "android-emulator-launch-failed"
            ),
            "details": [*analysis["markers"], *_tail_lines(emulator_log_path)],
            "analysis": analysis,
            "process": process,
            "state_dir": state_dir,
            "serial": serial,
            "log_path": str(emulator_log_path),
            "command": command,
            "launch_mode": launch_mode,
        }

    if boot_mode == "minimal-linux":
        return {
            "status": "pass",
            "serial": serial,
            "process": process,
            "state_dir": state_dir,
            "log_path": str(emulator_log_path),
            "command": command,
            "assets": {key: str(value) for key, value in assets.items()},
            "synthetic_assets": synthetic_assets,
            "ramdisk_patch": ramdisk_patch,
            "image_patches": image_patches,
            "kernel_source": kernel_source,
            "launch_mode": launch_mode,
            "guest_tester": minimal_guest["guest_tester"],
            "initramfs": minimal_guest["initramfs"],
            "build": {
                "build_dir": str(resolved_build_dir) if resolved_build_dir is not None else None,
                "build_dir_source": build_dir_source,
                "kernel_build": kernel_build,
            },
            "steps": [
                {
                    "step_id": "launch-managed-android-emulator",
                    "status": "pass",
                    "details": [
                        f"serial={serial}",
                        f"log={emulator_log_path}",
                        f"launch_mode={launch_mode}",
                    ],
                }
            ],
        }

    adb_path = env_report["tool_paths"]["adb"]
    wait_device = _wait_for_adb_device(
        adb_path,
        serial,
        timeout_sec=int(managed_guest.get("adb_wait_timeout_sec", 120)),
        process=process,
    )
    if wait_device["status"] != "pass":
        analysis = _classify_android_boot_failure(emulator_log_path)
        return {
            "status": "blocked",
            "blocker_kind": analysis["blocker_kind"] or "android-emulator-adb-timeout",
            "details": [*wait_device["details"], *analysis["markers"]],
            "analysis": analysis,
            "process": process,
            "state_dir": state_dir,
            "serial": serial,
            "log_path": str(emulator_log_path),
            "command": command,
            "launch_mode": launch_mode,
        }

    wait_boot = _wait_for_android_boot(
        adb_path,
        serial,
        timeout_sec=int(managed_guest.get("boot_timeout_sec", 240)),
        boot_props=managed_guest.get("boot_complete_props", ["sys.boot_completed", "dev.bootcomplete"]),
        process=process,
    )
    if wait_boot["status"] != "pass":
        analysis = _classify_android_boot_failure(emulator_log_path)
        return {
            "status": "blocked",
            "blocker_kind": analysis["blocker_kind"] or "android-emulator-boot-timeout",
            "details": [*wait_boot["details"], *analysis["markers"]],
            "analysis": analysis,
            "process": process,
            "state_dir": state_dir,
            "serial": serial,
            "log_path": str(emulator_log_path),
            "command": command,
            "launch_mode": launch_mode,
        }

    return {
        "status": "pass",
        "serial": serial,
        "process": process,
        "state_dir": state_dir,
        "log_path": str(emulator_log_path),
        "command": command,
        "assets": {key: str(value) for key, value in assets.items()},
        "synthetic_assets": synthetic_assets,
        "ramdisk_patch": ramdisk_patch,
        "image_patches": image_patches,
        "kernel_source": kernel_source,
        "launch_mode": launch_mode,
        "build": {
            "build_dir": str(resolved_build_dir) if resolved_build_dir is not None else None,
            "build_dir_source": build_dir_source,
            "kernel_build": kernel_build,
        },
        "steps": [
            {
                "step_id": "launch-managed-android-emulator",
                "status": "pass",
                "details": [f"serial={serial}", f"log={emulator_log_path}", f"launch_mode={launch_mode}"],
            },
            {
                "step_id": "wait-managed-android-adb",
                "status": "pass",
                "details": wait_device["details"],
            },
            {
                "step_id": "wait-managed-android-boot",
                "status": "pass",
                "details": wait_boot["details"],
            },
        ],
    }


def _resolve_guest_tester_source(tester: dict) -> Path:
    source = Path(tester["source"])
    return source if source.is_absolute() else (REPO_ROOT / source)


def _resolve_linux_static_guest_compiler(tester: dict) -> dict:
    candidates = []
    if tester.get("compiler_name"):
        candidates.append(str(tester["compiler_name"]))
    candidates.extend(tester.get("compiler_candidates", ["clang", "cc", "x86_64-linux-gnu-gcc"]))

    searched = []
    seen = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        resolved = shutil.which(candidate)
        searched.append(resolved or candidate)
        if resolved is not None:
            return {
                "available": True,
                "compiler": resolved,
                "compiler_name": Path(resolved).name,
                "searched": searched,
            }

    return {
        "available": False,
        "compiler": None,
        "compiler_name": candidates[0] if candidates else "clang",
        "searched": searched,
    }


def _build_linux_static_guest_tester(
    module_id: str,
    scenario: dict,
) -> dict:
    tester = scenario["guest_tester"]
    compiler_info = _resolve_linux_static_guest_compiler(tester)
    source_path = _resolve_guest_tester_source(tester)
    binary_name = tester.get("binary_name", source_path.stem)
    build_dir = Path(tempfile.mkdtemp(prefix=f"c2saferust-{module_id}-guest-tester-"))
    output_path = build_dir / binary_name

    if not compiler_info["available"]:
        return {
            "status": "blocked",
            "blocker_kind": "linux-guest-tester-toolchain-missing",
            "details": [
                f"missing compiler `{compiler_info['compiler_name']}`",
                *compiler_info["searched"][-10:],
            ],
            "source_path": str(source_path),
        }

    command = [
        compiler_info["compiler"],
        "-std=c11",
        "-O2",
        "-Wall",
        "-Wextra",
        "-Werror",
        "-static",
        "-o",
        str(output_path),
        str(source_path),
        *tester.get("compiler_flags", []),
    ]

    completed = _run_command(command, timeout_sec=int(tester.get("build_timeout_sec", 60)))
    payload = {
        "status": "pass" if completed.returncode == 0 and output_path.exists() else "fail",
        "source_path": str(source_path),
        "output_path": str(output_path),
        "compiler": compiler_info["compiler"],
        "command": command,
        "exit_code": completed.returncode,
        "stdout_tail": completed.stdout.splitlines()[-40:],
        "stderr_tail": completed.stderr.splitlines()[-40:],
    }
    if payload["status"] != "pass":
        payload["blocker_kind"] = "minimal-guest-tester-build-failed"
    return payload


def _build_guest_tester(
    module_id: str,
    scenario: dict,
) -> dict:
    tester = scenario["guest_tester"]
    build_mode = tester.get("build_mode", "android-ndk")
    if build_mode == "android-ndk":
        return _build_android_guest_tester(module_id, scenario)
    if build_mode == "linux-static":
        return _build_linux_static_guest_tester(module_id, scenario)
    return {
        "status": "blocked",
        "blocker_kind": "guest-tester-build-mode-unsupported",
        "details": [f"build_mode={build_mode}"],
        "source_path": str(_resolve_guest_tester_source(tester)),
    }


def _build_android_guest_tester(
    module_id: str,
    scenario: dict,
) -> dict:
    tester = scenario["guest_tester"]
    compiler_info = _resolve_android_guest_compiler(tester)
    source_path = _resolve_guest_tester_source(tester)
    binary_name = tester.get("binary_name", source_path.stem)
    build_dir = Path(tempfile.mkdtemp(prefix=f"c2saferust-{module_id}-guest-tester-"))
    output_path = build_dir / binary_name

    if not compiler_info["available"]:
        return {
            "status": "blocked",
            "blocker_kind": "android-guest-tester-toolchain-missing",
            "details": [
                f"missing compiler `{compiler_info['compiler_name']}`",
                *compiler_info["searched"][-10:],
            ],
            "source_path": str(source_path),
        }

    command = [
        compiler_info["compiler"],
        "-std=c11",
        "-O2",
        "-Wall",
        "-Wextra",
        "-Werror",
        "-o",
        str(output_path),
        str(source_path),
        *tester.get("compiler_flags", []),
    ]

    completed = _run_command(command, timeout_sec=int(tester.get("build_timeout_sec", 60)))
    payload = {
        "status": "pass" if completed.returncode == 0 and output_path.exists() else "fail",
        "source_path": str(source_path),
        "output_path": str(output_path),
        "compiler": compiler_info["compiler"],
        "ndk_root": compiler_info["ndk_root"],
        "command": command,
        "exit_code": completed.returncode,
        "stdout_tail": completed.stdout.splitlines()[-40:],
        "stderr_tail": completed.stderr.splitlines()[-40:],
    }
    if payload["status"] != "pass":
        payload["blocker_kind"] = "android-guest-tester-build-failed"
    return payload


def _classify_minimal_guest_failure(log_path: Path, scenario: dict) -> dict:
    android_payload = _classify_android_boot_failure(log_path)
    text = log_path.read_text(errors="replace") if log_path.exists() else ""
    result_fail_marker = f"[{scenario['log_prefix']}] RESULT=FAIL"
    markers = list(android_payload["markers"])
    blocker_kind = android_payload["blocker_kind"]

    if f"[{scenario['log_prefix']}] DEVICE_NODE_MISSING=1" in text:
        blocker_kind = blocker_kind or "minimal-guest-device-missing"
        markers.append("minimal-guest-device-missing")
    if not text:
        blocker_kind = blocker_kind or "minimal-guest-summary-missing"
        markers.append("minimal-guest-empty-log")
    elif scenario["result_success_marker"] not in text and result_fail_marker not in text:
        blocker_kind = blocker_kind or "minimal-guest-summary-missing"
        markers.append("minimal-guest-summary-missing")

    if blocker_kind is None:
        blocker_kind = "minimal-guest-emulator-contract-failure"
        markers.append("minimal-guest-emulator-contract-failure")

    return {
        "blocker_kind": blocker_kind,
        "markers": markers,
        "log_tail": _tail_lines(log_path, limit=120),
    }


def _prepare_minimal_linux_guest(
    *,
    repo_root: Path,
    module_id: str,
    scenario: dict,
    scenario_inputs: dict,
    managed_guest: dict,
    artifact_root: str | Path | None = None,
    build_dir: str | Path | None = None,
    make_llvm: str | None = None,
) -> dict:
    label = managed_guest.get("label", "managed")
    kernel_source = managed_guest.get("kernel_source", "build-tree")
    resolved_build_dir = None
    build_dir_source = None
    kernel_build = None

    if kernel_source == "build-tree":
        resolved_build_dir, build_dir_source = _resolve_build_dir(repo_root, module_id, build_dir)
        try:
            kernel_image, kernel_build = _ensure_kernel_image(
                repo_root,
                resolved_build_dir,
                make_llvm=make_llvm,
            )
        except RuntimeError as exc:
            return {
                "status": "blocked",
                "blocker_kind": "minimal-guest-kernel-build-failed",
                "details": [str(exc), f"build_dir={resolved_build_dir}"],
            }
    elif kernel_source == "explicit-path":
        kernel_image = _expand_path(managed_guest.get("kernel_path"))
        if kernel_image is None or not kernel_image.exists():
            return {
                "status": "blocked",
                "blocker_kind": "minimal-guest-kernel-missing",
                "details": [f"kernel_path={managed_guest.get('kernel_path')}"],
            }
    else:
        return {
            "status": "blocked",
            "blocker_kind": "minimal-guest-kernel-source-unsupported",
            "details": [f"kernel_source={kernel_source}"],
        }

    tester_build = _build_guest_tester(module_id, scenario)
    if tester_build["status"] != "pass":
        return {
            "status": "blocked",
            "blocker_kind": tester_build.get("blocker_kind", "minimal-guest-tester-build-failed"),
            "details": tester_build.get("details")
            or tester_build.get("stderr_tail")
            or tester_build.get("stdout_tail")
            or ["guest-tester-build-failed"],
            "guest_tester": tester_build,
        }

    configured_image_dir = _expand_path(managed_guest.get("image_dir"))
    if configured_image_dir is not None:
        if not configured_image_dir.exists():
            return {
                "status": "blocked",
                "blocker_kind": "minimal-guest-image-dir-missing",
                "details": [f"image_dir={configured_image_dir}"],
            }
        sysdir = configured_image_dir
    else:
        sysdir = _artifact_output(
            repo_root,
            module_id,
            managed_guest.get("synthetic_sysdir_name", f"android-emulator-{label}-minimal-sysdir"),
            artifact_root=artifact_root,
        )
        sysdir.mkdir(parents=True, exist_ok=True)

    assets: dict[str, Path] = {"image_dir": sysdir}
    synthetic_assets: dict[str, dict] = {}
    for asset_name, default_size, default_label in [
        ("system", 64, "system"),
        ("vendor", 32, "vendor"),
        ("userdata", 64, "userdata"),
    ]:
        configured_path = _expand_path(managed_guest.get(f"{asset_name}_path"))
        if configured_path is not None:
            if not configured_path.exists():
                return {
                    "status": "blocked",
                    "blocker_kind": "minimal-guest-asset-missing",
                    "details": [f"{asset_name}={configured_path}"],
                }
            assets[asset_name] = configured_path
            continue

        image_path = _artifact_output(
            repo_root,
            module_id,
            managed_guest.get(
                f"synthetic_{asset_name}_filename",
                f"android-emulator-{label}-{asset_name}.img",
            ),
            artifact_root=artifact_root,
        )
        size_mb = int(managed_guest.get(f"synthetic_{asset_name}_size_mb", default_size))
        fs_label = str(managed_guest.get(f"synthetic_{asset_name}_label", default_label))
        try:
            _synthesize_ext4_image(image_path, size_mb=size_mb, label=fs_label)
        except Exception as exc:
            return {
                "status": "blocked",
                "blocker_kind": "minimal-guest-image-synthesis-failed",
                "details": [f"{asset_name}: {exc}"],
            }
        assets[asset_name] = image_path
        synthetic_assets[asset_name] = {
            "path": str(image_path),
            "size_mb": size_mb,
            "label": fs_label,
        }

    tester = scenario["guest_tester"]
    guest_tester_path = tester.get("guest_path", f"bin/{tester.get('binary_name', Path(tester_build['output_path']).name)}")
    guest_tester_guest_path = f"/{guest_tester_path.lstrip('/')}"
    initramfs_inputs = dict(scenario_inputs)
    initramfs_inputs["guest_tester_path"] = guest_tester_guest_path
    initramfs_temp, initramfs_result = _build_initramfs(
        scenario,
        initramfs_inputs,
        extra_files=[(Path(tester_build["output_path"]), guest_tester_path.lstrip("/"))],
    )
    initramfs_artifact = _artifact_output(
        repo_root,
        module_id,
        managed_guest.get(
            "minimal_guest_ramdisk_filename",
            f"android-emulator-{label}-minimal-ramdisk.cpio",
        ),
        artifact_root=artifact_root,
    )
    initramfs_artifact.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(initramfs_temp, initramfs_artifact)

    return {
        "status": "pass",
        "assets": {
            "image_dir": str(sysdir),
            "kernel": str(kernel_image),
            "ramdisk": str(initramfs_artifact),
            "system": str(assets["system"]),
            "vendor": str(assets["vendor"]),
            "userdata": str(assets["userdata"]),
        },
        "synthetic_assets": synthetic_assets,
        "guest_tester": tester_build,
        "guest_tester_path": guest_tester_guest_path,
        "initramfs": {
            **initramfs_result,
            "artifact_path": str(initramfs_artifact),
        },
        "kernel_source": kernel_source,
        "build": {
            "build_dir": str(resolved_build_dir) if resolved_build_dir is not None else None,
            "build_dir_source": build_dir_source,
            "kernel_build": kernel_build,
        },
    }


def _extract_remote_exit_status(output: str) -> int | None:
    match = re.search(r"__C2SAFERUST_EXIT__=(-?\d+)", output)
    return int(match.group(1)) if match else None


def _run_android_guest_tester(
    *,
    repo_root: Path,
    module_id: str,
    scenario: dict,
    scenario_inputs: dict,
    adb_path: str,
    serial: str,
    qemu_log_output: str | Path | None = None,
    artifact_root: str | Path | None = None,
    timeout_sec: int = 120,
) -> dict:
    tester = scenario["guest_tester"]
    build_result = _build_android_guest_tester(module_id, scenario)
    transcript_path = _artifact_output(
        repo_root,
        module_id,
        tester.get("log_artifact", "android-oracle.log"),
        qemu_log_output,
        artifact_root=artifact_root,
    )
    transcript_path.parent.mkdir(parents=True, exist_ok=True)

    if build_result["status"] == "blocked":
        transcript_path.write_text("\n".join(build_result["details"]) + "\n")
        return {
            "status": "blocked",
            "blocker_kind": build_result["blocker_kind"],
            "transcript_path": str(transcript_path),
            "steps": [
                {
                    "step_id": "build-android-guest-tester",
                    "status": "blocked",
                    "details": build_result["details"],
                }
            ],
            "build": build_result,
        }

    if build_result["status"] != "pass":
        transcript_path.write_text(
            "\n".join([*build_result["stdout_tail"], *build_result["stderr_tail"]]) + "\n"
        )
        return {
            "status": "blocked",
            "blocker_kind": build_result["blocker_kind"],
            "transcript_path": str(transcript_path),
            "steps": [
                {
                    "step_id": "build-android-guest-tester",
                    "status": "fail",
                    "details": build_result["stderr_tail"] or build_result["stdout_tail"] or ["guest-tester-build-failed"],
                }
            ],
            "build": build_result,
        }

    remote_path = tester["remote_path"]
    push = _adb_device_command(
        adb_path,
        serial,
        "push",
        build_result["output_path"],
        remote_path,
        timeout_sec=min(timeout_sec, int(tester.get("push_timeout_sec", 60))),
    )
    if push.returncode != 0:
        transcript_path.write_text(push.stdout + ("\n[stderr]\n" + push.stderr if push.stderr else ""))
        return {
            "status": "blocked",
            "blocker_kind": "android-guest-tester-deploy-failed",
            "transcript_path": str(transcript_path),
            "steps": [
                {
                    "step_id": "build-android-guest-tester",
                    "status": "pass",
                    "details": [build_result["output_path"]],
                },
                {
                    "step_id": "push-android-guest-tester",
                    "status": "fail",
                    "details": push.stderr.splitlines()[-20:] or push.stdout.splitlines()[-20:] or ["adb-push-failed"],
                },
            ],
            "build": build_result,
            "deploy": {
                "command": [adb_path, "-s", serial, "push", build_result["output_path"], remote_path],
                "exit_code": push.returncode,
                "stdout_tail": push.stdout.splitlines()[-20:],
                "stderr_tail": push.stderr.splitlines()[-20:],
            },
        }

    chmod = _adb_device_command(
        adb_path,
        serial,
        "shell",
        "chmod",
        "0755",
        remote_path,
        timeout_sec=min(timeout_sec, 20),
    )
    if chmod.returncode != 0:
        transcript_path.write_text(chmod.stdout + ("\n[stderr]\n" + chmod.stderr if chmod.stderr else ""))
        return {
            "status": "blocked",
            "blocker_kind": "android-guest-tester-deploy-failed",
            "transcript_path": str(transcript_path),
            "steps": [
                {
                    "step_id": "build-android-guest-tester",
                    "status": "pass",
                    "details": [build_result["output_path"]],
                },
                {
                    "step_id": "push-android-guest-tester",
                    "status": "pass",
                    "details": push.stdout.splitlines()[-10:] or [remote_path],
                },
                {
                    "step_id": "chmod-android-guest-tester",
                    "status": "fail",
                    "details": chmod.stderr.splitlines()[-20:] or chmod.stdout.splitlines()[-20:] or ["adb-chmod-failed"],
                },
            ],
            "build": build_result,
        }

    remote_command = f"sh -c '{remote_path}; status=$?; echo __C2SAFERUST_EXIT__=$status'"
    executed = _adb_device_command(
        adb_path,
        serial,
        "shell",
        remote_command,
        timeout_sec=min(timeout_sec, int(tester.get("run_timeout_sec", timeout_sec))),
    )
    transcript = executed.stdout + ("\n[stderr]\n" + executed.stderr if executed.stderr else "")
    transcript_path.write_text(transcript)
    summary = _extract_scenario_summary(transcript, scenario)
    remote_exit = _extract_remote_exit_status(transcript)
    run_status = "pass" if summary["result"] == "PASS" else "fail"

    return {
        "status": run_status,
        "transcript_path": str(transcript_path),
        "summary": summary,
        "remote_exit_code": remote_exit,
        "build": build_result,
        "deploy": {
            "remote_path": remote_path,
            "push_stdout_tail": push.stdout.splitlines()[-20:],
            "push_stderr_tail": push.stderr.splitlines()[-20:],
            "chmod_stdout_tail": chmod.stdout.splitlines()[-20:],
            "chmod_stderr_tail": chmod.stderr.splitlines()[-20:],
        },
        "run": {
            "command": [adb_path, "-s", serial, "shell", remote_command],
            "adb_exit_code": executed.returncode,
            "remote_exit_code": remote_exit,
            "stdout_tail": executed.stdout.splitlines()[-60:],
            "stderr_tail": executed.stderr.splitlines()[-40:],
        },
        "steps": [
            {
                "step_id": "build-android-guest-tester",
                "status": "pass",
                "details": [build_result["output_path"]],
            },
            {
                "step_id": "push-android-guest-tester",
                "status": "pass",
                "details": push.stdout.splitlines()[-10:] or [remote_path],
            },
            {
                "step_id": "chmod-android-guest-tester",
                "status": "pass",
                "details": [remote_path],
            },
            {
                "step_id": "run-android-guest-tester",
                "status": "pass" if run_status == "pass" else "fail",
                "details": executed.stdout.splitlines()[-20:] or executed.stderr.splitlines()[-20:] or ["guest-tester-empty-output"],
            },
        ],
    }


def _run_minimal_linux_guest_oracle(
    *,
    repo_root: Path,
    module_id: str,
    scenario: dict,
    scenario_inputs: dict,
    managed_guest: dict,
    managed_state: dict,
    timeout_sec: int = 120,
) -> dict:
    log_path = Path(managed_state["log_path"])
    process = managed_state["process"]
    deadline = time.monotonic() + min(timeout_sec, int(managed_guest.get("boot_timeout_sec", timeout_sec)))
    result_fail_marker = f"[{scenario['log_prefix']}] RESULT=FAIL"
    terminal_details: list[str] = []
    while time.monotonic() < deadline:
        log_text = log_path.read_text(errors="replace") if log_path.exists() else ""
        if (
            scenario["result_success_marker"] in log_text
            or result_fail_marker in log_text
            or f"[{scenario['log_prefix']}] DEVICE_NODE_MISSING=1" in log_text
        ):
            terminal_details = ["result-marker-seen"]
            break
        if process.poll() is not None:
            terminal_details = [f"process-exited={process.returncode}"]
            break
        time.sleep(1)

    if not terminal_details:
        return {
            "status": "blocked",
            "blocker_kind": "minimal-guest-tester-timeout",
            "transcript_path": str(log_path),
            "steps": [
                {
                    "step_id": "wait-minimal-guest-result",
                    "status": "fail",
                    "details": [f"result-timeout>{min(timeout_sec, int(managed_guest.get('boot_timeout_sec', timeout_sec)))}s"],
                }
            ],
        }

    log_text = log_path.read_text(errors="replace") if log_path.exists() else ""
    summary = _extract_scenario_summary(log_text, scenario)
    failure = _classify_minimal_guest_failure(log_path, scenario)
    if failure["blocker_kind"] == "minimal-guest-device-missing":
        return {
            "status": "blocked",
            "blocker_kind": failure["blocker_kind"],
            "transcript_path": str(log_path),
            "steps": [
                {
                    "step_id": "wait-minimal-guest-result",
                    "status": "pass",
                    "details": terminal_details,
                },
                {
                    "step_id": "probe-minimal-guest-device-node",
                    "status": "fail",
                    "details": failure["markers"] or ["minimal-guest-device-missing"],
                },
            ],
            "summary": summary,
        }
    if failure["blocker_kind"] == "minimal-guest-summary-missing":
        return {
            "status": "blocked",
            "blocker_kind": failure["blocker_kind"],
            "transcript_path": str(log_path),
            "steps": [
                {
                    "step_id": "wait-minimal-guest-result",
                    "status": "pass",
                    "details": terminal_details,
                },
                {
                    "step_id": "collect-minimal-guest-summary",
                    "status": "fail",
                    "details": failure["markers"] or ["minimal-guest-summary-missing"],
                },
            ],
            "summary": summary,
        }

    run_status = "pass" if summary["result"] == "PASS" else "fail"
    return {
        "status": run_status,
        "transcript_path": str(log_path),
        "summary": summary,
        "steps": [
            {
                "step_id": "wait-minimal-guest-result",
                "status": "pass",
                "details": terminal_details,
            },
            {
                "step_id": "run-minimal-linux-guest-tester",
                "status": "pass" if run_status == "pass" else "fail",
                "details": _tail_lines(log_path, limit=20) or ["minimal-guest-empty-output"],
            },
        ],
        "environment": {
            "device_node": scenario_inputs.get("device_node"),
        },
    }


def _detect_build_env() -> tuple[dict[str, str], dict]:
    env = dict(os.environ)
    profile = {
        "source": "inherited-environment",
        "make_llvm": None,
        "path_prefix": None,
        "libclang_path": env.get("LIBCLANG_PATH"),
        "ld_library_path_prefix": None,
    }

    clang15 = LOCAL_LLVM_ROOT / "bin" / "clang-15"
    libclang_dir = LOCAL_LLVM_ROOT / "lib" / "x86_64-linux-gnu"
    def local_llvm_is_runnable() -> bool:
        if not clang15.exists():
            return False
        try:
            completed = subprocess.run(
                [str(clang15), "--version"],
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
            )
        except Exception:
            return False
        return completed.returncode == 0

    if local_llvm_is_runnable():
        env["PATH"] = f"{LOCAL_LLVM_ROOT / 'bin'}:{env.get('PATH', '')}"
        env.setdefault("LIBCLANG_PATH", str(libclang_dir))
        current_ld = env.get("LD_LIBRARY_PATH", "")
        env["LD_LIBRARY_PATH"] = (
            f"{libclang_dir}:{current_ld}" if current_ld else str(libclang_dir)
        )
        profile.update(
            {
                "source": "auto-detected-/tmp/llvm-15-local",
                "make_llvm": "-15",
                "path_prefix": str(LOCAL_LLVM_ROOT / "bin"),
                "libclang_path": env["LIBCLANG_PATH"],
                "ld_library_path_prefix": str(libclang_dir),
            }
        )
    return env, profile


def _is_configured_build_dir(path: Path) -> bool:
    return (path / ".config").exists()


def _resolve_build_dir(repo_root: Path, module_id: str, build_dir: str | Path | None) -> tuple[Path, str]:
    if build_dir is not None:
        return Path(build_dir), "user-specified"
    candidates = [
        (Path(f"/tmp/{module_id}-build"), "auto-detected-module-build-dir"),
        (Path(f"/tmp/c2saferust-{module_id}-build"), "default-c2saferust-build-dir"),
        (repo_root, "in-tree-build"),
    ]
    for candidate, source in candidates:
        if _is_configured_build_dir(candidate):
            return candidate, source
    return Path(f"/tmp/c2saferust-{module_id}-build"), "default-c2saferust-build-dir"


def _ensure_kernel_image(
    repo_root: Path,
    build_dir: Path,
    *,
    make_llvm: str | None = None,
) -> tuple[Path, dict]:
    image = build_dir / "arch" / "x86" / "boot" / "bzImage"
    env, env_profile = _detect_build_env()
    llvm_arg = make_llvm if make_llvm is not None else env_profile["make_llvm"]

    if image.exists():
        return image, {
            "status": "already_built",
            "image": str(image),
            "env_profile": env_profile,
            "command": None,
            "exit_code": 0,
        }

    command = ["make", f"O={build_dir}"]
    if llvm_arg is not None:
        command.append(f"LLVM={llvm_arg}")
    command.extend([f"-j{os.cpu_count() or 1}", "bzImage"])

    completed = subprocess.run(
        command,
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    result = {
        "status": "built" if completed.returncode == 0 else "failed",
        "image": str(image),
        "env_profile": env_profile,
        "command": " ".join(command),
        "exit_code": completed.returncode,
        "stdout_tail": completed.stdout.splitlines()[-60:],
        "stderr_tail": completed.stderr.splitlines()[-60:],
    }
    if completed.returncode != 0 or not image.exists():
        raise RuntimeError("Failed to build `bzImage` for oracle run.")
    return image, result


def _ensure_build_target(
    repo_root: Path,
    build_dir: Path,
    target: str,
    *,
    make_llvm: str | None = None,
) -> tuple[Path, dict]:
    target_path = build_dir / target
    env, env_profile = _detect_build_env()
    llvm_arg = make_llvm if make_llvm is not None else env_profile["make_llvm"]

    if target_path.exists():
        return target_path, {
            "status": "already_built",
            "target": target,
            "target_path": str(target_path),
            "env_profile": env_profile,
            "command": None,
            "exit_code": 0,
        }

    command = ["make", f"O={build_dir}"]
    if llvm_arg is not None:
        command.append(f"LLVM={llvm_arg}")
    command.extend([f"-j{os.cpu_count() or 1}", target])

    completed = subprocess.run(
        command,
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    result = {
        "status": "built" if completed.returncode == 0 else "failed",
        "target": target,
        "target_path": str(target_path),
        "env_profile": env_profile,
        "command": " ".join(command),
        "exit_code": completed.returncode,
        "stdout_tail": completed.stdout.splitlines()[-60:],
        "stderr_tail": completed.stderr.splitlines()[-60:],
    }
    if completed.returncode != 0 or not target_path.exists():
        raise RuntimeError(f"Failed to build oracle target `{target}`.")
    return target_path, result


def _write_init_script(root: Path, scenario: dict, scenario_inputs: dict) -> None:
    format_context = dict(scenario_inputs)
    format_context["log_prefix"] = scenario["log_prefix"]
    init_text = "\n".join(line.format(**format_context) for line in scenario["script_lines"]) + "\n"
    (root / "init").write_text(init_text)
    (root / "init").chmod(0o755)


def _build_initramfs(
    scenario: dict,
    scenario_inputs: dict,
    *,
    extra_files: list[tuple[Path, str]] | None = None,
) -> tuple[Path, dict]:
    busybox = shutil.which("busybox")
    cpio = shutil.which("cpio")
    if busybox is None or cpio is None:
        raise RuntimeError("`busybox` and `cpio` are required for the QEMU oracle.")

    root = Path(tempfile.mkdtemp(prefix="c2saferust-smoke-rootfs-"))
    for relative in ["bin", "sbin", "proc", "sys", "dev", "etc", "tmp", "run"]:
        (root / relative).mkdir(parents=True, exist_ok=True)

    shutil.copy2(busybox, root / "bin" / "busybox")
    for app in [
        "sh",
        "mount",
        "mkdir",
        "mknod",
        "cat",
        "echo",
        "uname",
        "ip",
        "poweroff",
        "reboot",
        "sleep",
        "dmesg",
        "ls",
        "grep",
        "sed",
        "awk",
        "ifconfig",
        "tail",
    ]:
        (root / "bin" / app).symlink_to("/bin/busybox")
    (root / "sbin" / "modprobe").symlink_to("/bin/busybox")
    (root / "sbin" / "insmod").symlink_to("/bin/busybox")
    (root / "sbin" / "rmmod").symlink_to("/bin/busybox")

    for source, destination in extra_files or []:
        target = root / destination
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)

    _write_init_script(root, scenario, scenario_inputs)

    initramfs_path = Path(tempfile.mkstemp(prefix="c2saferust-smoke-", suffix=".cpio")[1])
    completed = subprocess.run(
        f"cd {root} && find . -print0 | cpio --null -o --format=newc > {initramfs_path}",
        shell=True,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise RuntimeError("Failed to build the oracle initramfs.")

    return initramfs_path, {
        "rootfs_dir": str(root),
        "initramfs_path": str(initramfs_path),
        "cpio_stdout_tail": completed.stdout.splitlines()[-20:],
        "cpio_stderr_tail": completed.stderr.splitlines()[-20:],
    }


def _run_qemu(kernel_image: Path, initramfs: Path, qemu_log: Path, *, timeout_sec: int) -> dict:
    qemu = shutil.which("qemu-system-x86_64")
    if qemu is None:
        raise RuntimeError("`qemu-system-x86_64` is required for the oracle.")

    command = [
        qemu,
        "-nodefaults",
        "-no-reboot",
        "-nographic",
        "-machine",
        "q35,accel=tcg",
        "-m",
        "1024",
        "-smp",
        "2",
        "-kernel",
        str(kernel_image),
        "-initrd",
        str(initramfs),
        "-append",
        "console=ttyS0 rdinit=/init nokaslr panic=-1",
        "-serial",
        "stdio",
    ]

    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout_sec,
    )
    qemu_log.write_text(completed.stdout + ("\n[stderr]\n" + completed.stderr if completed.stderr else ""))
    return {
        "command": command,
        "exit_code": completed.returncode,
        "stdout_tail": completed.stdout.splitlines()[-120:],
        "stderr_tail": completed.stderr.splitlines()[-80:],
    }


def _run_qemu_scenario(
    *,
    repo_root: Path,
    module_path: str | Path,
    module_profile: dict,
    scenario: dict,
    translation_plan: dict,
    scenario_inputs: dict,
    qemu_log_output: str | Path | None = None,
    artifact_root: str | Path | None = None,
    build_dir: str | Path | None = None,
    make_llvm: str | None = None,
    timeout_sec: int = 120,
) -> dict:
    del module_path, module_profile

    module_id = translation_plan["module_id"]
    resolved_build_dir, build_dir_source = _resolve_build_dir(repo_root, module_id, build_dir)
    kernel_image, build_result = _ensure_kernel_image(
        repo_root,
        resolved_build_dir,
        make_llvm=make_llvm,
    )
    initramfs, initramfs_result = _build_initramfs(scenario, scenario_inputs)

    qemu_log_path = _artifact_output(
        repo_root,
        module_id,
        "qemu-smoke.log",
        qemu_log_output,
        artifact_root=artifact_root,
    )
    qemu_log_path.parent.mkdir(parents=True, exist_ok=True)
    qemu_result = _run_qemu(kernel_image, initramfs, qemu_log_path, timeout_sec=timeout_sec)
    qemu_text = qemu_log_path.read_text()
    smoke_summary = _extract_scenario_summary(qemu_text, scenario)

    return {
        "runner": {
            "id": "qemu-scenario",
            "log_path": str(qemu_log_path),
            "command": qemu_result["command"],
            "exit_code": qemu_result["exit_code"],
            "stdout_tail": qemu_result["stdout_tail"],
            "stderr_tail": qemu_result["stderr_tail"],
        },
        "pass": smoke_summary["result"] == "PASS",
        "ready_for_oracle": smoke_summary["result"] == "PASS",
        "build": {
            "build_dir": str(resolved_build_dir),
            "build_dir_source": build_dir_source,
            "kernel_image": str(kernel_image),
            "kernel_build": build_result,
        },
        "initramfs": initramfs_result,
        "qemu": {
            "log_path": str(qemu_log_path),
            **qemu_result,
        },
        "smoke_summary": smoke_summary,
        "steps": [
            {
                "step_id": "build-kernel-image",
                "status": "pass",
                "details": [str(kernel_image)],
            },
            {
                "step_id": "build-initramfs",
                "status": "pass",
                "details": [str(initramfs)],
            },
            {
                "step_id": "run-qemu-link-smoke",
                "status": "pass" if smoke_summary["result"] == "PASS" else "fail",
                "details": [f"RESULT={smoke_summary['result']}"],
            },
        ],
    }


def _run_qemu_module_lifecycle(
    *,
    repo_root: Path,
    module_path: str | Path,
    module_profile: dict,
    scenario: dict,
    translation_plan: dict,
    scenario_inputs: dict,
    qemu_log_output: str | Path | None = None,
    artifact_root: str | Path | None = None,
    build_dir: str | Path | None = None,
    make_llvm: str | None = None,
    timeout_sec: int = 120,
) -> dict:
    module_id = translation_plan["module_id"]
    module_relpath = scenario_inputs["module_file_relpath"]
    module_file_name = scenario_inputs.get("module_file_name", Path(module_relpath).name)
    rust_gate = workflow._run_rust_toolchain_gate(
        module_path,
        repo_root=repo_root,
        profile_id=module_profile.get("profile_id"),
        source_tree=None,
        build_dir=build_dir,
        make_llvm=make_llvm,
    )
    if not rust_gate["pass"]:
        return _blocked_runner_payload(
            "qemu-module-lifecycle",
            blocker_kind="environment-gap",
            blockers=["`make rustavailable` did not pass for the candidate build environment."],
            evidence_tier="env_blocked",
            steps=[
                {
                    "step_id": "rust-toolchain-available",
                    "status": "fail",
                    "details": [
                        f"build_dir={rust_gate.get('build_dir')}",
                        f"command={rust_gate.get('command')}",
                    ],
                }
            ],
            environment={
                "build_dir": rust_gate.get("build_dir"),
                "build_dir_source": rust_gate.get("build_dir_source"),
                "env_profile": rust_gate.get("env_profile"),
            },
        )

    preparation = workflow._prepare_module_lifecycle_build(
        module_path,
        repo_root=repo_root,
        artifact_root=artifact_root,
        profile_id=module_profile.get("profile_id"),
        source_tree=None,
        build_dir=build_dir,
        make_llvm=make_llvm,
    )
    if not preparation["pass"]:
        return _blocked_runner_payload(
            "qemu-module-lifecycle",
            blocker_kind="environment-gap",
            blockers=["Module lifecycle build configuration did not converge before QEMU execution."],
            evidence_tier="env_blocked",
            steps=[
                {
                    "step_id": "rust-toolchain-available",
                    "status": "pass",
                    "details": [f"build_dir={rust_gate.get('build_dir')}"],
                },
                {
                    "step_id": "configure-module-lifecycle-build",
                    "status": "fail",
                    "details": [
                        preparation.get("reason", "module-lifecycle-config-merge-failed"),
                        *preparation.get("config_verification", {}).get("missing", []),
                    ],
                },
            ],
            environment={
                "build_dir": preparation.get("build_dir"),
                "build_dir_source": preparation.get("build_dir_source"),
                "env_profile": preparation.get("env_profile"),
                "module_lifecycle_config": preparation.get("fragment", {}),
            },
        )

    resolved_build_dir = Path(preparation["build_dir"])
    build_dir_source = preparation["build_dir_source"]
    kernel_image, build_result = _ensure_kernel_image(
        repo_root,
        resolved_build_dir,
        make_llvm=make_llvm,
    )
    module_ko, module_build = _ensure_build_target(
        repo_root,
        resolved_build_dir,
        module_relpath,
        make_llvm=make_llvm,
    )
    initramfs, initramfs_result = _build_initramfs(
        scenario,
        scenario_inputs,
        extra_files=[(module_ko, f"modules/{module_file_name}")],
    )

    qemu_log_path = _artifact_output(
        repo_root,
        module_id,
        "qemu-smoke.log",
        qemu_log_output,
        artifact_root=artifact_root,
    )
    qemu_log_path.parent.mkdir(parents=True, exist_ok=True)
    qemu_result = _run_qemu(kernel_image, initramfs, qemu_log_path, timeout_sec=timeout_sec)
    qemu_text = qemu_log_path.read_text()
    smoke_summary = _extract_scenario_summary(qemu_text, scenario)

    return {
        "runner": {
            "id": "qemu-module-lifecycle",
            "log_path": str(qemu_log_path),
            "command": qemu_result["command"],
            "exit_code": qemu_result["exit_code"],
            "stdout_tail": qemu_result["stdout_tail"],
            "stderr_tail": qemu_result["stderr_tail"],
        },
        "pass": smoke_summary["result"] == "PASS",
        "ready_for_oracle": smoke_summary["result"] == "PASS",
        "evidence_tier": "baseline_abi_pass" if smoke_summary["result"] == "PASS" else "env_ready",
        "build": {
            "build_dir": str(resolved_build_dir),
            "build_dir_source": build_dir_source,
            "kernel_image": str(kernel_image),
            "rust_toolchain_gate": rust_gate,
            "module_lifecycle_build": preparation,
            "kernel_build": build_result,
            "module_build": module_build,
        },
        "initramfs": initramfs_result,
        "qemu": {
            "log_path": str(qemu_log_path),
            **qemu_result,
        },
        "smoke_summary": smoke_summary,
        "steps": [
            {
                "step_id": "rust-toolchain-available",
                "status": "pass",
                "details": [f"build_dir={rust_gate['build_dir']}"],
            },
            {
                "step_id": "configure-module-lifecycle-build",
                "status": "pass",
                "details": [
                    preparation["fragment"]["path"] if preparation["fragment"]["path"] else "no-fragment-required",
                ],
            },
            {
                "step_id": "build-kernel-image",
                "status": "pass",
                "details": [str(kernel_image)],
            },
            {
                "step_id": "build-module",
                "status": "pass",
                "details": [str(module_ko)],
            },
            {
                "step_id": "build-initramfs",
                "status": "pass",
                "details": [str(initramfs)],
            },
            {
                "step_id": "run-qemu-module-lifecycle",
                "status": "pass" if smoke_summary["result"] == "PASS" else "fail",
                "details": [f"RESULT={smoke_summary['result']}"],
            },
        ],
    }


def _run_android_goldfish_oracle(
    *,
    repo_root: Path,
    module_path: str | Path,
    module_profile: dict,
    scenario: dict,
    translation_plan: dict,
    scenario_inputs: dict,
    qemu_log_output: str | Path | None = None,
    artifact_root: str | Path | None = None,
    build_dir: str | Path | None = None,
    make_llvm: str | None = None,
    timeout_sec: int = 120,
    runner_context: dict | None = None,
) -> dict:
    del module_path, module_profile
    module_id = translation_plan["module_id"]
    managed_guest = _resolve_managed_guest_config(scenario, runner_context)
    boot_mode = _managed_guest_boot_mode(managed_guest)
    required_tools = ["emulator"] if boot_mode == "minimal-linux" else None
    env_report = _detect_android_emulator_env(scenario, required_tools=required_tools)
    blocker_details = [
        f"missing_tool={tool}" for tool in env_report["missing_tools"]
    ] + [
        f"missing_env={name}" for name in env_report["missing_env"]
    ]
    if not env_report["available"]:
        return _blocked_runner_payload(
            "android-emulator-goldfish",
            blocker_kind="android-emulator-environment-missing",
            blockers=[
                (
                    "Minimal goldfish guest oracle requires the Android emulator plus local image-building tools before runtime checks can start."
                    if boot_mode == "minimal-linux"
                    else "Android emulator oracle requires `emulator` + `adb` plus the configured AVD name before goldfish runtime checks can start."
                )
            ],
            evidence_tier="env_blocked",
            steps=[
                _environment_missing_step(
                    "detect-android-emulator-env",
                    blocker_details or ["android-emulator-environment-missing"],
                ),
            ],
            environment={
                **env_report,
                "scenario_inputs": scenario_inputs,
            },
        )

    detect_step = {
        "step_id": "detect-android-emulator-env",
        "status": "pass",
        "details": [
            "emulator resolved for minimal guest" if boot_mode == "minimal-linux" else "emulator and adb resolved",
            *[f"{tool}={path}" for tool, path in env_report["tool_paths"].items() if path],
            *[f"{name}={value}" for name, value in env_report["env"].items() if value],
        ],
    }

    managed_state = None
    adb_path = env_report["tool_paths"].get("adb")
    devices: list[dict]
    serial: str
    common_steps: list[dict]
    environment = {
        **env_report,
        "scenario_inputs": scenario_inputs,
    }

    try:
        if managed_guest is not None:
            managed_state = _start_managed_android_guest(
                repo_root=repo_root,
                module_id=module_id,
                scenario=scenario,
                scenario_inputs=scenario_inputs,
                env_report=env_report,
                managed_guest=managed_guest,
                artifact_root=artifact_root,
                build_dir=build_dir,
                make_llvm=make_llvm,
            )
            environment["managed_guest"] = {
                "requested": managed_guest,
            }
            if managed_state["status"] != "pass":
                return _blocked_runner_payload(
                    "android-emulator-goldfish",
                    blocker_kind=managed_state["blocker_kind"],
                    blockers=["Managed Android emulator boot did not complete."],
                    evidence_tier="env_ready",
                    log_path=managed_state.get("log_path"),
                    steps=[
                        detect_step,
                        {
                            "step_id": "launch-managed-android-emulator",
                            "status": "fail",
                            "details": managed_state["details"],
                        },
                    ],
                    environment={
                        **environment,
                        "managed_guest": {
                            **environment["managed_guest"],
                            "result": {
                                key: value
                                for key, value in managed_state.items()
                                if key not in {"process", "state_dir"}
                            },
                        },
                    },
                )
            serial = managed_state["serial"]
            devices = [{"serial": serial, "state": "device", "details": ["managed-boot"]}]
            common_steps = [detect_step, *managed_state["steps"]]
            environment["managed_guest"]["result"] = {
                key: value
                for key, value in managed_state.items()
                if key not in {"process", "state_dir"}
            }

            if boot_mode == "minimal-linux":
                tester_run = _run_minimal_linux_guest_oracle(
                    repo_root=repo_root,
                    module_id=module_id,
                    scenario=scenario,
                    scenario_inputs=scenario_inputs,
                    managed_guest=managed_guest,
                    managed_state=managed_state,
                    timeout_sec=timeout_sec,
                )
                guest_tester = scenario.get("guest_tester", {})
                if tester_run["status"] == "blocked":
                    return _blocked_runner_payload(
                        "android-emulator-goldfish",
                        blocker_kind=tester_run["blocker_kind"],
                        blockers=["Minimal goldfish guest did not reach a usable oracle state."],
                        evidence_tier="env_ready",
                        log_path=tester_run["transcript_path"],
                        steps=[*common_steps, *tester_run["steps"]],
                        environment={
                            **environment,
                            "device_node": scenario_inputs.get("device_node"),
                            "guest_tester": {
                                "source": guest_tester.get("source"),
                                "guest_path": guest_tester.get("guest_path"),
                            },
                        },
                    )

                summary = tester_run["summary"]
                pass_run = tester_run["status"] == "pass"
                result = {
                    "runner": {
                        "id": "android-emulator-goldfish",
                        "status": "pass" if pass_run else "fail",
                        "selected_device": None,
                        "log_path": tester_run["transcript_path"],
                        "command": managed_state["command"],
                    },
                    "pass": pass_run,
                    "ready_for_oracle": True,
                    "blocked": False,
                    "evidence_tier": "baseline_abi_pass" if pass_run else "device_present",
                    "smoke_summary": summary,
                    "steps": [*common_steps, *tester_run["steps"]],
                    "environment": {
                        **environment,
                        "device_node": scenario_inputs.get("device_node"),
                        "guest_tester": {
                            "source": guest_tester.get("source"),
                            "guest_path": guest_tester.get("guest_path"),
                        },
                    },
                    "guest_tester": managed_state.get("guest_tester"),
                    "initramfs": managed_state.get("initramfs"),
                    "managed_guest": {
                        key: value
                        for key, value in managed_state.items()
                        if key not in {"process", "state_dir"}
                    },
                }
                return result
        else:
            adb_devices = _adb_command(adb_path, "devices", "-l", timeout_sec=min(timeout_sec, 20))
            if adb_devices.returncode != 0:
                return _blocked_runner_payload(
                    "android-emulator-goldfish",
                    blocker_kind="android-emulator-adb-query-failed",
                    blockers=["`adb devices -l` failed, so the runner could not confirm a ready Android guest."],
                    evidence_tier="env_ready",
                    steps=[
                        detect_step,
                        {
                            "step_id": "probe-adb-devices",
                            "status": "fail",
                            "details": adb_devices.stderr.splitlines()[-20:] or adb_devices.stdout.splitlines()[-20:],
                        },
                    ],
                    environment=environment,
                )

            devices = _parse_adb_devices(adb_devices.stdout)
            ready_device = _first_ready_adb_device(devices)
            if ready_device is None:
                return _blocked_runner_payload(
                    "android-emulator-goldfish",
                    blocker_kind="android-emulator-device-missing",
                    blockers=["No ready Android emulator instance is visible to `adb devices -l`."],
                    evidence_tier="env_ready",
                    steps=[
                        detect_step,
                        {
                            "step_id": "probe-adb-devices",
                            "status": "blocked",
                            "details": adb_devices.stdout.splitlines()[-20:] or ["no-ready-adb-device"],
                        },
                    ],
                    environment={
                        **environment,
                        "adb_devices": devices,
                    },
                )
            serial = ready_device["serial"]
            common_steps = [
                detect_step,
                {
                    "step_id": "probe-adb-devices",
                    "status": "pass",
                    "details": [f"selected_device={serial}"],
                },
            ]

        device_node = scenario_inputs.get("device_node", "/dev/goldfish_address_space")
        device_node_check = _adb_device_command(
            adb_path,
            serial,
            "shell",
            "ls",
            "-l",
            device_node,
            timeout_sec=min(timeout_sec, 20),
        )
        if device_node_check.returncode != 0:
            return _blocked_runner_payload(
                "android-emulator-goldfish",
                blocker_kind="android-emulator-device-node-missing",
                blockers=[f"Guest is reachable via adb, but `{device_node}` is not present."],
                evidence_tier="env_ready",
                steps=[
                    *common_steps,
                    {
                        "step_id": "probe-device-node",
                        "status": "fail",
                        "details": device_node_check.stderr.splitlines()[-20:] or device_node_check.stdout.splitlines()[-20:],
                    },
                ],
                environment={
                    **environment,
                    "adb_devices": devices,
                },
            )

        open_close_cmd = f"sh -c 'exec 3<>{device_node}; echo OPEN_OK; exec 3>&-; exec 3<&-; echo CLOSE_OK'"
        open_close = _adb_device_command(
            adb_path,
            serial,
            "shell",
            open_close_cmd,
            timeout_sec=min(timeout_sec, 20),
        )
        open_close_ok = (
            open_close.returncode == 0
            and "OPEN_OK" in open_close.stdout
            and "CLOSE_OK" in open_close.stdout
        )

        guest_tester = scenario.get("guest_tester")
        if guest_tester:
            tester_run = _run_android_guest_tester(
                repo_root=repo_root,
                module_id=module_id,
                scenario=scenario,
                scenario_inputs=scenario_inputs,
                adb_path=adb_path,
                serial=serial,
                qemu_log_output=qemu_log_output,
                artifact_root=artifact_root,
                timeout_sec=timeout_sec,
            )
            common_steps = [
                *common_steps,
                {
                    "step_id": "probe-device-node",
                    "status": "pass",
                    "details": device_node_check.stdout.splitlines()[-5:],
                },
            ]

            if tester_run["status"] == "blocked":
                return _blocked_runner_payload(
                    "android-emulator-goldfish",
                    blocker_kind=tester_run["blocker_kind"],
                    blockers=["Dedicated Android guest tester could not be built or deployed."],
                    evidence_tier="device_present",
                    log_path=tester_run["transcript_path"],
                    steps=[*common_steps, *tester_run["steps"]],
                    environment={
                        **environment,
                        "adb_devices": devices,
                        "device_node": device_node,
                        "guest_tester": {
                            "source": guest_tester["source"],
                            "remote_path": guest_tester["remote_path"],
                        },
                    },
                )

            summary = tester_run["summary"]
            pass_run = tester_run["status"] == "pass"
            result = {
                "runner": {
                    "id": "android-emulator-goldfish",
                    "status": "pass" if pass_run else "fail",
                    "selected_device": serial,
                    "log_path": tester_run["transcript_path"],
                    "commands": [
                        "adb devices -l",
                        f"adb -s {serial} shell ls -l {device_node}",
                        f"adb -s {serial} push {tester_run['build']['output_path']} {guest_tester['remote_path']}",
                        f"adb -s {serial} shell {guest_tester['remote_path']}",
                    ],
                },
                "pass": pass_run,
                "ready_for_oracle": True,
                "blocked": False,
                "evidence_tier": "baseline_abi_pass" if pass_run else "device_present",
                "smoke_summary": summary,
                "steps": [*common_steps, *tester_run["steps"]],
                "environment": {
                    **environment,
                    "adb_devices": devices,
                    "device_node": device_node,
                    "guest_tester": {
                        "source": guest_tester["source"],
                        "remote_path": guest_tester["remote_path"],
                    },
                },
                "guest_tester": tester_run,
            }
            if managed_state is not None:
                result["managed_guest"] = {
                    key: value
                    for key, value in managed_state.items()
                    if key not in {"process", "state_dir"}
                }
            return result

        result = {
            "runner": {
                "id": "android-emulator-goldfish",
                "status": "partial" if open_close_ok else "ready",
                "selected_device": serial,
                "commands": [
                    "adb devices -l",
                    f"adb -s {serial} shell ls -l {device_node}",
                    f"adb -s {serial} shell {open_close_cmd}",
                ],
            },
            "pass": False,
            "ready_for_oracle": open_close_ok,
            "blocked": False,
            "evidence_tier": "device_present",
            "smoke_summary": {
                "result": "PARTIAL" if open_close_ok else "READY",
                "device_present": 1,
                "open_close_smoke": "PASS" if open_close_ok else "FAIL",
            },
            "steps": [
                *common_steps,
                {
                    "step_id": "probe-device-node",
                    "status": "pass",
                    "details": device_node_check.stdout.splitlines()[-5:],
                },
                {
                    "step_id": "smoke-open-close",
                    "status": "pass" if open_close_ok else "fail",
                    "details": (open_close.stdout.splitlines()[-10:] or open_close.stderr.splitlines()[-10:] or ["open-close-smoke-empty-output"]),
                },
                {
                    "step_id": "full-abi-oracle",
                    "status": "pending",
                    "details": [
                        "allocate/deallocate/ping/mmap coverage is still pending a dedicated userspace tester.",
                    ],
                },
            ],
            "environment": {
                **environment,
                "adb_devices": devices,
                "device_node": device_node,
            },
        }
        if managed_state is not None:
            result["managed_guest"] = {
                key: value
                for key, value in managed_state.items()
                if key not in {"process", "state_dir"}
            }
        return result
    finally:
        if (
            managed_state is not None
            and (
                managed_state.get("serial") is not None
                or managed_state.get("process") is not None
                or managed_state.get("state_dir") is not None
            )
        ):
            _stop_managed_android_guest(
                adb_path=adb_path,
                serial=managed_state.get("serial"),
                process=managed_state.get("process"),
                timeout_sec=int(managed_guest.get("shutdown_timeout_sec", 30)),
                state_dir=managed_state.get("state_dir"),
            )


register_runner("qemu-scenario", _run_qemu_scenario)
register_runner("qemu-module-lifecycle", _run_qemu_module_lifecycle)
register_runner("android-goldfish-oracle", _run_android_goldfish_oracle)
register_runner("android-emulator-goldfish", _run_android_goldfish_oracle)
