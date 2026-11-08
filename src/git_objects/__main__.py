"""CLI interface for git_objects (pygitobj)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from git_objects.errors import GitObjectsError
from git_objects.parsers import parse_tree
from git_objects.store import ObjectStore


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="pygitobj", description="Pure Python Git object inspector and tool"
    )
    subparsers = parser.add_subparsers(dest="command", help="Subcommand to run")

    # cat-file
    p_cat = subparsers.add_parser(
        "cat-file", help="Provide content or details of repository objects"
    )
    p_cat.add_argument("-p", action="store_true", help="Pretty-print object content")
    p_cat.add_argument("-t", action="store_true", help="Show object type")
    p_cat.add_argument("-s", action="store_true", help="Show object size")
    p_cat.add_argument("object", help="The name of the object to show")

    # hash-object
    p_hash = subparsers.add_parser(
        "hash-object", help="Compute object ID and optionally create a blob from a file"
    )
    p_hash.add_argument("-t", default="blob", help="Object type (default: blob)")
    p_hash.add_argument("-w", action="store_true", help="Write the object into the object database")
    p_hash.add_argument("file", help="File to hash")

    # ls-tree
    p_lstree = subparsers.add_parser("ls-tree", help="List the contents of a tree object")
    p_lstree.add_argument("-r", action="store_true", help="Recurse into sub-trees")
    p_lstree.add_argument("tree_ish", help="Tree-ish object")

    # rev-parse
    p_rev = subparsers.add_parser("rev-parse", help="Pick out and massage parameters")
    p_rev.add_argument("rev", help="Revision string")

    # log
    p_log = subparsers.add_parser("log", help="Show commit logs")
    p_log.add_argument(
        "--topo-order", action="store_true", help="Show commit history in topological order"
    )
    p_log.add_argument(
        "--date-order", action="store_true", help="Show commit history in date order"
    )
    p_log.add_argument("start_rev", nargs="?", default="HEAD", help="Starting revision")

    # write-tree
    p_wtree = subparsers.add_parser(
        "write-tree", help="Create a tree object from the current directory"
    )
    p_wtree.add_argument("directory", nargs="?", default=".", help="Directory path")

    # commit-tree
    p_ctree = subparsers.add_parser("commit-tree", help="Create a new commit object")
    p_ctree.add_argument("tree", help="Tree object sha1")
    p_ctree.add_argument("-p", action="append", default=[], help="Parent commit")
    p_ctree.add_argument("-m", required=True, help="Commit message")
    p_ctree.add_argument("--author", default="Author <author@example.com>", help="Author string")
    p_ctree.add_argument(
        "--committer", default="Committer <committer@example.com>", help="Committer string"
    )

    # update-ref
    p_uref = subparsers.add_parser(
        "update-ref", help="Update the object name stored in a ref safely"
    )
    p_uref.add_argument("ref_name", help="Ref name")
    p_uref.add_argument("new_val", help="New value")
    p_uref.add_argument("old_val", nargs="?", default=None, help="Old value")

    # lint-commits (stretch)
    p_lint = subparsers.add_parser("lint-commits", help="Lint commits using trysac-commit-msg-lint")
    p_lint.add_argument("start_rev", nargs="?", default="HEAD", help="Starting revision")

    args = parser.parse_args(argv)
    if not args.command:
        parser.print_usage(sys.stderr)
        return 2

    store = ObjectStore(Path.cwd())

    try:
        if args.command == "cat-file":
            oid = store.rev_parse(args.object)
            obj = store.read_object(oid)
            if args.t:
                sys.stdout.write(f"{obj.type}\n")
            elif args.s:
                sys.stdout.write(f"{len(obj.data)}\n")
            elif args.p:
                if obj.type == "tree":
                    entries = parse_tree(obj.data)
                    for e in entries:
                        t = "tree" if e.mode.startswith("4") or e.mode == "040000" else "blob"
                        sys.stdout.write(f"{e.mode} {t} {e.oid}\t{e.name}\n")
                else:
                    sys.stdout.buffer.write(obj.data)
            return 0

        if args.command == "hash-object":
            file_path = Path(args.file)
            if not file_path.is_file():
                sys.stderr.write(f"File not found: {args.file}\n")
                return 1
            data = file_path.read_bytes()
            oid = store.hash_object(data, obj_type=args.t, write=args.w)
            sys.stdout.write(f"{oid}\n")
            return 0

        if args.command == "ls-tree":
            tree_oid = store.rev_parse(args.tree_ish)
            if args.r:
                files = store.get_tree_files(tree_oid)
                for p, (o, m) in sorted(files.items()):
                    sys.stdout.write(f"{m} blob {o}\t{p}\n")
            else:
                obj = store.read_object(tree_oid)
                entries = parse_tree(obj.data)
                for e in entries:
                    t = "tree" if e.mode.startswith("4") or e.mode == "040000" else "blob"
                    sys.stdout.write(f"{e.mode} {t} {e.oid}\t{e.name}\n")
            return 0

        if args.command == "rev-parse":
            oid = store.rev_parse(args.rev)
            sys.stdout.write(f"{oid}\n")
            return 0

        if args.command == "log":
            order = "topo" if args.topo_order else "date"
            for commit in store.walk_commits(args.start_rev, order=order):
                sys.stdout.write(
                    f"commit {commit.oid}\nAuthor: {commit.author}\n\n    {commit.message}\n\n"
                )
            return 0

        if args.command == "write-tree":
            oid = store.write_tree(args.directory)
            sys.stdout.write(f"{oid}\n")
            return 0

        if args.command == "commit-tree":
            oid = store.commit_tree(args.tree, args.p, args.m, args.author, args.committer)
            sys.stdout.write(f"{oid}\n")
            return 0

        if args.command == "update-ref":
            store.update_ref(args.ref_name, args.new_val, args.old_val)
            return 0

        if args.command == "lint-commits":
            from git_objects.adapters.lint import lint_commits

            clean = True
            for commit, findings in lint_commits(store, args.start_rev):
                if findings:
                    clean = False
                    sys.stderr.write(f"Commit {commit.oid[:7]}: {len(findings)} lint finding(s)\n")
                    for f in findings:
                        sys.stderr.write(f"  [{f.rule}] {f.message}\n")
            return 0 if clean else 1

    except GitObjectsError as err:
        sys.stderr.write(f"Error: {err}\n")
        return 1
    except Exception as err:
        sys.stderr.write(f"Unexpected error: {err}\n")
        return 2

    return 0


if __name__ == "__main__":
    sys.exit(main())
