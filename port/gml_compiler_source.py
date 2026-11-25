"""Compile selected GML code entries to C inside vm.c.

Each compiled entry is a C function with one label per original instruction. Branches jump directly.
Other instructions run the interpreter's own case bodies from executeBytecodeInstructions with fusion
disabled, so each body handles exactly one instruction. Operands are read from a private copy of the
entry's bytecode taken after patchReferenceOperands and before any packing rewrites the shared buffer.
At bind time the copy's instruction words must equal the words this file was generated from.
"""

import re

from gml_bytecode import read_code_entries
from runtime_verification_source import replace_once
from threaded_vm_source import opcode_handlers

FUNCTION = "static RValue executeBytecodeInstructions(VMContext* ctx, CodeEntry* code) {"
OPCODE_NAMES = {
    "PUSH": "OP_PUSH", "PUSH_LOCAL": "OP_PUSH_LOCAL", "PUSH_GLOBAL": "OP_PUSH_GLOBAL", "PUSH_BUILTIN": "OP_PUSH_BUILTIN",
    "PUSH_IMMEDIATE": "OP_PUSH_IMMEDIATE", "POP": "OP_POP", "DUP": "OP_DUP", "CONV": "OP_CONV", "MUL": "OP_MUL",
    "DIV": "OP_DIV", "REM": "OP_REM", "MOD": "OP_MOD", "ADD": "OP_ADD", "SUB": "OP_SUB", "AND": "OP_AND", "OR": "OP_OR",
    "XOR": "OP_XOR", "NEG": "OP_NEG", "NOT": "OP_NOT", "SHL": "OP_SHL", "SHR": "OP_SHR", "CMP": "OP_CMP", "B": "OP_B",
    "BT": "OP_BT", "BF": "OP_BF", "CALL": "OP_CALL", "PUSH_ENV": "OP_PUSH_ENV", "POP_ENV": "OP_POP_ENV",
    "RET": "OP_RET", "EXIT": "OP_EXIT", "POPZ": "OP_POPZ", "BREAK": "OP_BREAK",
}


def _function_text(code):
    start = code.index(FUNCTION)
    return code[start:code.index("\n}\n", start) + 3]


def _resolved_bodies(function):
    bodies = opcode_handlers(function)
    resolved, last = {}, None
    for name, body in reversed(list(bodies.items())):
        if body:
            last = body
        resolved[name] = last
    return resolved


def _symbol(name):
    return re.sub(r"\W", "_", name)


COMPARE = {1: "<", 2: "<=", 3: "==", 4: "!=", 5: ">=", 6: ">"}
INSTANCE_STACKTOP = -9
VARTYPE_NORMAL = 0xA0


class _Emitter:
    """Straight-line symbolic stack: pending values live in C temps until a label or fallback needs memory."""

    def __init__(self):
        self.lines, self.pending, self.depth, self.memory = [], [], 0, 0

    def temp(self):
        # Name a new value by its position among pending values so each function needs few C locals.
        name = f"p{len(self.pending)}"
        self.depth = max(self.depth, len(self.pending) + 1)
        return name

    def emit(self, line):
        self.lines.append("    " + line)

    def flush(self):
        for value in self.pending:
            self.emit(f"slots[ctx->stack.top++] = {value};")
        self.pending.clear()

    def take(self):
        if self.pending:
            return self.pending.pop()
        value = f"m{self.memory}"
        self.memory = (self.memory + 1) % 2
        self.emit(f"{value} = slots[--ctx->stack.top];")
        return value

    def slow(self, operands, body, result):
        """Runtime slow path: spill pending and operands, run the interpreter body, reload."""
        for value in self.pending + operands:
            self.emit(f"    slots[ctx->stack.top++] = {value};")
        self.emit("    do {")
        self.emit("        " + body)
        self.emit("    } while (0);")
        self.emit(f"    {result} = slots[--ctx->stack.top];")
        for value in reversed(self.pending):
            self.emit(f"    {value} = slots[--ctx->stack.top];")


def _fast(em, instruction, bodies, names):
    """Emit a fast path. Returns False when the instruction needs the generic fallback."""
    op, t1, t2 = instruction.name, instruction.type1, instruction.type2
    raw = int.from_bytes(instruction.operand, "little") if len(instruction.operand) == 4 else None
    if op == "PUSH_IMMEDIATE":
        value = em.temp()
        em.emit(f"{value} = RValue_makeInt32({instruction.extra}); {value}.gmlStackType = GML_TYPE_INT16;")
        em.pending.append(value)
        return True
    if op == "PUSH" and t1 in (0, 1, 2, 4):
        value = em.temp()
        make = {0: "RValue_makeReal(BinaryUtils_readFloat64Aligned(extraData))",
                1: "RValue_makeReal((GMLReal) BinaryUtils_readFloat32Aligned(extraData))",
                2: "RValue_makeInt32(BinaryUtils_readInt32Aligned(extraData))",
                4: "RValue_makeBool(BinaryUtils_readInt32Aligned(extraData) != 0)"}[t1]
        tag = {0: "GML_TYPE_DOUBLE", 1: "GML_TYPE_FLOAT", 2: "GML_TYPE_INT32", 4: "GML_TYPE_BOOL"}[t1]
        em.emit(f"{value} = {make}; {value}.gmlStackType = {tag};")
        em.pending.append(value)
        return True
    if op in ("PUSH", "PUSH_BUILTIN") and (op == "PUSH_BUILTIN" or t1 == 6):
        # Push-only bodies: they push one value and never read the stack.
        value = em.temp()
        em.emit("do {")
        em.emit("    " + bodies[names[op]])
        em.emit("} while (0);")
        em.emit(f"{value} = slots[--ctx->stack.top];")
        em.pending.append(value)
        return True
    if op == "PUSH" and t1 == 5 and raw is not None and ((raw >> 24) & 0xF8) == VARTYPE_NORMAL \
            and instruction.extra != INSTANCE_STACKTOP:
        value = em.temp()
        em.emit("{")
        em.emit("    Variable* varDef = resolveVarDef(ctx, resolveVarOperand(extraData));")
        em.emit(f"    if (!(varDef->varID >= 0 && tryFastVarRead(ctx, {instruction.extra}, varDef, &{value}))) {{")
        em.emit("        handlePush(ctx, opword, extraData, GML_TYPE_VARIABLE);")
        em.emit(f"        {value} = slots[--ctx->stack.top];")
        em.emit("    }")
        em.emit(f"    {value}.gmlStackType = GML_TYPE_VARIABLE;")
        em.emit("}")
        em.pending.append(value)
        return True
    if op == "PUSH_LOCAL" and raw is not None and ((raw >> 24) & 0xF8) == VARTYPE_NORMAL:
        value = em.temp()
        em.emit("{")
        em.emit("    Variable* varDef = resolveVarDef(ctx, resolveVarOperand(extraData));")
        em.emit("    uint32_t localSlot = resolveLocalSlot(ctx, varDef->varID);")
        em.emit("    require(ctx->currentCallFrame->localVarsCount > localSlot);")
        em.emit(f"    {value} = ctx->currentCallFrame->localVars[localSlot];")
        em.emit(f"    {value}.ownsReference = false;")
        em.emit(f"    {value}.gmlStackType = GML_TYPE_VARIABLE;")
        em.emit("}")
        em.pending.append(value)
        return True
    if op == "POP" and t1 == 5 and raw is not None and ((raw >> 24) & 0xF8) == VARTYPE_NORMAL \
            and instruction.extra != INSTANCE_STACKTOP:
        value = em.take()
        em.emit(f"{value} = coerceIntStoreToReal({value}, {t2});")
        em.emit(f"resolveVariableWrite(ctx, {instruction.extra}, resolveVarOperand(extraData), {value});")
        return True
    if op in ("ADD", "SUB", "MUL") or (op == "CMP" and instruction.comparison in COMPARE):
        b, a = em.take(), em.take()
        result = em.temp()
        numeric = (f"({a}.type == RVALUE_INT32 || {a}.type == RVALUE_REAL) && "
                   f"({b}.type == RVALUE_INT32 || {b}.type == RVALUE_REAL)")
        em.emit(f"if ({numeric}) {{")
        em.emit(f"    rr = {a};")
        if op == "CMP":
            em.emit(f"    GMLReal da = {a}.type == RVALUE_INT32 ? (GMLReal){a}.int32 : {a}.real;")
            em.emit(f"    GMLReal db = {b}.type == RVALUE_INT32 ? (GMLReal){b}.int32 : {b}.real;")
            em.emit("    GMLReal diff = da - db;")
            em.emit("    GMLReal epsilon = (ctx->runner != NULL) ? ctx->runner->mathEpsilon : GML_MATH_EPSILON;")
            em.emit("    int cmp = (GMLReal_fabs(diff) <= epsilon) ? 0 : (diff < 0) ? -1 : 1;")
            em.emit(f"    rr.int32 = (cmp {COMPARE[instruction.comparison]} 0) ? 1 : 0;")
            em.emit("    rr.type = RVALUE_BOOL;")
            em.emit("    rr.gmlStackType = GML_TYPE_BOOL;")
        else:
            symbol = {"ADD": "+", "SUB": "-", "MUL": "*"}[op]
            em.emit(f"    if ({a}.type == RVALUE_INT32 && {b}.type == RVALUE_INT32) {{")
            em.emit(f"        rr.int32 = {a}.int32 {symbol} {b}.int32;")
            em.emit("    } else {")
            em.emit(f"        GMLReal da = ({a}.type == RVALUE_INT32) ? (GMLReal) {a}.int32 : {a}.real;")
            em.emit(f"        GMLReal db = ({b}.type == RVALUE_INT32) ? (GMLReal) {b}.int32 : {b}.real;")
            em.emit("        rr.real = da {symbol} db;".replace("{symbol}", symbol))
            em.emit("        rr.type = RVALUE_REAL;")
            em.emit("    }")
            em.emit(f"    rr.gmlStackType = {t2};")
        em.emit(f"    {result} = rr;")
        em.emit("} else {")
        em.slow([a, b], bodies[names[op]], result)
        em.emit("}")
        em.pending.append(result)
        return True
    if op == "CONV" and ((t2 << 4) | t1) in (0x52, 0x45, 0x25, 0x02):
        key = (t2 << 4) | t1
        value = em.take()
        result = em.temp()
        if key == 0x52:
            em.emit(f"{result} = {value}; {result}.gmlStackType = {t2};")
        elif key == 0x02:
            em.emit(f"{result} = {value}; {result}.real = (GMLReal) {value}.int32; {result}.type = RVALUE_REAL; {result}.gmlStackType = {t2};")
        else:
            em.emit(f"{result} = {value};")
            if key == 0x45:
                em.emit(f"if ({value}.type == RVALUE_INT32) {{ {result}.int32 = {value}.int32 > 0 ? 1 : 0; {result}.type = RVALUE_BOOL; {result}.gmlStackType = {t2}; }}")
                em.emit(f"else if ({value}.type == RVALUE_BOOL) {{ {result}.gmlStackType = {t2}; }}")
                em.emit(f"else if ({value}.type == RVALUE_REAL) {{ {result}.int32 = {value}.real > (GMLReal) 0.5 ? 1 : 0; {result}.type = RVALUE_BOOL; {result}.gmlStackType = {t2}; }}")
            else:
                em.emit(f"if ({value}.type == RVALUE_INT32) {{ {result}.gmlStackType = {t2}; }}")
                em.emit(f"else if ({value}.type == RVALUE_BOOL) {{ {result}.type = RVALUE_INT32; {result}.gmlStackType = {t2}; }}")
                em.emit(f"else if ({value}.type == RVALUE_REAL) {{ {result}.int32 = (int32_t) {value}.real; {result}.type = RVALUE_INT32; {result}.gmlStackType = {t2}; }}")
            em.emit("else {")
            em.slow([value], bodies[names[op]], result)
            em.emit("}")
        em.pending.append(result)
        return True
    return False


def emit_entry(entry, bodies, fast=True):
    instructions = list(entry.instructions())
    base = entry.length - len(entry.bytecode)          # the entry's offset inside its blob
    addresses = {base + i.address for i in instructions} | {entry.length}
    labels = {base}
    for instruction in instructions:
        if instruction.name in ("B", "BT", "BF", "PUSH_ENV", "POP_ENV"):
            labels.add(base + instruction.branch_target)
    symbol = _symbol(entry.name)
    em = _Emitter()
    for instruction in instructions:
        address = base + instruction.address
        following = address + instruction.size
        name = OPCODE_NAMES.get(instruction.name)
        assert name is not None and name in bodies, (entry.name, str(instruction))
        if address in labels:
            em.flush()
        em.lines.append(f"hldc_{address:05x}: {{")
        em.emit(f"MAYBE_UNUSED const uint32_t instrAddr = {address};")
        em.emit(f"MAYBE_UNUSED const OpWord opword = {{0x{instruction.word:08x}u}};")
        em.emit(f"MAYBE_UNUSED const uint8_t* const extraData = bytecodeBase + {address + 4};")
        em.emit(f"MAYBE_UNUSED const uint8_t opcode = 0x{instruction.opcode:02x};")
        em.emit(f"ip = {following};")
        end = "return scriptFallthroughReturnValue(ctx);"
        if instruction.name in ("B", "BT", "BF"):
            target = base + instruction.branch_target
            assert target in addresses, (entry.name, str(instruction))
            jump = f"goto hldc_{target:05x};" if target != entry.length else end
            if instruction.name == "B":
                em.flush()
                em.emit(jump)
            else:
                value = em.take()
                em.flush()
                test = "== 0" if instruction.name == "BF" else "!= 0"
                em.emit(f"if (RValue_toInt32({value}) {test}) {jump}")
                if following == entry.length:
                    em.emit(end)
            em.lines.append("}")
            continue
        if fast and _fast(em, instruction, bodies, OPCODE_NAMES):
            if following == entry.length:
                em.flush()
                em.emit(end)
            em.lines.append("}")
            continue
        em.flush()
        em.emit("do {")
        em.emit("    " + bodies[name])
        em.emit("} while (0);")
        jump = f"goto hldc_{following:05x};" if following != entry.length else end
        em.emit(f"if (ip == {following}) {jump}")
        em.emit("goto hldc_resume;")
        em.lines.append("}")
    assert not em.pending
    lines = [f"static uint8_t hldcBytes_{symbol}[{entry.length}];",
             f"static const uint32_t hldcWords_{symbol}[{len(instructions)}] = {{",
             "    " + ", ".join(f"0x{i.word:08x}u" for i in instructions) + ",", "};",
             f"static const uint32_t hldcAddresses_{symbol}[{len(instructions)}] = {{",
             "    " + ", ".join(str(base + i.address) for i in instructions) + ",", "};",
             f"static RValue hldcExec_{symbol}(VMContext* ctx, CodeEntry* code) {{",
             "    MAYBE_UNUSED const uint32_t codeLength = code->length;",
             f"    const uint8_t* const bytecodeBase = hldcBytes_{symbol};",
             "    ctx->bytecodeBase = bytecodeBase;",
             "    uint32_t ip = code->offset;",
             "    MAYBE_UNUSED const bool useHldDistortionLoop = false;",
             "    MAYBE_UNUSED const bool fuseInstructions = false;",
             "    MAYBE_UNUSED const bool useHldSceneryCull = false;",
             "    MAYBE_UNUSED RValue* restrict slots = ctx->stack.slots;"]
    lines.append("    MAYBE_UNUSED RValue rr, m0, m1" + "".join(f", p{i}" for i in range(em.depth)) + ";")
    lines += [f"    if (ip != {base}) goto hldc_resume;",
              f"    goto hldc_{base:05x};",
              "hldc_resume:",
              "    if (ip >= codeLength) return scriptFallthroughReturnValue(ctx);",
              "    switch (ip) {"]
    for address in sorted(addresses - {entry.length}):
        lines.append(f"        case {address}: goto hldc_{address:05x};")
    lines += ['        default: logError("hldc: unknown address %u in %s\\n", ip, code->name); abort();', "    }"]
    lines += em.lines
    lines.append("}")
    return lines, symbol, len(instructions)


def with_compiled_gml(code, names, data_path=None, fast=True):
    entries = {entry.name: entry for entry in (read_code_entries(data_path) if data_path else read_code_entries())}
    bodies = _resolved_bodies(_function_text(code))
    out, table = [], []
    for name in names:
        entry = entries[name]
        lines, symbol, count = emit_entry(entry, bodies, fast)
        out += lines
        table.append((entry, symbol, count))
    out += ["static void hldcBind(VMContext* ctx) {",
            "    const char* disabled = getenv(\"HLD_COMPILED_GML_OFF\");",
            "    if (disabled != nullptr && disabled[0] == '1') return;",
            "    hldcFunctionCount = ctx->dataWin->code.count;",
            "    hldcFunctions = safeCalloc(hldcFunctionCount, sizeof(*hldcFunctions));",
            "    repeat(ctx->dataWin->code.count, index) {",
            "        CodeEntry* entry = &ctx->dataWin->code.entries[index];",
            "        if (!entry->present || entry->name == nullptr) continue;"]
    for entry, symbol, count in table:
        out += [f'        if (strcmp(entry->name, "{entry.name}") == 0 && entry->length == {entry.length}u) {{',
                "            const uint8_t* bytes = codeBytes(ctx, entry);",
                f"            memcpy(hldcBytes_{symbol}, bytes, {entry.length});",
                "            bool same = true;",
                f"            repeat({count}, i) same = same && BinaryUtils_readUint32Aligned(hldcBytes_{symbol} + hldcAddresses_{symbol}[i]) == hldcWords_{symbol}[i];",
                f"            if (same) {{ hldcFunctions[index] = hldcExec_{symbol}; hldcBound++; }}",
                "        }"]
    out += ["    }",
            '    logInfo("HLD: bound %u compiled GML entries\\n", hldcBound);',
            "}", ""]
    # Keep every original line number so existing profile feedback still matches the unchanged functions:
    # declarations and the hook share the function's opening line, and the generated code goes at the end.
    declarations = ("typedef RValue (*HldcFunction)(VMContext*, CodeEntry*); static HldcFunction* hldcFunctions; "
                    "static uint32_t hldcFunctionCount; static unsigned hldcBound; static void hldcBind(VMContext* ctx); ")
    hook = (" if (hldcFunctions != nullptr) { const uint32_t hldcIndex = (uint32_t)(code - ctx->dataWin->code.entries); "
            "if (hldcIndex < hldcFunctionCount && hldcFunctions[hldcIndex] != nullptr) "
            "return hldcFunctions[hldcIndex](ctx, code); }")
    code = replace_once(code, FUNCTION, declarations + FUNCTION + hook)
    code = replace_once(code, "    patchReferenceOperands(ctx);\n", "    patchReferenceOperands(ctx); hldcBind(ctx);\n")
    return code + "\n" + "\n".join(out) + "\n"
