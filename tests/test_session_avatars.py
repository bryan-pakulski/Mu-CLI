"""Session avatars (components/avatar.css): the web helper's monogram and pigment rules match
the Android SessionAvatar table (mobile/android/__tests__/session-avatar.test.tsx)."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TABLE = [("Harvest-House", "HH", "rose"), ("codex", "Co", "apricot"), ("general", "Ge", "lagoon"),
         ("pakulski.dev", "PD", "plum"), ("rtdetr", "Rt", "meadow"), ("wavebathy", "Wa", "lilac"),
         ("myCoolApp", "MC", "rose"), ("", "?", "lagoon")]


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_web_monogram_and_pigment_match_the_shared_table():
    src = (ROOT / "mu/gui/static/js/app.js").read_text(encoding="utf-8")
    start, end = src.index("const MUCLI_AVATAR_PIGMENTS"), src.index("// MUCLI_CONTAINER_PERSISTENCE_V1")
    script = ("global.window = globalThis;\n" + src[start:end] + "\nconst names = " + json.dumps([n for n, _m, _p in TABLE])
              + ";\nconsole.log(JSON.stringify(names.map(n => [n, window.sessionMonogram(n), window.sessionPigment(n)])));\n"
              + "console.log(window.sessionAvatarMarkup('a<b>', 'chat', 'lg'));")
    out = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True).stdout.splitlines()
    assert [tuple(row) for row in json.loads(out[0])] == TABLE
    markup = out[1]
    # Mu revision 2: the avatar is a flat tile with the type's Wash line icon (the
    # monogram/pigment rules above stay shared with Android); names never reach the markup.
    assert 'data-type="chat"' in markup and 'data-size="lg"' in markup and "wash.svg#i-message-circle" in markup
    assert "data-pigment" not in markup and "a<b>" not in markup and "<b>" not in markup


def test_avatar_component_is_layered_and_used_by_every_session_list():
    wash = (ROOT / "mu/gui/static/css/wash/wash.css").read_text(encoding="utf-8")
    assert '@import url("components/avatar.css") layer(components);' in wash
    css = (ROOT / "mu/gui/static/css/wash/components/avatar.css").read_text(encoding="utf-8")
    # Flat (Mu revision 2): a hairline tile, no pigment gradient, no monogram.
    assert "box-shadow: inset 0 0 0 1px var(--wash-hairline);" in css
    assert "data-pigment" not in css and "gradient" not in css
    # Mu §3.2: the rail lists sessions as plain lines (no avatars); the type
    # shows as the Wash line icon and on hover. Every other session list keeps the avatar.
    index = (ROOT / "mu/gui/templates/index.html").read_text()
    assert "sessionAvatarMarkup" not in index
    assert ":data-session-type=\"s.session_type || 'workspace'\"" in index
    welcome = (ROOT / "mu/gui/templates/fragments/welcome.html").read_text()
    assert "sessionAvatarMarkup(s.name, s.session_type, 'lg')" in welcome and "sessionGlyph" not in welcome
    drawer = (ROOT / "mobile/android/src/components/SwipeSessionsDrawer.tsx").read_text()
    assert "<SessionAvatar" in drawer
