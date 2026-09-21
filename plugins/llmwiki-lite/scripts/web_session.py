"""Ephemeral browser project context, independent of the agent registry selection."""
from contextvars import ContextVar
from http.cookies import CookieError, SimpleCookie
from urllib.parse import unquote

_context = ContextVar("workbench_web_context", default=None)
COOKIE = "llmwiki_web_project"


def current_project():
    context = _context.get()
    return context[0] if context else None


def session_id():
    context = _context.get()
    return context[1] if context else ""


def enter(project_id, epoch):
    return _context.set((project_id, epoch))


def leave(token):
    _context.reset(token)


def cookie_project(header, epoch):
    try:
        cookies = SimpleCookie()
        cookies.load(header or "")
        value = cookies[COOKIE].value if COOKIE in cookies else ""
        prefix = epoch + "."
        return unquote(value[len(prefix):]) if value.startswith(prefix) else None
    except (CookieError, ValueError):
        return None
