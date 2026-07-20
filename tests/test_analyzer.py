from pathlib import Path

from cpg_parser.analyzer import CPGAnalyzer


FIXTURE = Path(__file__).parent / "fixtures" / "sample_repo" / "app.py"


def analyze():
    source = FIXTURE.read_text(encoding="utf-8")
    return CPGAnalyzer(source, "f" * 64, "app.py").analyze()


def test_ids_are_deterministic():
    first = analyze()
    second = analyze()
    assert {node.id for node in first.nodes} == {node.id for node in second.nodes}
    assert {edge.id for edge in first.edges} == {edge.id for edge in second.edges}


def test_all_required_graph_categories_exist():
    result = analyze()
    assert {"AST", "CFG", "DFG", "CALL"} <= {edge.kind for edge in result.edges}
    assert any(edge.discriminator == "if-true" for edge in result.edges if edge.kind == "CFG")
    assert any(edge.discriminator == "loop-body" for edge in result.edges if edge.kind == "CFG")
    assert any(edge.variable == "total" for edge in result.edges if edge.kind == "DFG")


def test_call_resolution_has_internal_and_external_targets():
    result = analyze()
    call_edges = [edge for edge in result.edges if edge.kind == "CALL"]
    assert any(edge.resolved for edge in call_edges)
    assert any(not edge.resolved for edge in call_edges)

