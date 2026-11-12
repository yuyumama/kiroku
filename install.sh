#!/bin/sh
# kiroku を導入先のリポジトリにコピーする。
#   使い方: sh install.sh <導入先のリポジトリ> [--update]
# 既存のファイルは上書きしない。同じ名前で中身の違うファイルがあれば、何もせずに止まる。
# --update を付けると kiroku のファイルを新しい版で置き換える（config.json は置き換えない）。
set -eu

if [ $# -lt 1 ]; then
  echo "使い方: sh $0 <導入先のリポジトリ> [--update]" >&2
  exit 2
fi
target=$1
update=${2:-}
kit=$(cd "$(dirname "$0")/kit" && pwd)

if [ ! -d "$target" ]; then
  echo "導入先が見つかりません: $target" >&2
  exit 1
fi
if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 が見つかりません。kiroku のフックは python3 で動きます。" >&2
  exit 1
fi

files=$(cd "$kit" && find .kiro -type f ! -name '*.pyc' | sort)

conflicts=""
for file in $files; do
  # config.json は導入先のものなので、違っていても衝突とみなさない。
  if [ "$file" != ".kiro/kiroku/config.json" ] && [ "$update" != "--update" ] \
    && [ -e "$target/$file" ] && ! cmp -s "$kit/$file" "$target/$file"; then
    conflicts="$conflicts $file"
  fi
done
if [ -n "$conflicts" ]; then
  echo "次のファイルが既にあり、中身が違います。何も変更していません。" >&2
  for file in $conflicts; do echo "  $file" >&2; done
  echo "kiroku の更新なら --update を付けてください。別物なら名前の衝突を解消してください。" >&2
  exit 1
fi

for file in $files; do
  if [ -e "$target/$file" ] && [ "$file" = ".kiro/kiroku/config.json" ]; then
    echo "そのまま  $file"
    continue
  fi
  if [ -e "$target/$file" ] && cmp -s "$kit/$file" "$target/$file"; then
    continue
  fi
  mkdir -p "$(dirname "$target/$file")"
  cp "$kit/$file" "$target/$file"
  echo "コピー    $file"
done

# 旧版の記録先（.kiro/retro/）が残っていれば、新しい場所へ移す。
if [ -d "$target/.kiro/retro" ] && [ ! -e "$target/.kiro/kiroku/data" ]; then
  mv "$target/.kiro/retro" "$target/.kiro/kiroku/data"
  echo "移動      .kiro/retro/ → .kiro/kiroku/data/"
fi

ignore="$target/.gitignore"
if grep -qxF '.kiro/retro/' "$ignore" 2>/dev/null; then
  sed 's#^\.kiro/retro/$#.kiro/kiroku/data/#' "$ignore" > "$ignore.kiroku-tmp"
  mv "$ignore.kiroku-tmp" "$ignore"
  echo "書き換え  .gitignore（.kiro/retro/ → .kiro/kiroku/data/）"
fi
if ! grep -qxF '.kiro/kiroku/data/' "$ignore" 2>/dev/null; then
  if [ -s "$ignore" ] && [ -n "$(tail -c 1 "$ignore")" ]; then echo >> "$ignore"; fi
  printf '%s\n' '# kiroku の生データ。コードや認証情報が混ざりうるためコミットしない。' '.kiro/kiroku/data/' >> "$ignore"
  echo "追記      .gitignore（.kiro/kiroku/data/）"
fi

echo "完了。テスト結果も記録するには .kiro/kiroku/config.json の tests を設定してください。"
