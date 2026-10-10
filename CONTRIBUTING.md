# 贡献指南

## 环境要求
- 平台：Windows 10 / 11
- Python 3.10+（CI 使用 3.12）
- Rust（可选）：`rustup default stable-x86_64-pc-windows-msvc`
- 依赖：`pip install -r requirements.txt pytest ruff pyright`

## 构建 Rust 核心（可选）
未编译时程序会自动回退到纯 Python 实现，功能可用但性能较低。

```bash
pip install maturin
python build_rust.py
