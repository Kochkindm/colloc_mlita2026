# pyright: reportArgumentType=false
"""Роль 3 — семантика и weakest precondition.

Публичный интерфейс:
    wp(command, postcondition) -> Formula
    substitute(formula, mapping) -> Formula   # Q[e/x] без захвата переменных
    free_vars(formula) -> set[str]

Правила:
    wp(skip, Q)              = Q              (skip / pass — пустой список [])
    wp(x := e, Q)            = Q[e/x]
    wp(c1; c2, Q)            = wp(c1, wp(c2, Q))
    wp(if b c1 else c2, Q)   = (b => wp(c1, Q)) and (not b => wp(c2, Q))
    wp(assert P, Q)          = P and Q
    wp(assume P, Q)          = P => Q
    wp(return e, Q)          = R[e/result]    (R — постусловие функции)
    wp(while b {I} c, Q)     = I              (VC цикла собирает роль 4)

Общий контракт core/classes.py не меняется. Всё, чего в нём нет и что
нужно семантике, объявлено здесь:
    - строки операторов AND, OR, IMPLIES (BoolOp.op), FORALL, EXISTS;
    - RESULT — имя переменной возвращаемого значения в ensures;
    - Quantifier — квантор (нужен для постусловий вида "forall d. ...").
      Может стоять везде, где ожидается Formula (внутри BoolOp, Not и т.д.).
      Статический тип Formula из classes.py его не включает, поэтому проверка
      reportArgumentType в этом файле отключена директивой pyright в начале.

Ограничения:
    - присваивание только в Variable (не в Attribute);
    - множественное присваивание (a, b = b, a % b) парсер раскладывает
      через временные переменные.
"""

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import TypeAlias

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
    Formula as BaseFormula,
    If,
    Not,
    Return,
    UnaryOp,
    Variable,
    While,
    Quantifier,
)

AND = "and"          # n-арная конъюнкция
OR = "or"            # n-арная дизъюнкция
IMPLIES = "=>"       # импликация, ровно 2 операнда: [посылка, следствие]
FORALL = "forall"
EXISTS = "exists"
RESULT = "result"    # возвращаемое значение функции в ensures


TRUE = BoolConstant(True)
FALSE = BoolConstant(False)

LoopCallback = Callable[[While, Formula, Formula], None]


class WPError(Exception):
    """Команда или выражение не поддерживаются семантикой wp."""


def mk_not(f: Formula) -> Formula:
    """Строит not f; для булевой константы сразу возвращает её отрицание."""
    if isinstance(f, BoolConstant):
        return BoolConstant(not f.value)
    return Not(f)


def mk_and(*fs: Formula) -> Formula:
    """Строит конъюнкцию fs с упрощением констант.

    True выбрасывается, False поглощает всю конъюнкцию, вложенные "and"
    сплющиваются. Пустая конъюнкция — True, из одного операнда — сам операнд.
    """
    operands: list[Formula] = []
    for f in fs:
        if f == TRUE:
            continue
        if f == FALSE:
            return FALSE
        if isinstance(f, BoolOp) and f.op == AND:
            operands.extend(f.operands)
        else:
            operands.append(f)
    if not operands:
        return TRUE
    if len(operands) == 1:
        return operands[0]
    return BoolOp(AND, operands)


def mk_or(*fs: Formula) -> Formula:
    """Строит дизъюнкцию fs с упрощением констант.

    False выбрасывается, True поглощает всю дизъюнкцию, вложенные "or"
    сплющиваются. Пустая дизъюнкция — False, из одного операнда — сам операнд.
    """
    operands: list[Formula] = []
    for f in fs:
        if f == FALSE:
            continue
        if f == TRUE:
            return TRUE
        if isinstance(f, BoolOp) and f.op == OR:
            operands.extend(f.operands)
        else:
            operands.append(f)
    if not operands:
        return FALSE
    if len(operands) == 1:
        return operands[0]
    return BoolOp(OR, operands)


def mk_implies(premise: Formula, conclusion: Formula) -> Formula:
    """Строит premise => conclusion с упрощением констант.

    True => Q = Q;  False => Q = True;  P => True = True;  P => False = not P.
    """
    if premise == TRUE:
        return conclusion
    if premise == FALSE or conclusion == TRUE:
        return TRUE
    if conclusion == FALSE:
        return mk_not(premise)
    return BoolOp(IMPLIES, [premise, conclusion])


def free_vars_expr(e: Expr) -> set[str]:
    """Возвращает имена переменных, входящих в выражение e.

    Имя вызываемой функции (Call.function) переменной программы не считается,
    учитываются только аргументы вызова.
    """
    match e:
        case Variable(name):
            return {name}
        case Constant():
            return set()
        case BinaryOp(_, left, right):
            return free_vars_expr(left) | free_vars_expr(right)
        case UnaryOp(_, operand):
            return free_vars_expr(operand)
        case Call(_, args):
            return _union(free_vars_expr(a) for a in args)
        case Attribute(obj, _):
            return free_vars_expr(obj)
    raise WPError(f"неизвестное выражение: {e!r}")


def free_vars(f: Formula) -> set[str]:
    """Возвращает имена свободных переменных формулы f.

    Переменные, связанные квантором, внутри его тела свободными не считаются.
    """
    match f:
        case Comparison(_, left, right):
            return free_vars_expr(left) | free_vars_expr(right)
        case BoolOp(_, operands):
            return _union(free_vars(o) for o in operands)
        case Not(operand):
            return free_vars(operand)
        case BoolConstant():
            return set()
        case Quantifier(_, variables, body):
            return free_vars(body) - set(variables)
    raise WPError(f"неизвестная формула: {f!r}")


def _union(sets: Iterable[set[str]]) -> set[str]:
    """Объединение произвольного числа множеств."""
    result: set[str] = set()
    for s in sets:
        result |= s
    return result


def substitute_expr(e: Expr, mapping: Mapping[str, Expr]) -> Expr:
    """Одновременно заменяет в выражении e переменные из mapping.

    В выражениях нет связывающих конструкций, поэтому захват невозможен.
    Имя вызываемой функции не заменяется, только аргументы вызова.
    """
    match e:
        case Variable(name):
            return mapping.get(name, e)
        case Constant():
            return e
        case BinaryOp(op, left, right):
            return BinaryOp(op, substitute_expr(left, mapping), substitute_expr(right, mapping))
        case UnaryOp(op, operand):
            return UnaryOp(op, substitute_expr(operand, mapping))
        case Call(function, args):
            return Call(function, [substitute_expr(a, mapping) for a in args])
        case Attribute(obj, name):
            return Attribute(substitute_expr(obj, mapping), name)
    raise WPError(f"неизвестное выражение: {e!r}")


def substitute(f: Formula, mapping: Mapping[str, Expr]) -> Formula:
    """Подстановка Q[e/x] без захвата переменных.

    Одновременно заменяет свободные вхождения переменных mapping.keys()
    на соответствующие выражения: substitute(x == y, {x: y, y: x}) = (y == x).
    Связанные переменные кванторов, которые попали бы под захват,
    переименовываются в свежие (d -> d_1).
    """
    if not mapping:
        return f
    match f:
        case Comparison(op, left, right):
            return Comparison(op, substitute_expr(left, mapping), substitute_expr(right, mapping))
        case BoolOp(op, operands):
            return BoolOp(op, [substitute(o, mapping) for o in operands])
        case Not(operand):
            return Not(substitute(operand, mapping))
        case BoolConstant():
            return f
        case Quantifier(kind, variables, body):
            return _substitute_quantifier(kind, variables, body, mapping)
    raise WPError(f"неизвестная формула: {f!r}")


def _substitute_quantifier(
    kind: str, variables: list[str], body: Formula, mapping: Mapping[str, Expr]
) -> Formula:
    """Подстановка внутрь квантора kind variables. body.

    1. Из mapping убираются связанные переменные (их вхождения не свободны)
       и переменные, которых нет в теле.
    2. Связанные переменные, которые входят в подставляемые выражения,
       переименовываются в свежие — иначе они бы их захватили.
    3. Оставшаяся подстановка применяется к переименованному телу.
    """
    body_free = free_vars(body)
    active = {
        name: e for name, e in mapping.items()
        if name not in variables and name in body_free
    }
    if not active:
        return Quantifier(kind, list(variables), body)

    incoming = _union(free_vars_expr(e) for e in active.values())
    used = body_free | incoming | set(variables) | set(active)
    renaming: dict[str, Expr] = {}
    new_variables: list[str] = []
    for v in variables:
        if v in incoming:
            fresh = _fresh_name(v, used)
            used.add(fresh)
            renaming[v] = Variable(fresh)
            new_variables.append(fresh)
        else:
            new_variables.append(v)

    new_body = substitute(body, renaming) if renaming else body
    return Quantifier(kind, new_variables, substitute(new_body, active))


def _fresh_name(base: str, used: set[str]) -> str:
    """Первое имя вида base_1, base_2, ..., которого нет в used."""
    i = 1
    while f"{base}_{i}" in used:
        i += 1
    return f"{base}_{i}"


def wp(
    command: Command | Sequence[Command],
    postcondition: Formula,
    return_postcondition: Formula | None = None,
    on_loop: LoopCallback | None = None,
) -> Formula:
    """Слабейшее предусловие команды (или последовательности команд).

    postcondition        — Q, должно выполняться при нормальном завершении.
    return_postcondition — R, должно выполняться в момент return
                           (обычно конъюнкция ensures). По умолчанию = Q.
    on_loop              — вызывается для каждого встреченного цикла как
                           on_loop(loop, post, return_post):
                             post        — что должно выполняться после выхода
                                           из цикла;
                             return_post — постусловие для return внутри тела.
                           Роль 4 по этим данным строит VC цикла:
                             I and not b => post,  I and b => wp(body, I).

    command может быть командой или списком команд (пустой список — skip).
    Для цикла возвращается его инвариант I.
    Бросает WPError на неподдерживаемых командах.
    """
    ret = postcondition if return_postcondition is None else return_postcondition
    return _wp(command, postcondition, ret, on_loop)


def _wp(
    command: Command | Sequence[Command],
    post: Formula,
    ret: Formula,
    on_loop: LoopCallback | None,
) -> Formula:
    """Рекурсивное вычисление wp с двумя постусловиями.

    post — постусловие нормального завершения, ret — постусловие return.
    Последовательность сворачивается справа налево. Для return post
    игнорируется: команды после него недостижимы.
    """
    if isinstance(command, (list, tuple)):
        for c in reversed(command):
            post = _wp(c, post, ret, on_loop)
        return post

    match command:
        case Assign(Variable(name), value):
            return substitute(post, {name: value})
        case Assign(target, _):
            raise WPError(f"присваивание поддерживается только в переменную, получено: {target!r}")
        case If(condition, then_body, else_body):
            return mk_and(
                mk_implies(condition, _wp(then_body, post, ret, on_loop)),
                mk_implies(mk_not(condition), _wp(else_body, post, ret, on_loop)),
            )
        case Assert(condition):
            return mk_and(condition, post)
        case Assume(condition):
            return mk_implies(condition, post)
        case Return(value):
            return substitute(ret, {RESULT: value})
        case While(_, invariant, _):
            if on_loop is not None:
                on_loop(command, post, ret)
            return invariant
    raise WPError(f"неизвестная команда: {command!r}")
