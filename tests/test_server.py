import asyncio
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from mcp.server.fastmcp import FastMCP

import server


def _make_tool_config(
    *,
    command: list[str] | None = None,
    input_schema: dict[str, object] | None = None,
    log_path: Path | None = None,
    working_dir: Path | None = None,
) -> server.ToolConfig:
    return server.ToolConfig(
        name="build_project",
        description="Run a configured command.",
        command=command or ["python", "-m", "build"],
        log_path=log_path or Path("build.log").resolve(),
        working_dir=working_dir or Path.cwd().resolve(),
        input_schema=input_schema,
    )


class LoadConfigTests(unittest.TestCase):
    def test_resolve_path_returns_absolute_path_for_relative_input(
        self,
    ) -> None:
        base_dir = Path(tempfile.mkdtemp())

        resolved = server._resolve_path("logs/build.log", base_dir)

        self.assertEqual(resolved, (base_dir / "logs/build.log").resolve())

    def test_load_config_rejects_missing_config_path(self) -> None:
        with self.assertRaisesRegex(ValueError, "config path is required"):
            server._load_config(None)

    def test_load_config_reads_yaml_and_resolves_relative_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            config_path = base_dir / "config.yaml"
            config_path.write_text(
                "progress_interval_sec: 2\n"
                "tools:\n"
                "  - name: build_project\n"
                "    description: Run a configured command.\n"
                "    command:\n"
                "      [\"python\", \"-m\", \"build\"]\n"
                "    log_path: logs/build.log\n"
                "    working_dir: project\n"
                "  - name: run_partial_test\n"
                "    description: Run selected tests.\n"
                "    input_schema:\n"
                "      type: object\n"
                "      additionalProperties: false\n"
                "      properties:\n"
                "        target:\n"
                "          type: string\n"
                "          description: pytest node id\n"
                "        keyword:\n"
                "          type: string\n"
                "          description: pytest -k expression\n"
                "      required:\n"
                "        - target\n"
                "        - keyword\n"
                "    command:\n"
                "      [\"python\", \"-m\", \"pytest\", \"-k\",\n"
                "       \"${keyword}\", \"${target}\"]\n"
                "    log_path: logs/test.log\n"
                "    working_dir: tests_project\n",
                encoding="utf-8",
            )

            config = server._load_config(str(config_path))

            self.assertEqual(config.progress_interval_sec, 2)
            self.assertEqual(
                [tool.name for tool in config.tools],
                ["build_project", "run_partial_test"],
            )
            self.assertEqual(
                config.tools[0].log_path,
                (base_dir / "logs/build.log").resolve(),
            )
            self.assertEqual(
                config.tools[0].working_dir,
                (base_dir / "project").resolve(),
            )
            self.assertIsNone(config.tools[0].input_schema)
            self.assertEqual(
                config.tools[1].command,
                [
                    "python",
                    "-m",
                    "pytest",
                    "-k",
                    "${keyword}",
                    "${target}",
                ],
            )
            self.assertEqual(
                config.tools[1].input_schema,
                {
                    "type": "object",
                    "properties": {
                        "target": {
                            "type": "string",
                            "description": "pytest node id",
                        },
                        "keyword": {
                            "type": "string",
                            "description": "pytest -k expression",
                        },
                    },
                    "required": ["target", "keyword"],
                    "additionalProperties": False,
                },
            )

    def test_load_config_allows_arbitrary_tool_name(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.yaml"
            config_path.write_text(
                textwrap.dedent(
                    """
                    progress_interval_sec: 2
                    tools:
                      - name: custom_tool
                        description: Custom command.
                        command: ["python", "-m", "custom"]
                        log_path: custom.log
                        working_dir: .
                    """
                ),
                encoding="utf-8",
            )

            config = server._load_config(str(config_path))

            self.assertEqual(config.tools[0].name, "custom_tool")
            self.assertEqual(
                config.tools[0].command,
                ["python", "-m", "custom"],
            )

    def test_load_config_rejects_missing_description(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.yaml"
            config_path.write_text(
                textwrap.dedent(
                    """
                    progress_interval_sec: 2
                    tools:
                      - name: build_project
                        command: ["python", "-m", "build"]
                        log_path: build.log
                        working_dir: .
                    """
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                ValueError,
                r"tools\[0\]\.description is required",
            ):
                server._load_config(str(config_path))

    def test_load_config_rejects_missing_tools_key(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.yaml"
            config_path.write_text(
                "progress_interval_sec: 2\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "tools is required"):
                server._load_config(str(config_path))

    def test_load_config_rejects_missing_command(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.yaml"
            config_path.write_text(
                textwrap.dedent(
                    """
                    progress_interval_sec: 2
                    tools:
                      - name: build_project
                        description: Run a fixed build command.
                        log_path: build.log
                        working_dir: .
                    """
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                ValueError,
                r"tools\[0\]\.command is required",
            ):
                server._load_config(str(config_path))

    def test_load_config_rejects_duplicate_tool_names(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.yaml"
            config_path.write_text(
                textwrap.dedent(
                    """
                    progress_interval_sec: 2
                    tools:
                      - name: build_project
                        description: Run a fixed build command.
                        command: ["python", "-m", "build"]
                        log_path: build.log
                        working_dir: .
                      - name: build_project
                        description: Run another build command.
                        command: ["python", "-m", "build"]
                        log_path: build2.log
                        working_dir: .
                    """
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                ValueError,
                "duplicate tool name is not allowed",
            ):
                server._load_config(str(config_path))

    def test_load_config_rejects_placeholder_without_input_schema(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.yaml"
            config_path.write_text(
                textwrap.dedent(
                    """
                    progress_interval_sec: 2
                    tools:
                      - name: run_partial_test
                        description: Run selected tests.
                        command: ["python", "-m", "pytest", "${target}"]
                        log_path: test.log
                        working_dir: .
                    """
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                ValueError,
                r"tools\[0\]\.command contains placeholders",
            ):
                server._load_config(str(config_path))

    def test_load_config_rejects_unknown_placeholder_reference(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.yaml"
            config_path.write_text(
                textwrap.dedent(
                    """
                    progress_interval_sec: 2
                    tools:
                      - name: run_partial_test
                        description: Run selected tests.
                        input_schema:
                          type: object
                          properties:
                            target:
                              type: string
                          required:
                            - target
                        command: ["python", "-m", "pytest", "${keyword}"]
                        log_path: test.log
                        working_dir: .
                    """
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                ValueError,
                r"tools\[0\]\.command references undefined "
                r"input_schema property: keyword",
            ):
                server._load_config(str(config_path))

    def test_load_config_rejects_partial_placeholder_command_item(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.yaml"
            config_path.write_text(
                textwrap.dedent(
                    """
                    progress_interval_sec: 2
                    tools:
                      - name: run_partial_test
                        description: Run selected tests.
                        input_schema:
                          type: object
                          properties:
                            target:
                              type: string
                        command: ["prefix-${target}"]
                        log_path: test.log
                        working_dir: .
                    """
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                ValueError,
                r"tools\[0\]\.command\[0\] must be a plain string",
            ):
                server._load_config(str(config_path))

    def test_load_config_rejects_invalid_input_schema_type(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.yaml"
            config_path.write_text(
                textwrap.dedent(
                    """
                    progress_interval_sec: 2
                    tools:
                      - name: run_partial_test
                        description: Run selected tests.
                        input_schema:
                          type: array
                        command: ["python"]
                        log_path: test.log
                        working_dir: .
                    """
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                ValueError,
                r"tools\[0\]\.input_schema\.type must be object",
            ):
                server._load_config(str(config_path))


class MainTests(unittest.TestCase):
    def test_main_loads_config_and_runs_stdio(self) -> None:
        expected_config = server.ServerConfig(
            progress_interval_sec=5,
            tools=[
                server.ToolConfig(
                    name="build_project",
                    description="Run a fixed build command.",
                    command=["python", "-m", "build"],
                    log_path=Path("build_project.log").resolve(),
                    working_dir=Path.cwd().resolve(),
                ),
            ],
        )

        with (
            patch.object(sys, "argv", ["server.py", "config.yaml"]),
            patch.object(
                server,
                "_load_config",
                return_value=expected_config,
            ) as load_config,
            patch.object(server, "_register_tools") as register_tools,
            patch.object(server, "FastMCP") as fast_mcp,
        ):
            mcp_server = fast_mcp.return_value
            server.main()

        load_config.assert_called_once_with("config.yaml")
        register_tools.assert_called_once_with(mcp_server, expected_config)
        mcp_server.run.assert_called_once_with(transport="stdio")

    def test_main_rejects_too_many_arguments(self) -> None:
        with patch.object(sys, "argv", ["server.py", "a.yaml", "b.yaml"]):
            with self.assertRaisesRegex(
                SystemExit,
                r"Usage: python server.py config.yaml",
            ):
                server.main()

    def test_main_requires_config_argument(self) -> None:
        with patch.object(sys, "argv", ["server.py"]):
            with self.assertRaisesRegex(
                SystemExit,
                r"Usage: python server.py config.yaml",
            ):
                server.main()


class RegisterToolsTests(unittest.TestCase):
    def test_register_tools_adds_configured_tools(self) -> None:
        mcp_server = Mock()
        config = server.ServerConfig(
            progress_interval_sec=5,
            tools=[
                server.ToolConfig(
                    name="build_project",
                    description="Run a fixed build command.",
                    command=["python", "-m", "build"],
                    log_path=Path("build.log").resolve(),
                    working_dir=Path.cwd().resolve(),
                ),
                server.ToolConfig(
                    name="run_all_project_tests",
                    description="Run all tests in this project.",
                    command=[
                        "python",
                        "-m",
                        "unittest",
                        "discover",
                        "-s",
                        "tests",
                        "-v",
                    ],
                    log_path=Path("test.log").resolve(),
                    working_dir=Path.cwd().resolve(),
                ),
            ],
        )

        server._register_tools(mcp_server, config)

        self.assertEqual(mcp_server.add_tool.call_count, 2)
        self.assertEqual(
            mcp_server.add_tool.call_args_list[0].kwargs["name"],
            "build_project",
        )
        self.assertEqual(
            mcp_server.add_tool.call_args_list[1].kwargs["name"],
            "run_all_project_tests",
        )


class RegisteredToolSchemaTests(unittest.IsolatedAsyncioTestCase):
    async def test_register_tools_applies_input_schema_to_fastmcp_tool(
        self,
    ) -> None:
        mcp_server = FastMCP("test-server")
        config = server.ServerConfig(
            progress_interval_sec=5,
            tools=[
                server.ToolConfig(
                    name="run_partial_test",
                    description="Run selected tests.",
                    command=[
                        "python",
                        "-m",
                        "pytest",
                        "-k",
                        "${keyword}",
                        "${target}",
                    ],
                    log_path=Path("test.log").resolve(),
                    working_dir=Path.cwd().resolve(),
                    input_schema={
                        "type": "object",
                        "properties": {
                            "target": {"type": "string"},
                            "keyword": {"type": "string"},
                        },
                        "required": ["target", "keyword"],
                        "additionalProperties": False,
                    },
                ),
            ],
        )

        with patch.object(
            server,
            "_run_configured_tool",
            AsyncMock(return_value={"result": "success", "log": "test.log"}),
        ) as run_configured_tool:
            server._register_tools(mcp_server, config)

            tool = mcp_server._tool_manager.get_tool("run_partial_test")
            self.assertIsNotNone(tool)
            assert tool is not None
            self.assertEqual(
                tool.parameters,
                {
                    "type": "object",
                    "properties": {
                        "target": {"type": "string"},
                        "keyword": {"type": "string"},
                    },
                    "required": ["target", "keyword"],
                    "additionalProperties": False,
                    "title": "run_partial_testArguments",
                },
            )

            result = await tool.run(
                {
                    "target": "tests/test_server.py",
                    "keyword": "load_config",
                }
            )

        self.assertEqual(result, {"result": "success", "log": "test.log"})
        run_configured_tool.assert_awaited_once()
        self.assertEqual(
            run_configured_tool.await_args.args[3],
            {
                "target": "tests/test_server.py",
                "keyword": "load_config",
            },
        )

    async def test_register_tools_rejects_unexpected_arguments_via_tool_model(
        self,
    ) -> None:
        mcp_server = FastMCP("test-server")
        config = server.ServerConfig(
            progress_interval_sec=5,
            tools=[
                server.ToolConfig(
                    name="run_partial_test",
                    description="Run selected tests.",
                    command=["python", "-m", "pytest", "${target}"],
                    log_path=Path("test.log").resolve(),
                    working_dir=Path.cwd().resolve(),
                    input_schema={
                        "type": "object",
                        "properties": {"target": {"type": "string"}},
                        "required": ["target"],
                        "additionalProperties": False,
                    },
                ),
            ],
        )

        server._register_tools(mcp_server, config)
        tool = mcp_server._tool_manager.get_tool("run_partial_test")
        assert tool is not None

        with self.assertRaisesRegex(Exception, "extra_forbidden"):
            await tool.run(
                {
                    "target": "tests/test_server.py",
                    "unexpected": "value",
                }
            )


class FakeProcess:
    def __init__(self, final_returncode: int) -> None:
        self.final_returncode = final_returncode
        self.returncode: int | None = None
        self.kill_called = False

    async def wait(self) -> int:
        if self.returncode is None:
            self.returncode = self.final_returncode
        return self.returncode

    def kill(self) -> None:
        self.kill_called = True
        self.returncode = -9


class ToolArgumentTests(unittest.TestCase):
    def test_validate_tool_arguments_rejects_missing_required_argument(
        self,
    ) -> None:
        tool_config = _make_tool_config(
            input_schema={
                "type": "object",
                "properties": {"target": {"type": "string"}},
                "required": ["target"],
                "additionalProperties": False,
            }
        )

        with self.assertRaisesRegex(
            ValueError,
            r"missing required arguments: target",
        ):
            server._validate_tool_arguments(tool_config, {})

    def test_validate_tool_arguments_rejects_unexpected_argument(
        self,
    ) -> None:
        tool_config = _make_tool_config(
            input_schema={
                "type": "object",
                "properties": {"target": {"type": "string"}},
                "required": ["target"],
                "additionalProperties": False,
            }
        )

        with self.assertRaisesRegex(
            ValueError,
            r"unexpected arguments: keyword",
        ):
            server._validate_tool_arguments(
                tool_config,
                {"target": "tests/test_server.py", "keyword": "load"},
            )

    def test_render_command_substitutes_placeholders(self) -> None:
        tool_config = _make_tool_config(
            command=[
                "python",
                "-m",
                "pytest",
                "-k",
                "${keyword}",
                "${target}",
            ],
            input_schema={
                "type": "object",
                "properties": {
                    "target": {"type": "string"},
                    "keyword": {"type": "string"},
                },
                "required": ["target", "keyword"],
                "additionalProperties": False,
            },
        )

        command = server._render_command(
            tool_config,
            {"target": "tests/test_server.py", "keyword": "load_config"},
        )

        self.assertEqual(
            command,
            [
                "python",
                "-m",
                "pytest",
                "-k",
                "load_config",
                "tests/test_server.py",
            ],
        )


class RunConfiguredToolTests(unittest.IsolatedAsyncioTestCase):
    async def test_run_configured_tool_success_reports_progress(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            server_config = server.ServerConfig(
                progress_interval_sec=1,
                tools=[],
            )
            tool_config = server.ToolConfig(
                name="build_project",
                description="Run a fixed build command.",
                command=["python", "-m", "build"],
                log_path=temp_path / "build.log",
                working_dir=temp_path,
            )
            ctx = AsyncMock()
            process = FakeProcess(final_returncode=0)
            wait_calls = 0

            async def fake_wait_for(awaitable, timeout):
                del timeout
                nonlocal wait_calls
                wait_calls += 1
                if wait_calls == 1:
                    awaitable.close()
                    raise asyncio.TimeoutError()
                return await awaitable

            with (
                patch.object(
                    server.asyncio,
                    "create_subprocess_exec",
                    AsyncMock(return_value=process),
                ) as create_subprocess_exec,
                patch.object(
                    server.asyncio,
                    "wait_for",
                    side_effect=fake_wait_for,
                ),
            ):
                result = await server._run_configured_tool(
                    ctx,
                    server_config,
                    tool_config,
                )

            self.assertEqual(
                result,
                {
                    "result": "success",
                    "log": tool_config.log_path.as_posix(),
                },
            )
            create_subprocess_exec.assert_awaited_once_with(
                *tool_config.command,
                stdin=server.asyncio.subprocess.DEVNULL,
                stdout=unittest.mock.ANY,
                stderr=server.asyncio.subprocess.STDOUT,
                cwd=str(tool_config.working_dir),
            )
            self.assertEqual(ctx.report_progress.await_count, 2)
            first_call = ctx.report_progress.await_args_list[0]
            second_call = ctx.report_progress.await_args_list[1]
            self.assertEqual(first_call.kwargs["progress"], 0.0)
            self.assertEqual(
                first_call.kwargs["message"],
                "build_project started",
            )
            self.assertEqual(second_call.kwargs["progress"], 1.0)
            self.assertIn(
                "build_project running...",
                second_call.kwargs["message"],
            )

    async def test_run_configured_tool_renders_placeholder_arguments(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            server_config = server.ServerConfig(
                progress_interval_sec=1,
                tools=[],
            )
            tool_config = server.ToolConfig(
                name="run_partial_test",
                description="Run selected tests.",
                command=[
                    "python",
                    "-m",
                    "pytest",
                    "-k",
                    "${keyword}",
                    "${target}",
                ],
                log_path=temp_path / "test.log",
                working_dir=temp_path,
                input_schema={
                    "type": "object",
                    "properties": {
                        "target": {"type": "string"},
                        "keyword": {"type": "string"},
                    },
                    "required": ["target", "keyword"],
                    "additionalProperties": False,
                },
            )
            ctx = AsyncMock()
            process = FakeProcess(final_returncode=0)

            async def fake_wait_for(awaitable, timeout):
                del timeout
                return await awaitable

            with (
                patch.object(
                    server.asyncio,
                    "create_subprocess_exec",
                    AsyncMock(return_value=process),
                ) as create_subprocess_exec,
                patch.object(
                    server.asyncio,
                    "wait_for",
                    side_effect=fake_wait_for,
                ),
            ):
                result = await server._run_configured_tool(
                    ctx,
                    server_config,
                    tool_config,
                    {
                        "target": "tests/test_server.py",
                        "keyword": "load_config",
                    },
                )

            self.assertEqual(result["result"], "success")
            create_subprocess_exec.assert_awaited_once_with(
                "python",
                "-m",
                "pytest",
                "-k",
                "load_config",
                "tests/test_server.py",
                stdin=server.asyncio.subprocess.DEVNULL,
                stdout=unittest.mock.ANY,
                stderr=server.asyncio.subprocess.STDOUT,
                cwd=str(tool_config.working_dir),
            )

    async def test_run_configured_tool_returns_failure_for_nonzero_exit(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            server_config = server.ServerConfig(
                progress_interval_sec=1,
                tools=[],
            )
            tool_config = server.ToolConfig(
                name="build_project",
                description="Run a fixed build command.",
                command=["python", "-m", "build"],
                log_path=temp_path / "build.log",
                working_dir=temp_path,
            )
            ctx = AsyncMock()
            process = FakeProcess(final_returncode=2)

            async def fake_wait_for(awaitable, timeout):
                del timeout
                return await awaitable

            with (
                patch.object(
                    server.asyncio,
                    "create_subprocess_exec",
                    AsyncMock(return_value=process),
                ),
                patch.object(
                    server.asyncio,
                    "wait_for",
                    side_effect=fake_wait_for,
                ),
            ):
                result = await server._run_configured_tool(
                    ctx,
                    server_config,
                    tool_config,
                )

            self.assertEqual(
                result,
                {
                    "result": "failure",
                    "log": tool_config.log_path.as_posix(),
                    "exit_code": 2,
                },
            )
            self.assertEqual(ctx.report_progress.await_count, 1)

    async def test_run_configured_tool_returns_failure_for_missing_argument(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            server_config = server.ServerConfig(
                progress_interval_sec=1,
                tools=[],
            )
            tool_config = server.ToolConfig(
                name="run_partial_test",
                description="Run selected tests.",
                command=["python", "-m", "pytest", "${target}"],
                log_path=temp_path / "test.log",
                working_dir=temp_path,
                input_schema={
                    "type": "object",
                    "properties": {"target": {"type": "string"}},
                    "required": ["target"],
                    "additionalProperties": False,
                },
            )
            ctx = AsyncMock()

            with patch.object(
                server.asyncio,
                "create_subprocess_exec",
                AsyncMock(),
            ) as create_subprocess_exec:
                result = await server._run_configured_tool(
                    ctx,
                    server_config,
                    tool_config,
                    {},
                )

            self.assertEqual(result["result"], "failure")
            self.assertIn(
                "missing required arguments: target",
                result["error"],
            )
            create_subprocess_exec.assert_not_awaited()

    async def test_run_configured_tool_returns_failure_for_invalid_type(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            server_config = server.ServerConfig(
                progress_interval_sec=1,
                tools=[],
            )
            tool_config = server.ToolConfig(
                name="run_partial_test",
                description="Run selected tests.",
                command=["python", "-m", "pytest", "${max_fail}"],
                log_path=temp_path / "test.log",
                working_dir=temp_path,
                input_schema={
                    "type": "object",
                    "properties": {"max_fail": {"type": "integer"}},
                    "required": ["max_fail"],
                    "additionalProperties": False,
                },
            )
            ctx = AsyncMock()

            with patch.object(
                server.asyncio,
                "create_subprocess_exec",
                AsyncMock(),
            ) as create_subprocess_exec:
                result = await server._run_configured_tool(
                    ctx,
                    server_config,
                    tool_config,
                    {"max_fail": "3"},
                )

            self.assertEqual(result["result"], "failure")
            self.assertIn(
                "max_fail must be of type integer, got string",
                result["error"],
            )
            create_subprocess_exec.assert_not_awaited()

    async def test_run_configured_tool_start_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            server_config = server.ServerConfig(
                progress_interval_sec=1,
                tools=[],
            )
            tool_config = server.ToolConfig(
                name="build_project",
                description="Run a fixed build command.",
                command=["python", "-m", "build"],
                log_path=temp_path / "build.log",
                working_dir=temp_path,
            )
            ctx = AsyncMock()

            with patch.object(
                server.asyncio,
                "create_subprocess_exec",
                AsyncMock(side_effect=RuntimeError("spawn failed")),
            ):
                result = await server._run_configured_tool(
                    ctx,
                    server_config,
                    tool_config,
                )

            self.assertEqual(result["result"], "failure")
            self.assertEqual(result["log"], tool_config.log_path.as_posix())
            self.assertIn("spawn failed", result["error"])
            self.assertTrue(tool_config.log_path.exists())
            self.assertIn(
                "[server_error] spawn failed",
                tool_config.log_path.read_text(encoding="utf-8"),
            )


if __name__ == "__main__":
    unittest.main()
