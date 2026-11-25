"""Parse decompiled GameMaker 1.x GML (UndertaleModTool output) into a small AST.

Nodes are tuples whose first element names the kind. Expressions:
  ('num', float) ('str', text) ('name', ident) ('field', base, ident) ('index', base, [indices])
  ('call', name, [args]) ('unary', op, e) ('binary', op, a, b) ('ternary', c, a, b)
  ('preinc'|'predec'|'postinc'|'postdec', target)
Statements:
  ('block', [stmts]) ('var', [(name, init-or-None)]) ('assign', op, target, value) ('expr', e)
  ('if', c, then, else-or-None) ('while', c, body) ('dountil', body, c) ('for', init, c, step, body)
  ('repeat', n, body) ('with', target, body) ('switch', e, [(case-expr-or-None, [stmts])])
  ('break',) ('continue',) ('exit',) ('return', e-or-None)
"""

import re
import sys
from pathlib import Path

TOKEN = re.compile(r'''
    (?P<ws>\s+|//[^\n]*|/\*.*?\*/)
  | (?P<num>0x[0-9A-Fa-f]+|\$[0-9A-Fa-f]+|\d+\.\d*(?:[eE][-+]?\d+)?|\.\d+|\d+(?:[eE][-+]?\d+)?)
  | (?P<str>@"[^"]*"|"[^"]*"|'[^']*')
  | (?P<name>[A-Za-z_][A-Za-z0-9_]*)
  | (?P<op>\+\+|--|\+=|-=|\*=|/=|%=|\|=|&=|\^=|==|!=|<=|>=|&&|\|\||\^\^|<<|>>|[-+*/%<>=!&|^~?:;,.(){}\[\]])
''', re.S | re.X)

KEYWORDS = {"var", "if", "else", "while", "do", "until", "for", "repeat", "with", "switch", "case", "default",
            "break", "continue", "exit", "return", "div", "mod", "and", "or", "xor", "not", "globalvar"}

BINARY = [  # lowest to highest precedence
    ("||", "or"), ("&&", "and"), ("^^", "xor"),
    ("==", "!=", "<", "<=", ">", ">=", "="),
    ("|",), ("&",), ("^",), ("<<", ">>"), ("+", "-"), ("*", "/", "div", "mod", "%"),
]
CANONICAL = {"or": "||", "and": "&&", "xor": "^^", "=": "==", "mod": "%"}


class ParseError(Exception):
    pass


def tokenize(text):
    tokens = []
    position = 0
    while position < len(text):
        match = TOKEN.match(text, position)
        if match is None:
            raise ParseError(f"bad character {text[position]!r} at {position}")
        position = match.end()
        kind = match.lastgroup
        if kind == "ws":
            continue
        value = match.group(kind)
        if kind == "name" and value in KEYWORDS:
            kind = "kw"
        tokens.append((kind, value))
    tokens.append(("eof", ""))
    return tokens


def number(text):
    if text.startswith("0x"):
        return float(int(text, 16))
    if text.startswith("$"):
        return float(int(text[1:], 16))
    return float(text)


def string(text):
    if text.startswith('@"'):
        return text[2:-1]
    if text.startswith("'"):
        return text[1:-1]
    # GameMaker 1.x strings have no escape sequences.
    return text[1:-1]


class Parser:
    def __init__(self, text):
        self.tokens = tokenize(text)
        self.at = 0

    def peek(self, offset=0):
        return self.tokens[self.at + offset]

    def take(self):
        token = self.tokens[self.at]
        self.at += 1
        return token

    def accept(self, value):
        if self.peek()[1] == value and self.peek()[0] in ("op", "kw"):
            self.at += 1
            return True
        return False

    def expect(self, value):
        if not self.accept(value):
            raise ParseError(f"expected {value!r}, got {self.peek()} near token {self.at}")

    # ---- statements ----
    def program(self):
        statements = []
        while self.peek()[0] != "eof":
            statements.append(self.statement())
        return ("block", statements)

    def block_or_statement(self):
        return self.statement()

    def statement(self):
        kind, value = self.peek()
        if kind == "op" and value == ";":
            self.take()
            return ("block", [])
        if kind == "op" and value == "{":
            self.take()
            statements = []
            while not self.accept("}"):
                statements.append(self.statement())
            return ("block", statements)
        if kind == "kw":
            if value in ("var", "globalvar"):
                self.take()
                names = []
                while True:
                    name = self.take()
                    if name[0] != "name":
                        raise ParseError(f"bad var name {name}")
                    init = self.expression() if self.accept("=") else None
                    names.append((name[1], init))
                    if not self.accept(","):
                        break
                self.accept(";")
                return ("var" if value == "var" else "globalvar", names)
            if value == "if":
                self.take()
                condition = self.expression()
                self.accept("then") if self.peek()[1] == "then" else None
                then = self.statement()
                other = self.statement() if self.accept("else") else None
                return ("if", condition, then, other)
            if value == "while":
                self.take()
                condition = self.expression()
                return ("while", condition, self.statement())
            if value == "do":
                self.take()
                body = self.statement()
                self.expect("until")
                condition = self.expression()
                self.accept(";")
                return ("dountil", body, condition)
            if value == "for":
                self.take()
                self.expect("(")
                init = None if self.peek()[1] == ";" else self.simple_statement()
                self.expect(";")
                condition = None if self.peek()[1] == ";" else self.expression()
                self.expect(";")
                step = None if self.peek()[1] == ")" else self.simple_statement()
                self.expect(")")
                return ("for", init, condition, step, self.statement())
            if value == "repeat":
                self.take()
                count = self.expression()
                return ("repeat", count, self.statement())
            if value == "with":
                self.take()
                target = self.expression()
                return ("with", target, self.statement())
            if value == "switch":
                self.take()
                subject = self.expression()
                self.expect("{")
                cases = []
                while not self.accept("}"):
                    if self.accept("case"):
                        label = self.expression()
                        self.expect(":")
                    elif self.accept("default"):
                        label = None
                        self.expect(":")
                    else:
                        raise ParseError(f"expected case, got {self.peek()}")
                    body = []
                    while self.peek()[1] not in ("case", "default", "}") or self.peek()[0] not in ("kw", "op"):
                        body.append(self.statement())
                    cases.append((label, body))
                return ("switch", subject, cases)
            if value in ("break", "continue", "exit"):
                self.take()
                self.accept(";")
                return (value,)
            if value == "return":
                self.take()
                result = None if self.peek()[1] in (";", "}") else self.expression()
                self.accept(";")
                return ("return", result)
        statement = self.simple_statement()
        self.accept(";")
        return statement

    def simple_statement(self):
        if self.peek()[1] == "var" and self.peek()[0] == "kw":
            self.take()
            names = []
            while True:
                name = self.take()[1]
                init = self.expression() if self.accept("=") else None
                names.append((name, init))
                if not self.accept(","):
                    break
            return ("var", names)
        # Assignment targets are postfix expressions, so "=" here is assignment, not comparison.
        target = self.unary()
        kind, value = self.peek()
        if kind == "op" and value in ("=", "+=", "-=", "*=", "/=", "%=", "|=", "&=", "^="):
            self.take()
            return ("assign", value, target, self.expression())
        if target[0] in ("call", "preinc", "predec", "postinc", "postdec"):
            return ("expr", target)
        raise ParseError(f"statement without effect: {target} next {self.peek()}")

    # ---- expressions ----
    def expression(self):
        condition = self.binary(0)
        if self.accept("?"):
            first = self.expression()
            self.expect(":")
            second = self.expression()
            return ("ternary", condition, first, second)
        return condition

    def binary(self, level):
        if level == len(BINARY):
            return self.unary()
        left = self.binary(level + 1)
        while True:
            kind, value = self.peek()
            if kind in ("op", "kw") and value in BINARY[level]:
                # "=" inside an expression is comparison, but never at statement level (handled by caller).
                if value == "=" and level != 3:
                    break
                self.take()
                right = self.binary(level + 1)
                left = ("binary", CANONICAL.get(value, value), left, right)
            else:
                return left

    def unary(self):
        kind, value = self.peek()
        if kind in ("op", "kw") and value in ("!", "not", "-", "~", "+"):
            self.take()
            operand = self.unary()
            if value == "+":
                return operand
            if value == "-" and operand[0] == "num":
                return ("num", -operand[1])
            return ("unary", "!" if value == "not" else value, operand)
        if kind == "op" and value in ("++", "--"):
            self.take()
            return ("preinc" if value == "++" else "predec", self.postfix())
        return self.postfix()

    def postfix(self):
        node = self.primary()
        while True:
            kind, value = self.peek()
            if kind == "op" and value == ".":
                self.take()
                name = self.take()
                if name[0] not in ("name", "kw"):
                    raise ParseError(f"bad field {name}")
                node = ("field", node, name[1])
            elif kind == "op" and value == "[":
                self.take()
                indices = [self.expression()]
                while self.accept(","):
                    indices.append(self.expression())
                self.expect("]")
                node = ("index", node, indices)
            elif kind == "op" and value in ("++", "--"):
                self.take()
                node = ("postinc" if value == "++" else "postdec", node)
            else:
                return node

    def primary(self):
        kind, value = self.take()
        if kind == "num":
            return ("num", number(value))
        if kind == "str":
            return ("str", string(value))
        if kind == "op" and value == "(":
            inner = self.expression()
            self.expect(")")
            return inner
        if kind == "name" or (kind == "kw" and value in ("div", "mod")):
            if self.peek()[1] == "(" and self.peek()[0] == "op":
                self.take()
                args = []
                if not self.accept(")"):
                    args.append(self.expression())
                    while self.accept(","):
                        args.append(self.expression())
                    self.expect(")")
                return ("call", value, args)
            return ("name", value)
        raise ParseError(f"unexpected {kind} {value!r}")


def parse(text):
    return Parser(text).program()


if __name__ == "__main__":
    directory = Path(sys.argv[1])
    failures = 0
    files = sorted(directory.glob("*.gml"))
    for path in files:
        try:
            parse(path.read_text())
        except (ParseError, IndexError) as error:
            failures += 1
            if failures <= 15:
                print(f"{path.name}: {error}")
    print(f"parsed {len(files) - failures}/{len(files)}")
