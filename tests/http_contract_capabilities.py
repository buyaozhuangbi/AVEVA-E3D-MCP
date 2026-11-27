"""Keep adapter-owned optional interfaces in extracted production HTTP tests."""

from test_model_validation_native_contract import block_end


def append_element_ancestry_interface(adapter: str, harness: str) -> str:
    marker = "public interface IElementAncestryContext"
    if marker not in adapter:
        raise AssertionError("The production optional ancestry interface is missing.")
    start = adapter.index(marker)
    opening = adapter.index("{", start + len(marker))
    interface = adapter[start:block_end(adapter, opening)]
    optional = [interface]
    batch_marker = "public interface IElementAncestryBatchContext"
    if batch_marker in adapter:
        batch_start = adapter.index(batch_marker)
        batch_opening = adapter.index("{", batch_start + len(batch_marker))
        optional.append(adapter[batch_start:block_end(adapter, batch_opening)])
    revision_marker = "public interface IRevisionedTransformContext"
    if revision_marker in adapter:
        revision_start = adapter.index(revision_marker)
        revision_opening = adapter.index("{", revision_start + len(revision_marker))
        optional.append(adapter[revision_start:block_end(adapter, revision_opening)])
    return harness + "\nnamespace E3d21AiBridge {\n" + "\n".join(optional) + "\n}\n"
