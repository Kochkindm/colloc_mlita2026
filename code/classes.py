from dataclasses import dataclass
from typing import TypeAlias


@dataclass(frozen=True)
class Variable:
    name: str


@dataclass(frozen=True)
class Constant:
    value: int | float | bool


@dataclass(frozen=True)
class BinaryOp:
    op: str
    left: "Expr"
    right: "Expr"


@dataclass(frozen=True)
class UnaryOp:
    op: str
    operand: "Expr"


@dataclass(frozen=True)
class Call:
    function: "Expr"
    args: list["Expr"]


@dataclass(frozen=True)
class Attribute:
    object: "Expr"
    name: str


Expr: TypeAlias = Variable | Constant | BinaryOp | UnaryOp | Call | Attribute


@dataclass(frozen=True)
class Comparison:
    op: str
    left: Expr
    right: Expr


@dataclass(frozen=True)
class BoolOp:
    op: str
    operands: list["Formula"]


@dataclass(frozen=True)
class Not:
    operand: "Formula"


@dataclass(frozen=True)
class BoolConstant:
    value: bool


Formula: TypeAlias = Comparison | BoolOp | Not | BoolConstant


@dataclass(frozen=True)
class Assign:
    target: Expr
    value: Expr


@dataclass(frozen=True)
class If:
    condition: Formula
    then_body: list["Command"]
    else_body: list["Command"]


@dataclass(frozen=True)
class While:
    condition: Formula
    invariant: Formula
    body: list["Command"]


@dataclass(frozen=True)
class Assert:
    condition: Formula


@dataclass(frozen=True)
class Assume:
    condition: Formula


@dataclass(frozen=True)
class Return:
    value: Expr


Command: TypeAlias = Assign | If | While | Assert | Assume | Return


@dataclass(frozen=True)
class Program:
    name: str
    params: list[str]
    requires: list[Formula]
    ensures: list[Formula]
    body: list[Command]


@dataclass(frozen=True)
class VC:
    id: str
    formula: Formula
    source_line: int | None
    description: str


@dataclass(frozen=True)
class Proved:
    pass


@dataclass(frozen=True)
class Failed:
    model: dict[str, object]


@dataclass(frozen=True)
class Unknown:
    reason: str


Verdict: TypeAlias = Proved | Failed | Unknown


@dataclass(frozen=True)
class VCResult:
    vc_id: str
    verdict: Verdict