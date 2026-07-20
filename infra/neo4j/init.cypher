CREATE CONSTRAINT cpg_node_id IF NOT EXISTS
FOR (node:CPGNode) REQUIRE node.id IS UNIQUE;

CREATE INDEX cpg_node_file IF NOT EXISTS
FOR (node:CPGNode) ON (node.file_id);

CREATE INDEX cpg_edge_id IF NOT EXISTS
FOR ()-[edge:CPG_EDGE]-() ON (edge.id);

CREATE INDEX cpg_edge_file IF NOT EXISTS
FOR ()-[edge:CPG_EDGE]-() ON (edge.file_id);

