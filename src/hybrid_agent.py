"""
ハイブリッドRAGエージェント

質問の性質に応じて、以下2つのツールをLLM自身に選択させる(Agent機能):

1. vector_search: 「〇〇の報告書には何が書かれているか」等、
   自然文の類似検索が適した質問向け
2. graph_query: 「ポンプ3号機で過去に何回同じ症状が出たか」
   「異音の原因として最も多いのは何か」等、集約・因果関係を辿る質問向け
   (GraphCypherQAChain: 自然言語→Cypher変換→実行→自然言語回答、
   というLangChainの正規のグラフRAGチェーンを使用)

これは「Retrieval機能」(1)と「Agent機能」(ツール選択の自律判断、2)の
両方を含む構成になっている。
"""
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_community.vectorstores import Chroma
from langchain_community.graphs import Neo4jGraph
from langchain_community.chains.graph_qa.cypher import GraphCypherQAChain
from langchain.agents import create_agent
from langchain_core.prompts import PromptTemplate
from langchain_core.tools import Tool

from src.build_knowledge_graph import SYMPTOM_CATEGORIES

load_dotenv()

# build_vector_index.pyと同じCHROMA_PERSIST_DIRで、参照するインデックスを
# データソース(合成データ/OCR由来データ)に合わせて切り替えられるようにする。
PERSIST_DIR = Path(
    os.environ.get("CHROMA_PERSIST_DIR")
    or (Path(__file__).parent.parent / "chroma_db")
)
# 各ツール呼び出し・Agent全体の実行トレースを永続化するログ。標準出力(verbose=True等)
# にしか残らない情報を「後から見返せる生ログ」として残すための一連の仕組み
LOG_DIR = Path(__file__).parent.parent / "logs"
GRAPH_QUERY_LOG_PATH = LOG_DIR / "graph_query.jsonl"
VECTOR_SEARCH_LOG_PATH = LOG_DIR / "vector_search.jsonl"
AGENT_TRACE_LOG_PATH = LOG_DIR / "agent_trace.jsonl"


def _append_jsonl(path: Path, entry: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")

LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "anthropic")


def get_llm():
    if LLM_PROVIDER == "anthropic":
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(model="claude-sonnet-4-6", temperature=0)
    elif LLM_PROVIDER == "openai":
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(model="gpt-4o-mini", temperature=0)
    raise ValueError("hybrid_agent.py の実行には LLM_PROVIDER=anthropic か openai が必要です")


def _log_vector_search(query: str, docs: list) -> None:
    """vector_search 1回分の検索結果(ヒットしたチャンクとそのメタデータ)をJSONLに追記する。

    graph_queryのログと対称的に、「なぜその報告書が引っかかったか」を後から
    確認できるようにする(埋め込みの類似度スコアはretrieverのデフォルトAPIでは
    invoke()時に返らないため、report_id・チャンク本文のみを記録する)。
    """
    _append_jsonl(VECTOR_SEARCH_LOG_PATH, {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "question": query,
        "hits": [
            {
                "report_id": d.metadata.get("report_id"),
                "equipment": d.metadata.get("equipment"),
                "content": d.page_content,
            }
            for d in docs
        ],
    })


def build_vector_tool(llm):
    embeddings = HuggingFaceEmbeddings(model_name="intfloat/multilingual-e5-small")
    vectorstore = Chroma(persist_directory=str(PERSIST_DIR), embedding_function=embeddings)
    retriever = vectorstore.as_retriever(search_kwargs={"k": 3})

    def run(query: str) -> str:
        docs = retriever.invoke(query)
        _log_vector_search(query, docs)
        return "\n---\n".join(
            f"[{d.metadata['report_id']} / {d.metadata['equipment']}] {d.page_content}"
            for d in docs
        )

    return Tool(
        name="vector_search",
        description=(
            "報告書の原文に近い自然文検索。"
            "特定の状況や文脈に類似する過去の報告書を探す際に使う。"
        ),
        func=run,
    )


# Symptom.nameは自由記述ではなくSYMPTOM_CATEGORIESからの統制語彙(build_knowledge_graph.py参照)。
# デフォルトのCypher生成プロンプトはこれを知らず、質問文の言葉をそのままname一致に
# 使おうとして表記ゆれ以前に0件になる(例:「ベアリングの異音」)ため、
# カテゴリへのマッピングを明示的に指示するプロンプトに差し替える
CYPHER_GENERATION_TEMPLATE = """Task: Cypher文を生成してNeo4jグラフデータベースに問い合わせてください。
スキーマで示された関係・プロパティのみを使用してください。

重要: Symptom.name は自由記述ではなく、以下のカテゴリ一覧からのみ選ばれた統制語彙です。
質問文にどんな症状の言葉が出てきても、必ずこのカテゴリ一覧の中から意味的に最も近いものを
1つ選び、name プロパティの完全一致で問い合わせてください(部分一致・CONTAINSは使わない)。

カテゴリ一覧: {categories}

スキーマ:
{{schema}}

質問:
{{question}}

Cypher文のみを出力し、説明文やコードブロックの記号は含めないでください。
""".format(categories=", ".join(SYMPTOM_CATEGORIES))

CYPHER_GENERATION_PROMPT = PromptTemplate(
    input_variables=["schema", "question"], template=CYPHER_GENERATION_TEMPLATE
)


def _log_graph_query(query: str, result: dict) -> None:
    """graph_query 1回分の生成Cypher・Full Context・最終回答をJSONLに追記する。

    intermediate_steps は [{"query": <生成Cypher>}, {"context": <Neo4j実行結果>}]
    という2要素のリストを想定しているが、チェーンがCypher生成前にエラーになった
    場合等は要素数が変わりうるため、存在チェックしてから読む。
    """
    steps = result.get("intermediate_steps") or []
    generated_cypher = steps[0].get("query") if len(steps) > 0 else None
    context = steps[1].get("context") if len(steps) > 1 else None

    _append_jsonl(GRAPH_QUERY_LOG_PATH, {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "question": query,
        "generated_cypher": generated_cypher,
        "context": context,
        "answer": result.get("result"),
    })


def build_graph_tool(llm):
    graph = Neo4jGraph(
        url=os.environ["NEO4J_URI"],
        username=os.environ.get("NEO4J_USER", "neo4j"),
        password=os.environ["NEO4J_PASSWORD"],
        database=os.environ.get("NEO4J_DATABASE", "neo4j"),
    )
    graph.refresh_schema()

    chain = GraphCypherQAChain.from_llm(
        llm=llm,
        graph=graph,
        cypher_prompt=CYPHER_GENERATION_PROMPT,
        verbose=True,
        return_intermediate_steps=True,
        allow_dangerous_requests=True,  # ローカル検証用途のため許可。本番投入時は権限設計を別途行う
    )

    def run(query: str) -> str:
        result = chain.invoke({"query": query})
        _log_graph_query(query, result)
        return result.get("result", str(result))

    return Tool(
        name="graph_query",
        description=(
            "設備・症状・原因・対処法の関係や集計を扱う質問に使う。"
            "『何回発生したか』『最も多い原因は』『過去の対処法は』のような"
            "集約・因果関係の質問に強い。"
        ),
        func=run,
    )


SYSTEM_PROMPT = (
    "あなたは工場の設備保全担当者向けのアシスタントです。"
    "質問内容に応じて適切なツールを選び、日本語で簡潔に回答してください。"
    "ツールから得られた情報のみに基づいて回答し、憶測で補わないでください。"
)


def build_agent():
    llm = get_llm()
    tools = [build_vector_tool(llm), build_graph_tool(llm)]
    return create_agent(llm, tools, system_prompt=SYSTEM_PROMPT)


def _log_agent_trace(query: str, messages: list) -> None:
    """Agent全体の実行トレース(どのツールを、どの順で、どんな引数・結果で
    呼んだか)をJSONLに追記する。

    第2弾記事で「Agentがvector_searchも併用していたことに、回答文に
    report_idがたまたま含まれていたことから間接的に気づいた」というケースが
    あったため、以降はこの判断をログから直接確認できるようにする。
    """
    steps = []
    for m in messages:
        tool_calls = getattr(m, "tool_calls", None)
        if tool_calls:
            for tc in tool_calls:
                steps.append({"type": "tool_call", "tool": tc["name"], "args": tc["args"]})
        elif type(m).__name__ == "ToolMessage":
            steps.append({"type": "tool_result", "tool": m.name, "content": m.content})

    _append_jsonl(AGENT_TRACE_LOG_PATH, {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "question": query,
        "steps": steps,
        "final_answer": messages[-1].content,
    })


def run_agent(agent, query: str) -> str:
    result = agent.invoke({"messages": [{"role": "user", "content": query}]})
    _log_agent_trace(query, result["messages"])
    return result["messages"][-1].content


def main():
    agent = build_agent()
    print("設備保全ナレッジベース エージェント (終了は 'exit')")
    while True:
        query = input("\n質問> ")
        if query.strip().lower() in ("exit", "quit"):
            break
        answer = run_agent(agent, query)
        print(f"\n回答: {answer}")


if __name__ == "__main__":
    main()
