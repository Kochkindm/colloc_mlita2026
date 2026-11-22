import pytest

from core.classes import (
    Assert,
    Assign,
    Assume,
    Attribute,
    BinaryOp,
    BoolConstant,
    BoolOp,
    Call,
    Comparison,
    Constant,
    If,
    Not,
    Return,
    Variable,
    While,
)
from core.wp import (
    AND,
    FALSE,
    FORALL,
    IMPLIES,
    OR,
    RESULT,
    TRUE,
    Quantifier,
    WPError,
    free_vars,
    mk_and,
    mk_implies,
    mk_not,
    mk_or,
    substitute,
    wp,
)


# --- короткие конструкторы для читаемости тестов ---------------------------

def v(name):
    return Variable(name)


def c(value):
    return Constant(value)


def add(a, b):
    return BinaryOp("+", a, b)


def sub(a, b):
    return BinaryOp("-", a, b)


def mod(a, b):
    return BinaryOp("%", a, b)


def eq(a, b):
    return Comparison("==", a, b)


def gt(a, b):
    return Comparison(">", a, b)


def le(a, b):
    return Comparison("<=", a, b)


def AND_(*fs):
    return BoolOp(AND, list(fs))


def IMP(p, q):
    return BoolOp(IMPLIES, [p, q])


def forall(vars_, body):
    return Quantifier(FORALL, list(vars_), body)


x, y, z, d, a, b = (v(n) for n in "xyzdab")
result = v(RESULT)


# --- skip / присваивание / последовательность ------------------------------

def test_empty_sequence_is_skip():
    q = gt(x, c(0))
    assert wp([], q) == q


def test_assign_hoare_example():
    # {x + 3 == 8} x := x + 3 {x == 8}
    assert wp(Assign(x, add(x, c(3))), eq(x, c(8))) == eq(add(x, c(3)), c(8))


def test_assign_untouched_variable():
    q = eq(y, c(1))
    assert wp(Assign(x, c(5)), q) == q


def test_sequence_is_composed_right_to_left():
    # x := x + 1; y := x   { y == 2 }   ->   x + 1 == 2
    prog = [Assign(x, add(x, c(1))), Assign(y, x)]
    assert wp(prog, eq(y, c(2))) == eq(add(x, c(1)), c(2))


def test_swap_via_temp():
    # t := x; x := y; y := t   { x == 1 and y == 2 }  ->  y == 1 and x == 2
    t = v("t")
    prog = [Assign(t, x), Assign(x, y), Assign(y, t)]
    q = AND_(eq(x, c(1)), eq(y, c(2)))
    assert wp(prog, q) == AND_(eq(y, c(1)), eq(x, c(2)))


def test_assign_to_attribute_is_rejected():
    with pytest.raises(WPError):
        wp(Assign(Attribute(x, "num"), c(1)), TRUE)


# --- if ---------------------------------------------------------------------

def test_if():
    cond = gt(x, c(0))
    prog = If(cond, [Assign(y, x)], [Assign(y, c(0))])
    q = gt(y, c(-1))
    expected = AND_(
        IMP(cond, gt(x, c(-1))),
        IMP(Not(cond), gt(c(0), c(-1))),
    )
    assert wp(prog, q) == expected


def test_if_with_empty_else():
    cond = gt(x, c(0))
    q = eq(y, c(1))
    prog = If(cond, [Assign(y, c(1))], [])
    assert wp(prog, q) == AND_(
        IMP(cond, eq(c(1), c(1))),
        IMP(Not(cond), q),
    )


# --- assert / assume --------------------------------------------------------

def test_assert():
    p, q = gt(x, c(0)), eq(y, c(1))
    assert wp(Assert(p), q) == AND_(p, q)


def test_assume():
    p, q = gt(x, c(0)), eq(y, c(1))
    assert wp(Assume(p), q) == IMP(p, q)


def test_assume_false_gives_true():
    assert wp(Assume(FALSE), eq(x, c(1))) == TRUE


def test_assert_false_gives_false():
    assert wp(Assert(FALSE), eq(x, c(1))) == FALSE


# --- return -----------------------------------------------------------------

def test_return_substitutes_result():
    ensures = gt(result, c(0))
    assert wp(Return(add(x, c(1))), ensures) == gt(add(x, c(1)), c(0))


def test_code_after_return_is_ignored():
    ensures = eq(result, x)
    prog = [Return(x), Assign(x, c(42))]
    assert wp(prog, ensures) == eq(x, x)


def test_return_inside_branch_uses_return_postcondition():
    # if x > 0: return x
    # y := 0
    # return y
    ensures = gt(result, c(-1))
    cond = gt(x, c(0))
    prog = [If(cond, [Return(x)], []), Assign(y, c(0)), Return(y)]
    assert wp(prog, ensures) == AND_(
        IMP(cond, gt(x, c(-1))),
        IMP(Not(cond), gt(c(0), c(-1))),
    )


def test_explicit_return_postcondition():
    normal = FALSE  # функция обязана завершиться через return
    ensures = eq(result, c(1))
    assert wp([Return(c(1))], normal, return_postcondition=ensures) == eq(c(1), c(1))
    assert wp([], normal, return_postcondition=ensures) == FALSE


# --- while ------------------------------------------------------------------

def test_while_returns_invariant():
    inv = gt(x, c(-1))
    loop = While(gt(x, c(0)), inv, [Assign(x, sub(x, c(1)))])
    assert wp(loop, eq(x, c(0))) == inv


def test_while_callback_receives_post_after_loop():
    inv = gt(x, c(-1))
    loop = While(gt(x, c(0)), inv, [Assign(x, sub(x, c(1)))])
    seen = []
    ensures = eq(result, c(0))
    pre = wp([loop, Return(x)], ensures, on_loop=lambda *args: seen.append(args))
    assert pre == inv
    assert seen == [(loop, eq(x, c(0)), ensures)]


def test_nested_loops_reported_with_correct_post():
    inner = While(gt(y, c(0)), le(c(0), y), [Assign(y, sub(y, c(1)))])
    outer = While(gt(x, c(0)), le(c(0), x), [inner, Assign(x, sub(x, c(1)))])
    q = eq(x, c(0))
    seen = []

    def collect(loop, post, ret):
        seen.append((loop, post))
        # роль 4 сама считает wp тела — проверим, что внутренний цикл тоже виден
        wp(loop.body, loop.invariant, ret, on_loop=collect)

    wp(outer, q, on_loop=collect)
    assert seen[0] == (outer, q)
    # post внутреннего цикла = wp(x := x - 1, inv_outer)
    assert seen[1] == (inner, le(c(0), sub(x, c(1))))


def test_euclid_loop_body_preserves_invariant_vc():
    # while b != 0 inv (g == gcd(a, b)):  t := b; b := a % b; a := t
    g, t = v("g"), v("t")
    inv = eq(g, Call(v("gcd"), [a, b]))
    body = [Assign(t, b), Assign(b, mod(a, b)), Assign(a, t)]
    assert wp(body, inv) == eq(g, Call(v("gcd"), [b, mod(a, b)]))


# --- подстановка и захват переменных ---------------------------------------

def test_substitute_is_simultaneous():
    f = eq(x, y)
    assert substitute(f, {"x": y, "y": x}) == eq(y, x)


def test_bound_variable_is_not_substituted():
    f = forall(["x"], gt(x, c(0)))
    assert substitute(f, {"x": c(5)}) == f


def test_capture_is_avoided():
    # (forall d. d <= x)[d + 1 / x]  ->  forall d_1. d_1 <= d + 1
    f = forall(["d"], le(d, x))
    assert substitute(f, {"x": add(d, c(1))}) == forall(["d_1"], le(v("d_1"), add(d, c(1))))


def test_fresh_name_skips_used_names():
    # d_1 уже свободна в теле — свежее имя должно быть d_2
    f = forall(["d"], AND_(le(d, x), eq(v("d_1"), v("d_1"))))
    out = substitute(f, {"x": d})
    assert out == forall(["d_2"], AND_(le(v("d_2"), d), eq(v("d_1"), v("d_1"))))


def test_no_renaming_when_variable_absent_from_body():
    f = forall(["d"], gt(d, c(0)))
    assert substitute(f, {"x": d}) == f


def test_gcd_postcondition_through_assignment():
    # ensures: forall d. d > 0 and a % d == 0 and b % d == 0 => d <= result
    post = forall(["d"], IMP(
        AND_(gt(d, c(0)), eq(mod(a, d), c(0)), eq(mod(b, d), c(0))),
        le(d, result),
    ))
    # return d  (d — переменная программы, совпадает со связанной!)
    out = wp(Return(d), post)
    d1 = v("d_1")
    assert out == forall(["d_1"], IMP(
        AND_(gt(d1, c(0)), eq(mod(a, d1), c(0)), eq(mod(b, d1), c(0))),
        le(d1, d),
    ))


def test_substitute_into_attribute_and_call_args():
    f = eq(Attribute(result, "num"), Call(v("abs"), [x]))
    out = substitute(f, {RESULT: Call(v("Fraction"), [x, y]), "x": z})
    assert out == eq(Attribute(Call(v("Fraction"), [x, y]), "num"), Call(v("abs"), [z]))


def test_function_name_is_not_a_variable():
    f = eq(Call(v("f"), [x]), c(0))
    assert free_vars(f) == {"x"}
    assert substitute(f, {"f": c(1)}) == f


def test_free_vars():
    f = AND_(gt(x, c(0)), forall(["d"], le(d, y)))
    assert free_vars(f) == {"x", "y"}


# --- упрощение констант -----------------------------------------------------

def test_smart_constructors():
    p, q = gt(x, c(0)), gt(y, c(0))
    assert mk_and(TRUE, p) == p
    assert mk_and(p, FALSE) == FALSE
    assert mk_and(AND_(p, q), p) == AND_(p, q, p)
    assert mk_or(FALSE, p) == p
    assert mk_or(p, TRUE) == TRUE
    assert mk_or(p, q) == BoolOp(OR, [p, q])
    assert mk_implies(TRUE, p) == p
    assert mk_implies(FALSE, p) == TRUE
    assert mk_implies(p, TRUE) == TRUE
    assert mk_implies(p, FALSE) == Not(p)
    assert mk_not(TRUE) == BoolConstant(False)
