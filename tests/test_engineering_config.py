# -*- coding: utf-8 -*-
"""tests/test_engineering_config.py —— 守护"工程配置自身"的元测试。

★ 为什么要有这一类测试：
  这个仓库踩过一类很隐蔽的坑 —— **配置文件本身会腐烂**：
    · Makefile 引用了被移动走的 demo 文件，make demo 静默失败
    · demo 的 sys.path 插入错了目录层级，从仓库根运行必然 ModuleNotFoundError
    · tests/ 里混进真机脚本（顶层 import dotenv），裸环境必然 ImportError
    · 文件被写成 CRLF，在 Linux CI 上出各种诡异问题

  这些都**不会被业务测试发现**（业务测试只测逻辑），所以必须有元测试盯着。
"""
import io
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(rel: str) -> str:
    with io.open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


class TestMakefile(unittest.TestCase):
    def test_references_existing_demos(self):
        """make demo 里列的 demo 必须真实存在，否则会静默跳过。"""
        names = re.findall(r"\b(demo_[a-z0-9_]+)\b", read("Makefile"))
        self.assertTrue(names, "Makefile 里没找到任何 demo 引用")
        for n in sorted(set(names)):
            self.assertTrue(
                os.path.exists(os.path.join(ROOT, "demos", f"{n}.py")),
                f"Makefile 引用了不存在的 demos/{n}.py")

    def test_demos_insert_repo_root(self):
        """★ 每个 demo 的 sys.path 必须指向**仓库根**，而不是 demo 自己所在的目录。

        踩过的坑：demo 从根目录移进 demos/ 之后，sys.path 插入的仍是
        demos/ 自己，于是从仓库根运行全部 ModuleNotFoundError。
        这里按 dirname 的嵌套层数反推解析结果，确保它等于仓库根。
        """
        import glob
        checked = 0
        for path in sorted(glob.glob(os.path.join(ROOT, "demos", "**", "*.py"),
                                     recursive=True)):
            src = io.open(path, encoding="utf-8").read()
            m = re.search(r"sys\.path\.insert\(0,\s*(.+?)\)\s*$", src, re.M)
            if not m:
                continue
            expr = m.group(1)
            depth = expr.count("os.path.dirname")
            if not depth:
                continue  # 显式写死路径的写法不适用本规则
            resolved = os.path.abspath(path)
            for _ in range(depth):
                resolved = os.path.dirname(resolved)
            self.assertEqual(
                os.path.abspath(resolved), os.path.abspath(ROOT),
                f"{os.path.relpath(path, ROOT)} 的 sys.path 解析到 "
                f"{resolved}，应为仓库根 {ROOT}")
            checked += 1
        self.assertGreater(checked, 0, "没有任何 demo 被检查到，规则可能失效了")


class TestTestsAreOffline(unittest.TestCase):
    def test_no_toplevel_dotenv_import(self):
        """★ tests/ 下不允许顶层 import dotenv。

        踩过的坑：一个 88 行的真机冒烟脚本被放进 tests/，unittest discover
        收集后在没装 dotenv 的环境里直接 ImportError。
        真机脚本应放 demos/live/，并做惰性导入。
        """
        import glob
        for path in glob.glob(os.path.join(ROOT, "tests", "*.py")):
            src = io.open(path, encoding="utf-8").read()
            for line in src.splitlines():
                if line.startswith(("import dotenv", "from dotenv")):
                    self.fail(f"{os.path.basename(path)} 顶层导入了 dotenv，"
                              f"测试必须在裸环境可跑")

    def test_tests_never_require_api_key(self):
        """测试不允许依赖真实 API Key —— 否则 CI 必红。"""
        import glob
        self_path = os.path.abspath(__file__)
        for path in glob.glob(os.path.join(ROOT, "tests", "*.py")):
            if os.path.abspath(path) == self_path:
                continue  # 本文件只是把规则写进文档字符串，不算引用
            src = io.open(path, encoding="utf-8").read()
            self.assertNotIn("DEEPSEEK_API_KEY", src,
                             f"{os.path.basename(path)} 引用了真实 API Key")


class TestLineEndings(unittest.TestCase):
    def test_no_crlf_in_sources(self):
        """CRLF 在 Linux CI 上会引发各种诡异问题，源码统一 LF。"""
        import glob
        bad = []
        for path in glob.glob(os.path.join(ROOT, "**", "*.py"), recursive=True):
            if "__pycache__" in path:
                continue
            if b"\r\n" in io.open(path, "rb").read():
                bad.append(os.path.relpath(path, ROOT))
        self.assertEqual(bad, [], f"以下文件是 CRLF：{bad}")


class TestProjectFiles(unittest.TestCase):
    def test_license_present(self):
        self.assertTrue(os.path.exists(os.path.join(ROOT, "LICENSE")))

    def test_gitignore_blocks_env(self):
        """★ .env 必须被忽略 —— 这是密钥防护的第一道闸。"""
        self.assertIn(".env", read(".gitignore"))

    def test_pyproject_packages_match_layout(self):
        """pyproject 声明的包必须真实存在，否则 pip install -e . 会装空包。"""
        content = read("pyproject.toml")
        m = re.search(r'packages\s*=\s*\[(.*?)\]', content, re.S)
        self.assertTrue(m, "pyproject.toml 里没找到 packages 声明")
        for pkg in re.findall(r'"([^"]+)"', m.group(1)):
            self.assertTrue(os.path.isdir(os.path.join(ROOT, pkg)),
                            f"pyproject 声明了不存在的包 {pkg}")


if __name__ == "__main__":
    unittest.main()
