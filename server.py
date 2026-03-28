import asyncio
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, TextIO

import yaml
from pydantic import ConfigDict, Field, create_model
from mcp.server.fastmcp import Context, FastMCP
from mcp.server.fastmcp.utilities.func_metadata import ArgModelBase


_PLACEHOLDER_PATTERN = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$")
_SUPPORTED_INPUT_TYPES = {"string", "integer", "number", "boolean"}


@dataclass(slots=True, frozen=True)
class ToolConfig:
    name: str
    description: str
    command: list[str]
    log_path: Path
    working_dir: Path
    input_schema: dict[str, Any] | None = None


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
    if not isinstance(command_raw, list) or not command_raw:
        raise ValueError(f"{label} must be a non-empty array of strings")

    command: list[str] = []
    for index, item in enumerate(command_raw):
        if not isinstance(item, str) or not item:
            raise ValueError(f"{label} must be a non-empty array of strings")
        if "${" in item and _PLACEHOLDER_PATTERN.fullmatch(item) is None:
            raise ValueError(
                f"{label}[{index}] must be a plain string or "
                "a full placeholder like ${name}"
            )
        command.append(item)

    return command


def _get_command_placeholder(command_item: str) -> str | None:
    match = _PLACEHOLDER_PATTERN.fullmatch(command_item)
    if match is None:
        return None
    return match.group(1)


def _extract_command_placeholders(command: list[str]) -> set[str]:
    placeholders: set[str] = set()
    for command_item in command:
        placeholder = _get_command_placeholder(command_item)
        if placeholder is not None:
            placeholders.add(placeholder)
    return placeholders


def _normalize_input_schema_property(
    property_raw: Any,
    label: str,
) -> dict[str, Any]:
    if not isinstance(property_raw, dict):
        raise ValueError(f"{label} must be a mapping")

    if "type" not in property_raw:
        raise ValueError(f"{label}.type is required")

    type_name = property_raw["type"]
    if type_name not in _SUPPORTED_INPUT_TYPES:
        supported = ", ".join(sorted(_SUPPORTED_INPUT_TYPES))
        raise ValueError(
            f"{label}.type must be one of: {supported}"
        )

    normalized_property = {"type": type_name}

    if "description" in property_raw:
        description = property_raw["description"]
        if not isinstance(description, str) or not description:
            raise ValueError(
                f"{label}.description must be a non-empty string"
            )
        normalized_property["description"] = description

    return normalized_property


def _validate_input_schema(
    input_schema_raw: Any,
    label: str,
) -> dict[str, Any]:
    if not isinstance(input_schema_raw, dict):
        raise ValueError(f"{label} must be a mapping")

    schema_type = input_schema_raw.get("type")
    if schema_type != "object":
        raise ValueError(f"{label}.type must be object")

    properties_raw = input_schema_raw.get("properties", {})
    if not isinstance(properties_raw, dict):
        raise ValueError(f"{label}.properties must be a mapping")

    required_raw = input_schema_raw.get("required", [])
    if not isinstance(required_raw, list) or not all(
        isinstance(item, str) and item for item in required_raw
    ):
        raise ValueError(f"{label}.required must be an array of strings")

    additional_properties_raw = input_schema_raw.get(
        "additionalProperties",
        False,
    )
    if not isinstance(additional_properties_raw, bool):
        raise ValueError(f"{label}.additionalProperties must be a boolean")

    normalized_properties: dict[str, Any] = {}
    for property_name, property_raw in properties_raw.items():
        if not isinstance(property_name, str) or not property_name:
            raise ValueError(
                f"{label}.properties keys must be non-empty strings"
            )
        property_label = f"{label}.properties.{property_name}"
        normalized_properties[property_name] = (
            _normalize_input_schema_property(
                property_raw,
                property_label,
            )
        )

    for required_name in required_raw:
        if required_name not in normalized_properties:
            raise ValueError(
                f"{label}.required contains undefined property: "
                f"{required_name}"
            )

    normalized_schema: dict[str, Any] = {
        "type": "object",
        "properties": normalized_properties,
        "required": required_raw.copy(),
        "additionalProperties": additional_properties_raw,
    }

    return normalized_schema


def _validate_command_placeholders(
    command: list[str],
    input_schema: dict[str, Any] | None,
    label: str,
) -> None:
    placeholders = _extract_command_placeholders(command)
    if not placeholders:
        return

    if input_schema is None:
        raise ValueError(
            f"{label} contains placeholders but input_schema is not defined"
        )

    properties = input_schema["properties"]
    for placeholder in sorted(placeholders):
        if placeholder not in properties:
            raise ValueError(
                f"{label} references undefined input_schema "
                f"property: {placeholder}"
            )


def _get_input_value_type_name(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    return type(value).__name__


def _validate_tool_argument_value(
    value: Any,
    property_schema: dict[str, Any],
    label: str,
) -> None:
    type_name = property_schema["type"]

    is_valid = False
    if type_name == "string":
        is_valid = isinstance(value, str)
    elif type_name == "integer":
        is_valid = isinstance(value, int) and not isinstance(value, bool)
    elif type_name == "number":
        is_valid = isinstance(value, (int, float)) and not isinstance(
            value,
            bool,
        )
    elif type_name == "boolean":
        is_valid = isinstance(value, bool)

    if not is_valid:
        actual_type = _get_input_value_type_name(value)
        raise ValueError(
            f"{label} must be of type {type_name}, got {actual_type}"
        )


def _validate_tool_arguments(
    tool_config: ToolConfig,
    arguments: dict[str, Any] | None,
) -> dict[str, Any]:
    validated_arguments = {} if arguments is None else arguments.copy()

    if tool_config.input_schema is None:
        if validated_arguments:
            raise ValueError(f"{tool_config.name} does not accept arguments")
        return {}

    properties = tool_config.input_schema["properties"]
    required = tool_config.input_schema["required"]
    allow_additional_properties = tool_config.input_schema[
        "additionalProperties"
    ]

    missing_arguments = [
        name for name in required if name not in validated_arguments
    ]
    if missing_arguments:
        missing = ", ".join(missing_arguments)
        raise ValueError(f"missing required arguments: {missing}")

    unexpected_arguments = [
        name
        for name in validated_arguments
        if name not in properties
    ]
    if unexpected_arguments and not allow_additional_properties:
        unexpected = ", ".join(sorted(unexpected_arguments))
        raise ValueError(f"unexpected arguments: {unexpected}")

    for argument_name, argument_value in validated_arguments.items():
        property_schema = properties.get(argument_name)
        if property_schema is None:
            continue
        _validate_tool_argument_value(
            argument_value,
            property_schema,
            argument_name,
        )

    return validated_arguments


def _render_command(
    tool_config: ToolConfig,
    arguments: dict[str, Any],
) -> list[str]:
    rendered_command: list[str] = []
    for command_item in tool_config.command:
        placeholder = _get_command_placeholder(command_item)
        if placeholder is None:
            rendered_command.append(command_item)
            continue

        if placeholder not in arguments:
            raise ValueError(
                f"missing command argument: {placeholder}"
            )

        rendered_command.append(str(arguments[placeholder]))

    return rendered_command


def _get_pydantic_field_type(property_schema: dict[str, Any]) -> Any:
    description = property_schema.get("description")
    type_name = property_schema["type"]

    if type_name == "string":
        python_type: Any = str
    elif type_name == "integer":
        python_type = int
    elif type_name == "number":
        python_type = float
    else:
        python_type = bool

    if description is None:
        return python_type

    return Annotated[python_type, Field(description=description)]


def _build_input_model_base(
    tool_name: str,
    allow_additional_properties: bool,
) -> type[ArgModelBase]:
    extra_mode = "allow" if allow_additional_properties else "forbid"

    class _ConfiguredArgModelBase(ArgModelBase):
        def model_dump_one_level(self) -> dict[str, Any]:
            kwargs = super().model_dump_one_level()
            if allow_additional_properties:
                kwargs.update(getattr(self, "__pydantic_extra__", {}) or {})
            return kwargs

        model_config = ConfigDict(
            arbitrary_types_allowed=True,
            extra=extra_mode,
        )

    _ConfiguredArgModelBase.__name__ = f"{tool_name}ArgumentsBase"
    return _ConfiguredArgModelBase


def _build_input_arg_model(tool_config: ToolConfig) -> type[ArgModelBase]:
    if tool_config.input_schema is None:
        return create_model(
            f"{tool_config.name}Arguments",
            __base__=ArgModelBase,
        )

    properties = tool_config.input_schema["properties"]
    required = set(tool_config.input_schema["required"])
    allow_additional_properties = tool_config.input_schema[
        "additionalProperties"
    ]

    model_fields: dict[str, Any] = {}
    for property_name, property_schema in properties.items():
        field_type = _get_pydantic_field_type(property_schema)
        if property_name in required:
            model_fields[property_name] = field_type
        else:
            model_fields[property_name] = (field_type, None)

    return create_model(
        f"{tool_config.name}Arguments",
        __base__=_build_input_model_base(
            tool_config.name,
            allow_additional_properties,
        ),
        **model_fields,
    )


def _get_tool_parameters_schema(tool_config: ToolConfig) -> dict[str, Any]:
    if tool_config.input_schema is None:
        return {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
            "title": f"{tool_config.name}Arguments",
        }

    return {
        **tool_config.input_schema,
        "title": f"{tool_config.name}Arguments",
    }


def _configure_registered_tool(
    mcp_server: FastMCP,
    tool_config: ToolConfig,
) -> None:
    tool_manager = getattr(mcp_server, "_tool_manager", None)
    if tool_manager is None:
        return

    get_tool = getattr(tool_manager, "get_tool", None)
    if not callable(get_tool):
        return

    registered_tool = get_tool(tool_config.name)
    if registered_tool is None:
        return

    registered_tool.parameters = _get_tool_parameters_schema(tool_config)
    registered_tool.fn_metadata = registered_tool.fn_metadata.model_copy(
        update={"arg_model": _build_input_arg_model(tool_config)}
    )


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

        input_schema: dict[str, Any] | None = None
        if "input_schema" in tool_raw:
            input_schema = _validate_input_schema(
                tool_raw["input_schema"],
                f"tools[{index}].input_schema",
            )

        _validate_command_placeholders(
            command,
            input_schema,
            f"tools[{index}].command",
        )

        tools.append(
            ToolConfig(
                name=name,
                description=description,
                command=command,
                log_path=_resolve_path(log_path_raw, config_dir),
                working_dir=_resolve_path(working_dir_raw, config_dir),
                input_schema=input_schema,
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
    arguments: dict[str, Any] | None = None,
) -> dict[str, Any]:
    process: asyncio.subprocess.Process | None = None
    log_file: TextIO | None = None

    try:
        validated_arguments = _validate_tool_arguments(tool_config, arguments)
        rendered_command = _render_command(tool_config, validated_arguments)

        tool_config.log_path.parent.mkdir(parents=True, exist_ok=True)
        log_file = tool_config.log_path.open("w", encoding="utf-8")

        process = await asyncio.create_subprocess_exec(
            *rendered_command,
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
    if tool_config.input_schema is None:
        async def _tool(ctx: Context) -> dict[str, Any]:
            return await _run_configured_tool(
                ctx,
                server_config,
                tool_config,
            )
    else:
        async def _tool(ctx: Context, **arguments: Any) -> dict[str, Any]:
            return await _run_configured_tool(
                ctx,
                server_config,
                tool_config,
                arguments,
            )

    _tool.__name__ = tool_config.name
    _tool.__doc__ = tool_config.description
    return _tool


def _register_tools(
    mcp_server: FastMCP,
    server_config: ServerConfig,
) -> None:
    for tool_config in server_config.tools:
        handler = _create_tool_handler(server_config, tool_config)
        mcp_server.add_tool(
            handler,
            name=tool_config.name,
            description=tool_config.description,
        )
        _configure_registered_tool(mcp_server, tool_config)


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
