---
inclusion: fileMatch
fileMatchPattern: "src/**/*.py"
---

# Python の書き方

- 関数の引数と戻り値に、型ヒントを書きます。
- 例外は握りつぶしません。入力の誤りは `InvalidEntry` で伝えます。
- モジュールの先頭に、そのモジュールの役割を1行の docstring で書きます。
- 文字列の組み立ては `%` 書式にそろえます。既存のコードと混ぜないためです。
