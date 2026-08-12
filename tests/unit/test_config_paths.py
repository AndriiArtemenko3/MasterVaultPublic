from pathlib import Path

from mastervault.config import PathsCfg


def test_change_control_generation_root_is_derived_outside_legacy_paths() -> None:
    paths = PathsCfg(workspace=Path("/workspace"))

    assert paths.change_control_generation_root == Path("/workspace/change_control/generations")
    assert paths.change_control_generation_root != paths.vault_dir
    assert paths.change_control_generation_root != paths.sqlite_path
