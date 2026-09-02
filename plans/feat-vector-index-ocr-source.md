# plan: ベクトルインデックスをOCR由来データに対応させる

Issue: https://github.com/yuninaka/graph-rag-maintenance-demo/issues/2

## 目的

`build_knowledge_graph.py` は `MAINTENANCE_LOGS_PATH` 環境変数でOCR由来データ
(`data/maintenance_logs_ocr.jsonl`)に差し替え可能だが、`build_vector_index.py`
は合成データ固定のまま。ベクトル側もOCR由来データに対応させ、
「紙の書類→OCR→ベクトルRAG」の精度を実測する。

## 変更内容

1. `src/build_vector_index.py`
   - `DATA_PATH` を `build_knowledge_graph.py` と同じパターンで
     `MAINTENANCE_LOGS_PATH` 環境変数から差し替え可能にする
   - `PERSIST_DIR` もデータソースに応じて分ける(合成データ版とOCR版を
     混在させず、比較検証をやり直しやすくするため。例:
     `chroma_db_ocr/` を別ディレクトリにする環境変数 `CHROMA_PERSIST_DIR`)

2. 検証手順
   - 合成データで通常通りインデックス構築 → `evaluate.py` でベースラインスコア確認
   - OCR由来データ(`data/maintenance_logs_ocr.jsonl`、既存の
     `scripts/ocr_to_records.py` の出力をそのまま使う)でインデックス構築
   - グラフ側もOCR由来データに揃えて `evaluate.py` を実行し、
     ベクトル単体/グラフ単体/全体のスコアを合成データ版と比較
   - R012(「O→〇」誤読)・R013(チェックボックス誤検出)がベクトル検索
     (埋め込み類似度)にどう影響したかを個別に確認する
     (`logs/vector_search.jsonl` を突き合わせる)

3. ドキュメント更新
   - `docs/troubleshooting_log.md` に検証結果を追記
   - `README.md`「パイプライン完成」節を更新(ベクトル側も接続済みである旨)

4. Zenn記事第6弾(`zenn-content`リポジトリ、別PR)
   - 上記の実測結果をまとめる

## スコープ外

- OCR生成スクリプト自体の変更
- 第5弾記事の書き換え・タグ移動
