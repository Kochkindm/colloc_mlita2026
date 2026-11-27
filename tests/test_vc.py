import pytest

from core.classes import (
    Assign,
    BoolConstant,
    BoolOp,
    Comparison,
    Constant,
    If,
    Not,
    Program,
    Quantifier,
    Variable,
    While,
)
from core.parser import parse
from core.vc import VCGenerationError, generate, normalize_formula
from core.wp import AND, EXISTS, FORALL, IMPLIES, OR


def v(name):
    return Variable(name)


def c(value):
    return Constant(value)


def eq(left, right):
    return Comparison("==", left, right)


def gt(left, right):
    return Comparison(">", left, right)


def and_(*items):
    return BoolOp(AND, list(items))


def implies(left, right):
    return BoolOp(IMPLIES, [left, right])


def test_generate_program_without_loops_still_checks_contract():
    program = Program(
        name="identity",
        params=["x"],
        requires=[gt(v("x"), c(0))],
        ensures=[gt(v("result"), c(0))],
        body=[],
    )

    vcs = generate(program)

    assert len(vcs) == 1
    assert vcs[0].id == "program_correctness"
    assert vcs[0].formula == implies(gt(v("x"), c(0)), gt(v("result"), c(0)))


def test_generate_loop_exit_and_invariant_conditions():
    invariant = gt(v("x"), c(-1))
    condition = gt(v("x"), c(0))
    loop = While(condition, invariant, [Assign(v("x"), c(0))])
    program = Program(
        name="count_down",
        params=["x"],
        requires=[gt(v("x"), c(-1))],
        ensures=[eq(v("result"), c(0))],
        body=[loop],
    )

    vcs = generate(program)

    assert [vc.id for vc in vcs] == [
        "program_correctness",
        "loop_1_exit",
        "loop_1_invariant",
    ]
    assert vcs[1].formula == implies(
        and_(invariant, Not(condition)), eq(v("result"), c(0))
    )
    # wp(x := 0, invariant) = 0 > -1
    assert vcs[2].formula == implies(
        and_(invariant, condition), gt(c(0), c(-1))
    )
    assert all(vc.source_line is None for vc in vcs)


def test_nested_loops_are_collected_recursively_in_source_order():
    inner = While(
        gt(v("y"), c(0)),
        gt(v("y"), c(-1)),
        [Assign(v("y"), c(0))],
    )
    outer = While(
        gt(v("x"), c(0)),
        gt(v("x"), c(-1)),
        [inner, Assign(v("x"), c(0))],
    )
    program = Program("nested", ["x", "y"], [], [BoolConstant(True)], [outer])

    vcs = generate(program)

    assert [vc.id for vc in vcs] == [
        "program_correctness",
        "loop_1_exit",
        "loop_1_invariant",
        "loop_2_exit",
        "loop_2_invariant",
    ]


def test_loops_inside_both_if_branches_are_collected():
    first = While(BoolConstant(True), BoolConstant(True), [])
    second = While(BoolConstant(False), BoolConstant(True), [])
    program = Program(
        "branching",
        [],
        [],
        [BoolConstant(True)],
        [If(BoolConstant(True), [first], [second])],
    )

    vcs = generate(program)

    assert [vc.id for vc in vcs] == [
        "program_correctness",
        "loop_1_exit",
        "loop_1_invariant",
        "loop_2_exit",
        "loop_2_invariant",
    ]


def test_postcondition_after_loop_is_wp_of_the_remaining_program():
    loop = While(BoolConstant(True), BoolConstant(True), [])
    program = Program(
        "suffix",
        [],
        [],
        [eq(v("x"), c(5))],
        [loop, Assign(v("x"), c(5))],
    )

    vcs = generate(program)

    assert vcs[1].formula == BoolConstant(True)


def test_parser_programs_can_be_passed_to_generator():
    source = '''\
def compare(a, b):
    # requires a >= 0 and b >= 0
    # ensures result == 0
    x = a
    while x > 0:
        # invariant x >= 0
        x = x - 1
    return 0
'''
    vcs = generate(parse(source))

    assert len(vcs) == 3
    assert vcs[0].id == "program_correctness"


def test_normalize_removes_double_negation_and_negates_constants():
    formula = Not(Not(gt(v("x"), c(0))))
    assert normalize_formula(formula) == gt(v("x"), c(0))
    assert normalize_formula(Not(BoolConstant(True))) == BoolConstant(False)


def test_normalize_applies_demorgan_and_negated_implication():
    formula = Not(and_(gt(v("x"), c(0)), gt(v("y"), c(0))))
    assert normalize_formula(formula) == BoolOp(
        OR,
        [Not(gt(v("x"), c(0))), Not(gt(v("y"), c(0)))],
    )

    implication = Not(implies(gt(v("x"), c(0)), gt(v("y"), c(0))))
    assert normalize_formula(implication) == and_(
        gt(v("x"), c(0)), Not(gt(v("y"), c(0)))
    )


def test_normalize_pulls_quantifier_out_of_and_when_independent():
    quantified = Quantifier(FORALL, ["x"], gt(v("x"), c(0)))
    formula = and_(quantified, gt(v("y"), c(0)))

    assert normalize_formula(formula) == Quantifier(
        FORALL,
        ["x"],
        and_(gt(v("x"), c(0)), gt(v("y"), c(0))),
    )


def test_normalize_does_not_pull_quantifier_over_free_variable():
    quantified = Quantifier(FORALL, ["x"], gt(v("x"), c(0)))
    formula = and_(quantified, eq(v("x"), c(1)))

    assert normalize_formula(formula) == formula


def test_normalize_pulls_quantifier_out_of_or_when_independent():
    quantified = Quantifier(EXISTS, ["x"], gt(v("x"), c(0)))
    formula = BoolOp(OR, [quantified, eq(v("y"), c(1))])

    assert normalize_formula(formula) == Quantifier(
        EXISTS,
        ["x"],
        BoolOp(OR, [gt(v("x"), c(0)), eq(v("y"), c(1))]),
    )


def test_normalize_moves_quantifiers_through_implication_correctly():
    premise = Quantifier(FORALL, ["x"], gt(v("x"), c(0)))
    moved_left = normalize_formula(implies(premise, eq(v("y"), c(1))))
    assert moved_left == Quantifier(
        EXISTS,
        ["x"],
        implies(gt(v("x"), c(0)), eq(v("y"), c(1))),
    )

    conclusion = Quantifier(EXISTS, ["x"], gt(v("x"), c(0)))
    moved_right = normalize_formula(implies(eq(v("y"), c(1)), conclusion))
    assert moved_right == Quantifier(
        EXISTS,
        ["x"],
        implies(eq(v("y"), c(1)), gt(v("x"), c(0))),
    )


def test_normalize_renames_conflicting_bound_variables():
    left = Quantifier(FORALL, ["x"], gt(v("x"), c(0)))
    right = Quantifier(EXISTS, ["x"], gt(v("x"), c(1)))

    normalized = normalize_formula(and_(left, right))

    assert normalized == Quantifier(
        FORALL,
        ["x_1"],
        Quantifier(
            EXISTS,
            ["x"],
            and_(gt(v("x_1"), c(0)), gt(v("x"), c(1))),
        ),
    )



def test_alpha_renaming_avoids_free_names_inside_quantifier_body():
    left = Quantifier(FORALL, ["x"], and_(gt(v("x"), c(0)), gt(v("x_1"), c(0))))
    right = Quantifier(EXISTS, ["x"], gt(v("x"), c(1)))

    normalized = normalize_formula(and_(left, right))

    assert normalized == Quantifier(
        FORALL,
        ["x_2"],
        Quantifier(
            EXISTS,
            ["x"],
            and_(
                gt(v("x_2"), c(0)),
                gt(v("x_1"), c(0)),
                gt(v("x"), c(1)),
            ),
        ),
    )

def test_normalize_merges_adjacent_same_kind_quantifiers():
    formula = Quantifier(
        FORALL,
        ["x"],
        Quantifier(FORALL, ["y"], gt(v("x"), v("y"))),
    )
    assert normalize_formula(formula) == Quantifier(
        FORALL, ["x", "y"], gt(v("x"), v("y"))
    )


def test_generate_rejects_non_program_argument():
    with pytest.raises(TypeError, match="ожидался Program"):
        generate("not a program")


def test_normalize_rejects_unknown_quantifier():
    with pytest.raises(VCGenerationError, match="неизвестный вид квантора"):
        normalize_formula(Quantifier("unique", ["x"], BoolConstant(True)))