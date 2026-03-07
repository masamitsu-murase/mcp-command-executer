import asyncio
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import server


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
                "    description: Run a fixed build command.\n"
                "    command:\n"
                "      - python\n"
                "      - -m\n"
                "      - build\n"
                "    log_path: logs/build.log\n"
                "    working_dir: project\n"
                "  - name: run_all_project_tests\n"
                "    description: Run all tests in this project.\n"
                "    command:\n"
                "      - python\n"
                "      - -m\n"
                "      - unittest\n"
                "      - discover\n"
                "      - -s\n"
                "      - tests\n"
                "      - -v\n"
                "    log_path: logs/test.log\n"
                "    working_dir: tests_project\n",
                encoding="utf-8",
            )

            config = server._load_config(str(config_path))

            self.assertEqual(config.progress_interval_sec, 2)
            self.assertEqual(
                [tool.name for tool in config.tools],
                ["build_project", "run_all_project_tests"],
            )
            self.assertEqual(
                config.tools[0].log_path,
                (base_dir / "logs/build.log").resolve(),
            )
            self.assertEqual(
                config.tools[0].working_dir,
                (base_dir / "project").resolve(),
            )
            self.assertEqual(
                config.tools[1].command,
                [
                    "python",
                    "-m",
                    "unittest",
                    "discover",
                    "-s",
                    "tests",
                    "-v",
                ],
            )
            self.assertEqual(
                config.tools[1].log_path,
                (base_dir / "logs/test.log").resolve(),
            )

    def test_load_config_allows_arbitrary_tool_name(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.yaml"
            config_path.write_text(
                "progress_interval_sec: 2\n"
                "tools:\n"
                "  - name: custom_tool\n"
                "    description: Custom command.\n"
                "    command:\n"
                "      - python\n"
                "      - -m\n"
                "      - custom\n"
                "    log_path: custom.log\n"
                "    working_dir: .\n",
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
                "progress_interval_sec: 2\n"
                "tools:\n"
                "  - name: build_project\n"
                "    command:\n"
                "      - python\n"
                "      - -m\n"
                "      - build\n"
                "    log_path: build.log\n"
                "    working_dir: .\n",
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
                "progress_interval_sec: 2\n"
                "tools:\n"
                "  - name: build_project\n"
                "    description: Run a fixed build command.\n"
                "    command:\n"
                "      - python\n"
                "      - -m\n"
                "      - build\n"
                "    log_path: build.log\n"
                "    working_dir: .\n"
                "  - name: build_project\n"
                "    description: Run a fixed build command.\n"
                "    command:\n"
                "      - python\n"
                "      - -m\n"
                "      - build\n"
                "    log_path: build2.log\n"
                "    working_dir: .\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                ValueError,
                "duplicate tool name is not allowed",
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
        register_tools.assert_called_once_with(
            mcp_server,
            expected_config,
        )
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
        mcp_server = unittest.mock.Mock()
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

    async def test_run_configured_tool_start_failure(
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

            with (
                patch.object(
                    server.asyncio,
                    "create_subprocess_exec",
                    AsyncMock(side_effect=RuntimeError("spawn failed")),
                ),
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
