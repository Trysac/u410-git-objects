"""Similarity rename detection adapter using trysac-near-dup-detector."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from git_objects.models import TreeChange
    from git_objects.store import ObjectStore

try:
    from near_dup_detector import find_duplicates
except ImportError as err:
    raise ImportError(
        "The 'similarity' extra is required to use this module. "
        "Install it via 'pip install trysac-git-objects[similarity]'."
    ) from err


def diff_trees_with_similarity(
    store: ObjectStore,
    old_tree_oid: str | None,
    new_tree_oid: str | None,
    threshold: float = 0.8,
) -> list[TreeChange]:
    """Perform tree diff with fuzzy rename detection for non-exact matches."""
    changes = store.diff_trees(old_tree_oid, new_tree_oid)
    deleted = [c for c in changes if c.change_type == "deleted"]
    added = [c for c in changes if c.change_type == "added"]

    if not deleted or not added:
        return changes

    # Read blob texts
    docs: dict[str, str] = {}
    for d in deleted:
        if d.old_oid:
            data = store.read_object(d.old_oid).data
            docs[f"del:{d.path}"] = data.decode("utf-8", errors="ignore")
    for a in added:
        if a.new_oid:
            data = store.read_object(a.new_oid).data
            docs[f"add:{a.path}"] = data.decode("utf-8", errors="ignore")

    clusters = find_duplicates(docs, threshold=threshold)
    from git_objects.models import TreeChange

    renamed_pairs: dict[str, tuple[str, float]] = {}
    for cluster in clusters:
        del_ids = [doc_id for doc_id in cluster.ids if doc_id.startswith("del:")]
        add_ids = [doc_id for doc_id in cluster.ids if doc_id.startswith("add:")]
        if del_ids and add_ids:
            d_path = del_ids[0][4:]
            a_path = add_ids[0][4:]
            renamed_pairs[a_path] = (d_path, cluster.min_similarity)

    new_changes: list[TreeChange] = []
    deleted_by_path = {c.path: c for c in deleted}
    consumed_deleted: set[str] = set()

    for c in changes:
        if c.change_type == "added" and c.path in renamed_pairs:
            old_p, sim = renamed_pairs[c.path]
            old_c = deleted_by_path[old_p]
            consumed_deleted.add(old_p)
            new_changes.append(
                TreeChange(
                    change_type="renamed",
                    path=c.path,
                    old_path=old_p,
                    old_oid=old_c.old_oid,
                    new_oid=c.new_oid,
                    old_mode=old_c.old_mode,
                    new_mode=c.new_mode,
                    similarity=sim,
                )
            )
        elif c.change_type == "deleted":
            if c.path not in consumed_deleted:
                new_changes.append(c)
        else:
            new_changes.append(c)

    return sorted(new_changes, key=lambda ch: ch.path)
