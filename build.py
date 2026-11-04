#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
把 content/ 里的文章与照片，编译成页面要读的两个数据文件。

    文章：content/posts/0001-2026-11-04-标题.md   →  data/posts.js
    照片：content/photos/0001-标题.md             →  data/photos.js

只做三件事：读、校验、写。不碰 HTML，不碰样式。
改完内容跑一次 `python build.py` 就行。

不用装任何东西：图片宽高由 build.py 自己读文件头。
"""

import json
import os
import re
import struct
import sys
from datetime import date as _date

ROOT = os.path.dirname(os.path.abspath(__file__))
POSTS_DIR = os.path.join(ROOT, "content", "posts")
PHOTOS_DIR = os.path.join(ROOT, "content", "photos")
DATA_DIR = os.path.join(ROOT, "data")

CATS = ["摄像", "软件", "设计", "技术", "生活", "阅读"]

errors = []
notes = []


def err(msg):
    errors.append(msg)


def note(msg):
    notes.append(msg)


# ── 读文件 ──────────────────────────────────────────────

def read_text(path):
    with open(path, encoding="utf-8-sig") as f:
        return f.read().replace("\r\n", "\n")


def split_front_matter(text):
    """开头的 --- ... --- 是字段，剩下的是正文。没有字段也能用。"""
    if not text.startswith("---"):
        return {}, text.strip()
    m = re.match(r"^---\n(.*?)\n---\n?(.*)$", text, re.S)
    if not m:
        return None, text
    raw, body = m.group(1), m.group(2)
    meta = {}
    for line in raw.split("\n"):
        line = line.rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        meta[k.strip().lower()] = v.strip()
    return meta, body.strip()


def paragraphs(body):
    """空行分段。段内换行按两边字符决定补不补空格：中文直接接上，英文补一个。"""
    out = []
    for block in re.split(r"\n\s*\n", body):
        lines = [ln.strip() for ln in block.strip().split("\n") if ln.strip()]
        if not lines:
            continue
        buf = lines[0]
        for ln in lines[1:]:
            need_space = bool(re.search(r"[A-Za-z0-9,.!?;:)\]'\"”』）]$", buf)) and bool(
                re.match(r"[A-Za-z0-9(\['\"“‘（《]", ln)
            )
            buf += (" " if need_space else "") + ln
        out.append(buf)
    return out


# ── 图片尺寸：只读文件头，不依赖 Pillow ──────────────────

def img_size(path):
    try:
        with open(path, "rb") as f:
            head = f.read(32)
            if head[:8] == b"\x89PNG\r\n\x1a\n":
                w, h = struct.unpack(">II", head[16:24])
                return w, h
            if head[:2] == b"\xff\xd8":
                f.seek(2)
                while True:
                    b = f.read(1)
                    while b and b != b"\xff":
                        b = f.read(1)
                    while b == b"\xff":
                        b = f.read(1)
                    if not b:
                        return None
                    marker = b[0]
                    if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
                        continue
                    seg_len = struct.unpack(">H", f.read(2))[0]
                    if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                        f.read(1)
                        h, w = struct.unpack(">HH", f.read(4))
                        return w, h
                    f.seek(seg_len - 2, 1)
    except OSError:
        return None
    return None


# ── 文章 ────────────────────────────────────────────────

def load_posts():
    if not os.path.isdir(POSTS_DIR):
        return []
    posts = []
    seen = {}
    for name in sorted(os.listdir(POSTS_DIR)):
        if not name.endswith(".md") or name.startswith("_"):
            continue
        path = os.path.join(POSTS_DIR, name)
        m = re.match(r"^(\d{1,4})-(\d{4}-\d{2}-\d{2})-(.+)\.md$", name)
        if not m:
            err("文章文件名不对：%s（要 0001-2026-11-04-标题.md）" % name)
            continue
        num, fdate, _slug = m.groups()
        no = str(int(num)).zfill(2)

        meta, body = split_front_matter(read_text(path))
        if meta is None:
            err("%s：开头的 --- 没有配对" % name)
            continue

        d = meta.get("date", fdate)
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", d):
            err("%s：date 要写 2026-11-04" % name)
            continue
        try:
            _date.fromisoformat(d)
        except ValueError:
            err("%s：date 不是有效日期（%s）" % (name, d))
            continue

        cat = meta.get("cat", "")
        if cat not in CATS:
            err("%s：cat 只能是 %s，现在是「%s」" % (name, " / ".join(CATS), cat))
            continue

        tags = [t.strip() for t in re.split(r"[,，、]", meta.get("tags", cat)) if t.strip()]
        title = meta.get("title", "").strip()
        desc = meta.get("desc", "").strip()
        paras = paragraphs(body)

        if not title:
            err("%s：缺 title" % name)
        if not desc:
            err("%s：缺 desc（卡片上那一句）" % name)
        if not paras:
            err("%s：正文是空的" % name)
        if no in seen:
            err("编号 %s 重复：%s 与 %s" % (no, seen[no], name))
        seen[no] = name

        posts.append({
            "no": no,
            "date": d,
            "cat": cat,
            "tags": tags or [cat],
            "title": title,
            "desc": desc,
            "body": paras,
            "_file": name,
        })

    posts.sort(key=lambda p: (p["date"], p["no"]), reverse=True)
    return posts


# ── 照片 ────────────────────────────────────────────────

def parse_specs(raw):
    out = []
    for item in re.split(r"[,，]", raw):
        item = item.strip()
        if not item:
            continue
        if "|" in item:
            b, t = item.split("|", 1)
        else:
            b, t = re.match(r"^([\d./]+[A-Za-z/]*)\s*(.*)$", item).groups() if re.match(
                r"^([\d./]+[A-Za-z/]*)\s*(.*)$", item) else (item, "")
        out.append({"b": b.strip(), "t": t.strip()})
    return out


def load_photos():
    if not os.path.isdir(PHOTOS_DIR):
        return []
    photos = []
    for name in sorted(os.listdir(PHOTOS_DIR)):
        if not name.endswith(".md") or name.startswith("_"):
            continue
        meta, body = split_front_matter(read_text(os.path.join(PHOTOS_DIR, name)))
        if meta is None:
            err("%s：开头的 --- 没有配对" % name)
            continue

        rel = meta.get("file", "").replace("\\", "/")
        if not rel:
            err("%s：缺 file（图片路径）" % name)
            continue
        full = os.path.join(ROOT, rel)
        if not os.path.isfile(full):
            err("%s：找不到图片 %s" % (name, rel))
            continue

        w, h = img_size(full) or (None, None)
        mw = meta.get("width")
        mh = meta.get("height")
        if mw and mh:
            w, h = int(mw), int(mh)
        if not w or not h:
            err("%s：读不出图片宽高，请在字段里补 width / height" % name)

        specs = parse_specs(meta.get("specs", ""))
        if not specs and w and h:
            specs = [{"b": "%d × %d" % (w, h), "t": "像素"}]

        photos.append({
            "file": rel,
            "title": meta.get("title", "").strip(),
            "date": meta.get("date", "").strip(),
            "alt": meta.get("alt", "").strip(),
            "wide": meta.get("wide", "").lower() in ("1", "true", "yes", "是"),
            "desc": " ".join(paragraphs(body)),
            "w": w,
            "h": h,
            "specs": specs,
            "_file": name,
        })

        if not photos[-1]["title"]:
            err("%s：缺 title" % name)
        if not photos[-1]["alt"]:
            err("%s：缺 alt（读屏的人靠它看见这张照片）" % name)

    return photos


# ── 写 ──────────────────────────────────────────────────

def write_js(path, var, items, tagline, compact=False):
    if compact:
        payload = "[\n  " + ",\n  ".join(
            json.dumps(it, ensure_ascii=False, separators=(", ", ": ")) for it in items
        ) + "\n]"
    else:
        payload = json.dumps(items, ensure_ascii=False, indent=2)
    payload = payload.replace("</", "<\\/")   # 别让正文里的 </script> 提前收尾
    head = "/* 由 build.py 从 content/ 生成，不要手改。%s */\n" % tagline
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(head)
        f.write("window.%s = %s;\n" % (var, payload))


def main():
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass

    posts = load_posts()
    photos = load_photos()

    if errors:
        print("\n生成中止，先改这几处：")
        for e in errors:
            print("  × " + e)
        return 1

    os.makedirs(DATA_DIR, exist_ok=True)

    export_posts = [{k: v for k, v in p.items() if not k.startswith("_")} for p in posts]
    export_photos = [{k: v for k, v in ph.items() if not k.startswith("_")} for ph in photos]
    write_js(os.path.join(DATA_DIR, "posts.js"), "POSTS", export_posts,
             "加文章去 content/posts/，然后跑 python build.py")
    write_js(os.path.join(DATA_DIR, "photos.js"), "PHOTOS", export_photos,
             "加照片去 content/photos/，然后跑 python build.py", compact=True)

    total_chars = sum(len("".join(p["body"]).replace(" ", "")) for p in export_posts)
    print("文章 %d 篇，共 %d 字" % (len(posts), total_chars))
    for p in posts:
        print("  %s  %s  %-4s %s（%d 段）" % (p["no"], p["date"], p["cat"], p["title"], len(p["body"])))
    print("照片 %d 张" % len(photos))
    for ph in photos:
        print("  %s  %s  %s×%s" % (ph["title"], ph["date"], ph["w"], ph["h"]))
    for n in notes:
        print("  · " + n)
    print("\n写入 data/posts.js（%.1f KB）与 data/photos.js（%.1f KB）" % (
        os.path.getsize(os.path.join(DATA_DIR, "posts.js")) / 1024,
        os.path.getsize(os.path.join(DATA_DIR, "photos.js")) / 1024,
    ))
    return 0


if __name__ == "__main__":
    sys.exit(main())
