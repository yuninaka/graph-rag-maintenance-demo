"""
ナレッジグラフ構築: LLMによるエンティティ/関係抽出 → Neo4jへ投入

スキーマは docs/schema.md を参照。
ReportEvent -> Equipment / Symptom -> Cause -> Action -> Part
という因果チェーンを蓄積することで、ベクトル検索だけでは弱い
「同一症状の集約・原因分析」をグラフ探索で可能にする。

【LLM抽出 vs ルールベース抽出】
- extract_entities_llm(): 実案件想定。LLMに構造化JSON出力させる。
  精度は高いがAPI課金が発生する。
- extract_entities_rule_based(): API課金なしでパイプライン疎通確認したい
  場合の簡易フォールバック(キーワードマッチ)。精度は粗いが無料。

環境変数 LLM_PROVIDER=anthropic|openai|rule_based で切り替え可能。
"""
import json
import os
import re
from pathlib import Path

from dotenv import load_dotenv
from neo4j import GraphDatabase

load_dotenv()

DATA_PATH = Path(__file__).parent.parent / "data" / "maintenance_logs.jsonl"

NEO4J_URI = os.environ.get("NEO4J_URI")
NEO4J_USER = os.environ.get("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.environ.get("NEO4J_PASSWORD")
LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "rule_based")

# Symptomノードの名寄せ用カテゴリ辞書。自由記述のsymptomだと表記ゆれで
# 別ノードに分裂する(例:「吐出圧力低下」と「吐出圧力の低下」)ため、
# ここから選ばせたカテゴリをSymptomノードのMERGEキーにする(詳細はdocs/schema.md)
SYMPTOM_CATEGORIES = [
    "異音", "振動", "圧力低下", "発熱", "温度上昇", "停止", "起動不良",
    "動作異常", "位置精度低下", "冷却能力低下", "バッテリー低下",
    "部品損傷", "汚損・劣化", "その他",
]

EXTRACTION_PROMPT = """以下は設備保全のトラブル報告書です。
このテキストから、以下のJSON形式で情報を抽出してください。
出力はJSONのみとし、説明文は含めないでください。

{{
  "equipment": "設備名",
  "symptom": "発生した症状(短い名詞句、報告書の記述に近い自由記述)",
  "symptom_category": "以下のカテゴリ一覧から最も近いものを1つだけ選ぶ: {categories}",
  "cause": "原因(短い名詞句)",
  "action": "対処内容(短い名詞句)",
  "part": "使用部品名(なければnull)",
  "backreferences": [
    {{"report_id": "本文中に明示的に登場する過去の報告書ID(例: R005)", "implied_category": "その過去の報告書について、本文の記述から今回追加で分かった症状カテゴリ(上記カテゴリ一覧から1つ)"}}
  ]
}}

backreferencesは、本文が過去の報告書IDを明示的に挙げて、**その報告書自身の症状が
何だったかを個別に言い切っている**場合のみ含めてください。
- 含める例:「前回(R005)は取付ボルトの緩みが原因だった」→ R005について今回の症状カテゴリと同じ扱いにできると言い切っている
- **含めない例**:「過去に4回類似トラブル(R002, R007, R015, R022関連)が発生している」のように、
  複数の報告書IDを「類似」「関連」とまとめて言及しているだけで、個々の報告書の症状を
  明言していない場合。特に、参照先の報告書が「(過去のXXXとは異なる症状)」のように
  **自ら今回の症状と異なると明言している**場合は、たとえ後から緩く関連付けられていても
  絶対に含めないでください。
迷ったら含めない(空配列 [] にする)ことを優先してください。

報告書:
{text}
"""


def extract_entities_claude_code(text: str) -> dict:
    """Claude Code CLI(ヘッドレスモード)経由での抽出。

    バッチ的な単発呼び出しであり、LangChainのTool-calling(Function calling)
    プロトコルを必要としないため、API従量課金の代わりにClaude Pro/Maxプランの
    定額枠(Claude Codeのターミナル利用)で完結させられる。
    一方 hybrid_agent.py 側のLangChain Agentは、API側のfunction calling機構
    に依存するためこの置き換えができない。両者の違いは README 参照。

    事前に `claude` CLI がインストール・ログイン済みであることが前提
    (npm install -g @anthropic-ai/claude-code / claude auth login)。
    """
    import subprocess

    # ANTHROPIC_API_KEY(.envのダミー値含む)が環境にあると、claude CLIが
    # claude.aiログイン(Pro/Max定額)より優先してそれを使おうとし失敗するため除外する
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}

    prompt = EXTRACTION_PROMPT.format(text=text, categories=", ".join(SYMPTOM_CATEGORIES))
    result = subprocess.run(
        ["claude", "-p", prompt],
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Claude Code CLI呼び出し失敗: {result.stderr}")

    content = result.stdout.strip()
    content = re.sub(r"```(json)?", "", content).strip()
    return json.loads(content)


def extract_entities_llm(text: str) -> dict:
    """API従量課金経由での抽出(参考実装として残置)。

    実案件でバッチ処理の速度・安定性を重視する場合や、CI等で
    対話型CLIを使えない環境ではこちらを使う想定。
    """
    if LLM_PROVIDER == "anthropic":
        from langchain_anthropic import ChatAnthropic

        llm = ChatAnthropic(model="claude-sonnet-4-6", temperature=0)
    elif LLM_PROVIDER == "openai":
        from langchain_openai import ChatOpenAI

        llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)
    else:
        raise ValueError(f"Unknown LLM_PROVIDER: {LLM_PROVIDER}")

    resp = llm.invoke(EXTRACTION_PROMPT.format(text=text, categories=", ".join(SYMPTOM_CATEGORIES)))
    content = resp.content if hasattr(resp, "content") else str(resp)
    content = re.sub(r"```(json)?", "", content).strip()
    return json.loads(content)


def extract_entities_rule_based(text: str) -> dict:
    """API課金なしの簡易版。キーワード辞書でマッチングするだけの粗い実装。
    パイプラインの疎通確認・デモ用途。精度評価では低スコアになる想定で、
    LLM版との比較対象としてあえて残している。
    """
    symptom_kw = {"異音": "異音", "過熱": "過熱", "発熱": "発熱", "停止": "停止", "圧力が低下": "圧力低下", "振動": "振動"}
    cause_kw = {"摩耗": "摩耗", "詰まり": "詰まり", "緩み": "緩み", "劣化": "劣化", "目詰まり": "目詰まり", "潤滑不足": "潤滑不足", "異物": "異物噛み込み"}
    action_kw = {"交換": "交換", "注油": "注油", "締め直し": "締め直し", "清掃": "清掃", "除去": "除去"}
    part_kw = {"ベアリング": "ベアリング", "羽根車": "羽根車", "フィルター": "フィルター", "モーター": "モーター", "ボルト": "ボルト"}

    def find_first(kw_map):
        for k, v in kw_map.items():
            if k in text:
                return v
        return None

    symptom = find_first(symptom_kw) or "不明"
    return {
        "equipment": None,  # 呼び出し側で record["equipment"] を使う
        "symptom": symptom,
        "symptom_category": symptom if symptom in SYMPTOM_CATEGORIES else "その他",
        "cause": find_first(cause_kw) or "不明",
        "action": find_first(action_kw) or "不明",
        "part": find_first(part_kw),
        "backreferences": [],  # 簡易版では後方参照検出は行わない
    }


def load_records():
    with open(DATA_PATH, encoding="utf-8") as f:
        return [json.loads(line) for line in f]


CYPHER_UPSERT = """
MERGE (eq:Equipment {name: $equipment})
MERGE (r:ReportEvent {report_id: $report_id})
  SET r.date = $date, r.reporter = $reporter
MERGE (r)-[:OCCURRED_ON]->(eq)
MERGE (s:Symptom {name: $symptom_category})
MERGE (r)-[h:HAS_SYMPTOM]->(s)
  SET h.detail = $symptom
MERGE (c:Cause {name: $cause})
// CAUSED_BYはSymptomではなくReportEventから直接張る。Symptomはカテゴリ共有
// ノードのため、Symptom起点にすると「同じカテゴリを持つ別設備・別報告書の
// 原因」まで拾ってしまうクロス混入が起きる(Equipment条件で絞ってもすり抜ける)
MERGE (r)-[:CAUSED_BY]->(c)
MERGE (a:Action {name: $action})
MERGE (c)-[:RESOLVED_BY]->(a)
FOREACH (_ IN CASE WHEN $part IS NOT NULL THEN [1] ELSE [] END |
  MERGE (p:Part {name: $part})
  MERGE (a)-[:USES_PART]->(p)
)
"""

# 後方参照(「前回(R005)は...」等)による差分更新。参照先レポートの全履歴を
# 読み直すのではなく、今回のレポート本文だけから分かった追加カテゴリを
# 参照先ReportEventに軽量に追記する(件数が増えてもコストが増えないスケールする設計)
CYPHER_BACKREFERENCE_UPDATE = """
MATCH (r:ReportEvent {report_id: $ref_report_id})
MERGE (s:Symptom {name: $implied_category})
MERGE (r)-[h:HAS_SYMPTOM]->(s)
  SET h.detail = coalesce(h.detail, $implied_category),
      h.inferred_from = $source_report_id
"""


def main():
    if not NEO4J_URI or not NEO4J_PASSWORD:
        raise RuntimeError(
            "NEO4J_URI / NEO4J_PASSWORD が未設定です。.env を確認してください。"
            "Neo4j AuraDB Free (https://neo4j.com/product/auradb/) で無料インスタンスを作成できます。"
        )

    records = load_records()
    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))

    print(f"抽出方式: {LLM_PROVIDER}")
    with driver.session() as session:
        for record in records:
            if LLM_PROVIDER == "rule_based":
                entities = extract_entities_rule_based(record["text"])
            elif LLM_PROVIDER == "claude_code":
                entities = extract_entities_claude_code(record["text"])
            else:
                entities = extract_entities_llm(record["text"])

            session.run(
                CYPHER_UPSERT,
                report_id=record["report_id"],
                date=record["date"],
                reporter=record["reporter"],
                # 設備名はLLMの自由記述抽出だと表記ゆれが起きる(例:「コンベアB」→「コンベアBのモーター」)ため、
                # 常に元データの正規化済み値を使う(グラフのEquipmentノード分裂を防ぐ)
                equipment=record["equipment"],
                symptom=entities.get("symptom") or "不明",
                # Symptomノードは自由記述だと表記ゆれで分裂する(例:「吐出圧力低下」と
                # 「吐出圧力の低下」)ため、統制されたカテゴリでMERGEする。元の自由記述は
                # HAS_SYMPTOM関係のdetailプロパティとして保持する
                symptom_category=entities.get("symptom_category")
                if entities.get("symptom_category") in SYMPTOM_CATEGORIES
                else "その他",
                cause=entities.get("cause") or "不明",
                action=entities.get("action") or "不明",
                part=entities.get("part"),
            )

            # 後方参照(過去レポートIDへの言及)があれば、参照先レポートへ軽量な
            # 追加カテゴリを反映する。参照先が未処理(レポート順序の想定外)の
            # 場合はCypher側でMATCHが0件になり何も起きない
            for ref in entities.get("backreferences") or []:
                ref_id = ref.get("report_id")
                implied = ref.get("implied_category")
                if ref_id and implied in SYMPTOM_CATEGORIES:
                    session.run(
                        CYPHER_BACKREFERENCE_UPDATE,
                        ref_report_id=ref_id,
                        implied_category=implied,
                        source_report_id=record["report_id"],
                    )

            print(f"  {record['report_id']}: {entities}")

    driver.close()
    print("Neo4jへのナレッジグラフ投入が完了しました。")


if __name__ == "__main__":
    main()
