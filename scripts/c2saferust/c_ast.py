#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0

from __future__ import annotations

from dataclasses import dataclass

from tree_sitter import Language, Parser
import tree_sitter_c


_C_LANGUAGE = Language(tree_sitter_c.language())


@dataclass(frozen=True)
class _AssignmentChain:
    root: str
    accesses: list[dict[str, str]]
    operator: str
    value: str
    lhs: str


class CTranslationUnit:
    def __init__(self, source_text: str):
        self.source_text = source_text
        self._source_bytes = source_text.encode("utf-8")
        self._parser = Parser(_C_LANGUAGE)
        self._tree = self._parser.parse(self._source_bytes)
        self._root = self._tree.root_node
        self._function_definitions: dict[str, object] = {}
        self._top_level_declarations: list[object] = []
        self._record_definitions: dict[str, object] = {}
        self._function_names: list[str] = []
        self._index_nodes()

    def list_function_names(self) -> list[str]:
        return list(self._function_names)

    def get_function_signature(self, name: str) -> dict | None:
        function_definition = self._function_definitions.get(name)
        if function_definition is None:
            return None

        declarator = function_definition.child_by_field_name("declarator")
        if declarator is None:
            return None

        signature = {
            "name": name,
            "return_type_spelling": self._function_return_type_spelling(function_definition, declarator),
            "parameters": self._parameter_list(self._function_parameter_list(declarator)),
        }
        annotations = self._function_annotation_spellings(function_definition, declarator)
        if annotations:
            signature["annotations"] = annotations
        return signature

    def get_function_body_text(self, name: str) -> str | None:
        function_definition = self._function_definitions.get(name)
        if function_definition is None:
            return None

        body = function_definition.child_by_field_name("body")
        if body is None:
            return None

        body_text = self._text(body)
        if body_text.startswith("{") and body_text.endswith("}"):
            return body_text[1:-1]
        return body_text

    def list_static_struct_initializers(self) -> list[dict]:
        initializers: list[dict] = []
        for declaration in self._top_level_declarations:
            declarator, value = self._declaration_initializer_parts(declaration)
            if declarator is None or value is None or value.type != "initializer_list":
                continue
            if not self._has_storage_class(declaration, "static"):
                continue

            type_name = self._declaration_struct_type_name(declaration)
            if type_name is None:
                continue

            name = self._declared_name(declarator)
            if name is None:
                continue

            fields = self._collect_initializer_fields(value)
            initializers.append(
                {
                    "name": name,
                    "type_name": type_name,
                    "body_text": self._initializer_body_text(value),
                    "fields": fields,
                    "field_map": self._field_map(fields),
                }
            )
        return initializers

    def find_static_initializer(self, struct_name: str) -> dict | None:
        for initializer in self.list_static_struct_initializers():
            if initializer["type_name"] != struct_name:
                continue
            return {
                "name": initializer["name"],
                "body_text": initializer["body_text"],
                "fields": initializer["fields"],
            }
        return None

    def find_initializer_by_name(self, initializer_name: str) -> dict | None:
        for declaration in self._top_level_declarations:
            declarator, value = self._declaration_initializer_parts(declaration)
            if declarator is None or value is None or value.type != "initializer_list":
                continue
            if self._declared_name(declarator) != initializer_name:
                continue
            return {
                "name": initializer_name,
                "body_text": self._initializer_body_text(value),
                "fields": self._collect_initializer_fields(value),
            }
        return None

    def get_initializer_field_map(self, struct_name: str, prefer_first: bool = False) -> dict[str, str]:
        initializer = self.find_static_initializer(struct_name)
        if initializer is None:
            return {}

        field_map: dict[str, str] = {}
        for entry in initializer["fields"]:
            if prefer_first and entry["field"] in field_map:
                continue
            field_map[entry["field"]] = entry["value"]
        return field_map

    def find_record(self, name: str) -> dict | None:
        record = self._record_definitions.get(name)
        if record is None:
            return None

        return {
            "name": name,
            "kind": "struct" if record.type == "struct_specifier" else "union",
            "members": self._record_members(record),
        }

    def find_pointer_array_entries(self, array_name: str) -> list[str]:
        entries: list[str] = []
        for declaration in self._top_level_declarations:
            declarator, value = self._declaration_initializer_parts(declaration)
            if declarator is None or value is None or value.type != "initializer_list":
                continue
            if self._declared_name(declarator) != array_name:
                continue

            for child in value.named_children:
                if child.type != "pointer_expression":
                    continue
                operand = child.child_by_field_name("argument")
                if operand is None:
                    named_children = [item for item in child.named_children if item.type != "&"]
                    operand = named_children[0] if named_children else None
                if operand is None:
                    continue
                entry = self._text(operand).strip()
                if entry and entry not in entries:
                    entries.append(entry)

        return entries

    def list_macro_invocations(self, macro_name: str | None = None) -> list[dict]:
        invocations: list[dict] = []
        for node in self._walk(self._root):
            if node.type != "call_expression":
                continue
            function = node.child_by_field_name("function")
            if function is None:
                continue
            spelling = self._text(function).strip()
            if macro_name is not None and spelling != macro_name:
                continue
            arguments = node.child_by_field_name("arguments")
            if arguments is None:
                continue
            invocations.append(
                {
                    "name": spelling,
                    "arguments": [
                        self._text(argument).strip()
                        for argument in arguments.named_children
                    ],
                    "line": node.start_point[0] + 1,
                }
            )
        return invocations

    def find_macro_invocations(self, macro_name: str) -> list[dict]:
        return self.list_macro_invocations(macro_name)

    def list_call_sites(self, function_name: str) -> list[dict]:
        function_definition = self._function_definitions.get(function_name)
        if function_definition is None:
            return []

        body = function_definition.child_by_field_name("body")
        if body is None:
            return []

        calls: list[dict] = []
        for node in self._walk(body):
            if node.type != "call_expression":
                continue
            function = node.child_by_field_name("function")
            if function is None:
                continue
            calls.append(
                {
                    "function": self._text(function).strip(),
                    "arguments": [
                        self._text(argument).strip()
                        for argument in (node.child_by_field_name("arguments") or node).named_children
                    ]
                    if node.child_by_field_name("arguments") is not None
                    else [],
                    "line": node.start_point[0] + 1,
                }
            )
        return calls

    def find_call_expressions(self, function_name: str) -> list[str]:
        calls: list[str] = []
        for call in self.list_call_sites(function_name):
            spelling = call["function"]
            if spelling and spelling not in calls:
                calls.append(spelling)
        return calls

    def find_member_assignments(self, function_name: str, target_prefix: str) -> list[dict]:
        prefix_root, prefix_accesses = self._parse_target_prefix(target_prefix)
        if prefix_root is None:
            return []

        assignments: list[dict] = []
        for entry in self.find_assignment_chains(function_name):
            if entry["root"] != prefix_root:
                continue
            accesses = entry["accesses"]
            if len(accesses) < len(prefix_accesses) + 1:
                continue
            if accesses[: len(prefix_accesses)] != prefix_accesses:
                continue
            tail = accesses[len(prefix_accesses) :]
            if len(tail) != 1:
                continue
            assignments.append(
                {
                    "field": tail[0]["field"],
                    "operator": entry["operator"],
                    "value": entry["value"],
                }
            )
        return assignments

    def find_assignment_chains(self, function_name: str) -> list[dict]:
        function_definition = self._function_definitions.get(function_name)
        if function_definition is None:
            return []

        body = function_definition.child_by_field_name("body")
        if body is None:
            return []

        assignments: list[dict] = []
        for node in self._walk(body):
            if node.type != "assignment_expression":
                continue

            named_children = list(node.named_children)
            if len(named_children) != 2:
                continue
            lhs, rhs = named_children
            chain = self._field_expression_chain(lhs)
            if chain is None:
                continue

            operator = self._assignment_operator(node)
            assignments.append(
                {
                    "root": chain.root,
                    "accesses": chain.accesses,
                    "operator": operator,
                    "value": self._text(rhs).strip(),
                    "lhs": chain.lhs,
                }
            )

        return assignments

    def find_validate_checks(self, function_name: str) -> list[dict]:
        function_definition = self._function_definitions.get(function_name)
        if function_definition is None:
            return []

        body = function_definition.child_by_field_name("body")
        if body is None:
            return []

        checks: list[dict] = []
        seen: set[tuple[str, str]] = set()
        for node in self._walk(body):
            if node.type != "if_statement":
                continue

            condition = node.child_by_field_name("condition")
            consequence = node.child_by_field_name("consequence")
            if condition is None or consequence is None:
                continue

            field_names = self._condition_subscript_indices(condition)
            if not field_names:
                continue

            return_values = self._return_values_within(consequence)
            if not return_values:
                continue

            for field_name in field_names:
                for return_value in return_values:
                    key = (field_name, return_value)
                    if key in seen:
                        continue
                    seen.add(key)
                    checks.append(
                        {
                            "field": field_name,
                            "return": return_value,
                        }
                    )

        return checks

    def _index_nodes(self) -> None:
        for child in self._root.named_children:
            if child.type == "function_definition":
                declarator = child.child_by_field_name("declarator")
                name = self._declared_name(declarator) if declarator is not None else None
                if name is not None:
                    self._function_definitions.setdefault(name, child)
                    if name not in self._function_names:
                        self._function_names.append(name)
            elif child.type == "declaration":
                self._top_level_declarations.append(child)
            elif child.type in {"struct_specifier", "union_specifier"}:
                name = self._record_name(child)
                if name is not None and self._record_has_body(child):
                    self._record_definitions.setdefault(name, child)

        for declaration in self._top_level_declarations:
            for node in self._walk(declaration):
                if node.type not in {"struct_specifier", "union_specifier"}:
                    continue
                name = self._record_name(node)
                if name is not None and self._record_has_body(node):
                    self._record_definitions.setdefault(name, node)

    def _walk(self, node: object):
        yield node
        for child in node.named_children:
            yield from self._walk(child)

    def _text(self, node: object) -> str:
        return self._source_bytes[node.start_byte : node.end_byte].decode("utf-8", errors="replace")

    def _slice(self, start: int, end: int) -> str:
        return self._source_bytes[start:end].decode("utf-8", errors="replace")

    def _record_name(self, node: object) -> str | None:
        name_node = node.child_by_field_name("name")
        return self._text(name_node) if name_node is not None else None

    def _declaration_initializer_parts(self, declaration: object) -> tuple[object | None, object | None]:
        init_declarator = next(
            (child for child in declaration.named_children if child.type == "init_declarator"),
            None,
        )
        if init_declarator is not None:
            return (
                init_declarator.child_by_field_name("declarator"),
                init_declarator.child_by_field_name("value"),
            )
        return (
            declaration.child_by_field_name("declarator"),
            declaration.child_by_field_name("value"),
        )

    def _has_storage_class(self, declaration: object, storage_name: str) -> bool:
        return any(
            child.type == "storage_class_specifier" and self._text(child).strip() == storage_name
            for child in declaration.children
        )

    def _declaration_struct_type_name(self, declaration: object) -> str | None:
        for child in declaration.named_children:
            if child.type != "struct_specifier":
                continue
            return self._record_name(child)
        return None

    def _record_has_body(self, node: object) -> bool:
        return any(child.type == "field_declaration_list" for child in node.named_children)

    def _field_declaration_list(self, node: object) -> object | None:
        for child in node.named_children:
            if child.type == "field_declaration_list":
                return child
        return None

    def _declared_name(self, declarator: object | None) -> str | None:
        name_node = self._declared_name_node(declarator)
        return self._text(name_node) if name_node is not None else None

    def _declared_name_node(self, declarator: object | None) -> object | None:
        if declarator is None:
            return None
        if declarator.type in {"identifier", "field_identifier"}:
            return declarator
        nested = declarator.child_by_field_name("declarator")
        if nested is not None:
            return self._declared_name_node(nested)
        for child in declarator.named_children:
            name = self._declared_name_node(child)
            if name is not None:
                return name
        return None

    def _declarator_shape_spelling(self, declarator: object | None) -> str:
        if declarator is None:
            return ""
        name_node = self._declared_name_node(declarator)
        if name_node is None:
            return self._normalize_spelling(self._text(declarator))
        prefix = self._slice(declarator.start_byte, name_node.start_byte)
        suffix = self._slice(name_node.end_byte, declarator.end_byte)
        return self._normalize_spelling(f"{prefix}{suffix}")

    def _function_parameter_list(self, declarator: object) -> object | None:
        if declarator.type == "function_declarator":
            return declarator.child_by_field_name("parameters")
        nested = declarator.child_by_field_name("declarator")
        if nested is not None:
            return self._function_parameter_list(nested)
        return None

    def _parameter_list(self, parameter_list: object | None) -> list[dict]:
        if parameter_list is None:
            return []

        parameters: list[dict] = []
        declarations = [child for child in parameter_list.named_children if child.type == "parameter_declaration"]
        if len(declarations) == 1 and self._text(declarations[0]).strip() == "void":
            return []

        for parameter in declarations:
            declarator = parameter.child_by_field_name("declarator")
            name = self._declared_name(declarator)
            type_prefix = self._slice(parameter.start_byte, declarator.start_byte).strip() if declarator is not None else ""
            declarator_shape = self._declarator_shape_spelling(declarator)
            type_spelling = self._normalize_spelling(" ".join(part for part in [type_prefix, declarator_shape] if part))
            if not type_spelling:
                type_spelling = self._text(parameter).strip()
            parameters.append(
                {
                    "name": name,
                    "type_spelling": type_spelling,
                    "spelling": self._text(parameter).strip(),
                }
            )
        return parameters

    def _function_return_type_spelling(self, function_definition: object, declarator: object) -> str:
        base_parts: list[str] = []
        for child in function_definition.children:
            if child == declarator:
                break
            if not child.is_named or child.type == "storage_class_specifier":
                continue
            if self._is_type_like_node(child):
                base_parts.append(self._text(child).strip())
        return self._normalize_spelling(" ".join(base_parts))

    def _is_static_struct_initializer(self, declaration: object, struct_name: str) -> bool:
        if not self._has_storage_class(declaration, "static"):
            return False

        return self._declaration_struct_type_name(declaration) == struct_name

    def _initializer_body_text(self, initializer: object) -> str:
        text = self._text(initializer)
        if text.startswith("{") and text.endswith("}"):
            return text[1:-1]
        return text

    def _collect_initializer_fields(self, node: object) -> list[dict]:
        fields: list[dict] = []
        for child in node.named_children:
            if child.type == "initializer_pair":
                designator = child.child_by_field_name("designator")
                value = child.child_by_field_name("value")
                if designator is None or value is None:
                    continue
                field_identifier = None
                for designator_child in designator.named_children:
                    if designator_child.type == "field_identifier":
                        field_identifier = designator_child
                        break
                if field_identifier is None:
                    continue
                fields.append(
                    {
                        "field": self._text(field_identifier),
                        "value": self._text(value).strip(),
                    }
                )
            elif child.type == "initializer_list":
                fields.extend(self._collect_initializer_fields(child))
        return fields

    def _field_map(self, fields: list[dict], *, prefer_first: bool = False) -> dict[str, str]:
        field_map: dict[str, str] = {}
        for entry in fields:
            field = entry["field"]
            if prefer_first and field in field_map:
                continue
            field_map[field] = entry["value"]
        return field_map

    def _record_members(self, record: object) -> list[dict]:
        field_list = self._field_declaration_list(record)
        if field_list is None:
            return []

        members: list[dict] = []
        for child in field_list.named_children:
            if child.type != "field_declaration":
                continue
            members.extend(self._field_declaration_members(child))
        return members

    def _field_declaration_members(self, declaration: object) -> list[dict]:
        declarator = declaration.child_by_field_name("declarator")
        if declarator is not None:
            if declarator.type == "function_declarator":
                return [self._function_pointer_member(declaration, declarator)]
            if self._declaration_contains_function_declarator(declarator):
                function_declarator = self._innermost_function_declarator(declarator)
                if function_declarator is not None:
                    return [self._function_pointer_member(declaration, function_declarator)]
            return [self._regular_field_member(declaration, declarator)]

        named_specifier = next(
            (
                child
                for child in declaration.named_children
                if child.type in {"struct_specifier", "union_specifier"} and self._record_has_body(child)
            ),
            None,
        )
        if named_specifier is None:
            return []

        return [
            {
                "kind": "anonymous_struct" if named_specifier.type == "struct_specifier" else "anonymous_union",
                "name": None,
                "children": self._record_members(named_specifier),
            }
        ]

    def _regular_field_member(self, declaration: object, declarator: object) -> dict:
        type_prefix = self._slice(declaration.start_byte, declarator.start_byte).strip()
        declarator_shape = self._declarator_shape_spelling(declarator)
        type_spelling = self._normalize_spelling(" ".join(part for part in [type_prefix, declarator_shape] if part))
        member = {
            "kind": "field",
            "name": self._declared_name(declarator),
            "type_spelling": type_spelling or self._text(declaration).strip().removesuffix(";"),
            "children": [],
        }
        bit_width = self._bitfield_width(declaration)
        if bit_width is not None:
            member["bit_width_spelling"] = bit_width
            if bit_width.isdigit():
                member["bit_width"] = int(bit_width)
        return member

    def _function_pointer_member(self, declaration: object, function_declarator: object) -> dict:
        parameters = self._parameter_list(function_declarator.child_by_field_name("parameters"))
        return_type = self._slice(declaration.start_byte, function_declarator.start_byte).strip()
        return {
            "kind": "function_pointer",
            "name": self._declared_name(function_declarator),
            "return_type_spelling": return_type,
            "type_spelling": f"{return_type} (*)({', '.join(parameter['type_spelling'] for parameter in parameters)})",
            "parameters": parameters,
            "children": [],
        }

    def _declaration_contains_function_declarator(self, declarator: object) -> bool:
        if declarator.type == "function_declarator":
            return True
        nested = declarator.child_by_field_name("declarator")
        return self._declaration_contains_function_declarator(nested) if nested is not None else False

    def _innermost_function_declarator(self, declarator: object) -> object | None:
        if declarator.type == "function_declarator":
            return declarator
        nested = declarator.child_by_field_name("declarator")
        return self._innermost_function_declarator(nested) if nested is not None else None

    def _field_expression_chain(self, node: object) -> _AssignmentChain | None:
        if node.type == "identifier":
            return _AssignmentChain(
                root=self._text(node),
                accesses=[],
                operator="",
                value="",
                lhs=self._text(node),
            )
        if node.type != "field_expression":
            return None

        target = None
        operator = None
        field = None
        for child in node.children:
            if child.type == "field_identifier":
                field = self._text(child)
            elif child.type in {".", "->"}:
                operator = self._text(child)
            elif child.is_named:
                target = child

        if target is None or operator is None or field is None:
            return None

        prefix = self._field_expression_chain(target)
        if prefix is None:
            return None

        return _AssignmentChain(
            root=prefix.root,
            accesses=prefix.accesses + [{"operator": operator, "field": field}],
            operator="",
            value="",
            lhs=self._text(node),
        )

    def _assignment_operator(self, node: object) -> str:
        named = {child for child in node.named_children}
        for child in node.children:
            if child in named:
                continue
            spelling = self._text(child).strip()
            if spelling in {"=", "+=", "-=", "*=", "/=", "%=", "&=", "^=", "|=", "<<=", ">>="}:
                return spelling
        return "="

    def _parse_target_prefix(self, target_prefix: str) -> tuple[str | None, list[dict[str, str]]]:
        target_prefix = target_prefix.strip()
        if not target_prefix:
            return None, []

        operator = "->" if "->" in target_prefix else "."
        parts = [part for part in target_prefix.replace("->", ".").split(".") if part]
        if not parts:
            return None, []

        accesses: list[dict[str, str]] = []
        current_operator = operator
        for field in parts[1:]:
            accesses.append(
                {
                    "operator": current_operator,
                    "field": field,
                }
            )
            current_operator = "."
        return parts[0], accesses

    def _condition_subscript_indices(self, condition: object) -> list[str]:
        indices: list[str] = []
        for node in self._walk(condition):
            if node.type != "subscript_expression":
                continue
            index = node.child_by_field_name("index")
            if index is None and len(node.named_children) >= 2:
                index = node.named_children[1]
            if index is None:
                continue
            spelling = self._text(index).strip()
            if spelling and spelling not in indices:
                indices.append(spelling)
        return indices

    def _return_values_within(self, node: object) -> list[str]:
        values: list[str] = []
        for child in self._walk(node):
            if child.type != "return_statement":
                continue
            value = self._return_value_text(child)
            if value is not None and value not in values:
                values.append(value)
        return values

    def _return_value_text(self, node: object) -> str | None:
        if node.type != "return_statement":
            return None
        named_children = list(node.named_children)
        if not named_children:
            return ""
        return self._text(named_children[0]).strip()

    def _function_annotation_spellings(self, function_definition: object, declarator: object) -> list[str]:
        annotations: list[str] = []
        for child in function_definition.children:
            if child == declarator:
                break
            if not child.is_named or child.type == "storage_class_specifier" or self._is_type_like_node(child):
                continue
            annotation = self._text(child).strip()
            if annotation:
                annotations.append(annotation)
        return annotations

    def _is_type_like_node(self, node: object) -> bool:
        return node.type in {
            "primitive_type",
            "sized_type_specifier",
            "type_identifier",
            "struct_specifier",
            "union_specifier",
            "enum_specifier",
            "macro_type_specifier",
            "type_qualifier",
            "qualified_identifier",
        }

    def _normalize_spelling(self, text: str) -> str:
        return " ".join(text.split())

    def _bitfield_width(self, declaration: object) -> str | None:
        clause = next((child for child in declaration.named_children if child.type == "bitfield_clause"), None)
        if clause is None:
            return None
        text = self._text(clause).strip()
        return text[1:].strip() if text.startswith(":") else text
