"""Keep the VM operand stack index local across checked scalar handlers."""

from pathlib import Path
import re


STACK_HELPERS = Path(__file__).with_name("stack-register.inc")

SCALAR_HANDLERS = {
    "OP_PACKED_VAR_READ", "OP_PACKED_VAR_WRITE", "OP_PACKED_ARGUMENT_ARRAY", "OP_PACKED_ARGUMENT",
    "OP_PACKED_LITERAL_VALUE", "OP_PACKED_LITERAL_STORE", "OP_PACKED_PROPERTY_READ",
    "OP_ADD", "OP_SUB", "OP_MUL", "OP_POP", "OP_POPZ", "OP_CONV", "OP_CMP", "OP_BT", "OP_BF",
}

# These calls either operate on values or inspect context fields independent
# of the operand stack. Any new call keeps its handler on the materialized path.
STACK_INDEPENDENT_CALLS = {
    "if", "for", "while", "switch", "sizeof", "defined", "assert", "require", "abort",
    "BinaryUtils_readUint32Aligned", "OpWord_extra", "OpWord_type1", "OpWord_type2",
    "OpWord_comparisonFunction", "OpWord_branchOffset", "RValue_free", "RValue_makeInt32",
    "RValue_makeInt64", "RValue_makeReal", "RValue_toReal", "RValue_toInt32",
    "RValue_writeIntoSlotStealingOwnershipOrCopying", "coerceIntStoreToReal",
    "Instance_getSelfVar", "Instance_setSelfVar", "resolveLocalSlot", "resolveVarOperand",
    "packedScriptArgument", "packedScopeInstance", "branchOnPackedLiteral",
    "consumeConditionalBranch", "GMLReal_fabs", "stackPush", "stackPushTyped", "stackPop", "stackPopInt32",
    "resolveVariableRead", "resolveVariableWrite", "handleAddString", "handleMulString", "handleCmp",
    "handleConv", "handlePop",
}

STACK_FALLBACKS = ("handleAddString", "handleMulString", "handleCmp", "handleConv", "handlePop")


def cached_stack_handler(name, body):
    if name not in SCALAR_HANDLERS:
        return None
    statements = re.sub(r"/\*.*?\*/|//[^\n]*", "", body, flags=re.S)
    calls = set(re.findall(r"\b([A-Za-z_]\w*)\s*\(", statements))
    if calls - STACK_INDEPENDENT_CALLS or re.search(r"\breturn\b", statements):
        return None

    body = body.replace("ctx->stack.top", "stackTop")
    body = re.sub(r"\bstackPushTyped\(ctx,", "cachedStackPushTyped(slots, &stackTop,", body)
    body = re.sub(r"\bstackPush\(ctx,", "cachedStackPush(slots, &stackTop,", body)
    body = body.replace("stackPop(ctx)", "cachedStackPop(slots, &stackTop)")
    body = body.replace("stackPopInt32(ctx)", "RValue_toInt32(cachedStackPop(slots, &stackTop))")
    body = body.replace("resolveVariableRead(ctx,", "cachedStackReadVariable(ctx, &stackTop,")
    body = body.replace("resolveVariableWrite(ctx,", "cachedStackWriteVariable(ctx, &stackTop,")
    for name in STACK_FALLBACKS:
        body = re.sub(rf"\b{name}\(ctx,[^;\n]+\);",
                      lambda call: f"do {{ VM_FLUSH_STACK(); {call[0]} VM_RELOAD_STACK(); }} while (0);",
                      body)
    for name in STACK_FALLBACKS:
        assert len(re.findall(rf"\b{name}\(ctx,", body)) == body.count(f"VM_FLUSH_STACK(); {name}(ctx,"), name
    return body
