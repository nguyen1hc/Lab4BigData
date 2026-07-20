from __future__ import annotations

import ast
from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable

from .ids import edge_id, node_id
from .models import AnalysisResult, GraphEdge, GraphNode


SCOPE_TYPES = (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
FUNCTION_TYPES = (ast.FunctionDef, ast.AsyncFunctionDef)


@dataclass(slots=True)
class _LoopContext:
    continue_target: str | None = None
    break_targets: tuple[str, ...] = ()
    scope_exit: str = ""


class _NameVisitor(ast.NodeVisitor):
    """Collect names in one statement without entering nested scopes."""

    def __init__(self, root: ast.AST) -> None:
        self.root = root
        self.definitions: dict[str, set[ast.AST]] = defaultdict(set)
        self.uses: list[tuple[str, ast.Name]] = []

    def visit_Name(self, node: ast.Name) -> None:  # noqa: N802
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            self.definitions[node.id].add(node)
        elif isinstance(node.ctx, ast.Load):
            self.uses.append((node.id, node))

    def visit_Import(self, node: ast.Import) -> None:  # noqa: N802
        for alias in node.names:
            self.definitions[alias.asname or alias.name.split(".")[0]].add(alias)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:  # noqa: N802
        for alias in node.names:
            self.definitions[alias.asname or alias.name].add(alias)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        if node is self.root:
            self.definitions[node.name].add(node)
            return
        self.definitions[node.name].add(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # noqa: N802
        self.visit_FunctionDef(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:  # noqa: N802
        self.definitions[node.name].add(node)

    def visit_Lambda(self, node: ast.Lambda) -> None:  # noqa: N802
        return


class CPGAnalyzer:
    """Build a bounded, file-local educational Code Property Graph.

    The analyzer is intentionally conservative. CFG is statement-level, DFG uses
    reaching definitions inside lexical scopes, and call resolution only covers
    names that can be resolved from the current file.
    """

    def __init__(self, source: str, file_identifier: str, filename: str = "<unknown>") -> None:
        self.source = source
        self.file_identifier = file_identifier
        self.filename = filename
        self.tree = ast.parse(source, filename=filename, type_comments=True)
        self.result = AnalysisResult()
        self.paths: dict[int, str] = {}
        self.node_ids: dict[int, str] = {}
        self._node_index: dict[str, GraphNode] = {}
        self._edge_index: dict[str, GraphEdge] = {}

    def analyze(self) -> AnalysisResult:
        self._build_ast_graph()
        for scope in self._iter_scopes(self.tree):
            self._build_scope_graph(scope)
        self._build_call_graph()
        self.result.nodes = list(self._node_index.values())
        self.result.edges = list(self._edge_index.values())
        return self.result

    def _build_ast_graph(self) -> None:
        def visit(node: ast.AST, path: str, parent: ast.AST | None = None, field: str = "") -> None:
            self.paths[id(node)] = path
            identifier = node_id(self.file_identifier, path, type(node).__name__)
            self.node_ids[id(node)] = identifier
            graph_node = GraphNode(
                id=identifier,
                kind="AST",
                ast_type=type(node).__name__,
                file_id=self.file_identifier,
                structural_path=path,
                line=getattr(node, "lineno", 0),
                column=getattr(node, "col_offset", 0),
                end_line=getattr(node, "end_lineno", 0) or 0,
                end_column=getattr(node, "end_col_offset", 0) or 0,
                name=self._node_name(node),
                value=self._node_value(node),
            )
            self._add_node(graph_node)
            if parent is not None:
                self._add_edge("AST", self.node_ids[id(parent)], identifier, field)

            for child_field, value in ast.iter_fields(node):
                if isinstance(value, ast.AST):
                    visit(value, f"{path}.{child_field}", node, child_field)
                elif isinstance(value, list):
                    for index, item in enumerate(value):
                        if isinstance(item, ast.AST):
                            visit(item, f"{path}.{child_field}[{index}]", node, f"{child_field}[{index}]")

        visit(self.tree, "$")

    @staticmethod
    def _node_name(node: ast.AST) -> str:
        for attribute in ("name", "id", "arg", "module"):
            value = getattr(node, attribute, None)
            if isinstance(value, str):
                return value[:200]
        if isinstance(node, ast.alias):
            return node.asname or node.name
        return ""

    @staticmethod
    def _node_value(node: ast.AST) -> str:
        if isinstance(node, ast.Constant):
            return repr(node.value)[:200]
        if isinstance(node, ast.operator):
            return type(node).__name__
        return ""

    @staticmethod
    def _iter_scopes(root: ast.AST) -> Iterable[ast.AST]:
        for node in ast.walk(root):
            if isinstance(node, SCOPE_TYPES):
                yield node

    def _build_scope_graph(self, scope: ast.AST) -> None:
        path = self.paths[id(scope)]
        entry_id = self._add_synthetic_node(f"{path}.__entry__", "ScopeEntry", "ENTRY")
        exit_id = self._add_synthetic_node(f"{path}.__exit__", "ScopeExit", "EXIT")
        body = list(getattr(scope, "body", []))
        context = _LoopContext(scope_exit=exit_id)
        first = self._connect_block(body, (exit_id,), context)
        self._add_edge("CFG", entry_id, first or exit_id, "scope-entry")
        statements = self._statements_in_scope(scope)
        self._build_dfg(scope, statements, entry_id)

    def _connect_block(
        self,
        statements: list[ast.stmt],
        follow: tuple[str, ...],
        context: _LoopContext,
    ) -> str | None:
        current_follow = follow
        first: str | None = follow[0] if follow else None
        for statement in reversed(statements):
            first = self._connect_statement(statement, current_follow, context)
            current_follow = (first,)
        return first if statements else (follow[0] if follow else None)

    def _connect_statement(
        self,
        statement: ast.stmt,
        follow: tuple[str, ...],
        context: _LoopContext,
    ) -> str:
        current = self.node_ids[id(statement)]

        if isinstance(statement, ast.If):
            then_entry = self._connect_block(statement.body, follow, context)
            else_entry = self._connect_block(statement.orelse, follow, context) if statement.orelse else (follow[0] if follow else context.scope_exit)
            self._add_edge("CFG", current, then_entry or context.scope_exit, "if-true")
            self._add_edge("CFG", current, else_entry or context.scope_exit, "if-false")
            return current

        if isinstance(statement, (ast.For, ast.AsyncFor, ast.While)):
            after_loop = follow
            false_entry = self._connect_block(statement.orelse, follow, context) if statement.orelse else (follow[0] if follow else context.scope_exit)
            loop_context = _LoopContext(
                continue_target=current,
                break_targets=after_loop,
                scope_exit=context.scope_exit,
            )
            body_entry = self._connect_block(statement.body, (current,), loop_context)
            self._add_edge("CFG", current, body_entry or current, "loop-body")
            self._add_edge("CFG", current, false_entry or context.scope_exit, "loop-exit")
            return current

        if isinstance(statement, (ast.Return, ast.Raise)):
            self._add_edge("CFG", current, context.scope_exit, type(statement).__name__.lower())
            return current

        if isinstance(statement, ast.Break):
            target = context.break_targets[0] if context.break_targets else context.scope_exit
            self._add_edge("CFG", current, target, "break")
            if not context.break_targets:
                self.result.warnings.append(f"break outside modeled loop at line {getattr(statement, 'lineno', 0)}")
            return current

        if isinstance(statement, ast.Continue):
            target = context.continue_target or context.scope_exit
            self._add_edge("CFG", current, target, "continue")
            if context.continue_target is None:
                self.result.warnings.append(f"continue outside modeled loop at line {getattr(statement, 'lineno', 0)}")
            return current

        if isinstance(statement, (ast.With, ast.AsyncWith)):
            body_entry = self._connect_block(statement.body, follow, context)
            self._add_edge("CFG", current, body_entry or (follow[0] if follow else context.scope_exit), "with-body")
            return current

        if isinstance(statement, ast.Try):
            final_follow = follow
            if statement.finalbody:
                final_entry = self._connect_block(statement.finalbody, follow, context)
                final_follow = (final_entry,) if final_entry else follow
            normal_follow = final_follow
            if statement.orelse:
                else_entry = self._connect_block(statement.orelse, final_follow, context)
                normal_follow = (else_entry,) if else_entry else final_follow
            body_entry = self._connect_block(statement.body, normal_follow, context)
            self._add_edge("CFG", current, body_entry or context.scope_exit, "try-body")
            for index, handler in enumerate(statement.handlers):
                handler_entry = self._connect_block(handler.body, final_follow, context)
                self._add_edge("CFG", current, handler_entry or context.scope_exit, f"except-{index}")
            self.result.warnings.append(f"exception flow is conservative at line {getattr(statement, 'lineno', 0)}")
            return current

        if isinstance(statement, ast.Match):
            for index, case in enumerate(statement.cases):
                case_entry = self._connect_block(case.body, follow, context)
                self._add_edge("CFG", current, case_entry or context.scope_exit, f"match-case-{index}")
            self.result.warnings.append(f"match guards are conservative at line {getattr(statement, 'lineno', 0)}")
            return current

        targets = follow or (context.scope_exit,)
        for index, target in enumerate(targets):
            self._add_edge("CFG", current, target, "next" if index == 0 else f"next-{index}")
        return current

    def _statements_in_scope(self, scope: ast.AST) -> list[ast.stmt]:
        result: list[ast.stmt] = []

        def collect_block(block: list[ast.stmt]) -> None:
            for statement in block:
                result.append(statement)
                if isinstance(statement, FUNCTION_TYPES + (ast.ClassDef,)):
                    continue
                for field, value in ast.iter_fields(statement):
                    if field in {"body", "orelse", "finalbody"} and isinstance(value, list):
                        collect_block([item for item in value if isinstance(item, ast.stmt)])
                    elif field == "handlers" and isinstance(value, list):
                        for handler in value:
                            if isinstance(handler, ast.ExceptHandler):
                                collect_block(handler.body)
                    elif field == "cases" and isinstance(value, list):
                        for case in value:
                            if isinstance(case, ast.match_case):
                                collect_block(case.body)

        collect_block(list(getattr(scope, "body", [])))
        return result

    def _build_dfg(self, scope: ast.AST, statements: list[ast.stmt], entry_id: str) -> None:
        statement_ids = {self.node_ids[id(statement)] for statement in statements}
        defs_by_statement: dict[str, dict[str, set[str]]] = {}
        uses_by_statement: dict[str, list[tuple[str, str]]] = {}

        for statement in statements:
            visitor = _NameVisitor(statement)
            visitor.visit(statement)
            sid = self.node_ids[id(statement)]
            defs_by_statement[sid] = {
                name: {self.node_ids[id(node)] for node in nodes if id(node) in self.node_ids}
                for name, nodes in visitor.definitions.items()
            }
            uses_by_statement[sid] = [
                (name, self.node_ids[id(node)]) for name, node in visitor.uses if id(node) in self.node_ids
            ]

        seed: dict[str, set[str]] = defaultdict(set)
        if isinstance(scope, FUNCTION_TYPES):
            arguments = [*scope.args.posonlyargs, *scope.args.args, *scope.args.kwonlyargs]
            if scope.args.vararg:
                arguments.append(scope.args.vararg)
            if scope.args.kwarg:
                arguments.append(scope.args.kwarg)
            for argument in arguments:
                seed[argument.arg].add(self.node_ids[id(argument)])

        predecessors: dict[str, set[str]] = defaultdict(set)
        for edge in self._edge_index.values():
            if edge.kind != "CFG":
                continue
            if edge.target_id in statement_ids and (edge.source_id in statement_ids or edge.source_id == entry_id):
                predecessors[edge.target_id].add(edge.source_id)

        in_state: dict[str, dict[str, set[str]]] = {sid: {} for sid in statement_ids}
        out_state: dict[str, dict[str, set[str]]] = {sid: {} for sid in statement_ids}
        out_state[entry_id] = {name: set(ids) for name, ids in seed.items()}

        changed = True
        iterations = 0
        ordered = [self.node_ids[id(statement)] for statement in statements]
        while changed and iterations < max(10, len(statements) * 4):
            changed = False
            iterations += 1
            for sid in ordered:
                incoming: dict[str, set[str]] = defaultdict(set)
                for predecessor in predecessors.get(sid, {entry_id} if not predecessors.get(sid) else set()):
                    for name, ids in out_state.get(predecessor, {}).items():
                        incoming[name].update(ids)
                generated = defs_by_statement.get(sid, {})
                outgoing = {name: set(ids) for name, ids in incoming.items()}
                for name, ids in generated.items():
                    outgoing[name] = set(ids)
                normalized_in = {name: set(ids) for name, ids in incoming.items()}
                if normalized_in != in_state[sid] or outgoing != out_state[sid]:
                    in_state[sid] = normalized_in
                    out_state[sid] = outgoing
                    changed = True

        if iterations >= max(10, len(statements) * 4) and changed:
            self.result.warnings.append(f"DFG fixpoint limit reached for scope {self.paths[id(scope)]}")

        for sid, uses in uses_by_statement.items():
            for variable, use_id in uses:
                definitions = in_state.get(sid, {}).get(variable, set())
                if not definitions:
                    external = self._add_external_node(self.paths[id(scope)], f"dfg:{variable}", variable)
                    self._add_edge("DFG", external, use_id, variable, variable=variable, resolved=False)
                else:
                    for definition in sorted(definitions):
                        self._add_edge("DFG", definition, use_id, variable, variable=variable)

    def _build_call_graph(self) -> None:
        symbols: dict[str, str] = {}
        for node in ast.walk(self.tree):
            if isinstance(node, FUNCTION_TYPES):
                symbols.setdefault(node.name, self.node_ids[id(node)])

        for call in (node for node in ast.walk(self.tree) if isinstance(node, ast.Call)):
            callee = self._dotted_name(call.func) or "<dynamic-call>"
            short_name = callee.rsplit(".", 1)[-1]
            target = symbols.get(callee) or symbols.get(short_name)
            resolved = target is not None
            if target is None:
                target = self._add_external_node("$", f"call:{callee}", callee)
            self._add_edge("CALL", self.node_ids[id(call)], target, callee, resolved=resolved)

    @staticmethod
    def _dotted_name(node: ast.AST) -> str:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            prefix = CPGAnalyzer._dotted_name(node.value)
            return f"{prefix}.{node.attr}" if prefix else node.attr
        return ""

    def _add_synthetic_node(self, structural_path: str, ast_type: str, name: str) -> str:
        identifier = node_id(self.file_identifier, structural_path, ast_type)
        self._add_node(
            GraphNode(
                id=identifier,
                kind="SYNTHETIC",
                ast_type=ast_type,
                file_id=self.file_identifier,
                structural_path=structural_path,
                name=name,
            )
        )
        return identifier

    def _add_external_node(self, scope_path: str, discriminator: str, name: str) -> str:
        structural_path = f"{scope_path}.__external__.{discriminator}"
        identifier = node_id(self.file_identifier, structural_path, "ExternalSymbol")
        self._add_node(
            GraphNode(
                id=identifier,
                kind="EXTERNAL",
                ast_type="ExternalSymbol",
                file_id=self.file_identifier,
                structural_path=structural_path,
                name=name,
            )
        )
        return identifier

    def _add_node(self, node: GraphNode) -> None:
        self._node_index[node.id] = node

    def _add_edge(
        self,
        kind: str,
        source: str,
        target: str,
        discriminator: str = "",
        *,
        variable: str = "",
        resolved: bool = True,
    ) -> None:
        identifier = edge_id(kind, source, target, discriminator)
        self._edge_index[identifier] = GraphEdge(
            id=identifier,
            kind=kind,
            source_id=source,
            target_id=target,
            file_id=self.file_identifier,
            discriminator=discriminator,
            variable=variable,
            resolved=resolved,
        )

