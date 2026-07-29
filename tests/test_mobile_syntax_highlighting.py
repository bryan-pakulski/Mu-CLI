"""Regression tests for mobile code block syntax highlighting.

Verifies:
1. tokens.ts has ``syntax`` color palette in both light + dark themes.
2. CodeBlock component exists, exports, and has a tokenizer.
3. ChatScreen imports CodeBlock and uses it in fence/code_block rules.
4. code_inline uses ``colors.syntax.keyword``.
5. CodeBlock supports multiple languages (python, js, bash, diff, yaml, plain).
6. No new npm dependencies added (zero-bloat requirement).
"""

from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
MOBILE = ROOT / "mobile" / "android" / "src"
TOKENS = MOBILE / "theme" / "tokens.ts"
CODEBLOCK = MOBILE / "components" / "CodeBlock.tsx"
CHATSCREEN = MOBILE / "screens" / "ChatScreen.tsx"
INDEX = MOBILE / "components" / "index.ts"
PKG = ROOT / "mobile" / "android" / "package.json"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ── tokens.ts ─────────────────────────────────────────────────────────────


def test_tokens_has_syntax_type():
    """SyntaxColors type must be defined in tokens.ts."""
    src = read(TOKENS)
    assert "export type SyntaxColors" in src, "SyntaxColors type not found"


def test_tokens_has_syntax_color_keys():
    """SyntaxColors must include all required token type keys."""
    src = read(TOKENS)
    required = ["keyword", "string", "comment", "number", "func", "operator",
                "punctuation", "plain", "added", "removed", "diffHeader"]
    for key in required:
        assert f"{key}: string" in src, f"SyntaxColors missing key: {key}"


def test_theme_colors_includes_syntax():
    """ThemeColors type must include ``syntax: SyntaxColors`` field."""
    src = read(TOKENS)
    assert "syntax: SyntaxColors" in src, "ThemeColors missing syntax field"


def test_light_colors_has_syntax_palette():
    """lightColors object must have a ``syntax`` block with color values."""
    src = read(TOKENS)
    # Find lightColors syntax block — should contain hex colors
    assert "syntax: {" in src, "No syntax block found in tokens.ts"
    # Verify light theme has specific colors (purple-ish keyword for light)
    light_section = src.split("export const lightColors")[1].split("export const darkColors")[0]
    assert "keyword:" in light_section, "light colors missing syntax.keyword"
    assert light_section.count("keyword:") >= 1


def test_dark_colors_has_syntax_palette():
    """darkColors object must have a ``syntax`` block with color values."""
    src = read(TOKENS)
    dark_section = src.split("export const darkColors")[1]
    assert "syntax: {" in dark_section, "dark colors missing syntax block"
    assert "keyword:" in dark_section, "dark colors missing syntax.keyword"
    assert "string:" in dark_section, "dark colors missing syntax.string"


def test_both_themes_have_all_syntax_keys():
    """Both lightColors and darkColors must define all 11 syntax keys."""
    src = read(TOKENS)
    light = src.split("export const lightColors")[1].split("export const darkColors")[0]
    dark = src.split("export const darkColors")[1].split("export type Theme")[0]
    required = ["keyword", "string", "comment", "number", "func", "operator",
                "punctuation", "plain", "added", "removed", "diffHeader"]
    for key in required:
        assert f"{key}:" in light, f"lightColors syntax missing key: {key}"
        assert f"{key}:" in dark, f"darkColors syntax missing key: {key}"


# ── CodeBlock.tsx ─────────────────────────────────────────────────────────


def test_codeblock_file_exists():
    """CodeBlock component file must exist."""
    assert CODEBLOCK.exists(), "CodeBlock.tsx not found"


def test_codeblock_exports_component():
    """CodeBlock must export a CodeBlock component."""
    src = read(CODEBLOCK)
    assert "export function CodeBlock" in src, "CodeBlock export not found"


def test_codeblock_has_tokenizer():
    """CodeBlock must have a tokenizer function (not just plain text)."""
    src = read(CODEBLOCK)
    assert "function tokenize" in src, "tokenize function not found"
    assert "TokenType" in src or "Token" in src, "Token type not found"


def test_codeblock_uses_syntax_colors():
    """CodeBlock must use colors.syntax for token coloring."""
    src = read(CODEBLOCK)
    assert "colors.syntax" in src, "CodeBlock doesn't reference colors.syntax"
    # Must map token types to syntax colors
    assert "syntax[tok" in src or "syntax[token" in src, \
        "CodeBlock doesn't map token types to syntax colors"


def test_codeblock_supports_multiple_languages():
    """CodeBlock tokenizer must handle multiple languages."""
    src = read(CODEBLOCK)
    # Check keyword sets for multiple languages
    for lang in ["python", "javascript", "typescript", "bash", "go", "rust",
                 "java", "cpp", "ruby", "php", "sql"]:
        assert f"'{lang}'" in src or f'"{lang}"' in src, \
            f"Language {lang} not found in CodeBlock keyword sets"


def test_codeblock_supports_diff():
    """CodeBlock must handle diff/patch syntax (added/removed lines)."""
    src = read(CODEBLOCK)
    assert "diff" in src.lower(), "diff language not handled"
    assert "added" in src, "diff 'added' token type not found"
    assert "removed" in src, "diff 'removed' token type not found"


def test_codeblock_supports_yaml():
    """CodeBlock must handle YAML key-value highlighting."""
    src = read(CODEBLOCK)
    assert "yaml" in src.lower(), "yaml language not handled"


def test_codeblock_has_language_label():
    """CodeBlock should display the language label in the header."""
    src = read(CODEBLOCK)
    assert "langLabel" in src or "lang_label" in src, \
        "CodeBlock missing language label"


def test_codeblock_has_copy_button():
    """CodeBlock should have a copy-to-clipboard button."""
    src = read(CODEBLOCK)
    assert "copy" in src.lower(), "CodeBlock missing copy functionality"
    assert "Clipboard" in src, "CodeBlock missing Clipboard import"


def test_codeblock_no_new_native_deps():
    """CodeBlock must not require any new native dependencies."""
    src = read(CODEBLOCK)
    # Should only use react-native, expo-clipboard, @expo/vector-icons — all already in package.json
    imports = [l.strip() for l in src.split("\n") if l.strip().startswith("import ")]
    for imp in imports:
        # No new external packages — only react, react-native, expo, local imports
        assert "from 'react'" in imp or "from 'react-native'" in imp or \
               "from '@expo" in imp or "from 'expo-clipboard'" in imp or \
               "from '../" in imp, \
               f"Unexpected import that may need new deps: {imp}"


# ── ChatScreen.tsx wiring ─────────────────────────────────────────────────


def test_chatscreen_imports_codeblock():
    """ChatScreen must import CodeBlock."""
    src = read(CHATSCREEN)
    assert "CodeBlock" in src, "CodeBlock not referenced in ChatScreen"
    assert "import { CodeBlock }" in src or "import {CodeBlock}" in src, \
        "CodeBlock import statement not found"


def test_chatscreen_fence_uses_codeblock():
    """ChatScreen fence rule must use CodeBlock component."""
    src = read(CHATSCREEN)
    # Find the fence rule
    fence_section = re.search(r"fence:\s*\(node\)\s*=>\s*{", src)
    assert fence_section, "fence rule not found in ChatScreen"
    # Get text from fence rule to next rule
    start = fence_section.end()
    end = src.find("},", start)
    fence_body = src[start:end]
    assert "CodeBlock" in fence_body, "fence rule doesn't use CodeBlock"
    assert "sourceInfo" in fence_body or "language" in fence_body, \
        "fence rule doesn't extract language from sourceInfo"


def test_chatscreen_code_block_uses_codeblock():
    """ChatScreen code_block rule must use CodeBlock component."""
    src = read(CHATSCREEN)
    # Find code_block rule
    cb_section = re.search(r"code_block:\s*\(node\)\s*=>\s*{", src)
    assert cb_section, "code_block rule not found in ChatScreen"
    start = cb_section.end()
    end = src.find("},", start)
    cb_body = src[start:end]
    assert "CodeBlock" in cb_body, "code_block rule doesn't use CodeBlock"


def test_chatscreen_code_inline_uses_syntax_color():
    """code_inline must use colors.syntax.keyword (not colors.accent)."""
    src = read(CHATSCREEN)
    ci_section = re.search(r"code_inline:\s*\(node\)\s*=>\s*{", src)
    assert ci_section, "code_inline rule not found"
    start = ci_section.end()
    end = src.find("},", start)
    ci_body = src[start:end]
    assert "syntax.keyword" in ci_body, \
        "code_inline doesn't use colors.syntax.keyword"


def test_chatscreen_no_old_plain_code_block():
    """Old plain Text code blocks should be removed from fence/code_block."""
    src = read(CHATSCREEN)
    # The old pattern was: <Text variant="sm" style={{ color: colors.textSoft, fontFamily: 'monospace' }}>
    # inside fence/code_block rules. Should be gone now.
    old_pattern = "colors.textSoft, fontFamily: 'monospace'"
    # This pattern might still exist in markdownStyles() for the style object —
    # that's fine. What we want to check is that fence/code_block rules
    # don't use it. Count occurrences — old code had 2 (fence + code_block).
    # markdownStyles has fence + code_block style entries but those use
    # backgroundColor, not textSoft. So the old plain-text pattern should
    # be gone from the fence/code_block *render* functions.
    fence_section = re.search(r"fence:\s*\(node\)\s*=>\s*{(.*?)},", src, re.DOTALL)
    assert fence_section, "fence rule not found"
    assert "colors.textSoft, fontFamily: 'monospace'" not in fence_section.group(1), \
        "Old plain text pattern still in fence rule"


# ── index.ts export ───────────────────────────────────────────────────────


def test_codeblock_exported_from_index():
    """CodeBlock should be exported from components/index.ts."""
    src = read(INDEX)
    assert "CodeBlock" in src, "CodeBlock not exported from components/index.ts"


# ── No new package.json deps ──────────────────────────────────────────────


def test_no_new_npm_dependencies():
    """No new npm packages should have been added for syntax highlighting."""
    src = read(PKG)
    # Should NOT have syntax-highlighting specific packages
    forbidden = [
        "react-native-syntax-highlighter",
        "react-syntax-highlighter",
        "prism-react-native",
        "highlight.js",
        "shiki",
    ]
    for pkg in forbidden:
        assert pkg not in src, f"New dependency {pkg} added — should be zero-dep"