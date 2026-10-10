"""验证打包后的 exe 能真的启动（offscreen 下跑起来且进程存活）。

GUI 程序是 console=False，没有 stdout 可看，只能靠「进程还活着」来判断：
起来之后若立刻退出，说明缺插件 / 少模块 / 初始化抛异常。

目标 exe 复用 `make_shortcut.find_target()` 的探测规则（取 exe 最新的 dist*/），
所以临时打到 dist_tmp/ 之类也能直接探到，不用改这里。
"""
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from make_shortcut import find_target  # noqa: E402  (需先插入 sys.path)

EXE = find_target(ROOT) or os.path.join(
    ROOT, "dist", "手写笔记本与PDF批注", "手写笔记本与PDF批注.exe")


def main() -> int:
    if not os.path.isfile(EXE):
        print("找不到 exe:", EXE)
        return 1
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    proc = subprocess.Popen([EXE], env=env)
    try:
        # 冻结版启动要解包 + 加载 Qt/fitz，给足时间
        for i in range(30):
            time.sleep(1)
            if proc.poll() is not None:
                print(f"进程启动后 {i + 1}s 退出，退出码={proc.returncode}")
                return 1
        print(f"ALIVE (pid {proc.pid}) ——启动成功，30s 后仍存活")
        return 0
    finally:
        try:
            proc.kill()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
