# ナレッジグラフ スキーマ設計

設備保全ドメインの操業日誌・トラブル報告書を想定したノード/エッジ設計。
求人の「ナレッジグラフのデータ構造(ノード・エッジ設計)の検討」に
対応する成果物として、面談や職務経歴書で説明できるようにしています。

## ノード種別

| ノードラベル | 説明 | 主なプロパティ |
|---|---|---|
| `Equipment` | 設備・機器(例: コンベアA、ポンプ3号機) | name, equipment_type, location |
| `Symptom` | 発生した症状カテゴリ(例: 異音、圧力低下、停止。統制語彙でMERGEし名寄せする) | name(カテゴリ名) |
| `Cause` | 原因(例: ベアリング摩耗、潤滑不足) | name, description |
| `Action` | 対処・修理内容(例: ベアリング交換、注油) | name, description |
| `Part` | 使用部品(例: ベアリングNo.6205) | name, part_number |
| `ReportEvent` | 個々の報告書・日誌エントリ(タイムスタンプを持つ) | report_id, date, reporter, raw_text_ref |

## エッジ種別(関係)

| 関係 | From → To | 意味 |
|---|---|---|
| `OCCURRED_ON` | ReportEvent → Equipment | どの設備の報告か |
| `HAS_SYMPTOM` | ReportEvent → Symptom | どんな症状が報告されたか(関係のdetailプロパティに報告書原文に近い自由記述を保持) |
| `CAUSED_BY` | ReportEvent → Cause | その報告書固有の原因(SymptomではなくReportEvent起点。理由は下記) |
| `RESOLVED_BY` | Cause → Action | 原因への対処方法 |
| `USES_PART` | Action → Part | 対処に使った部品 |
| `SIMILAR_TO` | Equipment → Equipment | 類似設備(型式が同じ等、任意) |

## 設計意図

- `Symptom → Cause → Action` のチェーンを蓄積していくことで、
  「同じ症状が過去に何度発生し、どう解決されたか」をグラフ探索だけで
  即答できる(ベクトル検索だけでは「類似文書」は出せても
  「因果の集約」は苦手なため、ここがグラフDBを使う意義)。
- `ReportEvent` を独立ノードにすることで、時系列分析
  (「この設備は直近3ヶ月で同じ症状が何回出ているか」等)にも
  Cypherクエリで対応可能。
- 実案件でAI-OCR(Azure Document Intelligence等)を使う場合は、
  OCRで抽出した構造化フィールド(帳票の項目)を
  `ReportEvent` のプロパティ、または新規ノードとしてマッピングする
  拡張ポイントとして設計している。
- `Symptom.name` はLLMが報告書本文から自由記述抽出すると、同じ現象でも
  「吐出圧力低下」「吐出圧力の低下」のように表記が微妙に揺れ、`MERGE`で
  別ノードに分裂してしまう(名寄せされない)。これを防ぐため、抽出プロンプトで
  あらかじめ定義したカテゴリ辞書(`build_knowledge_graph.py`の
  `SYMPTOM_CATEGORIES`、例: 異音/振動/圧力低下/停止など)から1つ選ばせ、
  それを`Symptom`ノードのMERGEキーにしている。報告書原文に近い自由記述は
  情報として失わないよう、`HAS_SYMPTOM`関係の`detail`プロパティに保持する。
- `CAUSED_BY`は`Symptom → Cause`ではなく`ReportEvent → Cause`にしている。
  `Symptom`はカテゴリ共有ノードのため、もし`Symptom`起点で`CAUSED_BY`を張ると、
  `Equipment`条件で`ReportEvent`を絞り込んでも、その先の`Symptom → Cause`は
  絞り込みをすり抜けて「同じカテゴリを持つ別設備・別報告書の原因」まで
  混入してしまう(実際にこの設計ミスで精度評価のスコアが悪化する事象が発生した)。
  `ReportEvent`起点にすることで、各報告書固有の原因だけが正しく辿れる。

## Cypher例(このスキーマでできること)

```cypher
// 「ポンプ3号機」で過去に発生した症状と原因、対処法を一覧
MATCH (e:Equipment {name: "ポンプ3号機"})<-[:OCCURRED_ON]-(r:ReportEvent)
MATCH (r)-[:HAS_SYMPTOM]->(s:Symptom)
MATCH (r)-[:CAUSED_BY]->(c:Cause)-[:RESOLVED_BY]->(a:Action)
RETURN s.name, c.name, a.name, count(r) AS occurrence
ORDER BY occurrence DESC
```
