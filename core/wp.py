"""Роль 3 — семантика и weakest precondition.

Публичный интерфейс:
    wp(command, postcondition) -> Formula
    substitute(formula, mapping) -> Formula   # Q[e/x] без захвата переменных
    free_vars(formula) -> set[str]

Правила:
    wp(skip, Q)              = Q
    wp(x := e, Q)            = Q[e/x]
    wp(c1; c2, Q)            = wp(c1, wp(c2, Q))
    wp(if b c1 else c2, Q)   = (b => wp(c1, Q)) and (not b => wp(c2, Q))
    wp(assert P, Q)          = P and Q
    wp(assume P, Q)          = P => Q
    wp(return e, Q)          = R[e/result]    (R — постусловие функции)
    wp(while b {I} c, Q)     = I              (VC цикла собирает роль 4)
"""

from collections.abc import Callable, Iterable, Mapping, Sequence

from core.classes import (
    AND,
    IMPLIES,
    OR,
    RESULT,
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
    Quantifier,
    Return,
    Skip,
    UnaryOp,
    Variable,
    While,
)

TRUE = BoolConstant(True)
FALSE = BoolConstant(False)

# Вызывается для каждого встреченного цикла:
#   on_loop(loop, post, return_post)
# post        — постусловие, которое должно выполняться после выхода из цикла;
# return_post — постусловие для return внутри тела цикла.
# Роль 4 по этим данным строит VC: I and not b => post, I and b => wp(body, I).
LoopCallback = Callable[[While, Formula, Formula], None]


class WPError(Exception):
    """Команда или выражение не поддерживаются семантикой wp."""


# ---------------------------------------------------------------------------
# Конструкторы формул с упрощением булевых констант
# ---------------------------------------------------------------------------

def mk_not(f: Formula) -> Formula:
    if isinstance(f, BoolConstant):
        return BoolConstant(not f.value)
    return Not(f)


def mk_and(*fs: Formula) -> Formula:
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
    if premise == TRUE:
        return conclusion
    if premise == FALSE or conclusion == TRUE:
        return TRUE
    if conclusion == FALSE:
        return mk_not(premise)
    return BoolOp(IMPLIES, [premise, conclusion])


# ---------------------------------------------------------------------------
# Свободные переменные
# ---------------------------------------------------------------------------

def free_vars_expr(e: Expr) -> set[str]:
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
            # Имя функции — не переменная программы.
            return _union(free_vars_expr(a) for a in args)
        case Attribute(obj, _):
            return free_vars_expr(obj)
    raise WPError(f"неизвестное выражение: {e!r}")


def free_vars(f: Formula) -> set[str]:
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
    result: set[str] = set()
    for s in sets:
        result |= s
    return result


# ---------------------------------------------------------------------------
# Подстановка Q[e/x] (одновременная, без захвата переменных)
# ---------------------------------------------------------------------------

def substitute_expr(e: Expr, mapping: Mapping[str, Expr]) -> Expr:
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
    """Одновременно заменяет свободные вхождения переменных mapping.keys()
    на соответствующие выражения. Связанные переменные кванторов, которые
    попали бы под захват, переименовываются в свежие."""
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
    body_free = free_vars(body)
    # Связанные переменные не заменяются, лишние пары выбрасываем.
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
    on_loop              — колбэк для сбора VC циклов (см. LoopCallback).
    """
    ret = postcondition if return_postcondition is None else return_postcondition
    return _wp(command, postcondition, ret, on_loop)


def _wp(
    command: Command | Sequence[Command],
    post: Formula,
    ret: Formula,
    on_loop: LoopCallback | None,
) -> Formula:
    if isinstance(command, (list, tuple)):
        for c in reversed(command):
            post = _wp(c, post, ret, on_loop)
        return post

    match command:
        case Skip():
            return post
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
