"""Разбор одной аннотированной Python-функции в core.classes.Program.

Пример:

    def count_down(n):
        # requires n >= 0
        # ensures result == 0
        while n > 0:
            # invariant n >= 0
            n -= 1
        return n

Аннотации стоят в начале нужного тела на его уровне отступа.
Формы логики: implies(P, Q), forall(lambda x: P), exists(lambda x: P).
Имя result обозначает возвращаемое значение.
Вызовы в выражениях трактуются как термы без побочных эффектов.
"""

import ast
import math
import re
import tokenize
from bisect import bisect_left
from dataclasses import dataclass
from io import StringIO
from itertools import count, pairwise

from core.classes import (
    Assert,
    Assign,
    Assume,
    Attribute,
    BinaryOp,
    BoolConstant,
    BoolOp,
    Call,
    Command,
    Comparison,
    Constant,
    Expr,
    Formula,
    If,
    Not,
    Program,
    Quantifier,
    Return,
    UnaryOp,
    Variable,
    While,
)


_ANNOTATION = re.compile(r"#\s*(requires|ensures|invariant)\b\s*:?\s*")
_OPERATORS = {
    ast.Add: "+", ast.Sub: "-", ast.Mult: "*", ast.Div: "/",
    ast.FloorDiv: "//", ast.Mod: "%",
    ast.UAdd: "+", ast.USub: "-",
    ast.Eq: "==", ast.NotEq: "!=", ast.Lt: "<", ast.LtE: "<=",
    ast.Gt: ">", ast.GtE: ">=", ast.And: "and", ast.Or: "or",
}


class ParseError(SyntaxError):
    """Недопустимый синтаксис с номером строки и столбца исходного текста."""


@dataclass(frozen=True)
class _Annotation:
    kind: str
    column: int
    expression: ast.expr


def parse(source: str) -> Program:
    """Построить AST проекта из текста Python-функции."""
    return _Parser(source).parse_program()


class _Parser:
    def __init__(self, source: str) -> None:
        source = source.replace("\r\n", "\n").replace("\r", "\n")
        self.lines = source.split("\n")
        try:
            self.tree = ast.parse(source, filename="<source>")
        except SyntaxError as error:
            raise ParseError(*error.args) from None

        self.annotations: dict[int, _Annotation] = {}
        self.logical_line_ends: list[int] = []
        self.used_names: set[str] = set()
        # tokenize сохраняет комментарии с аннотациями.
        for token in tokenize.generate_tokens(StringIO(source).readline):
            if token.type == tokenize.NEWLINE:
                self.logical_line_ends.append(token.start[0])
            elif token.type == tokenize.COMMENT:
                self._read_annotation(token)

        for node in ast.walk(self.tree):
            self._remember_name(node)
        self.temporary_ids = count()

    def parse_program(self) -> Program:
        function, params = self._read_function_signature()
        body, annotations = self._body_with_annotations(function, "requires", "ensures")

        contract: dict[str, list[Formula]] = {"requires": [], "ensures": []}
        for annotation in annotations:
            formula = self._convert_formula(annotation.expression)
            contract[annotation.kind].append(formula)

        commands = self._convert_block(body)
        if self.annotations:
            line, annotation = next(iter(self.annotations.items()))
            raise self._error_at(
                f"Аннотация {annotation.kind} находится вне начала нужного блока",
                line, annotation.column,
            )
        return Program(
            name=function.name,
            params=params,
            requires=contract["requires"],
            ensures=contract["ensures"],
            body=commands,
        )

    def _read_function_signature(self) -> tuple[ast.FunctionDef, list[str]]:
        statements = self.tree.body
        if ast.get_docstring(self.tree) is not None:
            statements = statements[1:]
        if not statements:
            raise self._error_at("Ожидается одна функция def", 1, 0)
        function = statements[0]
        if not isinstance(function, ast.FunctionDef):
            raise self._error("Ожидается функция def", function)
        if len(statements) != 1:
            raise self._error("В исходном тексте должна быть ровно одна функция", statements[1])
        if function.decorator_list:
            raise self._error("Декораторы функции не поддерживаются", function.decorator_list[0])
        if getattr(function, "type_params", []):
            raise self._error("Параметры типов не поддерживаются", function)

        args = function.args
        has_keyword_defaults = any(default is not None for default in args.kw_defaults)
        if args.vararg or args.kwarg or args.defaults or has_keyword_defaults:
            raise self._error("Параметры с * или значениями по умолчанию не поддерживаются", function)
        parameters = [*args.posonlyargs, *args.args, *args.kwonlyargs]
        for parameter in parameters:
            if parameter.arg == "result":
                raise self._error("Имя result зарезервировано для возвращаемого значения", parameter)
        return function, self._parameter_names(parameters)

    def _parameter_names(self, args: list[ast.arg]) -> list[str]:
        names = []
        for arg in args:
            if arg.arg in names:
                raise self._error(f"Повторное имя параметра {arg.arg}", arg)
            names.append(arg.arg)
        return names

    def _remember_name(self, node: ast.AST) -> None:
        if isinstance(node, ast.Name):
            self.used_names.add(node.id)
        elif isinstance(node, ast.arg):
            self.used_names.add(node.arg)

    def _error_at(self, message: str, line: int, column: int) -> ParseError:
        text = self.lines[line - 1]
        return ParseError(message, ("<source>", line, column + 1, text))

    def _error(self, message: str, node: ast.AST) -> ParseError:
        line = node.lineno
        # AST хранит смещение в байтах UTF-8.
        byte_prefix = self.lines[line - 1].encode("utf-8")[:node.col_offset]
        return self._error_at(message, line, len(byte_prefix.decode("utf-8")))

    def _read_annotation(self, token: tokenize.TokenInfo) -> None:
        match = _ANNOTATION.match(token.string)
        if match is None:
            return
        kind = match[1]
        line, column = token.start
        if token.line[:column].strip():
            raise self._error_at(f"Аннотацию {kind} нужно писать отдельной строкой", line, column)

        start = column + match.end()
        formula = token.string[match.end():]
        if not formula:
            raise self._error_at(f"После {kind} ожидается формула", line, start)
        try:
            expression = ast.parse(formula, mode="eval").body
        except SyntaxError as error:
            offset = max((error.offset or 1) - 1, 0)
            raise self._error_at(f"Ошибка в {kind}: {error.msg}", line, start + offset) from None

        # Восстанавливаем координаты формулы в исходнике.
        ast.increment_lineno(expression, line - 1)
        prefix_size = len(token.line[:start].encode("utf-8"))
        for node in ast.walk(expression):
            if hasattr(node, "col_offset"):
                node.col_offset += prefix_size
            self._remember_name(node)
        self.annotations[line] = _Annotation(kind, column, expression)

    def _take_leading_annotations(
        self, after_line: int, first_statement: ast.stmt, allowed: tuple[str, ...],
    ) -> list[_Annotation]:
        annotations = []
        for line in range(after_line + 1, first_statement.lineno):
            annotation = self.annotations.get(line)
            if annotation is None or annotation.column != first_statement.col_offset:
                continue
            if annotation.kind not in allowed:
                raise self._error_at(
                    f"Аннотация {annotation.kind} здесь недопустима", line, annotation.column
                )
            annotations.append(self.annotations.pop(line))
        return annotations

    def _body_with_annotations(
        self, node: ast.FunctionDef | ast.While, *allowed: str,
    ) -> tuple[list[ast.stmt], list[_Annotation]]:
        # NEWLINE завершает логическую строку заголовка.
        end_index = bisect_left(self.logical_line_ends, node.lineno)
        header_end = self.logical_line_ends[end_index]
        body = node.body
        annotations = self._take_leading_annotations(header_end, body[0], allowed)
        if isinstance(node, ast.FunctionDef) and ast.get_docstring(node) is not None:
            docstring, *body = body
            if body:
                annotations.extend(self._take_leading_annotations(docstring.end_lineno, body[0], allowed))
        return body, annotations

    def _convert_block(self, statements: list[ast.stmt]) -> list[Command]:
        commands = []
        for statement in statements:
            commands.extend(self._convert_statement(statement))
        return commands

    def _convert_statement(self, node: ast.stmt) -> list[Command]:
        match node:
            case ast.Pass():
                # Пустая последовательность соответствует skip.
                return []
            case ast.Assign(targets=[target], value=value):
                return self._convert_assignment(target, value)
            case ast.AnnAssign(target=ast.Name() as target, value=value) if value is not None:
                return self._convert_assignment(target, value)
            case ast.AugAssign(target=target, op=operator, value=value):
                variable = self._assignment_target(target)
                op = self._operator(operator, node)
                expression = BinaryOp(op, variable, self._convert_expression(value))
                return [Assign(variable, expression)]
            case ast.If(test=test, body=body, orelse=otherwise):
                # В AST Python elif уже вложен в ветвь else.
                condition = self._convert_formula(test)
                then_body = self._convert_block(body)
                else_body = self._convert_block(otherwise)
                return [If(condition, then_body, else_body)]
            case ast.While(test=test, orelse=[]):
                body, annotations = self._body_with_annotations(node, "invariant")
                if len(annotations) != 1:
                    raise self._error("Циклу while требуется ровно одна аннотация invariant", node)
                invariant = self._convert_formula(annotations[0].expression)
                condition = self._convert_formula(test)
                return [While(condition, invariant, self._convert_block(body))]
            case ast.Assert(test=test):
                return [Assert(self._convert_formula(test))]
            case ast.Expr(value=ast.Call(func=ast.Name(id="assume"), args=[test], keywords=[])):
                return [Assume(self._convert_formula(test))]
            case ast.Return(value=value) if value is not None:
                return [Return(self._convert_expression(value))]
            case _:
                raise self._error(f"Команда {type(node).__name__} в таком виде не поддерживается", node)

    def _convert_assignment(self, target: ast.expr, value: ast.expr) -> list[Command]:
        if not isinstance(target, (ast.Tuple, ast.List)):
            return [Assign(self._assignment_target(target), self._convert_expression(value))]
        if not isinstance(value, (ast.Tuple, ast.List)) or len(target.elts) != len(value.elts):
            raise self._error("В параллельном присваивании нужны списки одинаковой длины", value)

        variables = [self._assignment_target(item) for item in target.elts]
        values = [self._convert_expression(item) for item in value.elts]
        temporaries = [self._fresh_variable() for _ in values]
        # Сохраняем правые части до изменения целей.
        save_values = [Assign(temp, expr) for temp, expr in zip(temporaries, values)]
        update_targets = [Assign(var, temp) for var, temp in zip(variables, temporaries)]
        return save_values + update_targets

    def _fresh_variable(self) -> Variable:
        for number in self.temporary_ids:
            name = f"__parser_tmp_{number}"
            if name not in self.used_names:
                return Variable(name)

    def _assignment_target(self, node: ast.expr) -> Variable:
        if not isinstance(node, ast.Name):
            raise self._error("Цель присваивания должна быть переменной", node)
        if node.id == "result":
            raise self._error("Имя result зарезервировано для возвращаемого значения", node)
        return Variable(node.id)

    def _operator(self, operator: ast.AST, node: ast.AST) -> str:
        symbol = _OPERATORS.get(type(operator))
        if symbol is None:
            raise self._error(f"Оператор {type(operator).__name__} не поддерживается", node)
        return symbol

    def _convert_expression(self, node: ast.expr) -> Expr:
        """Преобразовать выражение, вычисляющее значение."""
        match node:
            case ast.Name(id=name):
                return Variable(name)
            case ast.Constant(value=value) if isinstance(value, int) or (
                isinstance(value, float) and math.isfinite(value)
            ):
                return Constant(value)
            case ast.BinOp(left=left, op=operator, right=right):
                op = self._operator(operator, node)
                return BinaryOp(op, self._convert_expression(left), self._convert_expression(right))
            case ast.UnaryOp(op=operator, operand=operand):
                op = self._operator(operator, node)
                return UnaryOp(op, self._convert_expression(operand))
            case ast.Attribute(value=owner, attr=name):
                return Attribute(self._convert_expression(owner), name)
            case ast.Call(func=(ast.Name() | ast.Attribute()) as function, args=args, keywords=[]):
                return Call(self._convert_expression(function), [self._convert_expression(arg) for arg in args])
            case _:
                raise self._error(f"Выражение {type(node).__name__} в таком виде не поддерживается", node)

    def _convert_formula(self, node: ast.expr) -> Formula:
        """Преобразовать логическое условие."""
        match node:
            case ast.Constant(value=value) if isinstance(value, bool):
                return BoolConstant(value)
            case ast.UnaryOp(op=ast.Not(), operand=operand):
                return Not(self._convert_formula(operand))
            case ast.BoolOp(op=operator, values=values):
                op = self._operator(operator, node)
                return BoolOp(op, [self._convert_formula(value) for value in values])
            case ast.Compare(left=left, ops=operators, comparators=right_operands):
                # 0 <= x < 10 означает (0 <= x) and (x < 10).
                operands = [self._convert_expression(item) for item in [left, *right_operands]]
                comparisons = [
                    Comparison(self._operator(op, node), first, second)
                    for op, (first, second) in zip(operators, pairwise(operands))
                ]
                if len(comparisons) == 1:
                    return comparisons[0]
                return BoolOp("and", comparisons)
            case ast.Call(func=ast.Name(id="implies"), args=[premise, conclusion], keywords=[]):
                return BoolOp("=>", [self._convert_formula(premise), self._convert_formula(conclusion)])
            case ast.Call(
                func=ast.Name(id="forall" | "exists" as kind),
                args=[ast.Lambda(args=args, body=body)],
                keywords=[],
            ):
                if not args.args or any((args.posonlyargs, args.kwonlyargs, args.vararg, args.kwarg, args.defaults)):
                    raise self._error("Квантору нужны простые параметры lambda", node)
                # Параметры lambda задают связанные переменные.
                bound_variables = self._parameter_names(args.args)
                return Quantifier(kind, bound_variables, self._convert_formula(body))
            case _:
                raise self._error("Ожидается формула, например x > 0 или flag == True", node)
