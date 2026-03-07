import asyncio
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO

import yaml
from mcp.server.fastmcp import Context, FastMCP


@dataclass(slots=True, frozen=True)
class ToolConfig:
    name: str
    description: str
    command: list[str]
    log_path: Path
    working_dir: Path


@dataclass(slots=True)
class ServerConfig:
    progress_interval_sec: int | float
    tools: list[ToolConfig]


def _resolve_path(raw_path: str, base_dir: Path) -> Path:
    path = Path(raw_path)
    if path.is_absolute():
        return path
    return (base_dir / path).resolve()


def _validate_command(command_raw: Any, label: str) -> list[str]:
    if (
        not isinstance(command_raw, list)
        or not command_raw
        or not all(isinstance(item, str) and item for item in command_raw)
    ):
        raise ValueError(f"{label} must be a non-empty array of strings")
    return command_raw.copy()


def _load_config(config_path_arg: str | None) -> ServerConfig:
    if config_path_arg is None:
        raise ValueError("config path is required")

    config_path = Path(config_path_arg).resolve()
    config_dir = config_path.parent

    with config_path.open("r", encoding="utf-8") as config_file:
        config_data = yaml.safe_load(config_file) or {}

    if not isinstance(config_data, dict):
        raise ValueError("config file must contain a YAML mapping")

    if "progress_interval_sec" not in config_data:
        raise ValueError("progress_interval_sec is required")

    progress_interval_raw = config_data["progress_interval_sec"]
    if not isinstance(progress_interval_raw, int | float):
        raise ValueError("progress_interval_sec must be a number")
    if progress_interval_raw <= 0:
        raise ValueError("progress_interval_sec must be greater than 0")

    if "tools" not in config_data:
        raise ValueError("tools is required")

    tools_raw = config_data["tools"]
    if not isinstance(tools_raw, list) or not tools_raw:
        raise ValueError("tools must be a non-empty array")

    tools: list[ToolConfig] = []
    seen_names: set[str] = set()

    for index, tool_raw in enumerate(tools_raw):
        if not isinstance(tool_raw, dict):
            raise ValueError(f"tools[{index}] must be a mapping")

        if "name" not in tool_raw:
            raise ValueError(f"tools[{index}].name is required")
        name = tool_raw["name"]
        if not isinstance(name, str) or not name:
            raise ValueError(f"tools[{index}].name must be a non-empty string")
        if name in seen_names:
            raise ValueError(f"duplicate tool name is not allowed: {name}")

        if "description" not in tool_raw:
            raise ValueError(f"tools[{index}].description is required")
        description = tool_raw["description"]
        if not isinstance(description, str) or not description:
            raise ValueError(
                f"tools[{index}].description must be a non-empty string"
            )

        if "log_path" not in tool_raw:
            raise ValueError(f"tools[{index}].log_path is required")
        log_path_raw = tool_raw["log_path"]
        if not isinstance(log_path_raw, str) or not log_path_raw:
            raise ValueError(
                f"tools[{index}].log_path must be a non-empty string"
            )

        if "working_dir" not in tool_raw:
            raise ValueError(f"tools[{index}].working_dir is required")
        working_dir_raw = tool_raw["working_dir"]
        if not isinstance(working_dir_raw, str) or not working_dir_raw:
            raise ValueError(
                f"tools[{index}].working_dir must be a non-empty string"
            )

        if "command" not in tool_raw:
            raise ValueError(f"tools[{index}].command is required")
        command = _validate_command(
            tool_raw["command"],
            f"tools[{index}].command",
        )

        tools.append(
            ToolConfig(
                name=name,
                description=description,
                command=command,
                log_path=_resolve_path(log_path_raw, config_dir),
                working_dir=_resolve_path(working_dir_raw, config_dir),
            ),
        )
        seen_names.add(name)

    return ServerConfig(
        progress_interval_sec=progress_interval_raw,
        tools=tools,
    )


def _get_log_path_for_response(log_path: Path) -> str:
    return log_path.as_posix()


async def _run_configured_tool(
    ctx: Context,
    server_config: ServerConfig,
    tool_config: ToolConfig,
) -> dict[str, Any]:
    process: asyncio.subprocess.Process | None = None
    log_file: TextIO | None = None

    try:
        tool_config.log_path.parent.mkdir(parents=True, exist_ok=True)
        log_file = tool_config.log_path.open("w", encoding="utf-8")

        process = await asyncio.create_subprocess_exec(
            *tool_config.command,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=log_file,
            stderr=asyncio.subprocess.STDOUT,
            cwd=str(tool_config.working_dir),
        )

        progress = 0.0
        await ctx.report_progress(
            progress=progress,
            total=None,
            message=f"{tool_config.name} started",
        )

        started_at = asyncio.get_running_loop().time()

        while process.returncode is None:
            try:
                await asyncio.wait_for(
                    process.wait(),
                    timeout=server_config.progress_interval_sec,
                )
            except asyncio.TimeoutError:
                progress += 1.0
                elapsed_sec = int(
                    asyncio.get_running_loop().time() - started_at
                )
                await ctx.report_progress(
                    progress=progress,
                    total=None,
                    message=f"{tool_config.name} running... ({elapsed_sec}s)",
                )

        if process.returncode is not None:
            exit_code = process.returncode
        else:
            exit_code = await process.wait()

        if exit_code == 0:
            return {
                "result": "success",
                "log": _get_log_path_for_response(tool_config.log_path),
            }

        return {
            "result": "failure",
            "log": _get_log_path_for_response(tool_config.log_path),
            "exit_code": exit_code,
        }

    except Exception as exc:
        if process is not None and process.returncode is None:
            process.kill()
            await process.wait()

        if tool_config.log_path.exists():
            with tool_config.log_path.open("a", encoding="utf-8") as error_log:
                error_log.write(f"\n[server_error] {exc}\n")

        return {
            "result": "failure",
            "log": _get_log_path_for_response(tool_config.log_path),
            "error": str(exc),
        }

    finally:
        if log_file is not None:
            log_file.flush()
            log_file.close()


def _create_tool_handler(server_config: ServerConfig, tool_config: ToolConfig):
    async def _tool(ctx: Context) -> dict[str, Any]:
        return await _run_configured_tool(ctx, server_config, tool_config)

    _tool.__name__ = tool_config.name
    _tool.__doc__ = tool_config.description
    return _tool


def _register_tools(
    mcp_server: FastMCP,
    server_config: ServerConfig,
) -> None:
    for tool_config in server_config.tools:
        mcp_server.add_tool(
            _create_tool_handler(server_config, tool_config),
            name=tool_config.name,
            description=tool_config.description,
        )


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python server.py config.yaml")

    mcp_server = FastMCP("build-project-server")
    config_path_arg = sys.argv[1]
    server_config = _load_config(config_path_arg)
    _register_tools(mcp_server, server_config)

    mcp_server.run(transport="stdio")


if __name__ == "__main__":
    main()
