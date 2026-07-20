from cpg_parser.manifest import Manifest


def test_manifest_returns_stale_elements(tmp_path):
    path = tmp_path / "manifest.sqlite"
    with Manifest(path) as manifest:
        manifest.replace_file("file", "hash1", "time1", {"n1", "n2"}, {"e1", "e2"})
        stale_nodes, stale_edges = manifest.stale_elements("file", {"n2", "n3"}, {"e2", "e3"})
        assert stale_nodes == {"n1"}
        assert stale_edges == {"e1"}
        assert manifest.content_hash("file") == "hash1"

