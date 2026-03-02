"""Tree-sitter based repository map and symbol extraction.

Optional dependency: pip install tree-sitter tree-sitter-languages
Falls back to regex-based extraction if tree-sitter is not installed.
"""

import os
import re

# Language detection by file extension
LANG_MAP = {
    ".py": "python", ".js": "javascript", ".ts": "typescript", ".tsx": "typescript",
    ".go": "go", ".rs": "rust", ".java": "java", ".rb": "ruby",
    ".c": "c", ".cpp": "cpp", ".h": "c", ".hpp": "cpp",
    ".cs": "c_sharp", ".swift": "swift", ".kt": "kotlin",
}

# Regex fallback patterns for symbol extraction
REGEX_PATTERNS = {
    "python": r"^\s*(def |class )\s*(\w+)",
    "javascript": r"^\s*(function |class |export function |export class |export default function )(\w+)",
    "typescript": r"^\s*(function |class |export function |export class |interface |type |export default function )(\w+)",
    "go": r"^\s*(func |type )\s*(\w+)",
    "rust": r"^\s*(fn |struct |enum |trait |impl |pub fn |pub struct )(\w+)",
    "java": r"^\s*(public |private |protected )?(static )?(class |interface |enum |void |int |String )\s*(\w+)",
    "ruby": r"^\s*(def |class |module )\s*(\w+)",
    "c": r"^\s*(\w+\s+\*?\s*)(\w+)\s*\(",
    "cpp": r"^\s*(class |struct |namespace )\s*(\w+)",
}


class RepoMap:
    """Build and format a repository map with symbol extraction."""

    def __init__(self, cwd, skip_dirs=None, skip_exts=None):
        self.cwd = cwd
        self.skip_dirs = skip_dirs or set()
        self.skip_exts = skip_exts or set()
        self._files = {}  # {rel_path: [symbols]}
        self._has_treesitter = False
        self._parsers = {}
        try:
            import tree_sitter_languages
            self._has_treesitter = True
        except ImportError:
            pass

    def _detect_language(self, filepath):
        ext = os.path.splitext(filepath)[1].lower()
        return LANG_MAP.get(ext)

    def _extract_symbols_regex(self, filepath, lang):
        """Regex-based symbol extraction (fallback)."""
        symbols = []
        pattern = REGEX_PATTERNS.get(lang)
        if not pattern:
            # Generic fallback
            pattern = r"^\s*(def |class |function |export function |export class )\s*(\w+)"
        try:
            with open(filepath, 'r', errors='replace') as f:
                for i, line in enumerate(f, 1):
                    m = re.match(pattern, line)
                    if m:
                        groups = [g for g in m.groups() if g and g.strip()]
                        if groups:
                            name = groups[-1].strip()
                            kind = groups[0].strip() if len(groups) > 1 else ""
                            symbols.append({"name": name, "kind": kind, "line": i})
        except Exception:
            pass
        return symbols

    def _extract_symbols_treesitter(self, filepath, lang):
        """Tree-sitter based symbol extraction."""
        try:
            import tree_sitter_languages
            parser = tree_sitter_languages.get_parser(lang)
            with open(filepath, 'rb') as f:
                source = f.read()
            tree = parser.parse(source)
            symbols = []
            # Walk tree for function/class/method definitions
            def walk(node, depth=0):
                if depth > 10:
                    return
                ntype = node.type
                # Python, JS, TS, Go, Rust definitions
                if ntype in (
                    "function_definition", "class_definition", "method_definition",
                    "function_declaration", "class_declaration", "method_declaration",
                    "function_item", "struct_item", "enum_item", "trait_item", "impl_item",
                    "type_declaration", "interface_declaration",
                ):
                    name_node = node.child_by_field_name("name")
                    if name_node:
                        name = source[name_node.start_byte:name_node.end_byte].decode('utf-8', errors='replace')
                        kind = ntype.replace("_definition", "").replace("_declaration", "").replace("_item", "")
                        symbols.append({"name": name, "kind": kind, "line": node.start_point[0] + 1})
                for child in node.children:
                    walk(child, depth + 1)
            walk(tree.root_node)
            return symbols
        except Exception:
            return self._extract_symbols_regex(filepath, lang)

    def extract_symbols(self, filepath):
        """Extract symbols from a file using tree-sitter or regex fallback."""
        lang = self._detect_language(filepath)
        if not lang:
            return []
        if self._has_treesitter:
            return self._extract_symbols_treesitter(filepath, lang)
        return self._extract_symbols_regex(filepath, lang)

    def build_map(self):
        """Walk repo and extract symbols from all source files."""
        self._files = {}
        for root, dirs, files in os.walk(self.cwd):
            dirs[:] = [d for d in dirs if d not in self.skip_dirs and not d.startswith('.')]
            for fname in sorted(files):
                ext = os.path.splitext(fname)[1].lower()
                if ext in self.skip_exts or not ext:
                    continue
                if ext not in LANG_MAP:
                    continue
                filepath = os.path.join(root, fname)
                rel = os.path.relpath(filepath, self.cwd)
                symbols = self.extract_symbols(filepath)
                if symbols:
                    self._files[rel] = symbols

    def format_map(self, max_files=50):
        """Format the repo map as readable text."""
        if not self._files:
            return "(no symbols found)"
        lines = []
        method = "tree-sitter" if self._has_treesitter else "regex"
        lines.append(f"Repository Map ({len(self._files)} files, {method} extraction)\n")
        for i, (rel, symbols) in enumerate(sorted(self._files.items())):
            if i >= max_files:
                lines.append(f"\n... and {len(self._files) - max_files} more files")
                break
            lines.append(f"📄 {rel}")
            for sym in symbols[:20]:  # Cap symbols per file
                kind = sym["kind"]
                name = sym["name"]
                line = sym["line"]
                lines.append(f"  ├─ {kind} {name} (L{line})")
        return "\n".join(lines)

    def get_all_symbols(self):
        """Return flat list of all symbols with file paths."""
        result = []
        for rel, symbols in self._files.items():
            for sym in symbols:
                result.append({**sym, "file": rel})
        return result
