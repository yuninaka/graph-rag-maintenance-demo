"""
Azure Document Intelligence によるOCR精度検証ツール

data/inspection_forms/ の点検記録表PDF(scripts/render_inspection_form.py で
生成、正解データが既知)をDocument Intelligenceに投げ、抽出結果と正解データを
difflibで比較して精度を測定する。

【注意】このPDFはweasyprintで生成した「デジタルネイティブ」なPDF(テキスト
レイヤーを持つ)であり、紙をスキャン/撮影した画像ではない。Document Intelligence
は画像から視覚的にOCRするだけでなく、PDFのテキストレイヤーがあればそれを
直接利用することが多いため、ここで出る精度は実際の紙のスキャン/写真に対する
OCR精度の目安にはならない可能性が高い(埋め込みテキストの抽出に近くなり、
非常に高い精度が出やすい)。あくまで「パイプラインの疎通確認」用途と考えること。
"""
import difflib
import json
import os
import re
import sys
from pathlib import Path

from azure.ai.documentintelligence import DocumentIntelligenceClient
from azure.core.credentials import AzureKeyCredential
from dotenv import load_dotenv

load_dotenv()

DATA_PATH = Path(__file__).parent.parent / "data" / "maintenance_logs.jsonl"
PDF_DIR = Path(__file__).parent.parent / "data" / "inspection_forms"

ENDPOINT = os.environ.get("AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT")
KEY = os.environ.get("AZURE_DOCUMENT_INTELLIGENCE_KEY")


def get_client() -> DocumentIntelligenceClient:
    if not ENDPOINT or not KEY:
        raise RuntimeError(
            "AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT / AZURE_DOCUMENT_INTELLIGENCE_KEY が"
            ".envに未設定です。Azureポータルで発行されたリソースの値を設定してください。"
        )
    return DocumentIntelligenceClient(endpoint=ENDPOINT, credential=AzureKeyCredential(KEY))


def load_all_records() -> list[dict]:
    with open(DATA_PATH, encoding="utf-8") as f:
        return [json.loads(line) for line in f]


def load_record(report_id: str) -> dict:
    for record in load_all_records():
        if record["report_id"] == report_id:
            return record
    raise ValueError(f"report_id {report_id} が見つかりません: {DATA_PATH}")


def expected_text(record: dict) -> str:
    """render_inspection_form.py のテンプレートに基づく、帳票に印字されているはずの全文。"""
    return (
        f"報告書No. {record['report_id']}\n"
        "設備点検・トラブル報告書\n"
        f"点検日 {record['date']}\n"
        f"対象設備 {record['equipment']}\n"
        f"報告者 {record['reporter']}\n"
        "状況・原因・対処内容\n"
        f"{record['text']}\n"
        "確認済"
    )


def analyze_pdf(client: DocumentIntelligenceClient, pdf_path: Path) -> str:
    if not pdf_path.exists():
        raise FileNotFoundError(
            f"{pdf_path} が見つかりません。先に "
            "`python scripts/render_inspection_form.py` でPDFを生成してください。"
        )
    with open(pdf_path, "rb") as f:
        poller = client.begin_analyze_document("prebuilt-layout", body=f)
    result = poller.result()
    return result.content or ""


def normalize(text: str) -> str:
    """空白・改行・中黒の表記ゆれを吸収する。

    Document Intelligenceは行の折り返し位置にスペースを挿入したり、
    ラベルと値の間で改行したり、中黒(・)を類似の別のUnicode文字(·)で
    認識することがある。これらはレイアウト上の差であり文字認識の誤りでは
    ないため、比較前に正規化して除外する。
    """
    text = text.replace("·", "・")
    text = re.sub(r"\s+", "", text)  # 空白・改行をすべて除去
    return text


def compare(report_id: str, client: DocumentIntelligenceClient) -> dict:
    record = load_record(report_id)
    expected = expected_text(record)
    actual = analyze_pdf(client, PDF_DIR / f"{report_id}.pdf")

    expected_norm = normalize(expected)
    actual_norm = normalize(actual)
    ratio = difflib.SequenceMatcher(None, expected_norm, actual_norm).ratio()
    return {
        "report_id": report_id,
        "similarity": ratio,
        "expected": expected,
        "actual": actual,
        "expected_norm": expected_norm,
        "actual_norm": actual_norm,
    }


def print_diff(expected_norm: str, actual_norm: str) -> None:
    diff = difflib.unified_diff(
        [expected_norm], [actual_norm], fromfile="正解(正規化後)", tofile="OCR結果(正規化後)", lineterm=""
    )
    print("\n".join(diff))


def run_one(report_id: str, client: DocumentIntelligenceClient) -> dict:
    result = compare(report_id, client)
    print(f"{result['report_id']}: 類似度 {result['similarity']:.4f}")
    if result["similarity"] < 1.0:
        print_diff(result["expected_norm"], result["actual_norm"])
    return result


def run_all(client: DocumentIntelligenceClient) -> list[dict]:
    results = []
    for record in load_all_records():
        results.append(run_one(record["report_id"], client))
    avg = sum(r["similarity"] for r in results) / len(results)
    print(f"\n=== {len(results)}件 平均類似度: {avg:.4f} ===")
    below_perfect = [r for r in results if r["similarity"] < 1.0]
    if below_perfect:
        print(f"満点でなかった件数: {len(below_perfect)} ({', '.join(r['report_id'] for r in below_perfect)})")
    return results


if __name__ == "__main__":
    client = get_client()
    if len(sys.argv) > 1:
        run_one(sys.argv[1], client)
    else:
        run_all(client)
