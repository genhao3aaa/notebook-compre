"""给打包产物做代码签名（消除 Windows SmartScreen「未知发布者」的唯一正解）。

为什么需要它
------------
未签名的 exe：
- 「属性 → 详细信息」里的发布者是空的；
- SmartScreen 会以「Windows 已保护你的电脑 / 未知发布者」拦一下，需要点「更多信息 → 仍要运行」；
- **每次重新打包，文件哈希都会变**，之前那份攒下的声誉不会继承——所以提示会反复出现。

签名之后：发布者显示为你的证书主体，SmartScreen 不再拦（OV 证书需要累积一点下载量，
EV 证书 / Azure Trusted Signing 立即获得信任）。

用法
----
    # 1) 先打包
    pyinstaller build.spec

    # 2) 用 .pfx 证书签名（Windows SDK 的 signtool）
    .venv\\Scripts\\python.exe sign_dist.py --pfx my.pfx --password ********

    # 3) 或者用已装进证书库的证书（按指纹）
    .venv\\Scripts\\python.exe sign_dist.py --thumbprint ABCDEF...

    # 只看当前签名状态
    .venv\\Scripts\\python.exe sign_dist.py --check

自签名证书**不能**消除 SmartScreen 提示（发布者依旧不被信任），只适合内部机器分发；
正式对外发布请用商业 OV/EV 证书或 Azure Trusted Signing（约 $9.99/月，需企业身份验证）。
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys

REPO = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DIST = os.path.join(REPO, "dist", "手写笔记本与PDF批注", "手写笔记本与PDF批注.exe")
#: 常见的时间戳服务（签名时间戳保证证书过期后签名仍有效）
DEFAULT_TIMESTAMP = "http://timestamp.digicert.com"

_SDK_HINTS = (
    r"C:\Program Files (x86)\Windows Kits\10\bin",
    r"C:\Program Files\Windows Kits\10\bin",
)


def find_signtool() -> str | None:
    """在 PATH 与 Windows SDK 目录里找 signtool.exe。"""
    found = shutil.which("signtool")
    if found:
        return found
    for root in _SDK_HINTS:
        if not os.path.isdir(root):
            continue
        best = None
        for base, _dirs, files in os.walk(root):
            if "signtool.exe" in [f.lower() for f in files]:
                candidate = os.path.join(base, "signtool.exe")
                # 优先 x64
                if "x64" in base.lower():
                    return candidate
                best = best or candidate
        if best:
            return best
    return None


def signature_status(exe: str) -> str:
    """用 PowerShell 读签名状态（不依赖 signtool）。"""
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             f"(Get-AuthenticodeSignature -LiteralPath '{exe}').Status"],
            capture_output=True, text=True, timeout=60)
        return (out.stdout or "").strip() or "Unknown"
    except Exception as e:                      # pragma: no cover
        return f"查询失败：{e}"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="对 dist 里的 exe 做代码签名")
    ap.add_argument("--exe", default=DEFAULT_DIST, help="要签名的 exe（默认 dist 里的成品）")
    ap.add_argument("--pfx", help=".pfx / .p12 证书文件")
    ap.add_argument("--password", default="", help="证书口令")
    ap.add_argument("--thumbprint", help="证书指纹（证书已安装在当前用户证书库时用）")
    ap.add_argument("--timestamp", default=DEFAULT_TIMESTAMP, help="时间戳服务器（留空则不加时间戳）")
    ap.add_argument("--check", action="store_true", help="只打印签名状态")
    a = ap.parse_args(argv)

    exe = os.path.abspath(a.exe)
    if not os.path.isfile(exe):
        print(f"找不到可执行文件：{exe}\n（先执行 pyinstaller build.spec）", file=sys.stderr)
        return 2

    print(f"目标：{exe}")
    print(f"当前签名状态：{signature_status(exe)}")
    if a.check:
        return 0

    if not (a.pfx or a.thumbprint):
        print("\n没有给出证书（--pfx 或 --thumbprint），只做了状态检查。\n"
              "SmartScreen 提示的根治办法是代码签名，见本文件头部说明。", file=sys.stderr)
        return 3

    signtool = find_signtool()
    if not signtool:
        print("\n找不到 signtool.exe。安装方式（任选其一）：\n"
              "  1) 安装 Windows SDK 的「Signing Tools」组件（含 signtool）；\n"
              "  2) 用 Azure Trusted Signing 的 trusted-signing-cli；\n"
              "  3) 用 pip 安装的 pyinstaller 自带 bootloader 签名工具并不存在——必须装上面两者之一。",
              file=sys.stderr)
        return 4

    cmd = [signtool, "sign", "/fd", "SHA256", "/v"]
    if a.timestamp:
        cmd += ["/td", "SHA256", "/tr", a.timestamp]
    if a.pfx:
        cmd += ["/f", os.path.abspath(a.pfx)]
        if a.password:
            cmd += ["/p", a.password]
    else:
        cmd += ["/sha1", a.thumbprint]
    cmd.append(exe)

    print("执行：" + " ".join(cmd[:6]) + " …")
    proc = subprocess.run(cmd)
    if proc.returncode != 0:
        print(f"签名失败（退出码 {proc.returncode}）", file=sys.stderr)
        return proc.returncode

    status = signature_status(exe)
    print(f"签名完成，当前状态：{status}")
    if status != "Valid":
        print("提示：状态不是 Valid，SmartScreen 可能仍会提示——请检查证书链与时间戳。",
              file=sys.stderr)
        return 5
    return 0


if __name__ == "__main__":
    sys.exit(main())
