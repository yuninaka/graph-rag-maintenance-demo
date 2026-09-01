"""
点検記録表(HTML)レンダリングツール

Azure Document Intelligence等のAI-OCR連携を検証する際、
AI画像生成(DALL-E等)は密度の高い日本語テキストの描画が不得意で
生成結果が読めないことが多い。そのため、data/maintenance_logs.jsonl の
実データをそのままテキストとしてHTML帳票に流し込み、正解が確実な
サンプル書類を作る用途のスクリプト。

HTMLに加えてPDFも自動生成する(weasyprintでHTML/CSSを直接PDF化。
ヘッドレスブラウザのダウンロードが不要な軽量な実装のため採用)。
生成したPDFはそのままDocument Intelligenceへの入力サンプルとして使える。
"""
import json
import sys
from pathlib import Path

from weasyprint import HTML

DATA_PATH = Path(__file__).parent.parent / "data" / "maintenance_logs.jsonl"
OUTPUT_DIR = Path(__file__).parent.parent / "data" / "inspection_forms"

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<title>設備点検・トラブル報告書 {report_id}</title>
<style>
  body {{
    font-family: "Yu Mincho", "Hiragino Mincho ProN", serif;
    max-width: 800px;
    margin: 40px auto;
    color: #222;
  }}
  h1 {{
    text-align: center;
    font-size: 20px;
    letter-spacing: 4px;
    border-bottom: 3px double #222;
    padding-bottom: 12px;
  }}
  table {{
    width: 100%;
    border-collapse: collapse;
    margin-top: 24px;
  }}
  table.meta td, table.meta th {{
    border: 1px solid #444;
    padding: 8px 12px;
    font-size: 14px;
  }}
  table.meta th {{
    background: #eee;
    width: 120px;
    text-align: left;
  }}
  .report-id {{
    text-align: right;
    font-size: 13px;
    margin-bottom: 4px;
  }}
  .body-box {{
    border: 1px solid #444;
    margin-top: 16px;
    padding: 16px;
    min-height: 220px;
    line-height: 1.9;
    font-size: 15px;
  }}
  .body-box .label {{
    font-size: 13px;
    color: #555;
    margin-bottom: 8px;
  }}
  .stamp-box {{
    margin-top: 24px;
    display: flex;
    justify-content: flex-end;
  }}
  .stamp {{
    width: 70px;
    height: 70px;
    border: 2px solid #b00;
    border-radius: 50%;
    color: #b00;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 13px;
    transform: rotate(-8deg);
  }}
</style>
</head>
<body>
  <div class="report-id">報告書No. {report_id}</div>
  <h1>設備点検・トラブル報告書</h1>
  <table class="meta">
    <tr><th>点検日</th><td>{date}</td></tr>
    <tr><th>対象設備</th><td>{equipment}</td></tr>
    <tr><th>報告者</th><td>{reporter}</td></tr>
  </table>
  <div class="body-box">
    <div class="label">状況・原因・対処内容</div>
    {text}
  </div>
  <div class="stamp-box"><div class="stamp">確認済</div></div>
</body>
</html>
"""


def load_all_records() -> list[dict]:
    with open(DATA_PATH, encoding="utf-8") as f:
        return [json.loads(line) for line in f]


def load_record(report_id: str) -> dict:
    for record in load_all_records():
        if record["report_id"] == report_id:
            return record
    raise ValueError(f"report_id {report_id} が見つかりません")


def render_record(record: dict) -> tuple[Path, Path]:
    html = HTML_TEMPLATE.format(
        report_id=record["report_id"],
        date=record["date"],
        equipment=record["equipment"],
        reporter=record["reporter"],
        text=record["text"],
    )
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    html_path = OUTPUT_DIR / f"{record['report_id']}.html"
    html_path.write_text(html, encoding="utf-8")

    pdf_path = OUTPUT_DIR / f"{record['report_id']}.pdf"
    HTML(string=html, base_url=str(OUTPUT_DIR)).write_pdf(pdf_path)

    return html_path, pdf_path


def render(report_id: str) -> tuple[Path, Path]:
    return render_record(load_record(report_id))


def render_all() -> list[tuple[Path, Path]]:
    return [render_record(record) for record in load_all_records()]


if __name__ == "__main__":
    if len(sys.argv) > 1:
        html_path, pdf_path = render(sys.argv[1])
        print(f"生成しました: {html_path}, {pdf_path}")
    else:
        results = render_all()
        print(f"{len(results)}件(HTML+PDF)生成しました: {OUTPUT_DIR}")
