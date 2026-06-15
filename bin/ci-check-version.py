"""CNB CI: 验证 tag 与 VERSION 文件一致"""
import os, sys

tag = (sys.argv[1] or "").lstrip("v")
version = open("VERSION").read().strip()

if tag != version:
    print(f"❌ Tag '{tag}' != VERSION '{version}'")
    sys.exit(1)
print(f"✅ Tag {tag} matches VERSION")
