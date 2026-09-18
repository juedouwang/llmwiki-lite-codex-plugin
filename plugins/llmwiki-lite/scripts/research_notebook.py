"""Local block notebooks. Markdown is canonical; no database or external services."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import stat
import tempfile
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from llmwiki_core import LLMWikiError

LOCK = threading.RLock()
MAX_IMAGE = 10 * 1024 * 1024
MAX_DOCUMENT = 2 * 1024 * 1024
ID = re.compile(r"[a-f0-9]{32}")
IMAGE = re.compile(r"[a-f0-9]{64}\.(png|jpg|gif|webp)")
MARKER = re.compile(r"\n<!-- llmwiki-notebook-v1:([A-Za-z0-9+/=]+) -->\n\Z")


class NotebookConflict(LLMWikiError):
    """An external edit must not be overwritten."""


def safe_file(root: Path, relative: str) -> Path:
    root = root.resolve()
    candidate = root / relative
    candidate.resolve().relative_to(root)
    current = candidate
    while current != root:
        if current.is_symlink() or (
            current.exists()
            and getattr(current.lstat(), "st_file_attributes", 0)
            & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        ):
            raise LLMWikiError("不允许通过符号链接访问笔记。")
        current = current.parent
    return candidate


def _path(project: dict, note_id: str) -> Path:
    if not ID.fullmatch(note_id):
        raise LLMWikiError("笔记 ID 无效。")
    return safe_file(Path(project["wiki_root"]), f"records/manual/{note_id}.md")


def _revision(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _text(value: Any, limit: int = 100_000) -> str:
    if not isinstance(value, str) or len(value) > limit or "\x00" in value:
        raise LLMWikiError("文本格式不正确或超过长度限制。")
    return value


def validate(document: Any) -> dict:
    if not isinstance(document, dict):
        raise LLMWikiError("笔记格式无效。")
    title = (
        _text(document.get("title", ""), 200).replace("\n", " ").strip()
        or "未命名科研笔记"
    )
    tags = document.get("tags", [])
    if not isinstance(tags, list) or len(tags) > 30:
        raise LLMWikiError("标签最多 30 个。")
    tags = list(dict.fromkeys(_text(tag, 50).strip() for tag in tags if tag))
    blocks = document.get("blocks")
    if not isinstance(blocks, list) or len(blocks) > 300:
        raise LLMWikiError("笔记最多包含 300 个块。")
    clean = []
    ids = set()
    for block in blocks:
        if not isinstance(block, dict):
            raise LLMWikiError("内容块无效。")
        bid = _text(block.get("id"), 80)
        kind = block.get("type")
        if not re.fullmatch(r"[\w-]{1,80}", bid) or bid in ids:
            raise LLMWikiError("内容块 ID 重复或无效。")
        ids.add(bid)
        if kind not in {"markdown", "heading", "code", "image", "callout", "divider"}:
            raise LLMWikiError("不支持的内容块。")
        image = _text(block.get("image", ""), 100)
        if image and not IMAGE.fullmatch(image):
            raise LLMWikiError("图片引用无效。")
        comments = block.get("comments", [])
        if not isinstance(comments, list) or len(comments) > 100:
            raise LLMWikiError("每个块最多 100 条批注。")
        clean.append(
            {
                "id": bid,
                "type": kind,
                "text": _text(block.get("text", "")),
                "image": image,
                "comments": [_text(c, 10000) for c in comments],
            }
        )
    result = {"title": title, "tags": tags, "blocks": clean}
    if len(json.dumps(result, ensure_ascii=False).encode("utf-8")) > MAX_DOCUMENT:
        raise LLMWikiError("笔记文字超过 2 MB，请拆分笔记。")
    return result


def _timestamp(value: Any) -> str | None:
    """Read trusted stored metadata; missing legacy times stay unknown."""
    if value is None:
        return None
    value = _text(value, 60)
    if datetime.fromisoformat(value).utcoffset() is None:
        raise ValueError("timestamp must include timezone")
    return value


def markdown(document: dict, project_id: str) -> str:
    meta = {
        "type": "research-notebook",
        "title": document["title"],
        "recorded_at": document["created_at"],
        "updated_at": document["updated_at"],
        "tags": document["tags"],
        "project_id": project_id,
    }
    lines = [
        "---",
        *(
            f"{key}: {json.dumps(value, ensure_ascii=False)}"
            for key, value in meta.items()
        ),
        "---",
        "",
        f"# {document['title']}",
        "",
    ]
    for block in document["blocks"]:
        text = block["text"]
        kind = block["type"]
        if kind == "heading":
            lines.append("## " + text.replace("\n", " "))
        elif kind == "code":
            fence = "`" * max(
                3, max((len(m[0]) + 1 for m in re.finditer(r"`+", text)), default=3)
            )
            lines.append(f"{fence}\n{text}\n{fence}")
        elif kind == "image":
            if block["image"]:
                lines.append(f"![截图](../assets/{block['image']})")
            if text:
                lines.append(text)
        elif kind == "callout":
            lines.append("\n".join("> " + line for line in text.splitlines()))
        elif kind == "divider":
            lines.append("---")
        else:
            lines.append(text)
        for comment in block["comments"]:
            lines.append(
                "\n".join(
                    "> 批注：" + line if i == 0 else "> " + line
                    for i, line in enumerate(comment.splitlines())
                )
            )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _decode(raw: bytes) -> dict:
    try:
        if len(raw) > 8 * 1024 * 1024:
            raise ValueError("too large")
        text = raw.decode("utf-8").replace("\r\n", "\n")
        match = MARKER.search(text)
        if not match:
            raise ValueError("missing state")
        state = json.loads(base64.b64decode(match[1], validate=True))
        if state["body_sha"] != _revision(text[: match.start()].encode("utf-8")):
            raise NotebookConflict(
                "此笔记的 Markdown 已被外部修改。请保留外部内容，复制为新笔记或人工合并，不能直接覆盖。"
            )
        document = validate(state["document"])
        for key in ("created_at", "updated_at"):
            document[key] = _timestamp(state["document"].get(key))
        for block, stored in zip(document["blocks"], state["document"]["blocks"]):
            for key in ("created_at", "updated_at"):
                block[key] = _timestamp(stored.get(key))
        return document
    except NotebookConflict:
        raise
    except (ValueError, KeyError, TypeError) as exc:
        raise LLMWikiError(
            "块编辑数据损坏；原 Markdown 保持不变，请通过记录阅读页查看。"
        ) from exc


def reader_markdown(text: str, project_id: str) -> str:
    """Hide old generated time paragraphs without editing files or user text."""
    if "<!-- llmwiki-notebook-v1:" not in text:
        return text
    try:
        return markdown(_decode(text.encode("utf-8")), project_id)
    except (LLMWikiError, ValueError):
        # External edits are authoritative; never reconstruct over those edits.
        return text


def _atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".notebook-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


@contextmanager
def _file_lock(project: dict, note_id: str):
    """Serialize cooperating web servers, including dev/installed instances."""
    path = safe_file(Path(project["wiki_root"]), f".notebook-history/{note_id}/.lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        if stream.seek(0, 2) == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        if os.name == "nt":
            import msvcrt

            try:
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise LLMWikiError("另一窗口正在保存，请稍后重试。") from exc
        else:
            import fcntl

            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def load(project: dict, note_id: str) -> dict:
    with LOCK:
        path = _path(project, note_id)
        if not path.exists():
            return {
                "ok": True,
                "exists": False,
                "revision": "",
                "document": {"title": "", "tags": [], "blocks": []},
            }
        raw = path.read_bytes()
        return {
            "ok": True,
            "exists": True,
            "revision": _revision(raw),
            "document": _decode(raw),
        }


def save(project: dict, note_id: str, payload: dict) -> dict:
    document = validate(payload.get("document"))
    path = _path(project, note_id)
    with LOCK, _file_lock(project, note_id):
        old = path.read_bytes() if path.exists() else b""
        revision = _revision(old) if old else ""
        if payload.get("revision") != revision:
            raise NotebookConflict(
                "另一页面或程序已修改这篇笔记。你的内容已保留为浏览器草稿，请比较后再保存。"
            )
        previous = _decode(old) if old else None
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        document.update(
            created_at=previous["created_at"] if previous else now, updated_at=now
        )
        old_blocks = {b["id"]: b for b in previous["blocks"]} if previous else {}
        for block in document["blocks"]:
            prior = old_blocks.get(block["id"])
            changed = prior is None or any(block[k] != prior[k] for k in block)
            block.update(
                created_at=prior["created_at"] if prior else now,
                updated_at=now if changed else prior["updated_at"],
            )
            if block["image"] and not image_path(project, block["image"]).is_file():
                raise LLMWikiError("图片尚未上传完成。请稍后再保存。")
        body = markdown(document, str(project["id"]))
        state = {"document": document, "body_sha": _revision(body.encode("utf-8"))}
        encoded = base64.b64encode(
            json.dumps(state, ensure_ascii=False).encode("utf-8")
        ).decode("ascii")
        raw = (body + f"\n<!-- llmwiki-notebook-v1:{encoded} -->\n").encode("utf-8")
        if len(raw) > 4 * 1024 * 1024:
            raise LLMWikiError("笔记文件超过 4 MB，请拆分为多篇记录。")
        if old:
            history = safe_file(
                Path(project["wiki_root"]),
                f".notebook-history/{note_id}/{revision}.snapshot",
            )
            if not history.exists():
                _atomic(history, old)
        # Re-check after history write to catch non-cooperating external edits.
        current = path.read_bytes() if path.exists() else b""
        if current != old:
            raise NotebookConflict("保存期间文件发生变化，已停止覆盖。")
        _atomic(path, raw)
        return {
            "ok": True,
            "revision": _revision(raw),
            "updated_at": now,
            "timestamps": {
                "created_at": document["created_at"],
                "updated_at": now,
                "blocks": [
                    {key: b[key] for key in ("id", "created_at", "updated_at")}
                    for b in document["blocks"]
                ],
            },
            "path": f"records/manual/{note_id}.md",
        }


def history(project: dict, note_id: str, revision: str | None = None) -> dict:
    _path(project, note_id)
    root = Path(project["wiki_root"])
    if revision:
        if not re.fullmatch(r"[a-f0-9]{64}", revision):
            raise LLMWikiError("历史版本无效。")
        path = safe_file(root, f".notebook-history/{note_id}/{revision}.snapshot")
        return {"ok": True, "document": _decode(path.read_bytes())}
    folder = safe_file(root, f".notebook-history/{note_id}")
    files = sorted(
        folder.glob("*.snapshot"), key=lambda p: p.stat().st_mtime, reverse=True
    )[:50]
    versions = []
    for path in files:
        safe_file(root, path.relative_to(root).as_posix())
        doc = _decode(path.read_bytes())
        versions.append(
            {
                "revision": path.stem,
                "updated_at": doc["updated_at"],
                "title": doc["title"],
            }
        )
    return {"ok": True, "versions": versions}


def image_path(project: dict, name: str) -> Path:
    if not IMAGE.fullmatch(name):
        raise LLMWikiError("图片文件名无效。")
    return safe_file(Path(project["wiki_root"]), f"records/assets/{name}")


def upload(project: dict, payload: dict) -> dict:
    try:
        encoded = payload.get("data", "")
        if not isinstance(encoded, str) or len(encoded) > (MAX_IMAGE + 2) // 3 * 4:
            raise ValueError("size")
        raw = base64.b64decode(encoded, validate=True)
    except ValueError as exc:
        raise LLMWikiError("图片编码无效或超过 10 MB。") from exc
    if not raw or len(raw) > MAX_IMAGE:
        raise LLMWikiError("图片为空或超过 10 MB。")
    if (
        raw.startswith(b"\x89PNG\r\n\x1a\n")
        and len(raw) >= 33
        and raw[12:16] == b"IHDR"
    ):
        width, height = (
            int.from_bytes(raw[16:20], "big"),
            int.from_bytes(raw[20:24], "big"),
        )
        if not (
            0 < width <= 20000 and 0 < height <= 20000 and width * height <= 80_000_000
        ):
            raise LLMWikiError("PNG 图片尺寸过大或无效。")
        ext = "png"
    elif raw.startswith(b"\xff\xd8\xff") and raw.endswith(b"\xff\xd9"):
        ext = "jpg"
    elif raw.startswith((b"GIF87a", b"GIF89a")) and len(raw) >= 14:
        ext = "gif"
    elif raw.startswith(b"RIFF") and raw[8:12] == b"WEBP" and len(raw) >= 20:
        ext = "webp"
    else:
        raise LLMWikiError("仅接受 PNG、JPEG、GIF、WebP 图片；不接受 SVG 或 HTML。")
    name = _revision(raw) + "." + ext
    with LOCK:
        path = image_path(project, name)
        if not path.exists():
            _atomic(path, raw)
    return {"ok": True, "image": name}


def editor_page(home: str, project_id: str, note_id: str | None = None) -> str:
    from llmwiki_registry import get_project
    from research_web_ui import esc, layout, purl

    project = get_project(project_id, home=home)["project"]
    note_id = note_id or uuid4().hex
    _path(project, note_id)
    body = f'''<link rel="stylesheet" href="/static/notebook.css">
<section id="notebook" data-project="{esc(project_id)}" data-note="{note_id}">
<header class="nb-top"><a href="{purl(project_id)}/records">← 科研记录</a><span id="nb-status" role="status" aria-live="polite">正在打开…</span><div class="nb-actions"><button id="nb-save" title="保存（Ctrl S）">保存</button><button id="nb-undo" disabled>撤销</button><details class="action-menu"><summary aria-label="笔记菜单">更多</summary><div class="action-menu-items"><button id="nb-info">笔记信息</button><button id="nb-history">版本历史</button><button id="nb-export">导出 Markdown</button><button id="nb-focus">专注模式</button><p class="meta">Ctrl V 粘贴截图<br>Ctrl Enter 新增文本块</p></div></details></div></header>
<div id="nb-banner" class="nb-banner" role="alert" hidden></div>
<div class="nb-paper"><label class="nb-sr" for="nb-title">笔记标题</label><input id="nb-title" maxlength="200" placeholder="给这次探索一个标题" autocomplete="off">
<label class="nb-sr" for="nb-tags">标签，以逗号分隔</label><input id="nb-tags" placeholder="添加标签，以逗号分隔">

<div id="nb-blocks"></div><div id="nb-empty" hidden>直接粘贴截图，或在下方开始记录。</div><div id="nb-bottom"></div></div>

<input id="nb-file" type="file" accept="image/png,image/jpeg,image/gif,image/webp" multiple hidden>
<dialog id="nb-dialog"><div id="nb-dialog-content"></div><button id="nb-dialog-close">关闭</button></dialog>
</section><script src="/static/notebook.js" defer></script>'''
    return layout(
        "手动科研笔记 · " + str(project["name"]),
        body,
        project_id=project_id,
        active="records",
        home=home,
    )
