import asyncio
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO

from mcp.server.fastmcp import Context, FastMCP


@dataclass(slots=True)
class ServerConfig:
    build_command: list[str]
    build_log_path: Path
    progress_interval_sec: int | float
    working_dir: Path


DEFAULT_BUILD_COMMAND = ["python", "-m", "build"]
DEFAULT_BUILD_LOG_PATH = Path("build_project.log")
DEFAULT_PROGRESS_INTERVAL_SEC = 5
DEFAULT_WORKING_DIR = Path.cwd()

SERVER_CONFIG = ServerConfig(
    build_command=DEFAULT_BUILD_COMMAND.copy(),
    build_log_path=DEFAULT_BUILD_LOG_PATH,
    progress_interval_sec=DEFAULT_PROGRESS_INTERVAL_SEC,
    working_dir=DEFAULT_WORKING_DIR,
)

mcp = FastMCP("build-project-server")


def _resolve_path(raw_path: str, base_dir: Path) -> Path:
    path = Path(raw_path)
    if path.is_absolute():
        return path
    return (base_dir / path).resolve()


def _load_config(config_path_arg: str | None) -> ServerConfig:
    if config_path_arg is None:
        return ServerConfig(
            build_command=DEFAULT_BUILD_COMMAND.copy(),
            build_log_path=DEFAULT_BUILD_LOG_PATH.resolve(),
            progress_interval_sec=DEFAULT_PROGRESS_INTERVAL_SEC,
            working_dir=DEFAULT_WORKING_DIR.resolve(),
        )

    config_path = Path(config_path_arg).resolve()
    config_dir = config_path.parent

    with config_path.open("r", encoding="utf-8") as config_file:
        config_data = json.load(config_file)

    build_command_raw = config_data.get("BUILD_COMMAND", DEFAULT_BUILD_COMMAND)
    if (
        not isinstance(build_command_raw, list)
        or not build_command_raw
        or not all(
            isinstance(item, str) and item
            for item in build_command_raw
        )
    ):
        raise ValueError("BUILD_COMMAND must be a non-empty array of strings")

    build_log_path_raw = config_data.get(
        "BUILD_LOG_PATH",
        str(DEFAULT_BUILD_LOG_PATH),
    )
    if not isinstance(build_log_path_raw, str) or not build_log_path_raw:
        raise ValueError("BUILD_LOG_PATH must be a non-empty string")

    progress_interval_raw = config_data.get(
        "PROGRESS_INTERVAL_SEC",
        DEFAULT_PROGRESS_INTERVAL_SEC,
    )
    if not isinstance(progress_interval_raw, int | float):
        raise ValueError("PROGRESS_INTERVAL_SEC must be a number")
    if progress_interval_raw <= 0:
        raise ValueError("PROGRESS_INTERVAL_SEC must be greater than 0")

    working_dir_raw = config_data.get("WORKING_DIR", str(config_dir))
    if not isinstance(working_dir_raw, str) or not working_dir_raw:
        raise ValueError("WORKING_DIR must be a non-empty string")

    working_dir = _resolve_path(working_dir_raw, config_dir)
    build_log_path = _resolve_path(build_log_path_raw, config_dir)

    return ServerConfig(
        build_command=build_command_raw,
        build_log_path=build_log_path,
        progress_interval_sec=progress_interval_raw,
        working_dir=working_dir,
    )


def _get_log_path_for_response(log_path: Path) -> str:
    return log_path.as_posix()


@mcp.tool()
async def build_project(ctx: Context) -> dict[str, Any]:
    """Run a fixed build command and report progress every 5 seconds."""
    config = SERVER_CONFIG
    process: asyncio.subprocess.Process | None = None
    log_file: TextIO | None = None

    try:
        config.build_log_path.parent.mkdir(parents=True, exist_ok=True)
        log_file = config.build_log_path.open("w", encoding="utf-8")

        process = await asyncio.create_subprocess_exec(
            *config.build_command,
            stdout=log_file,
            stderr=asyncio.subprocess.STDOUT,
            cwd=str(config.working_dir),
        )

        progress = 0.0
        await ctx.report_progress(
            progress=progress,
            total=None,
            message="build started",
        )

        started_at = asyncio.get_running_loop().time()

        while process.returncode is None:
            try:
                await asyncio.wait_for(
                    process.wait(),
                    timeout=config.progress_interval_sec,
                )
            except asyncio.TimeoutError:
                progress += 1.0
                elapsed_sec = int(
                    asyncio.get_running_loop().time() - started_at,
                )
                await ctx.report_progress(
                    progress=progress,
                    total=None,
                    message=f"build running... ({elapsed_sec}s)",
                )

        if process.returncode is not None:
            exit_code = process.returncode
        else:
            exit_code = await process.wait()

        if exit_code == 0:
            return {
                "result": "success",
                "log": _get_log_path_for_response(config.build_log_path),
            }

        return {
            "result": "failure",
            "log": _get_log_path_for_response(config.build_log_path),
            "exit_code": exit_code,
        }

    except Exception as exc:
        if process is not None and process.returncode is None:
            process.kill()
            await process.wait()

        if config.build_log_path.exists():
            with config.build_log_path.open(
                "a",
                encoding="utf-8",
            ) as error_log:
                error_log.write(f"\n[server_error] {exc}\n")

        return {
            "result": "failure",
            "log": _get_log_path_for_response(config.build_log_path),
            "error": str(exc),
        }

    finally:
        if log_file is not None:
            log_file.flush()
            log_file.close()


def main() -> None:
    global SERVER_CONFIG

    if len(sys.argv) > 2:
        raise SystemExit("Usage: python server.py [config.json]")

    config_path_arg = sys.argv[1] if len(sys.argv) == 2 else None
    SERVER_CONFIG = _load_config(config_path_arg)

    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
