#!/usr/bin/env python3
"""One version, one place: the VERSION file. Checks that everything else agrees and derives the image tags.

    python3 scripts/release_info.py check            # VERSION = compose.yaml tag = Dockerfile default = CHANGELOG heading
    python3 scripts/release_info.py check --tag v2.0.0   # a release tag: also equals VERSION, and the CHANGELOG entry is dated
    python3 scripts/release_info.py tags v2.0.0      # the image tags a release tag publishes, one per line
    python3 scripts/release_info.py notes v2.0.0     # the CHANGELOG entry of that version (the release notes)

Standard library only. Exit 0 when everything agrees, 1 and the list of differences otherwise.
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
SEMVER = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?$")


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as fh:
        return fh.read()


def version_file():
    return read("VERSION").strip()


def compose_default():
    m = re.search(r"image: ghcr\.io/[\w.-]+/headscale-easy:\$\{HSE_VERSION:-([^}]+)\}", read("compose.yaml"))
    return m.group(1) if m else None


def dockerfile_default():
    m = re.search(r"^ARG HSE_VERSION=(\S+)$", read("aio", "Dockerfile"), re.M)
    return m.group(1) if m else None


def changelog_entries(text=None):
    """[(version, rest of the heading line)] in file order, for the '## [x.y.z] - ...' headings."""
    text = read("CHANGELOG.md") if text is None else text
    return re.findall(r"^## \[(\d+\.\d+\.\d+[^\]]*)\](.*)$", text, re.M)


def changelog_notes(version, text=None):
    """The body of the CHANGELOG entry of a version (without its heading); '' when there is none."""
    text = read("CHANGELOG.md") if text is None else text
    out, on = [], False
    for line in text.splitlines():
        if re.match(r"^## \[%s\]" % re.escape(version), line):
            on = True
            continue
        if on and (line.startswith("## [") or line.startswith("[")):
            break
        if on:
            out.append(line)
    return "\n".join(out).strip()


def tags_for(ref):
    """The image tags a release tag (vX.Y.Z or vX.Y.Z-rc.1) publishes: X.Y.Z, X.Y, X and latest.

    A pre-release only publishes its own tag, never the moving ones.
    """
    m = SEMVER.match(ref[1:] if ref.startswith("v") else ref)
    if not m:
        raise ValueError("%r is not a version tag (vX.Y.Z)" % ref)
    major, minor, patch, pre = m.groups()
    if pre:
        return ["%s.%s.%s-%s" % (major, minor, patch, pre)]
    return ["%s.%s.%s" % (major, minor, patch), "%s.%s" % (major, minor), major, "latest"]


def check(tag=None):
    """The list of differences (empty = consistent)."""
    problems = []
    version = version_file()
    if not SEMVER.match(version):
        problems.append("VERSION %r is not a semantic version" % version)
    for name, value in (("compose.yaml default tag", compose_default()), ("aio/Dockerfile ARG HSE_VERSION", dockerfile_default())):
        if value != version:
            problems.append("%s is %r, VERSION is %r" % (name, value, version))
    entries = changelog_entries()
    if not entries:
        problems.append("CHANGELOG.md has no '## [x.y.z]' heading")
    elif entries[0][0] != version:
        problems.append("the top CHANGELOG.md heading is [%s], VERSION is %s" % (entries[0][0], version))
    if tag:
        if tag != "v" + version:
            problems.append("the tag %s does not match VERSION %s (expected v%s)" % (tag, version, version))
        if entries and "unreleased" in entries[0][1].lower():
            problems.append("the CHANGELOG.md entry of %s is still 'Unreleased': date it before tagging" % version)
        if not changelog_notes(version):
            problems.append("CHANGELOG.md has no release notes for %s" % version)
    return problems


def main(argv):
    cmd = argv[1] if len(argv) > 1 else ""
    if cmd == "check":
        tag = argv[3] if len(argv) > 3 and argv[2] == "--tag" else None
        problems = check(tag)
        for p in problems:
            print("release_info: " + p, file=sys.stderr)
        if not problems:
            print("version %s: VERSION, compose.yaml, aio/Dockerfile and CHANGELOG.md agree" % version_file())
        return 1 if problems else 0
    if cmd == "tags" and len(argv) == 3:
        try:
            print("\n".join(tags_for(argv[2])))
        except ValueError as exc:
            print("release_info: %s" % exc, file=sys.stderr)
            return 1
        return 0
    if cmd == "notes" and len(argv) == 3:
        notes = changelog_notes(argv[2].lstrip("v"))
        if not notes:
            print("release_info: no CHANGELOG.md entry for %s" % argv[2], file=sys.stderr)
            return 1
        print(notes)
        return 0
    print(__doc__.strip(), file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
