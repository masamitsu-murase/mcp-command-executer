import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import server


class LoadConfigTests(unittest.TestCase):
    def test_resolve_path_returns_absolute_path_for_relative_input(self) -> None:
        base_dir = Path(tempfile.mkdtemp())

        resolved = server._resolve_path("logs/build.log", base_dir)

        self.assertEqual(resolved, (base_dir / "logs/build.log").resolve())

    def test_load_config_uses_defaults_when_path_is_none(self) -> None:
        config = server._load_config(None)

        self.assertEqual(config.build_command, server.DEFAULT_BUILD_COMMAND)
        self.assertEqual(
            config.build_log_path,
            server.DEFAULT_BUILD_LOG_PATH.resolve(),
        )
        self.assertEqual(
            config.progress_interval_sec,
            server.DEFAULT_PROGRESS_INTERVAL_SEC,
        )
        self.assertEqual(
            config.working_dir,
            server.DEFAULT_WORKING_DIR.resolve(),
        )

    def test_load_config_reads_json_and_resolves_relative_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            config_path = base_dir / "config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "BUILD_COMMAND": ["python", "-m", "build"],
                        "BUILD_LOG_PATH": "logs/build.log",
                        "PROGRESS_INTERVAL_SEC": 2,
                        "WORKING_DIR": "project",
                    }
                ),
                encoding="utf-8",
            )

            config = server._load_config(str(config_path))

            self.assertEqual(config.build_command, ["python", "-m", "build"])
            self.assertEqual(
                config.build_log_path,
                (base_dir / "logs/build.log").resolve(),
            )
            self.assertEqual(config.progress_interval_sec, 2)
            self.assertEqual(
                config.working_dir,
                (base_dir / "project").resolve(),
            )

    def test_load_config_rejects_invalid_build_command(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.json"
            config_path.write_text(
                json.dumps({"BUILD_COMMAND": "python -m build"}),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                ValueError,
                "BUILD_COMMAND must be a non-empty array of strings",
            ):
                server._load_config(str(config_path))


class MainTests(unittest.TestCase):
    def test_main_loads_config_and_runs_stdio(self) -> None:
        expected_config = server.ServerConfig(
            build_command=["python", "-m", "build"],
            build_log_path=Path("build_project.log").resolve(),
            progress_interval_sec=5,
            working_dir=Path.cwd().resolve(),
        )

        with (
            patch.object(sys, "argv", ["server.py", "config.json"]),
            patch.object(server, "_load_config", return_value=expected_config) as load_config,
            patch.object(server.mcp, "run") as run_mcp,
        ):
            server.main()

        load_config.assert_called_once_with("config.json")
        run_mcp.assert_called_once_with(transport="stdio")
        self.assertEqual(server.SERVER_CONFIG, expected_config)

    def test_main_rejects_too_many_arguments(self) -> None:
        with patch.object(sys, "argv", ["server.py", "a.json", "b.json"]):
            with self.assertRaisesRegex(
                SystemExit,
                r"Usage: python server.py \[config.json\]",
            ):
                server.main()


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


class BuildProjectTests(unittest.IsolatedAsyncioTestCase):
    async def test_build_project_success_reports_progress(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            config = server.ServerConfig(
                build_command=["python", "-m", "build"],
                build_log_path=temp_path / "build.log",
                progress_interval_sec=1,
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
                patch.object(server, "SERVER_CONFIG", config),
                patch.object(
                    server.asyncio,
                    "create_subprocess_exec",
                    AsyncMock(return_value=process),
                ) as create_subprocess_exec,
                patch.object(server.asyncio, "wait_for", side_effect=fake_wait_for),
            ):
                result = await server.build_project(ctx)

            self.assertEqual(
                result,
                {
                    "result": "success",
                    "log": config.build_log_path.as_posix(),
                },
            )
            create_subprocess_exec.assert_awaited_once_with(
                *config.build_command,
                stdout=unittest.mock.ANY,
                stderr=server.asyncio.subprocess.STDOUT,
                cwd=str(config.working_dir),
            )
            self.assertEqual(ctx.report_progress.await_count, 2)
            first_call = ctx.report_progress.await_args_list[0]
            second_call = ctx.report_progress.await_args_list[1]
            self.assertEqual(first_call.kwargs["progress"], 0.0)
            self.assertEqual(first_call.kwargs["message"], "build started")
            self.assertEqual(second_call.kwargs["progress"], 1.0)
            self.assertIn("build running...", second_call.kwargs["message"])

    async def test_build_project_returns_failure_for_nonzero_exit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            config = server.ServerConfig(
                build_command=["python", "-m", "build"],
                build_log_path=temp_path / "build.log",
                progress_interval_sec=1,
                working_dir=temp_path,
            )
            ctx = AsyncMock()
            process = FakeProcess(final_returncode=2)

            async def fake_wait_for(awaitable, timeout):
                return await awaitable

            with (
                patch.object(server, "SERVER_CONFIG", config),
                patch.object(
                    server.asyncio,
                    "create_subprocess_exec",
                    AsyncMock(return_value=process),
                ),
                patch.object(server.asyncio, "wait_for", side_effect=fake_wait_for),
            ):
                result = await server.build_project(ctx)

            self.assertEqual(
                result,
                {
                    "result": "failure",
                    "log": config.build_log_path.as_posix(),
                    "exit_code": 2,
                },
            )
            self.assertEqual(ctx.report_progress.await_count, 1)

    async def test_build_project_returns_failure_when_subprocess_start_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            config = server.ServerConfig(
                build_command=["python", "-m", "build"],
                build_log_path=temp_path / "build.log",
                progress_interval_sec=1,
                working_dir=temp_path,
            )
            ctx = AsyncMock()

            with (
                patch.object(server, "SERVER_CONFIG", config),
                patch.object(
                    server.asyncio,
                    "create_subprocess_exec",
                    AsyncMock(side_effect=RuntimeError("spawn failed")),
                ),
            ):
                result = await server.build_project(ctx)

            self.assertEqual(result["result"], "failure")
            self.assertEqual(result["log"], config.build_log_path.as_posix())
            self.assertIn("spawn failed", result["error"])
            self.assertTrue(config.build_log_path.exists())
            self.assertIn(
                "[server_error] spawn failed",
                config.build_log_path.read_text(encoding="utf-8"),
            )


if __name__ == "__main__":
    unittest.main()
