---
name: kiroku-probe
description: kiroku の検証用。スラッシュコマンドとして呼べるかを確かめるためだけの skill。ユーザーが /kiroku-probe と入力したときに使う。
---

# kiroku-probe

この skill が読み込まれたら、次の1行だけを返して終わること。ほかの作業はしない。

```
KIROKU-PROBE-SKILL-LOADED
```

`/kiroku-probe` の後ろに文字が続いていた場合は、2行目にその文字をそのまま書くこと。
