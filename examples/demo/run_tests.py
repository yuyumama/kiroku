#!/usr/bin/env python3
"""動作確認用のテスト実行。unittest を走らせ、結果を reports/junit.xml に JUnit XML で書く。

追加のパッケージは要らない。実際のリポジトリでは、使っているテストツールの
JUnit XML 出力（pytest の --junitxml など）を使うこと。
"""
import os
import sys
import unittest
from xml.sax.saxutils import quoteattr

ROOT = os.path.dirname(os.path.abspath(__file__))


class Collect(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.rows = []

    def _row(self, test, inner):
        module = sys.modules.get(type(test).__module__)
        source = os.path.relpath(getattr(module, "__file__", "") or "", ROOT)
        self.rows.append('<testcase classname=%s name=%s file=%s>%s</testcase>' % (
            quoteattr("%s.%s" % (type(test).__module__, type(test).__name__)),
            quoteattr(test._testMethodName), quoteattr(source), inner))

    def addSuccess(self, test):
        super().addSuccess(test)
        self._row(test, "")

    def addFailure(self, test, err):
        super().addFailure(test, err)
        self._row(test, "<failure message=%s/>" % quoteattr(str(err[1])[:200]))

    def addError(self, test, err):
        super().addError(test, err)
        if hasattr(test, "_testMethodName"):
            self._row(test, "<error message=%s/>" % quoteattr(str(err[1])[:200]))

    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        self._row(test, "<skipped/>")


def main():
    sys.dont_write_bytecode = True  # 直後の編集が古いキャッシュで隠れないようにする
    sys.path.insert(0, ROOT)
    suite = unittest.defaultTestLoader.discover(os.path.join(ROOT, "tests"), top_level_dir=ROOT)
    result = unittest.TextTestRunner(resultclass=Collect, verbosity=2).run(suite)
    os.makedirs(os.path.join(ROOT, "reports"), exist_ok=True)
    with open(os.path.join(ROOT, "reports", "junit.xml"), "w", encoding="utf-8") as handle:
        handle.write('<?xml version="1.0" encoding="utf-8"?>\n<testsuites><testsuite name="demo">%s'
                     '</testsuite></testsuites>\n' % "".join(result.rows))
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
