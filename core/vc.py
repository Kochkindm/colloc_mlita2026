# pyright: reportArgumentType=false, reportReturnType=false
"""Роль 4 — генератор условий корректности (Verification Conditions - VC)

Публичный интерфейс:
    generate(program) -> list[VC]
    normalize_formula(formula) -> Formula

Для каждой программы создаётся основное условие корректности:
    requires => wp(program.body, ensures)

Для каждого цикла while с инвариантом I и условием b создаются два VC:
    I and not b => Q          (корректность выхода из цикла)
    I and b     => wp(body, I) (сохранение инварианта)

Q — требование, которое должно выполняться после конкретного цикла. Оно
получается из WP оставшейся части программы, поэтому корректно учитывает
команды после цикла, ветвления и return.

Вложенные циклы обрабатываются рекурсивно через callback on_loop модуля
core.wp. Публичные классы AST и сигнатура wp не изменяются.

Нормализация:
- вычисление отрицаний булевых констант и устранение двойного отрицания;
- законы де Моргана и отрицание импликации;
- свёртка констант, опускание лишних скобок для and/or и упрощение импликации;
- безопасный вынос кванторов из and/or/=> при условии, что связанные 
  переменные не свободны в соседних формулах;
- переименование связанных переменных при выносе, чтобы избежать конфликта имён
"""

from collections.abc import Iterable, Sequence

from core.classes import (
    BoolConstant,
    BoolOp,
    Comparison,
    Formula,
    If,
    Not,
    Program,
    Quantifier,
    VC,
    Variable,
    While,
)
from core.wp import (
    AND,
    EXISTS,
    FORALL,
    IMPLIES,
    OR,
    WPError,
    free_vars,
    mk_and,
    mk_implies,
    mk_not,
    mk_or,
    substitute,
    wp,
)


class VCGenerationError(Exception):
    """Ошибка при построении условий корректности."""


def _dual_quantifier(kind: str) -> str:
    """Возвращает двойственный квантор для отрицания/импликации."""
    if kind == FORALL:
        return EXISTS
    if kind == EXISTS:
        return FORALL
    raise VCGenerationError(f"неизвестный вид квантора: {kind!r}")


def _all_names(formula: Formula) -> set[str]:
    """Все имена переменных формулы, включая связанные кванторами."""
    names = set(free_vars(formula))
    if isinstance(formula, Quantifier):
        names.update(formula.variables)
        names.update(_all_names(formula.body))
    elif isinstance(formula, BoolOp):
        for operand in formula.operands:
            names.update(_all_names(operand))
    elif isinstance(formula, Not):
        names.update(_all_names(formula.operand))
    return names


def _union_names(formulas: Iterable[Formula]) -> set[str]:
    result: set[str] = set()
    for formula in formulas:
        result.update(_all_names(formula))
    return result


def _rename_quantifier_variables(
    variables: list[str], body: Formula, forbidden: set[str]
) -> tuple[list[str], Formula]:
    """Переименовывает связанные переменные, конфликтующие с соседями.

    Новое имя не должно конфликтовать ни с соседями, ни со свободными или
    связанными именами внутри тела исходного квантора.
    """
    used = set(forbidden) | set(variables) | _all_names(body)
    renaming = {}
    renamed: list[str] = []
    for variable in variables:
        if variable in forbidden:
            fresh = _fresh_name(variable, used)
            used.add(fresh)
            renaming[variable] = _variable(fresh)
            renamed.append(fresh)
        else:
            renamed.append(variable)
    if renaming:
        body = substitute(body, renaming)
    return renamed, body


def _variable(name: str) -> Variable:
    return Variable(name)


def _fresh_name(base: str, used: set[str]) -> str:
    """Первое свободное имя base_1, base_2, ..."""
    index = 1
    while f"{base}_{index}" in used:
        index += 1
    return f"{base}_{index}"


def _make_boolean(op: str, operands: list[Formula]) -> Formula:
    if op == AND:
        return mk_and(*operands)
    if op == OR:
        return mk_or(*operands)
    if op == IMPLIES:
        if len(operands) != 2:
            raise VCGenerationError("импликация должна содержать ровно два операнда")
        return mk_implies(operands[0], operands[1])
    return BoolOp(op, operands)


def _flatten(op: str, operands: list[Formula]) -> list[Formula]:
    """Распрямляет вложенные AND/OR, сохраняя порядок операндов."""
    flattened: list[Formula] = []
    for operand in operands:
        if isinstance(operand, BoolOp) and operand.op == op:
            flattened.extend(_flatten(op, operand.operands))
        else:
            flattened.append(operand)
    return flattened


def _normalize_negation(formula: Formula) -> Formula:
    """Нормализует отрицание с применением базовых законов логики."""
    operand = normalize_formula(formula)

    if isinstance(operand, BoolConstant):
        return BoolConstant(not operand.value)
    if isinstance(operand, Not):
        return normalize_formula(operand.operand)
    if isinstance(operand, Quantifier):
        return Quantifier(
            _dual_quantifier(operand.kind),
            list(operand.variables),
            _normalize_negation(operand.body),
        )
    if isinstance(operand, BoolOp):
        if operand.op == AND:
            return normalize_formula(mk_or(*[Not(item) for item in operand.operands]))
        if operand.op == OR:
            return normalize_formula(mk_and(*[Not(item) for item in operand.operands]))
        if operand.op == IMPLIES and len(operand.operands) == 2:
            premise, conclusion = operand.operands
            return normalize_formula(mk_and(premise, Not(conclusion)))
    return Not(operand)


def _pull_quantifiers_from_boolean(op: str, operands: list[Formula]) -> Formula:
    """Выносит независимые кванторы из and/or в начало формулы."""
    current = _flatten(op, operands)
    prefix: list[tuple[str, list[str]]] = []
    index = 0

    while index < len(current):
        candidate = current[index]
        if not isinstance(candidate, Quantifier):
            index += 1
            continue

        siblings = [item for pos, item in enumerate(current) if pos != index]
        sibling_free: set[str] = set()
        for sibling in siblings:
            sibling_free.update(free_vars(sibling))

        bound = set(candidate.variables)
        if bound & sibling_free:
            index += 1
            continue

        forbidden = _union_names(siblings)
        forbidden.update(name for _, variables in prefix for name in variables)
        variables, body = _rename_quantifier_variables(
            list(candidate.variables), candidate.body, forbidden
        )
        prefix.append((candidate.kind, variables))

        current[index:index + 1] = [body]
        if isinstance(body, BoolOp) and body.op == op:
            current[index:index + 1] = _flatten(op, body.operands)

    result = _make_boolean(op, current)
    for kind, variables in reversed(prefix):
        result = Quantifier(kind, variables, result)
    return result


def _pull_quantifier_from_implication(premise: Formula, conclusion: Formula) -> Formula | None:
    """Выносит квантор через =>, если его переменные не свободны с другой стороны."""
    if isinstance(premise, Quantifier):
        if not (set(premise.variables) & free_vars(conclusion)):
            variables, body = _rename_quantifier_variables(
                list(premise.variables), premise.body, _all_names(conclusion)
            )
            inner = normalize_formula(BoolOp(IMPLIES, [body, conclusion]))
            return Quantifier(_dual_quantifier(premise.kind), variables, inner)

    if isinstance(conclusion, Quantifier):
        if not (set(conclusion.variables) & free_vars(premise)):
            variables, body = _rename_quantifier_variables(
                list(conclusion.variables), conclusion.body, _all_names(premise)
            )
            inner = normalize_formula(BoolOp(IMPLIES, [premise, body]))
            return Quantifier(conclusion.kind, variables, inner)

    return None


def normalize_formula(formula: Formula) -> Formula:
    """Упрощает логическую формулу и безопасно выносит кванторы."""
    if isinstance(formula, (Comparison, BoolConstant)):
        return formula

    if isinstance(formula, Not):
        return _normalize_negation(formula.operand)

    if isinstance(formula, Quantifier):
        if formula.kind not in (FORALL, EXISTS):
            raise VCGenerationError(f"неизвестный вид квантора: {formula.kind!r}")
        body = normalize_formula(formula.body)
        if isinstance(body, Quantifier) and body.kind == formula.kind:
            if not (set(formula.variables) & set(body.variables)):
                return Quantifier(
                    formula.kind,
                    [*formula.variables, *body.variables],
                    body.body,
                )
        return Quantifier(formula.kind, list(formula.variables), body)

    if isinstance(formula, BoolOp):
        operands = [normalize_formula(item) for item in formula.operands]

        if formula.op == AND:
            simplified = mk_and(*operands)
            if isinstance(simplified, BoolOp) and simplified.op == AND:
                return _pull_quantifiers_from_boolean(AND, simplified.operands)
            return simplified

        if formula.op == OR:
            simplified = mk_or(*operands)
            if isinstance(simplified, BoolOp) and simplified.op == OR:
                return _pull_quantifiers_from_boolean(OR, simplified.operands)
            return simplified

        if formula.op == IMPLIES:
            if len(operands) != 2:
                raise VCGenerationError("импликация должна содержать ровно два операнда")
            simplified = mk_implies(operands[0], operands[1])
            if not (isinstance(simplified, BoolOp) and simplified.op == IMPLIES):
                return normalize_formula(simplified)
            moved = _pull_quantifier_from_implication(*simplified.operands)
            return moved if moved is not None else simplified

        return BoolOp(formula.op, operands)

    raise VCGenerationError(f"неизвестная формула: {formula!r}")


def _iter_loops(commands: Sequence) -> Iterable[While]:
    """Обходит дерево команд в порядке исходного текста."""
    for command in commands:
        if isinstance(command, While):
            yield command
            yield from _iter_loops(command.body)
        elif isinstance(command, If):
            yield from _iter_loops(command.then_body)
            yield from _iter_loops(command.else_body)


def generate(program: Program) -> list[VC]:
    """Строит основное VC программы и по два VC для каждого цикла."""
    if not isinstance(program, Program):
        raise TypeError(f"ожидался Program, получено: {type(program).__name__}")

    precondition = mk_and(*program.requires)
    postcondition = mk_and(*program.ensures)
    loop_conditions: dict[int, list[VC]] = {}
    loop_numbers = {id(loop): number for number, loop in enumerate(_iter_loops(program.body), 1)}

    def collect_loop(loop: While, post: Formula, return_post: Formula) -> None:
        """Callback WP: генерирует два VC и рекурсивно обходит тело цикла."""
        number = loop_numbers.get(id(loop))
        if number is None:
            raise VCGenerationError("WP сообщил о цикле, отсутствующем в AST программы")

        try:
            body_wp = wp(
                loop.body,
                loop.invariant,
                return_postcondition=return_post,
                on_loop=collect_loop,
            )
        except WPError as error:
            raise VCGenerationError(f"не удалось вычислить wp тела цикла №{number}: {error}") from error

        exit_condition = normalize_formula(
            mk_implies(mk_and(loop.invariant, mk_not(loop.condition)), post)
        )
        invariant_condition = normalize_formula(
            mk_implies(mk_and(loop.invariant, loop.condition), body_wp)
        )

        loop_conditions[id(loop)] = [
            VC(
                id=f"loop_{number}_exit",
                formula=exit_condition,
                source_line=None,
                description=(
                    f"Цикл №{number}: инвариант и отрицание условия цикла "
                    "влекут постусловие после выхода"
                ),
            ),
            VC(
                id=f"loop_{number}_invariant",
                formula=invariant_condition,
                source_line=None,
                description=(
                    f"Цикл №{number}: тело цикла сохраняет инвариант "
                    "при выполнении условия цикла"
                ),
            ),
        ]

    try:
        program_wp = wp(
            program.body,
            postcondition,
            return_postcondition=postcondition,
            on_loop=collect_loop,
        )
    except WPError as error:
        raise VCGenerationError(f"не удалось вычислить wp программы {program.name!r}: {error}") from error

    main_condition = normalize_formula(mk_implies(precondition, program_wp))
    result = [
        VC(
            id="program_correctness",
            formula=main_condition,
            source_line=None,
            description=f"Предусловие программы {program.name} влечёт её weakest precondition",
        )
    ]

    for loop in _iter_loops(program.body):
        conditions = loop_conditions.get(id(loop))
        if conditions is None:
            raise VCGenerationError("не удалось собрать VC для одного из циклов программы")
        result.extend(conditions)

    return result


__all__ = ["VCGenerationError", "generate", "normalize_formula"]