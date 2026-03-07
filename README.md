# build_python

Python で実装した stdio 専用の MCP Server です。

このサーバーは、YAML 設定ファイルに定義された複数のツールを提供します。各ツールは引数を取らず、サーバー起動時に読み込んだ設定に従って固定コマンドを実行します。

## 機能

- stdio 専用 MCP Server
- YAML 設定から複数ツールを動的登録
- ツール実行中に一定間隔で progress 通知を送信
- 完了後に結果とログファイルパスを返却
- `config.yaml` からツール設定を読込

## 必要ライブラリ

- `mcp`
- `PyYAML`

インストール例:

```bash
pip install -r requirements.txt
```

## 起動方法

設定ファイルを指定して起動します。

```bash
python server.py config.yaml
```

## config.yaml

指定できる項目:

- `progress_interval_sec`: progress 通知間隔（秒）
- `tools`: ツール定義の配列
  - `name`: ツール名
  - `description`: ツール説明
  - `command`: 実行コマンドの配列
  - `log_path`: ログファイルの出力先
  - `working_dir`: 実行時のカレントディレクトリ

例:

```yaml
progress_interval_sec: 5
tools:
  - name: "build_project"
    description: "Run a fixed build command."
    command: ["python", "-m", "build"]
    log_path: "build_project.log"
    working_dir: "."
  - name: "run_all_project_tests"
    description: "Run all tests in this project."
    command: ["python", "-m", "unittest", "discover", "-s", "tests", "-v"]
    log_path: "test_project.log"
    working_dir: "."
```

### パス解決ルール

- `log_path` と `working_dir` が相対パスの場合、`config.yaml` が置かれているディレクトリ基準で解決されます。
- 絶対パスの場合はそのまま使われます。

## ツール仕様

入力:
- なし

動作:
1. 設定済みの `command` を実行
2. 標準出力・標準エラーを `log_path` に保存
3. 実行中は progress 通知を送信
4. 完了後に最終結果を返却

補足:
- ツール名は任意の文字列を使えます。
- すべてのツールで `command` の指定が必須です。

成功時のレスポンス例:

```json
{"result": "success", "log": "build_project.log"}
```

失敗時のレスポンス例:

```json
{"result": "failure", "log": "build_project.log", "exit_code": 1}
```

例外時のレスポンス例:

```json
{"result": "failure", "log": "build_project.log", "error": "..."}
```

## テスト

`unittest` でテストを作成しています。

実行例:

```bash
python -m unittest discover -s tests -v
```

## 主なファイル

- [server.py](server.py)
- [plan.md](plan.md)
- [tests/test_server.py](tests/test_server.py)
