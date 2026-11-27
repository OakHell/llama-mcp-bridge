"""File discovery, .gitignore pruning, and context-budget prompt assembly."""

from __future__ import annotations

import glob as glob_mod
import os
from fnmatch import fnmatch
from pathlib import Path

import pathspec

__all__ = [
    "DEFAULT_IGNORES",
    "_build_prompt_with_files",
    "_discover_ancestor_gitignores",
    "_expand_target_files",
    "_is_gitignored",
    "_load_gitignore_spec",
    "_matches_any_pattern",
    "_standalone_rel_path",
    "_walk_dir_pruned",
]

DEFAULT_IGNORES: frozenset[str] = frozenset({
    ".git",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "dist",
    "build",
    ".next",
    "*.lock",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "uv.lock",
    "Cargo.lock",
    "poetry.lock",
    "composer.lock",
})


def _load_gitignore_spec(gitignore_file: Path) -> pathspec.GitIgnoreSpec | None:
    try:
        if gitignore_file.is_file():
            lines = gitignore_file.read_text(encoding="utf-8", errors="replace").splitlines()
            return pathspec.GitIgnoreSpec.from_lines(lines)
    except (OSError, ValueError):
        return None
    return None


def _discover_ancestor_gitignores(start_path: Path) -> list[tuple[Path, pathspec.GitIgnoreSpec]]:
    """Walks upward from start_path to the git repo root (if any) collecting (base_dir, spec) pairs."""
    cur = start_path if start_path.is_dir() else start_path.parent
    cur = cur.resolve()
    ancestors: list[Path] = []
    while True:
        ancestors.append(cur)
        if (cur / ".git").exists() or cur.parent == cur:
            break
        cur = cur.parent

    specs: list[tuple[Path, pathspec.GitIgnoreSpec]] = []
    for folder in reversed(ancestors):
        spec = _load_gitignore_spec(folder / ".gitignore")
        if spec is not None:
            specs.append((folder, spec))
    return specs


def _is_gitignored(
    target_path: Path,
    rel_path: str,
    is_dir: bool,
    scoped_specs: list[tuple[Path, pathspec.GitIgnoreSpec]],
    global_specs: list[pathspec.GitIgnoreSpec],
) -> bool:
    """Evaluates scoped (anchor-relative) and global gitignore specs, honouring !negations."""
    resolved = target_path.resolve()
    ignored = False

    for base_dir, spec in scoped_specs:
        try:
            rel = resolved.relative_to(base_dir).as_posix()
        except ValueError:
            continue
        if not rel or rel == ".":
            continue
        check_path = f"{rel}/" if is_dir else rel
        result = spec.check_file(check_path)
        if result.include is not None:
            ignored = result.include

    rel_posix = Path(rel_path).as_posix()
    check_rel = f"{rel_posix}/" if is_dir else rel_posix
    for gspec in global_specs:
        result = gspec.check_file(check_rel)
        if result.include is not None:
            ignored = result.include

    return ignored


def _matches_any_pattern(rel_path: str, patterns: set[str]) -> bool:
    """Checks if a relative file path, filename, or any relative path segment matches a pattern set."""
    rel_obj = Path(rel_path)
    name = rel_obj.name
    parts = rel_obj.parts
    for pat in patterns:
        if fnmatch(name, pat) or fnmatch(rel_path, pat):
            return True
        if any(fnmatch(part, pat) for part in parts):
            return True
    return False


def _walk_dir_pruned(
    root_dir: Path,
    effective_ignores: set[str],
    include_set: set[str],
    use_gitignore: bool,
    global_specs: list[pathspec.GitIgnoreSpec],
    seen: set[Path],
    candidates: list[tuple[Path, str, bool]],
) -> None:
    """Walks root_dir without following symlinks, pruning DEFAULT_IGNORES and nested .gitignore rules."""
    base_specs = _discover_ancestor_gitignores(root_dir) if use_gitignore else []
    dir_specs_map: dict[Path, list[tuple[Path, pathspec.GitIgnoreSpec]]] = {
        root_dir.resolve(): base_specs
    }

    for dirpath, dirnames, filenames in os.walk(root_dir, topdown=True, followlinks=False):
        cur_dir = Path(dirpath)
        cur_resolved = cur_dir.resolve()
        rel_dir = cur_dir.relative_to(root_dir)

        active_specs = list(dir_specs_map.get(cur_resolved, base_specs))
        if use_gitignore and cur_resolved != root_dir.resolve():
            local_spec = _load_gitignore_spec(cur_dir / ".gitignore")
            if local_spec is not None:
                active_specs.append((cur_resolved, local_spec))

        kept_dirs: list[str] = []
        for d in sorted(dirnames):
            sub_path = cur_dir / d
            sub_rel = str(rel_dir / d)
            dir_specs_map[sub_path.resolve()] = active_specs

            if not include_set:
                if _matches_any_pattern(sub_rel, effective_ignores):
                    continue
                if (use_gitignore or global_specs) and _is_gitignored(
                    sub_path,
                    rel_path=sub_rel,
                    is_dir=True,
                    scoped_specs=active_specs,
                    global_specs=global_specs,
                ):
                    continue
            kept_dirs.append(d)
        dirnames[:] = kept_dirs

        for fname in sorted(filenames):
            fpath = cur_dir / fname
            if not fpath.is_file():
                continue
            resolved = fpath.resolve()
            if resolved not in seen:
                seen.add(resolved)
                rel_str = str(fpath.relative_to(root_dir))
                git_ignored = (use_gitignore or bool(global_specs)) and _is_gitignored(
                    fpath,
                    rel_path=rel_str,
                    is_dir=False,
                    scoped_specs=active_specs,
                    global_specs=global_specs,
                )
                candidates.append((fpath, rel_str, git_ignored))


def _standalone_rel_path(file_path: Path) -> str:
    """Computes repo-relative (or cwd-relative) path for standalone files so path-anchored gitignore rules match."""
    resolved = file_path.resolve()
    cur = resolved.parent
    while True:
        if (cur / ".git").exists():
            return resolved.relative_to(cur).as_posix()
        if cur.parent == cur:
            break
        cur = cur.parent
    try:
        return resolved.relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        return file_path.name


def _expand_target_files(
    file_paths: list[str],
    ignore: list[str] | None = None,
    include: list[str] | None = None,
    use_gitignore: bool = True,
    gitignore_paths: list[str] | None = None,
) -> tuple[list[Path], str | None]:
    """Expands files, directories, and globs with include > (gitignore + ignore + defaults) precedence."""
    effective_ignores = set(DEFAULT_IGNORES)
    if ignore:
        effective_ignores.update(ignore)
    include_set = set(include) if include else set()

    global_specs: list[pathspec.GitIgnoreSpec] = []
    if gitignore_paths:
        for gp_raw in gitignore_paths:
            gp = Path(gp_raw).expanduser()
            spec = _load_gitignore_spec(gp)
            if spec is None:
                return [], f"Error: Provided gitignore file not found or unreadable: {gp_raw}"
            global_specs.append(spec)

    candidates: list[tuple[Path, str, bool]] = []
    seen: set[Path] = set()

    for raw in file_paths:
        expanded_str = str(Path(raw).expanduser())
        has_magic = glob_mod.has_magic(expanded_str)

        if has_magic:
            first_magic = min(
                (i for i, ch in enumerate(expanded_str) if ch in "*?["),
                default=len(expanded_str),
            )
            prefix_dir = Path(os.path.dirname(expanded_str[:first_magic]) or ".") if first_magic > 0 else Path(".")
            matches = sorted(glob_mod.glob(expanded_str, recursive=True))
            if not matches:
                return [], f"Error: Glob pattern matched no files: {raw}"
            for m in matches:
                mp = Path(m)
                if mp.is_file():
                    resolved = mp.resolve()
                    if resolved not in seen:
                        seen.add(resolved)
                        try:
                            rel = str(mp.relative_to(prefix_dir))
                        except ValueError:
                            rel = _standalone_rel_path(mp)
                        anc_specs = _discover_ancestor_gitignores(mp) if use_gitignore else []
                        git_ignored = (use_gitignore or bool(global_specs)) and _is_gitignored(
                            mp,
                            rel_path=rel,
                            is_dir=False,
                            scoped_specs=anc_specs,
                            global_specs=global_specs,
                        )
                        candidates.append((mp, rel, git_ignored))
                elif mp.is_dir():
                    _walk_dir_pruned(
                        mp, effective_ignores, include_set, use_gitignore, global_specs, seen, candidates
                    )
        else:
            p = Path(expanded_str)
            if not p.exists():
                return [], f"Error: Attached path not found: {raw}"
            if p.is_file():
                resolved = p.resolve()
                if resolved not in seen:
                    seen.add(resolved)
                    rel = _standalone_rel_path(p)
                    anc_specs = _discover_ancestor_gitignores(p) if use_gitignore else []
                    git_ignored = (use_gitignore or bool(global_specs)) and _is_gitignored(
                        p,
                        rel_path=rel,
                        is_dir=False,
                        scoped_specs=anc_specs,
                        global_specs=global_specs,
                    )
                    candidates.append((p, rel, git_ignored))
            elif p.is_dir():
                _walk_dir_pruned(
                    p, effective_ignores, include_set, use_gitignore, global_specs, seen, candidates
                )
            else:
                return [], f"Error: Attached path is neither a regular file nor directory: {raw}"

    filtered: list[Path] = []
    for file_path, rel_path, git_ignored in candidates:
        if include_set and _matches_any_pattern(rel_path, include_set):
            filtered.append(file_path)
            continue
        if git_ignored or _matches_any_pattern(rel_path, effective_ignores):
            continue
        filtered.append(file_path)

    filtered.sort(key=lambda fp: (fp.stat().st_size, str(fp)))
    return filtered, None


def _build_prompt_with_files(
    prompt: str,
    file_paths: list[str] | None = None,
    max_total_chars: int = 100_000,
    ignore: list[str] | None = None,
    include: list[str] | None = None,
    use_gitignore: bool = True,
    gitignore_paths: list[str] | None = None,
) -> tuple[str, str | None]:
    """Expands files/dirs/globs, applies .gitignore + include/ignore precedence, and prepends <file> blocks."""
    if not file_paths:
        return prompt, None

    target_files, err = _expand_target_files(
        file_paths,
        ignore=ignore,
        include=include,
        use_gitignore=use_gitignore,
        gitignore_paths=gitignore_paths,
    )
    if err:
        return "", err

    if not target_files:
        return "", "Error: No files remained after applying ignore/include filters."

    sections: list[str] = []
    remaining = max_total_chars

    for p in target_files:
        if remaining <= 0:
            sections.append(f'<file path="{p}" status="omitted_budget_exhausted" />')
            continue

        try:
            with p.open("rb") as f:
                raw_bytes = f.read(remaining + 1)
        except OSError as e:
            return "", f"Error reading attached file {p}: {e}"

        if b"\x00" in raw_bytes[:4096]:
            sections.append(f'<file path="{p}" status="skipped_binary" />')
            continue

        content = raw_bytes.decode("utf-8", errors="replace")
        if len(content) > remaining:
            content = (
                content[:remaining]
                + f"\n... [TRUNCATED after {remaining} chars to fit context budget]"
            )
            remaining = 0
        else:
            remaining -= len(content)

        sections.append(f'<file path="{p}">\n{content}\n</file>')

    attached_block = "\n\n".join(sections)
    combined = f"Attached Files:\n\n{attached_block}\n\nTask:\n{prompt}"
    return combined, None
