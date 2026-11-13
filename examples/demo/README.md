# kakeibo

支出を記録し、月ごとに集計する家計簿の CLI です。Python の標準ライブラリだけで動きます。

## 使い方

```bash
python3 -m kakeibo add 2026-11-03 食費 1280 昼食
python3 -m kakeibo list
python3 -m kakeibo report 2026-11
```

記録は `data/kakeibo.json` に保存されます。別のファイルを使うときは `--file` を付けます。

## テスト

```bash
python3 run_tests.py
```

結果は画面に出るほか、`reports/junit.xml` にも書かれます。

## 構成

| 場所 | 中身 |
|---|---|
| `kakeibo/models.py` | 支出1件の形と、入力の検査 |
| `kakeibo/storage.py` | JSON ファイルへの保存 |
| `kakeibo/report.py` | 月ごとの集計 |
| `kakeibo/cli.py` | コマンドライン |
| `tests/` | テスト |
| `scripts/` | フックから呼ばれる検査 |
