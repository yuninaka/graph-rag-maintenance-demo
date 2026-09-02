"""
ベクトルRAG: チャンク分割・埋め込み・インデックス構築

【チャンク設計の意図】
本データは1レポート(数百字)単位でも十分に短いため、
レポート単位=チャンク単位とする(チャンク分割不要)。
これは「チャンク設計の判断」自体が経験としてカウントされる部分で、
実案件でより長い文書(仕様書・図面添付テキスト等)を扱う場合は
RecursiveCharacterTextSplitter 等でのオーバーラップ付き分割が必要になる。
その拡張ポイントをコメントで明示している。

【埋め込みモデルの選定】
API課金を避けるため、ローカルで動く多言語埋め込みモデル
(intfloat/multilingual-e5-small)を使用。日本語の意味検索に対応。
実案件でAzure OpenAI Embeddings等を使う場合は
HuggingFaceEmbeddings を AzureOpenAIEmbeddings に差し替えるだけでよい設計。
"""
import json
import os
from pathlib import Path

from langchain_huggingface import HuggingFaceEmbeddings
from langchain_community.vectorstores import Chroma
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_core.documents import Document

# 環境変数MAINTENANCE_LOGS_PATHで差し替え可能(例: OCR由来のdata/maintenance_logs_ocr.jsonl)。
# build_knowledge_graph.pyと同じ差し替えパターン。
DATA_PATH = Path(
    os.environ.get("MAINTENANCE_LOGS_PATH")
    or (Path(__file__).parent.parent / "data" / "maintenance_logs.jsonl")
)
# データソースを変えて検証し直す際に既存インデックスを混在させないよう、
# 永続化先も環境変数CHROMA_PERSIST_DIRで分けられるようにする。
PERSIST_DIR = Path(
    os.environ.get("CHROMA_PERSIST_DIR")
    or (Path(__file__).parent.parent / "chroma_db")
)

# 拡張ポイント: 長文書を扱う場合はここでチャンクサイズ/オーバーラップを調整
SPLITTER = RecursiveCharacterTextSplitter(
    chunk_size=400,
    chunk_overlap=50,
    separators=["\n", "。", "、"],
)


def load_documents() -> list[Document]:
    docs = []
    with open(DATA_PATH, encoding="utf-8") as f:
        for line in f:
            record = json.loads(line)
            # レポート単位が十分短いのでそのままでも良いが、
            # 長文データにも対応できるよう split_text を通す
            chunks = SPLITTER.split_text(record["text"])
            for i, chunk in enumerate(chunks):
                docs.append(
                    Document(
                        page_content=chunk,
                        metadata={
                            "report_id": record["report_id"],
                            "date": record["date"],
                            "equipment": record["equipment"],
                            "chunk_index": i,
                        },
                    )
                )
    return docs


def main():
    print("[1/3] 合成データを読み込み中...")
    docs = load_documents()
    print(f"  -> {len(docs)} チャンクを生成")

    print("[2/3] 埋め込みモデルをロード中(初回はモデルダウンロードが発生)...")
    embeddings = HuggingFaceEmbeddings(model_name="intfloat/multilingual-e5-small")

    print("[3/3] Chromaへインデックス構築中...")
    vectorstore = Chroma.from_documents(
        documents=docs,
        embedding=embeddings,
        persist_directory=str(PERSIST_DIR),
    )
    vectorstore.persist()
    print(f"完了。インデックスは {PERSIST_DIR} に保存されました。")


if __name__ == "__main__":
    main()
