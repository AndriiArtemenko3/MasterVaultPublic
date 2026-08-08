from importlib.resources import files

import mastervault.change_control as change_control_package
import mastervault.change_control.bootstrap as bootstrap_module
import mastervault.change_control.inference_repository as inference_repository_module
import mastervault.change_control.recorded_inference as recorded_inference_module
import mastervault.change_control.source_note_inventory as source_note_inventory_module
import mastervault.change_control.temporal_analysis as temporal_analysis_module
import mastervault.change_control.temporal_commit as temporal_commit_module
import mastervault.change_control.temporal_proposal as temporal_proposal_module


def test_mastervault_distribution_declares_pep_561_typing() -> None:
    marker = files("mastervault").joinpath("py.typed")
    assert marker.is_file()
    assert marker.read_bytes() in {b"", b"\n"}


def test_change_control_root_reexports_completed_operational_boundaries() -> None:
    for module in (
        bootstrap_module,
        source_note_inventory_module,
        recorded_inference_module,
        inference_repository_module,
        temporal_analysis_module,
        temporal_proposal_module,
        temporal_commit_module,
    ):
        assert set(module.__all__) <= set(change_control_package.__all__)
        for name in module.__all__:
            assert getattr(change_control_package, name) is getattr(module, name)
