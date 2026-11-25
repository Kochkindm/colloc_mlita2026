from textwrap import dedent

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
    Program,
    Quantifier,
    Return,
    UnaryOp,
    Variable,
    While,
)
from core.parser import ParseError, parse


def test_parse_program_with_contract_and_loop():
    source = dedent("""\
        def count_down(n: int) -> int:
            # requires n >= 0
            # requires: n <= 100
            # ensures result == 0
            while n > 0:
                # invariant n >= 0
                n -= 1
            return n
        """)
    n = Variable("n")
    nonnegative = Comparison(">=", n, Constant(0))
    assert parse(source) == Program(
        "count_down", ["n"],
        [nonnegative, Comparison("<=", n, Constant(100))],
        [Comparison("==", Variable("result"), Constant(0))],
        [
            While(
                Comparison(">", n, Constant(0)), nonnegative,
                [Assign(n, BinaryOp("-", n, Constant(1)))],
            ),
            Return(n),
        ],
    )


def test_assert_assume_and_elif():
    source = dedent("""\
        def sign(n):
            assume(n != 0)
            if n < 0:
                assert not (n > 0 or n == 0)
                return -1
            elif n > 0:
                return 1
            else:
                pass
        """)
    n = Variable("n")
    positive = Comparison(">", n, Constant(0))
    zero = Comparison("==", n, Constant(0))
    assert parse(source).body == [
        Assume(Comparison("!=", n, Constant(0))),
        If(
            Comparison("<", n, Constant(0)),
            [Assert(Not(BoolOp("or", [positive, zero]))), Return(UnaryOp("-", Constant(1)))],
            [If(positive, [Return(Constant(1))], [])],
        ),
    ]


def test_arithmetic_priority_and_calls_with_attributes():
    source = "def f(x, data):\n    return data.value + abs(-x) * 3 // 2 % 5\n"
    numerator = BinaryOp("*", Call(Variable("abs"), [UnaryOp("-", Variable("x"))]), Constant(3))
    postfix = BinaryOp("%", BinaryOp("//", numerator, Constant(2)), Constant(5))
    assert parse(source).body == [
        Return(BinaryOp("+", Attribute(Variable("data"), "value"), postfix))
    ]


def test_chained_comparison_implication_and_quantifiers():
    source = dedent("""\
        def f(n):
            # requires 0 <= n < 100
            # ensures forall(lambda d: implies(d > 0, exists(lambda k: d * k == result)))
            return n
        """)
    program = parse(source)
    n, d, k = Variable("n"), Variable("d"), Variable("k")
    assert program.requires == [
        BoolOp("and", [Comparison("<=", Constant(0), n), Comparison("<", n, Constant(100))])
    ]
    assert program.ensures == [
        Quantifier("forall", ["d"], BoolOp("=>", [
            Comparison(">", d, Constant(0)),
            Quantifier("exists", ["k"], Comparison("==", BinaryOp("*", d, k), Variable("result"))),
        ]))
    ]


def test_parallel_assignment_preserves_order_and_avoids_name_collision():
    source = dedent("""\
        def f(a, b, __parser_tmp_0):
            # requires __parser_tmp_1 == 0
            # ensures result == __parser_tmp_0
            a, b = b, a % b
            return b
        """)
    program = parse(source)
    a, b = Variable("a"), Variable("b")
    temps = [command.target for command in program.body[:2]]
    assert len(set(temps)) == 2
    assert set(temps).isdisjoint({a, b, Variable("__parser_tmp_0"), Variable("__parser_tmp_1")})
    assert program.body == [
        Assign(temps[0], b), Assign(temps[1], BinaryOp("%", a, b)),
        Assign(a, temps[0]), Assign(b, temps[1]), Return(b),
    ]


def test_annotations_respect_logical_headers_and_ignore_docstring_text():
    source = dedent('''\
        def f(
            n,
        ):
            """Многострочное описание.
            # requires False
            """
            # requires True
            while (
                n > 0
            ):
                # invariant True
                while n > 1:
                    # invariant n > 0
                    n -= 1
                n -= 1
            return n
        ''')
    program = parse(source)
    outer = program.body[0]
    assert program.requires == [BoolConstant(True)]
    assert outer.invariant == BoolConstant(True)
    assert outer.body[0].invariant == Comparison(">", Variable("n"), Constant(0))


@pytest.mark.parametrize(
    ("source", "line", "message"),
    [
        ("def f(:\n    pass\n", 1, "invalid syntax"),
        ("def f():\n    # requires x +\n    pass\n", 2, "Ошибка в requires"),
        ("def f():\n    # ensures\n    pass\n", 2, "ожидается формула"),
        ("def f():\n    while True:\n        pass\n", 2, "ровно одна"),
        ("def f():\n    while True:\n        # invariant True\n        # invariant False\n        pass\n", 2, "ровно одна"),
        ("def f():\n    # invariant True\n    pass\n", 2, "здесь недопустима"),
        ("def f():\n  # ensures True\n    pass\n", 2, "вне начала"),
        ("def f():\n    pass\n    # requires True\n    return 0\n", 3, "вне начала"),
        ("def f():\n    return 1  # ensures True\n", 2, "отдельной строкой"),
        ("def f():\n    obj.value = 1\n", 2, "Цель присваивания"),
        ("def f():\n    if 1:\n        pass\n", 2, "Ожидается формула"),
        ("def f():\n    for x in xs:\n        pass\n", 2, "Команда For"),
        ("def f():\n    while True:\n        # invariant True\n        pass\n    else:\n        pass\n", 2, "Команда While"),
        ("def f():\n    return 1\n\ndef g():\n    return 2\n", 4, "ровно одна функция"),
    ],
)
def test_invalid_programs_report_source_line(source, line, message):
    with pytest.raises(ParseError, match=message) as error:
        parse(source)
    assert error.value.lineno == line


def test_error_in_annotation_points_to_original_column():
    source = "def f(число):\n    # requires число > 0 and flag\n    return число\n"
    with pytest.raises(ParseError) as error:
        parse(source)
    assert error.value.lineno == 2
    assert error.value.offset == source.splitlines()[1].index("flag") + 1
