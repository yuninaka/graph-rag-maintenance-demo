# グラフRAG 設備保全ナレッジベース 最小実証プロジェクト

生成AI×ナレッジグラフ(グラフRAG)案件で求められる技術要素を、
最小構成で「実装経験」として積み上げるための個人検証プロジェクトです。
設備保全ドメイン(操業日誌・トラブル報告書)を模した合成データを使い、
非構造化ログ → ナレッジグラフ化 → ハイブリッド検索(ベクトル+グラフ) →
AIエージェント応答 → 精度評価、までの一気通貫パイプラインを構築します。

## なぜこの構成か(仮想要件とのマッピング)

| 仮想要件(想定する必須/歓迎スキル) | 本プロジェクトでの対応箇所 |
|---|---|
| ナレッジグラフのデータ構造(ノード・エッジ)設計 | `docs/schema.md`、`src/build_knowledge_graph.py` |
| AI-OCRで構造化したデータのグラフ化(Python) | `data/maintenance_logs.jsonl`(構造化済み想定データ) + `src/build_knowledge_graph.py`。実案件ではAzure Document Intelligenceの出力をここに差し替える想定で拡張ポイントをコメントで明示 |
| グラフDB(Neo4j等OSS)へのデータ投入・運用 | `src/build_knowledge_graph.py`(Neo4j AuraDB Free) |
| LangChain/LangGraphでのAIエージェント(チャットボット) | `src/hybrid_agent.py` |
| 想定通りの回答が得られない場合の原因分析 | `docs/troubleshooting_log.md`(検証時に実際に記録する運用) |
| 回答精度の測定方法の設計・評価 | `eval/golden_qa.jsonl` + `src/evaluate.py` |
| ベクトルRAG(チャンク設計・インデックス設計) | `src/build_vector_index.py` |

## アーキテクチャ

```
[非構造化ログ(合成データ)]
        │
        ├─→ [チャンク分割 + 埋め込み] → [Chroma(ベクトルDB)] ──┐
        │                                                      │
        └─→ [LLMによるエンティティ/関係抽出] → [Neo4j(グラフDB)]─┤
                                                                 ▼
                                                    [LangChain ハイブリッド検索]
                                                    ・ベクトル類似検索 Retriever
                                                    ・GraphCypherQAChain
                                                                 │
                                                                 ▼
                                                   [Agent: 質問に応じてツール選択]
                                                                 │
                                                                 ▼
                                                        [回答 + 精度評価]
```

## セットアップ

```bash
python -m venv venv
source venv/bin/activate  # Windowsは venv\Scripts\activate
pip install -r requirements.txt
```

### Neo4j AuraDB Free の準備
1. https://neo4j.com/product/auradb/ からFree tierでインスタンス作成(クレジットカード不要)
2. 発行される接続情報を `.env` に設定

```
NEO4J_URI=neo4j+s://xxxxx.databases.neo4j.io
NEO4J_USER=neo4j
NEO4J_PASSWORD=xxxxx
LLM_PROVIDER=claude_code   # rule_based / claude_code / anthropic / openai から選択
ANTHROPIC_API_KEY=xxxxx    # LLM_PROVIDER=anthropic の場合のみ必要
OPENAI_API_KEY=xxxxx       # LLM_PROVIDER=openai の場合のみ必要
```

### LLM_PROVIDERの選択肢とコスト方針

| 値 | コスト | 用途 |
|---|---|---|
| `rule_based` | 無料 | パイプラインの疎通確認。精度は粗い |
| `claude_code` | **Pro/Maxプラン定額内**(要`claude` CLIインストール・ログイン) | `build_knowledge_graph.py`のエンティティ抽出(バッチ処理)。推奨 |
| `anthropic` / `openai` | **API従量課金** | `hybrid_agent.py`のLangChain Agent実行時のみ。Tool-calling(Function calling)プロトコルに依存するため、Claude Codeでの定額代替が構造上できない |

**方針の理由**: エンティティ抽出は1件ずつの単発呼び出しで完結するバッチ処理のため、
Claude Code CLI(`claude -p "..."`)への置き換えでProプラン定額に収められる。
一方、ハイブリッドエージェント(`hybrid_agent.py`)はLangChainのTool-calling機構
そのものを検証する部分であり、ここをClaude Codeに置き換えると
LangChain Agent実装の検証という本来の目的から外れてしまうため、
少額のAPI課金を許容してAPI経由のまま実装している。
「どこはコスト最適化してよく、どこは目的に直結するので手を抜かないか」
という判断の一例として、Zenn記事にもそのまま書ける。

## 実行順序

```bash
# 1. ベクトルインデックス構築
python src/build_vector_index.py

# 2. ナレッジグラフ構築(Neo4jへ投入)
python src/build_knowledge_graph.py

# 3. ハイブリッドエージェントで質問応答(対話形式)
python src/hybrid_agent.py

# 4. 精度評価(golden_qa.jsonlに対する自動採点)
# evaluate.py は `from src.hybrid_agent import ...` と絶対importのため、
# モジュールとして実行する(python src/evaluate.py 直接実行だと失敗する)
python -m src.evaluate
```

## 評価結果(直近の実行)

`eval/golden_qa.jsonl`(9問)に対する `evaluate.py` の実行結果。詳細は `eval/eval_results.json`、
低スコア質問の原因分析は `docs/troubleshooting_log.md` を参照。

| type別 | 平均スコア | 件数 |
|---|---|---|
| graph | 1.00 | 5件 |
| vector | 1.00 | 4件 |
| **全体** | **1.00** | 9件 |

Symptomノードの名寄せ(カテゴリ辞書によるMERGE)、後方参照検出による差分更新
(「前回(R005)は...」のように過去レポートを明示的に再解釈する記述を検出し、
参照先レポートへ軽量に追加カテゴリを反映する、履歴件数に依存せずスケールする設計)、
そして評価データ側の期待キーワード(質問の意図に沿わない`report_id`要求)の是正を
経て、全問満点に到達した。途中、Equipmentノード分裂・`CAUSED_BY`のクロス混入・
後方参照の誤検出(false positive)など複数の実装バグを発見・修正している。
経緯の全体は `docs/troubleshooting_log.md` を参照。

### graph_queryの生ログを見る

`GraphCypherQAChain`の`verbose=True`出力は標準出力にしか残らないため、
`hybrid_agent.py`/`evaluate.py`経由で`graph_query`ツールが呼ばれるたびに、
生成Cypher・Neo4jの実行結果(Full Context)・最終回答を`logs/graph_query.jsonl`
に追記するようにしている(1呼び出し1行のJSONL、`logs/`は`.gitignore`対象)。

```bash
python -m src.evaluate   # 実行後、logs/graph_query.jsonl に9問分(graph typeのみ)が追記される
tail -f logs/graph_query.jsonl | jq .   # 1件ずつ整形して確認する場合
```

## AI-OCR連携検証(Azure Document Intelligence)

合成データ35件を `scripts/render_inspection_form.py` で点検記録表PDFにレンダリングし
(正解データが既知)、`scripts/ocr_document_intelligence.py` でAzure Document
Intelligence(`prebuilt-layout`モデル)に投げて抽出結果を正解と比較した。

| 指標 | 結果 |
|---|---|
| 平均類似度(35件) | **0.9990** |
| 完全一致 | 33/35件 |

2件で興味深い誤りが見つかった: アルファベット「O」を丸記号「〇」と混同する視覚的な
誤認識(R012)、帳票の印影をチェックボックスと誤検出(R013)。今回のPDFはテキストが
埋め込まれた「デジタルネイティブPDF」であり、テキストレイヤーがそのまま使われる
なら起きないはずの誤りが実際に発生したことから、**Document Intelligenceは単純に
テキストレイヤーを再利用せず、実際に画像として視覚的な認識処理を行っている**
可能性が示唆される。詳細は `docs/troubleshooting_log.md` を参照。

```bash
# 点検記録表PDFを生成(全35件、または引数でreport_idを1件指定)
python scripts/render_inspection_form.py

# Document Intelligenceで精度検証(.envにAZURE_DOCUMENT_INTELLIGENCE_*が必要)
python scripts/ocr_document_intelligence.py
```

### パイプライン完成: OCR結果をナレッジグラフ構築に接続

`scripts/ocr_to_records.py` で、Document Intelligenceの構造化レスポンス
(`result.tables` / `result.paragraphs`)から `maintenance_logs.jsonl` 互換の
レコードを組み立て、`build_knowledge_graph.py` の `MAINTENANCE_LOGS_PATH`
環境変数でデータソースを差し替えられるようにした。これで
「紙の書類(PDF)→OCR→ナレッジグラフ→ハイブリッド検索→回答」という
一気通貫パイプラインが完成した。

```bash
# PDF35件をOCRし、maintenance_logs.jsonl互換のJSONLを生成
python scripts/ocr_to_records.py

# OCR由来データでナレッジグラフを構築(通常はdata/maintenance_logs.jsonlを使用)
MAINTENANCE_LOGS_PATH=data/maintenance_logs_ocr.jsonl python src/build_knowledge_graph.py
```

OCR由来データで`evaluate.py`を実行したところ、合成データを直接使った場合と同じ
**9問全問score=1.00**を達成した。R012のOCR誤読(「O」→「〇」)はLLMによる
エンティティ抽出段階で「Oリング」という一般的な部品名として自動的に補正され、
最終的な回答精度には影響しなかった。詳細は `docs/troubleshooting_log.md` を参照。

## この後のロードマップ(目安)

| フェーズ | 内容 | 目安期間 |
|---|---|---|
| 1 | 本スキャフォールドを動かし、Neo4j Aura Free接続・LangChain疎通を確認 | 1〜2日 |
| 2 | 合成データを自分でもう少し拡張(30〜50件)し、ノード種別を増やす(例: 部品の型番、メーカー等) | 2〜3日 |
| 3 | `evaluate.py` で精度測定の方法論を自分の言葉で整理し、ベクトル単体/グラフ単体/ハイブリッドの精度比較を行う | 2〜3日 |
| 4 | 検証結果をZenn記事化。過去記事と同様に「うまくいかなかった点・原因分析」を含めると説得力が増す | 2〜3日 |
| 5(任意) | ~~Azure Document Intelligenceの無料枠でOCR部分も実装し、パイプラインを完全に仮想要件と一致させる~~ → 完了(上記「AI-OCR連携検証」参照) | 追加2〜3日 |

合計で正味1〜2週間程度(実務と並行)を想定した設計です。
フェーズ3までで「ベクトルRAGの取り扱い経験」「グラフDB実務経験」
「LangChainでのRetrieval/Agent機能の実装経験」を職務経歴書に
具体的な数字(精度○%、評価件数○件等)とともに書ける状態になります。
