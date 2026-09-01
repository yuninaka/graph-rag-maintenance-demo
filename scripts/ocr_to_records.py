"""
Document Intelligenceの抽出結果 → maintenance_logs.jsonl互換レコードへの変換層

「紙の書類(PDF)→OCR→構造化テキスト→ナレッジグラフ→回答」という
一気通貫パイプラインを完成させるための最後のピース。
data/inspection_forms/*.pdf をDocument Intelligenceに投げ、実際の
レスポンス構造(tables: 点検日/対象設備/報告者、paragraphs: report_id・本文)
から構造化フィールドを抽出し、build_knowledge_graph.py がそのまま読める
JSONL形式で出力する。

【方針】OCR出力は人手で補正しない。Document Intelligenceが実際に返した
文字列(第4弾記事のR012「O→〇」誤読やR013の余計なタグ等も含む)をそのまま
下流の抽出パイプラインに流し、ノイズが最終的な精度にどう伝播するかを
実測することが目的。
"""
import json
import re
import sys
from pathlib import Path

from azure.ai.documentintelligence import DocumentIntelligenceClient
from azure.ai.documentintelligence.models import AnalyzeResult
from azure.core.credentials import AzureKeyCredential
from dotenv import load_dotenv

import os

load_dotenv()

PDF_DIR = Path(__file__).parent.parent / "data" / "inspection_forms"
OUTPUT_PATH = Path(__file__).parent.parent / "data" / "maintenance_logs_ocr.jsonl"

ENDPOINT = os.environ.get("AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT")
KEY = os.environ.get("AZURE_DOCUMENT_INTELLIGENCE_KEY")

REPORT_ID_RE = re.compile(r"報告書No\.\s*(\S+)")


def get_client() -> DocumentIntelligenceClient:
    if not ENDPOINT or not KEY:
        raise RuntimeError(
            "AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT / AZURE_DOCUMENT_INTELLIGENCE_KEY が"
            ".envに未設定です。"
        )
    return DocumentIntelligenceClient(endpoint=ENDPOINT, credential=AzureKeyCredential(KEY))


def analyze(client: DocumentIntelligenceClient, pdf_path: Path) -> AnalyzeResult:
    if not pdf_path.exists():
        raise FileNotFoundError(
            f"{pdf_path} が見つかりません。先に "
            "`python scripts/render_inspection_form.py` でPDFを生成してください。"
        )
    with open(pdf_path, "rb") as f:
        poller = client.begin_analyze_document("prebuilt-layout", body=f)
    return poller.result()


def extract_meta_from_table(result: AnalyzeResult) -> dict:
    """点検日・対象設備・報告者のテーブル(3行2列、右列が値)を読む。

    テーブルが取れなかった場合(構造検出に失敗した場合)は空文字で埋め、
    後段で気づけるようにする(黙って欠損させない)。
    """
    if not result.tables:
        return {"date": "", "equipment": "", "reporter": ""}
    table = result.tables[0]
    cells = {(c.row_index, c.column_index): c.content for c in table.cells}
    return {
        "date": cells.get((0, 1), ""),
        "equipment": cells.get((1, 1), ""),
        "reporter": cells.get((2, 1), ""),
    }


def extract_report_id(result: AnalyzeResult) -> str:
    for p in result.paragraphs or []:
        m = REPORT_ID_RE.search(p.content)
        if m:
            return m.group(1)
    raise ValueError("report_idを抽出できませんでした(ページヘッダーに'報告書No.'が見当たりません)")


def extract_body_text(result: AnalyzeResult) -> str:
    """「状況・原因・対処内容」ラベルの次から、末尾の「確認済」スタンプの手前までを本文とする。

    OCR結果は中黒(・)が別のUnicode文字(·)として認識されることがあるため、
    ラベル判定は「状況」「原因」「対処内容」という3つの部分文字列の有無で行う
    (中黒の表記ゆれに影響されないようにする)。
    """
    paragraphs = [p.content for p in (result.paragraphs or [])]
    label_idx = next(
        (i for i, p in enumerate(paragraphs) if "状況" in p and "原因" in p and "対処内容" in p),
        None,
    )
    if label_idx is None:
        raise ValueError("本文ラベル(状況・原因・対処内容)を検出できませんでした")

    end_idx = next(
        (i for i in range(label_idx + 1, len(paragraphs)) if paragraphs[i].strip() == "確認済"),
        len(paragraphs),
    )
    return "".join(paragraphs[label_idx + 1 : end_idx])


def result_to_record(result: AnalyzeResult) -> dict:
    record = {"report_id": extract_report_id(result)}
    record.update(extract_meta_from_table(result))
    record["reporter"] = record["reporter"]
    record["text"] = extract_body_text(result)
    # JSONLの元スキーマ(report_id, date, equipment, reporter, text)の順に揃える
    return {k: record[k] for k in ["report_id", "date", "equipment", "reporter", "text"]}


def convert_all() -> list[dict]:
    client = get_client()
    records = []
    for pdf_path in sorted(PDF_DIR.glob("*.pdf")):
        result = analyze(client, pdf_path)
        records.append(result_to_record(result))
        print(f"  {pdf_path.stem}: OK")
    records.sort(key=lambda r: r["report_id"])
    return records


def write_jsonl(records: list[dict], path: Path = OUTPUT_PATH) -> Path:
    with open(path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return path


if __name__ == "__main__":
    if len(sys.argv) > 1:
        client = get_client()
        result = analyze(client, PDF_DIR / f"{sys.argv[1]}.pdf")
        print(json.dumps(result_to_record(result), ensure_ascii=False, indent=2))
    else:
        records = convert_all()
        out_path = write_jsonl(records)
        print(f"\n{len(records)}件を {out_path} に出力しました。")
