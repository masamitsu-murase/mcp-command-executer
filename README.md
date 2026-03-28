# mcp-command-executer

Python で実装した stdio 専用の MCP Server です。

このサーバーは、YAML 設定ファイルに定義された複数のツールを提供します。各ツールは固定コマンドだけでなく、`input_schema` と `command` 内のプレースホルダを使って引数を受け取り、実行時にコマンドへ埋め込めます。

## 機能

- stdio 専用 MCP Server
- YAML 設定から複数ツールを動的登録
- `input_schema` によるツール引数定義
- `command` 配列内の `${name}` プレースホルダ展開
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
  - `input_schema`: ツール入力の schema（任意）
  - `command`: 実行コマンドの配列
  - `log_path`: ログファイルの出力先
  - `working_dir`: 実行時のカレントディレクトリ

例:

```yaml
progress_interval_sec: 5
tools:
  - name: "run_partial_test"
    description: "Run selected tests."
    input_schema:
      type: object
      additionalProperties: false
      properties:
        target:
          type: string
          description: "pytest node id or file path"
        keyword:
          type: string
          description: "pytest -k expression"
      required:
        - target
        - keyword
    command: ["python", "-m", "pytest", "-k", "${keyword}", "${target}"]
    log_path: "logs/run_partial_test.log"
    working_dir: "."
  - name: "run_all_project_tests"
    description: "Run all tests in this project."
    command: ["python", "-m", "unittest", "discover", "-s", "tests", "-v"]
    log_path: "logs/test_project.log"
    working_dir: "."
```

### `input_schema` のルール

- `type: object` のみサポートします。
- `properties` では `string`、`integer`、`number`、`boolean` を使えます。
- `required` に必須入力を列挙します。
- `additionalProperties: false` を指定すると、未定義の入力を拒否します。

### `command` のルール

- `command` は最終的な argv 配列です。
- 各要素は固定文字列、または `${name}` 形式のプレースホルダです。
- プレースホルダはツール入力の値で置換されます。
- プレースホルダは 1 要素全体に一致する必要があります。
  - OK: `${target}`
  - NG: `prefix-${target}`

### パス解決ルール

- `log_path` と `working_dir` が相対パスの場合、`config.yaml` が置かれているディレクトリ基準で解決されます。
- 絶対パスの場合はそのまま使われます。

## ツール仕様

入力:
- `input_schema` 未指定のツールは入力なし
- `input_schema` 指定時は schema に従う object 入力

入力例:

```json
{"target": "tests/test_server.py", "keyword": "load_config"}
```

動作:
1. 入力値を検証する
2. `command` 内の `${name}` を入力値で置換する
3. 展開済みの `command` を実行する
4. 標準出力・標準エラーを `log_path` に保存
5. 実行中は progress 通知を送信
6. 完了後に最終結果を返却

補足:
- ツール名は任意の文字列を使えます。
- すべてのツールで `command` の指定が必須です。
- `command` で参照する `${name}` は `input_schema.properties` に定義されている必要があります。

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
