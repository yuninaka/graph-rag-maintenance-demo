"""
回答精度の測定方法の設計・評価

求人要件「回答精度の測定方法の設計・評価」に対応する実装。

【評価方法の設計方針】
1. golden_qa.jsonl に、想定回答に含まれるべきキーワード群を人手で定義
2. エージェントの実際の回答にキーワードが何割含まれるかで簡易スコアリング
   (キーワード網羅率。LLM-as-judgeより安価・再現性が高いが、
   表現の言い換えを拾えない弱点があることをレポートに明記する)
3. type(vector/graph)別に集計し、「ベクトル検索が得意な質問」
   「グラフ検索が得意な質問」の傾向を分析する
   → これが実案件での「想定通りの回答が得られない場合の原因分析」の
     ベースになる

本格運用する場合は、LLM-as-judge(別のLLMに正誤判定させる)や
人手評価とのハイブリッドに拡張することを推奨(コード内にTODOで明記)。
"""
import json
from pathlib import Path

from src.hybrid_agent import build_agent, run_agent

EVAL_PATH = Path(__file__).parent.parent / "eval" / "golden_qa.jsonl"


def keyword_coverage(answer: str, keywords: list[str]) -> float:
    hit = sum(1 for kw in keywords if kw in answer)
    return hit / len(keywords) if keywords else 0.0


def main():
    agent = build_agent()

    with open(EVAL_PATH, encoding="utf-8") as f:
        cases = [json.loads(line) for line in f]

    results = []
    for case in cases:
        answer = run_agent(agent, case["question"])
        score = keyword_coverage(answer, case["expected_keywords"])
        results.append({**case, "answer": answer, "score": score})
        print(f"[{case['id']} / {case['type']}] score={score:.2f}")
        print(f"  Q: {case['question']}")
        print(f"  A: {answer}\n")

    # type別集計
    by_type = {}
    for r in results:
        by_type.setdefault(r["type"], []).append(r["score"])

    print("=== 集計結果 ===")
    for t, scores in by_type.items():
        avg = sum(scores) / len(scores)
        print(f"  {t}: 平均スコア {avg:.2f} ({len(scores)}件)")

    overall = sum(r["score"] for r in results) / len(results)
    print(f"  全体: 平均スコア {overall:.2f} ({len(results)}件)")

    out_path = Path(__file__).parent.parent / "eval" / "eval_results.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\n詳細結果を {out_path} に保存しました。")

    # TODO(次のステップ): LLM-as-judgeによる意味的正誤判定を追加し、
    # キーワード網羅率とのスコア相関を検証する。
    # TODO: type別スコア差が大きい場合、docs/troubleshooting_log.md に
    # 「なぜグラフ/ベクトルどちらかが弱かったか」の原因分析を記録する。


if __name__ == "__main__":
    main()
