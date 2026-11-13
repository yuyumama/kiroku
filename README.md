# kiroku

Kiro CLI V3／IDE 1.xでの開発中に起きた失敗や手直しを記録し、ハーネスの改善案を出すための仕組みです。

- 使い方：[docs/retro/README.md](docs/retro/README.md)
- 評価手順と未確認事項：[docs/retro/evaluation.md](docs/retro/evaluation.md)
- 互換性の調査：[docs/retro/compatibility.md](docs/retro/compatibility.md)
- 過去の検証結果：[docs/retro/verification.md](docs/retro/verification.md)

| 場所 | 中身 |
|---|---|
| `kit/` | 導入先のリポジトリにコピーするファイル |
| `install.sh`、`install.ps1`、`install.py` | POSIX／PowerShell の導入スクリプトと共通処理 |
| `uninstall.sh`、`uninstall.ps1`、`uninstall.py` | 取り除くスクリプトと共通処理 |
| `examples/demo/` | 動作確認用のサンプル |
| `tests/` | テスト |
| `probe/` | 段階1で使った検証キット |
