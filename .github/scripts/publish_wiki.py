"""Export the public documentation allowlist and update its GitHub Wiki pages."""

from __future__ import annotations

import argparse
import os
import re
import subprocess
from pathlib import Path, PurePosixPath
from urllib.parse import quote, unquote, urlsplit


class WikiError(ValueError):
    """A fixed publication diagnostic, safe to display in workflow logs."""


class WikiTree:
    """Read or replace a finite set of regular files without following symlinks."""

    def __init__(self, root: Path):
        self.root = root.absolute()
        if self.root.is_symlink() or (self.root.exists() and not self.root.is_dir()):
            raise WikiError("Wiki directory is missing or unsafe.")

    def existing(self, relative: PurePosixPath) -> Path:
        if relative.is_absolute() or ".." in relative.parts:
            raise WikiError("Documentation path leaves its source tree.")
        path = self.root
        for part in relative.parts:
            path /= part
            if path.is_symlink() or not path.exists():
                raise WikiError("Documentation path is missing or unsafe.")
        if not (path.is_file() or path.is_dir()):
            raise WikiError("Documentation path is not a regular file or directory.")
        return path

    def read(self, name: str) -> str:
        path = self.existing(PurePosixPath(name))
        if not path.is_file():
            raise WikiError("Expected a regular Markdown file.")
        return path.read_text(encoding="utf-8")

    def apply(self, pages: dict[str, str]) -> tuple[str, ...]:
        # Validate every destination before creating or changing any file.
        changed = []
        for name, content in pages.items():
            if PurePosixPath(name).name != name or not name.endswith(".md"):
                raise WikiError("Invalid generated wiki filename.")
            path = self.root / name
            if path.is_symlink() or (path.exists() and not path.is_file()):
                raise WikiError("Managed wiki destination is unsafe.")
            if not path.exists() or path.read_text(encoding="utf-8") != content:
                changed.append(name)
        self.root.mkdir(parents=True, exist_ok=True)
        for name in changed:
            (self.root / name).write_text(pages[name], encoding="utf-8")
        return tuple(changed)


class WikiExporter:
    # This is the whole publication surface. Never discover pages recursively.
    PAGES = {
        "README.md": "Home", "installation.md": "Installation",
        "authentication.md": "Authentication", "usage.md": "Using-TDM",
        "dashboard-access.md": "Dashboard-access", "notifications.md": "Telegram-notifications",
        "troubleshooting.md": "Troubleshooting", "credits.md": "Credits",
    }
    PRIVATE = {".dev-notes", "notes", "plans"}

    def __init__(self, source: Path, repository: str):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*", repository):
            raise WikiError("Invalid GitHub repository name.")
        self.source = WikiTree(source)
        self.repository = repository
        self.base = f"https://github.com/{repository}"

    @classmethod
    def filenames(cls) -> set[str]:
        return {page + ".md" for page in cls.PAGES.values()} | {"_Sidebar.md"}

    def _link(self, value: str) -> str:
        # Decode Markdown escapes and URL path escapes before checking boundaries.
        value = re.sub(r"\\([^\w\s])", r"\1", value)
        parsed = urlsplit(value)
        if parsed.scheme:
            if parsed.scheme not in {"https", "http", "mailto"}:
                raise WikiError("Unsupported documentation link scheme.")
            return value
        if parsed.netloc or parsed.path.startswith("/") or parsed.query:
            raise WikiError("Expected a relative documentation link without a query.")
        if not parsed.path:
            return value
        if re.search(r"%(?![0-9A-Fa-f]{2})", parsed.path):
            raise WikiError("Invalid encoded documentation path.")
        path = unquote(parsed.path, errors="strict")
        if path.startswith("/") or "\\" in path or any(ord(char) < 32 for char in path):
            raise WikiError("Invalid documentation path.")
        parts = ["docs"]
        for part in path.split("/"):
            if part in {"", "."}:
                continue
            if part == "..":
                if not parts:
                    raise WikiError("Documentation link leaves the repository.")
                parts.pop()
            else:
                if part.casefold() in self.PRIVATE or (part.startswith(".") and part != ".github"):
                    raise WikiError("Documentation links to an unpublished path.")
                parts.append(part)
        relative = PurePosixPath(*parts)
        target = self.source.existing(relative)
        if parts and parts[0] == "docs":
            name = "README.md" if parts == ["docs"] else "/".join(parts[1:])
            if name not in self.PAGES:
                raise WikiError("Documentation links to an unpublished guide.")
            destination = self.base + "/wiki/" + self.PAGES[name]
        else:
            kind = "tree" if target.is_dir() else "blob"
            destination = self.base + f"/{kind}/main/" + quote(relative.as_posix(), safe="/")
        return destination + ("#" + parsed.fragment if parsed.fragment else "")

    @staticmethod
    def _destination(line: str, start: int) -> tuple[int, int] | None:
        while start < len(line) and line[start] in " \t":
            start += 1
        if start == len(line):
            return None
        if line[start] == "<":
            end = line.find(">", start + 1)
            return (start + 1, end) if end != -1 else None
        end, depth = start, 0
        while end < len(line):
            char = line[end]
            if char == "\\" and end + 1 < len(line):
                end += 2
                continue
            if char == "(":
                depth += 1
            elif char == ")":
                if depth == 0:
                    break
                depth -= 1
            elif char.isspace() and depth == 0:
                break
            end += 1
        return (start, end) if end > start else None

    def _inline(self, line: str) -> str:
        reference = re.match(r" {0,3}\[[^\]\n]+\]:[ \t]*", line)
        if reference:
            span = self._destination(line, reference.end())
            if span:
                start, end = span
                return line[:start] + self._link(line[start:end]) + line[end:]
        result, index = [], 0
        while index < len(line):
            if line[index] == "\\" and index + 1 < len(line):
                result.append(line[index:index + 2])
                index += 2
                continue
            if line[index] == "`":
                end = index + 1
                while end < len(line) and line[end] == "`":
                    end += 1
                ticks = line[index:end]
                end = line.find(ticks, index + len(ticks))
                if end != -1:
                    end += len(ticks)
                    result.append(line[index:end])
                    index = end
                    continue
            if line.startswith("](", index):
                span = self._destination(line, index + 2)
                if span:
                    start, end = span
                    result.append(line[index:start] + self._link(line[start:end]))
                    index = end
                    continue
            result.append(line[index])
            index += 1
        return "".join(result)

    def _markdown(self, text: str) -> str:
        result, fence = [], ""
        for line in text.splitlines(keepends=True):
            marker = re.match(r" {0,3}(`{3,}|~{3,})", line)
            if fence:
                result.append(line)
                if re.fullmatch(r" {0,3}" + re.escape(fence[0]) + "{" + str(len(fence)) + r",}\s*", line):
                    fence = ""
            elif marker:
                fence = marker.group(1)
                result.append(line)
            elif line.startswith(("    ", "\t")):
                result.append(line)
            else:
                result.append(self._inline(line))
        return "".join(result)

    def render(self) -> dict[str, str]:
        sources = {name: self.source.read("docs/" + name) for name in self.PAGES}
        pages = {
            page + ".md": (
                f"> Published from [docs/{name}]({self.base}/blob/main/docs/{name}). "
                "To change this page, edit the source guide and open a pull request.\n\n"
                + self._markdown(sources[name])
            )
            for name, page in self.PAGES.items()
        }
        pages["_Sidebar.md"] = "## TDM guides\n\n" + "".join(
            f"- [{page.replace('-', ' ')}]({self.base}/wiki/{page})\n" for page in self.PAGES.values()
        ) + f"\n[Source repository]({self.base})\n"
        return pages

    def export(self, destination: Path) -> tuple[str, ...]:
        return WikiTree(destination).apply(self.render())


class WikiPublisher:
    """Publish only generated pages, retaining all other wiki files and history."""

    INPUT_PATHS = ("docs", ".github/scripts/publish_wiki.py", ".github/workflows/wiki.yml")

    def __init__(self, repository: str, exported: Path, checkout: Path, source_sha: str, *, source: Path = Path(".")):
        self.repository = WikiExporter(exported, repository).repository
        if not re.fullmatch(r"[0-9a-f]{40}", source_sha):
            raise WikiError("Expected the exact source commit SHA.")
        self.exported, self.checkout, self.source_sha = exported, checkout, source_sha
        self.source = WikiTree(source).root
        self.obsolete = False

    @staticmethod
    def _git(*args: str) -> str:
        try:
            return subprocess.run([
                "git", "-c", "credential.helper=", "-c", "credential.helper=!gh auth git-credential",
                "-c", "core.hooksPath=" + os.devnull, *args,
            ], check=True, capture_output=True, text=True,
                env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "GH_PROMPT_DISABLED": "1"}).stdout.strip()
        except (OSError, subprocess.CalledProcessError):
            raise WikiError("Git wiki publication failed; no force push was attempted.") from None

    def _current(self) -> bool:
        if self._git("-C", str(self.source), "rev-parse", "--verify", "HEAD") != self.source_sha:
            raise WikiError("Wiki source checkout does not match the triggering commit.")
        self._git("-C", str(self.source), "fetch", "--no-tags", "--depth=1", "--",
                  f"https://github.com/{self.repository}.git", "refs/heads/main")
        # A contributor README-only commit does not trigger this workflow. Compare
        # publication inputs, not HEAD identity, so those jobs can still publish.
        return not self._git("-C", str(self.source), "diff", "--no-ext-diff", "--name-only",
                             self.source_sha, "FETCH_HEAD", "--", *self.INPUT_PATHS)

    def publish(self) -> bool:
        exported = WikiTree(self.exported)
        if not exported.root.is_dir() or {p.name for p in exported.root.iterdir()} != WikiExporter.filenames():
            raise WikiError("Expected exactly the public wiki export files.")
        pages = {name: exported.read(name) for name in sorted(WikiExporter.filenames())}
        if self.checkout.exists() or self.checkout.is_symlink():
            raise WikiError("Wiki clone destination must be new.")
        self.obsolete = not self._current()
        if self.obsolete:
            return False
        self._git("clone", "--single-branch", "--", f"https://github.com/{self.repository}.wiki.git", str(self.checkout))
        changed = WikiTree(self.checkout).apply(pages)
        if not changed:
            return False
        self._git("-C", str(self.checkout), "add", "--", *changed)
        self._git("-C", str(self.checkout), "-c", "user.name=github-actions[bot]",
                  "-c", "user.email=41898282+github-actions[bot]@users.noreply.github.com",
                  "commit", "-m", f"Sync public guides from {self.source_sha}")
        self._git("-C", str(self.checkout), "push", "origin", "HEAD")
        return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    export = commands.add_parser("export")
    export.add_argument("--repository", required=True)
    export.add_argument("--source", type=Path, required=True)
    export.add_argument("--output", type=Path, required=True)
    publish = commands.add_parser("publish")
    publish.add_argument("--repository", required=True)
    publish.add_argument("--exported", type=Path, required=True)
    publish.add_argument("--checkout", type=Path, required=True)
    publish.add_argument("--source-sha", required=True)
    publish.add_argument("--source", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "export":
            WikiExporter(args.source, args.repository).export(args.output)
            print("Validated and exported the eight public guides and sidebar.")
        else:
            publisher = WikiPublisher(args.repository, args.exported, args.checkout, args.source_sha, source=args.source)
            changed = publisher.publish()
            if publisher.obsolete:
                print("Skipped obsolete run: wiki publication inputs have changed on main.")
            else:
                print("Published public guide changes." if changed else "Wiki public guides are already current.")
    except WikiError as error:
        parser.exit(1, str(error) + "\n")
    except (OSError, UnicodeError, ValueError):
        parser.exit(1, "Wiki files could not be validated or written.\n")
