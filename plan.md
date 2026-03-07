# MCP Server 実装プラン（Python / stdio / YAML 複数ツール対応）

## 1. 目的
- Python で **stdio 専用**の MCP Server を実装する。
- サーバー起動時に YAML 設定ファイルを必須で読み込み、複数のツールを登録できるようにする。
- 各ツールは **引数なし**で実行できるようにし、設定済みの固定コマンドを実行する。
- 実行中は一定間隔で `notifications/progress` を送信する。
- 完了後は結果とログファイルパスを返す。

成功時レスポンス例:

```json
{"result": "success", "log": "build_log.txt"}
```

## 2. 前提・制約
- 通信方式は stdio のみとする。
- ツール実行時にコマンドやログパスは引数で受け取らない。
- 実行内容はサーバー起動時に読み込んだ YAML 設定で決定する。
- `progress` は MCP の `notifications/progress` として送信する。
- デフォルト設定は持たない。
- ツール名は任意の文字列を許容する。

## 3. 利用ライブラリ

### 外部ライブラリ
- `mcp`
  - MCP Server 実装
  - `FastMCP` によるツール定義
  - stdio トランスポート実行
- `PyYAML`
  - YAML 設定ファイルの読み込み

### 標準ライブラリ
- `asyncio`
  - 非同期 subprocess 実行
  - 定期 progress 送信制御
- `sys`
  - 起動引数処理
- `dataclasses`
  - 設定値の保持
- `pathlib`
  - パス解決
- `typing`
  - 型ヒント

## 4. 設定ファイル設計

### 4.1 読み込み方式
- 起動形式は以下とする。
  - `python server.py config.yaml`
- 設定ファイル引数は必須とする。
- 指定された YAML ファイルを読み込む。

### 4.2 `config.yaml` で指定する項目
- `progress_interval_sec`
  - progress 通知の間隔（秒）
- `tools`
  - 実行可能なツール定義の配列
  - 各要素は以下を持つ
    - `name`
    - `description`
    - `command`
    - `log_path`
    - `working_dir`

例:

```yaml
progress_interval_sec: 5
tools:
  - name: "build_project"
    description: "Run a fixed build command."
    command: ["python", "-m", "build"]
    log_path: "build_log.txt"
    working_dir: "C:/work"
  - name: "run_all_project_tests"
    description: "Run all tests in this project."
    command: ["python", "-m", "unittest", "discover", "-s", "tests", "-v"]
    log_path: "test_log.txt"
    working_dir: "C:/work"
```

### 4.3 コマンド決定ルール
- すべてのツールは YAML 内の `command` で実行コマンドを明示する。
- ツール名によるコマンド自動決定は行わない。
- 任意のツール名を許容する。

### 4.4 パス解決ルール
- `log_path` と `working_dir` が相対パスの場合、`config.yaml` の配置ディレクトリ基準で解決する。
- 絶対パスの場合はそのまま使用する。

### 4.5 設定値の検証
- `progress_interval_sec` は 0 より大きい数値であることを検証する。
- `progress_interval_sec` は必須とする。
- `tools` は空でない配列であることを検証する。
- `name`、`description`、`log_path`、`working_dir` は必須かつ空でない文字列であることを検証する。
- `command` は必須かつ空でない文字列配列であることを検証する。
- ツール名の重複を禁止する。

## 5. サーバー構成

### 5.1 実装ファイル
- `server.py`
  - MCP Server 本体
  - YAML 設定読み込み
  - ツール登録
  - コマンド実行

### 5.2 ツール登録方式
- `FastMCP.add_tool()` を用いて設定に応じて動的にツールを登録する。


## 6. ツール実行設計

### 6.1 入力
- 各ツールとも引数なし

### 6.2 処理フロー
1. ツール設定からコマンド・ログパス・作業ディレクトリを取得する。
2. ログファイルの親ディレクトリを作成する。
3. ログファイルを上書きモードで開く。
4. YAML に定義されたコマンドを `subprocess exec` 方式で起動する。
5. `working_dir` を `cwd` に設定する。
6. 開始時に progress を送信する。
7. プロセス完了まで `progress_interval_sec` ごとに progress を送信する。
8. 終了コードに応じて成功/失敗レスポンスを返す。

### 6.3 progress の扱い
- `ctx.report_progress(...)` を使用する。
- `progress` 値は単調増加とする。
- `total` は不明のため `None` とする。
- `message` には `<tool_name> started` や `<tool_name> running... (10s)` のような文言を入れる。

### 6.4 最終レスポンス
- 成功時:

```json
{"result": "success", "log": "..."}
```

- 失敗時:

```json
{"result": "failure", "log": "...", "exit_code": 1}
```

- 例外時:

```json
{"result": "failure", "log": "...", "error": "..."}
```

## 7. ログ出力設計
- 標準出力と標準エラーは同じログファイルへ出力する。
- ログファイルは毎回上書きで開始する。
- サーバー内部例外が発生した場合は、可能ならログ末尾に追記する。
- 最終レスポンスには常にログファイルパスを含める。

## 8. エラーハンドリング
- YAML の形式不正や必須項目不足は起動時に例外として扱う。
- subprocess 起動失敗時は `failure` を返す。
- 実行中例外が発生した場合、子プロセスが生きていれば停止を試みる。
- 例外情報はレスポンスの `error` に含める。

## 9. テスト方針
- YAML 読み込みと相対パス解決を検証する。
- 必須キー不足時のエラーを検証する。
- 重複ツール名を検証する。
- 任意ツール名と `command` 必須を検証する。
- 動的ツール登録を検証する。
- コマンド成功・失敗・起動例外時のレスポンスを検証する。

## 10. 成果物
- `server.py`
- `plan.md`
- `tests/test_server.py`

## 11. 実装ステップ
1. 設定ファイル必須の起動方式にする。
2. `tools` 配列から複数ツールを読み込む。
3. `command` を含む必須キー不足を起動時エラーとして扱う。
4. 動的ツール登録処理を追加する。
5. 新しい設定仕様と動作をテストで固定する。
