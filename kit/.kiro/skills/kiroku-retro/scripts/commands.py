"""実行せずに、単純なシェル入力から実コマンドを取り出す。

対応は単一コマンド、先頭の cd DIR &&、末尾のリダイレクト。
heredoc・展開・パイプ・複合構文は推測せず None を返す。
"""
import hashlib
import os
import re
import shlex

TOKEN = re.compile(r'''\s*(?:(?P<op>&&|\|\||>>|>&|<&|[;&|<>()])|(?P<word>(?:[^\s'"\\;&|<>()]+|\\.|'[^']*'|"(?:\\.|[^"\\])*")+))''')


def parse(command):
    if not isinstance(command, str) or any(c in command for c in ('\n', '\r', '$', '`')):
        return None
    parts, position, last_raw, last_end = [], 0, '', -1
    try:
        while position < len(command.rstrip()):
            match = TOKEN.match(command, position)
            if not match:
                return None
            position = match.end()
            if match.group('op'):
                if match.group('op') in ('>', '>>', '<', '>&', '<&') \
                        and last_raw.isdigit() and last_end == match.start('op'):
                    parts.pop()  # 空白なしの 2> だけがファイル記述子。echo 2 > は引数。
                parts.append(('op', match.group('op')))
                last_raw = ''
            else:
                raw = match.group('word')
                words = shlex.split(raw, comments=False)
                if len(words) != 1 or raw.startswith('#'):
                    return None
                parts.append(('word', words[0]))
                last_raw, last_end = raw, match.end()
    except ValueError:
        return None
    directory = None
    if parts and parts[0] == ('word', 'cd'):
        count = 3 if len(parts) > 1 and parts[1] == ('word', '--') else 2
        if len(parts) <= count or parts[count] != ('op', '&&') or parts[count - 1][0] != 'word':
            return None
        directory = parts[count - 1][1]
        parts = parts[count + 1:]
    argv, index, redirected = [], 0, False
    while index < len(parts):
        kind, value = parts[index]
        if kind == 'word' and not redirected:
            argv.append(value)
            index += 1
            continue
        if kind != 'op' or value not in ('>', '>>', '<', '>&', '<&'):
            return None
        if index + 1 >= len(parts) or parts[index + 1][0] != 'word':
            return None
        redirected = True
        index += 2
    if not argv or '=' in argv[0]:
        return None
    return {'argv': argv, 'directory': directory}


def key(command):
    parsed = parse(command)
    if parsed:
        return shlex.join(parsed['argv'])
    # 不明な入力同士を先頭の数語だけで混ぜない。長い本文はキーに複製しない。
    return 'unparsed:' + hashlib.sha256((command or '').encode('utf-8')).hexdigest()


def invocation(command):
    """テスト検出に使う起動先。echo や検索の引数をランナーと誤認しない。"""
    parsed = parse(command)
    if not parsed:
        return None
    argv = parsed['argv']
    name = os.path.basename(argv[0].replace('\\', '/'))
    count = 1
    if re.fullmatch(r'(?:python(?:\d+(?:\.\d+)*)?|py)(?:\.exe)?', name):
        index = 1
        while index < len(argv) and argv[index] in ('-3', '-B', '-u', '-I', '-E', '-s', '-S'):
            index += 1
        if index >= len(argv) or argv[index] in ('-c', '-'):
            return None
        count = index + (2 if argv[index] == '-m' else 1)
        if count > len(argv) or (argv[index].startswith('-') and argv[index] != '-m'):
            return None
    elif name in ('make', 'npm', 'pnpm', 'yarn', 'npx', 'cargo', 'go'):
        count = min(len(argv), 3 if len(argv) > 1 and argv[1] == 'run' else 2)
    return shlex.join(argv[:count])


def test_entry(command, config):
    target = invocation(command)
    if target is None:
        return None
    return next((entry for entry in config['tests']
                 if re.search(entry.get('match', '(?!)'), target)), None)


def own_script(command):
    target = invocation(command)
    parsed = parse(target) if target else None
    if not parsed:
        return None
    argv = parsed['argv']
    if len(argv) < 2 or not re.fullmatch(r'python(?:\d+(?:\.\d+)*)?|py', os.path.basename(argv[0])):
        return None
    path = argv[-1].replace('\\', '/')
    prefix = '.kiro/skills/kiroku-retro/scripts/'
    if path.startswith(prefix) and '/' not in path[len(prefix):]:
        return path[len(prefix):]
    return None
