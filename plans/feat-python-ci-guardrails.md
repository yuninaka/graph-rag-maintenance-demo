# plan: Python CI ガードレール(uv移行 + Ruff strict/mypy strict/vulture)の基盤整備

Issue: https://github.com/yuninaka/graph-rag-maintenance-demo/issues/4

## 目的

azure-rag-erp-navigatorで整備した品質ゲート(参考:
https://zenn.dev/singularity/articles/stopped-reviewing-my-code)を、このリポジトリにも導入する。
詳細な設計判断は `~/.claude/docs/python-quality-gates.md` を参照。

## スコープ

当初は基盤整備のみ(既存コードへの指摘は報告のみ)とする方針だったが、Ruff/mypyを
ゲート化するとこのPR自体のCIが既存コードの指摘で赤くなってしまうため、方針を確認した上で
**既存コードの指摘もこのPR内ですべて解消する**ことにした(詳細は「実行結果」参照)。

## 変更内容

1. **uvへの移行**: `requirements.txt`の依存を`pyproject.toml`に移す。`venv/`はuvの`.venv/`に
   置き換わるため`.gitignore`を更新。既存の`.env`読み込み等の実行方法(`python src/xxx.py`)は
   `uv run python src/xxx.py`に変わる
2. **pyproject.tomlの品質ゲート設定**: Ruffの`select`に`C90`/`PLR`/`N`/`B`/`ANN`を追加、
   mypy `strict = true`、pytestの`pythonpath`設定
3. **`scripts/ci_check.sh`**: pytest → ruff → mypy → vulture(report-only)を1スクリプトに集約
4. **`.github/workflows/ci.yml`**: pull_requestトリガーで`ci_check.sh`を呼ぶだけの薄いワークフロー
5. **最小限のテスト**: 現状`tests/`が存在せず、pytestが0件収集でexit code 5になり
   ゲートとして機能しないため、既存の唯一の純粋関数`src/evaluate.py::keyword_coverage`に
   最小限の単体テスト(正常系・空リスト・部分一致)を追加する
6. **CLAUDE.md新設**: azure-rag-erp-navigatorのCLAUDE.mdをベースに、このリポジトリ向けに
   簡略化して追加(PR前品質チェック・テストカバレッジ観点・noqa運用ルール)
7. **README.md更新**: セットアップ手順を`pip install -r requirements.txt`から`uv sync`に、
   実行コマンドを`python ...`から`uv run python ...`に更新。品質チェック節を追加

## 実行結果

導入直後の検出件数: Ruff 43件(E501長すぎる行29件、ANN201/ANN001/ANN202型注釈欠落14件)、
mypy strict 41件(型注釈欠落・`Any`推論・`dict`/`list`の型引数欠落・LangChain APIとの
型不整合)、vulture 0件。

すべて型を緩めずに解消した。主な対応:

- **`src/__init__.py`の追加**: `src`配下のファイルが内部で`from src.xxx import ...`という
  絶対importを使っているため、`src`を明示的なパッケージにしないと
  `mypy src`が"Source file found twice under different module names"で即座に失敗する
  (azure-rag-erp-navigatorの`src/__init__.py`と同じ理由)
- **`[tool.mypy] plugins = ["pydantic.mypy"]`の追加**: `ChatAnthropic(model=...)`という
  実際には正しいpydanticモデルのキーワード引数呼び出しを、プラグインなしのmypyが
  `call-arg`エラーとして誤検知していた。プラグイン追加で解消(2箇所)
- **`BaseChatModel`型の導入**: `llm`変数が`ChatAnthropic | ChatOpenAI`のどちらにもなりうる
  箇所で、LangChainの共通基底型`BaseChatModel`を使って型を安定させた
- **`build_agent`/`run_agent`の戻り値・引数**: `create_agent()`の戻り値はlanggraphの
  `CompiledStateGraph[...]`という、`create_agent`自身のTypeVarに依存する深いジェネリック型。
  型引数を再現する価値が薄いため、理由をコメントで明記した上で`Any`(+`# noqa: ANN401`)を
  使う境界とした
- その他は型注釈の追加・長すぎる行の分割・マジックナンバーの定数化

```bash
./scripts/ci_check.sh
```

pytest 4件・ruff・mypy・vultureすべてPASSで完走(exit code 0)。

## 意図的にやらないこと

- LangChain/Neo4j/ChromaDB等の型スタブ不足に起因する、今後新たに増える可能性のあるmypy指摘への
  個別対応方針の事前決定(発生時に都度判断する)
- `keyword_coverage`以外の既存関数への網羅的なテスト追加(今回はpytestゲートを機能させるための
  最小限の1関数のみ)
- 旧`venv/`ディレクトリ・`requirements.txt`利用者側の手元環境のクリーンアップ(`requirements.txt`は
  リポジトリから削除したが、各自の`venv/`は`.gitignore`済みのため触れていない。uv移行後は
  不要になるので、削除して`uv sync`で作られる`.venv/`に切り替えることを推奨)
