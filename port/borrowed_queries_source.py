"""Avoid cloning instance lists for queries that cannot execute user code."""

import re


def with_borrowed_queries(code, compare=False):
    helper = r'''
typedef struct {
    Instance* const* items;
    int32_t count;
    int32_t snapshot;
} ReadOnlyInstanceList;

static inline ReadOnlyInstanceList readOnlyInstances(Runner* runner, int32_t target,
        bool allowInstanceId, Instance** single) {
    ReadOnlyInstanceList list = {.snapshot = -1};
    if (target == INSTANCE_ALL) {
        list.items = runner->instances;
        list.count = (int32_t)arrlen(runner->instances);
    } else if (target >= 0 && (!allowInstanceId || target < INSTANCE_ID_BASE)) {
        if ((uint32_t)target < runner->dataWin->objt.count) {
            list.items = runner->instancesByObject[target];
            list.count = (int32_t)arrlen(runner->instancesByObject[target]);
        }
    } else if (allowInstanceId && target >= INSTANCE_ID_BASE) {
        *single = hmget(runner->instancesById, target);
        list.items = single;
        list.count = *single != nullptr ? 1 : 0;
    }
#ifdef HLD_VERIFY_BORROWED_QUERIES
    const int32_t base = allowInstanceId ? Runner_pushInstancesForTarget(runner, target)
                                         : Runner_pushInstancesOfObject(runner, target);
    require((int32_t)arrlen(runner->instanceSnapshots) - base == list.count);
    for (int32_t i = 0; i < list.count; ++i)
        require(runner->instanceSnapshots[base + i] == list.items[i]);
    Runner_popInstanceSnapshot(runner, base);
#endif
    return list;
}
'''
    if compare:
        policy = r'''
static unsigned readOnlyQueries;
static unsigned readOnlyCandidates;

static inline bool useReadOnlyInstances(int frame) {
    return frame >= 180 && (frame / 180) % 2 != 0;
}

void hldReportReadOnlyInstances(int frame) {
    if (frame <= 0 || frame % 90 != 0) return;
    logInfo("Vita read-only instances frame=%d enabled=%d queries=%u candidates=%u\n",
            frame, (int)useReadOnlyInstances(frame - 1), readOnlyQueries, readOnlyCandidates);
    readOnlyQueries = readOnlyCandidates = 0;
}

'''
        baseline = r'''    if (!useReadOnlyInstances(runner->frameCount)) {
        list.snapshot = allowInstanceId ? Runner_pushInstancesForTarget(runner, target)
                                        : Runner_pushInstancesOfObject(runner, target);
        list.count = (int32_t)arrlen(runner->instanceSnapshots) - list.snapshot;
        if (list.count > 0) list.items = runner->instanceSnapshots + list.snapshot;
        return list;
    }
    ++readOnlyQueries;
'''
        helper = policy + helper.replace("    if (target == INSTANCE_ALL) {", baseline + "    if (target == INSTANCE_ALL) {")
        helper = helper.replace("    return list;\n}", "    readOnlyCandidates += (unsigned)list.count;\n    return list;\n}")

    anchor = "static RValue builtin_instance_number("
    assert code.count(anchor) == 1
    code = code.replace(anchor, helper + "\n" + anchor)
    for name in ("instance_number", "instance_find", "instance_nearest", "instance_exists",
                 "collision_line", "collision_point", "instance_position"):
        pattern = rf"^static RValue builtin_{name}\([^\n]+\) \{{.*?^\}}"
        match = re.search(pattern, code, re.M | re.S)
        body = match[0]
        body, replacements = re.subn(
            r"(?m)^( +)int32_t snapBase = Runner_pushInstances(OfObject|ForTarget)\(runner, (\w+)\);\n"
            r"\1int32_t snapEnd += \(int32_t\) arrlen\(runner->instanceSnapshots\);",
            lambda m: (f"{m[1]}Instance* single = nullptr;\n{m[1]}ReadOnlyInstanceList candidates = "
                       f"readOnlyInstances(runner, {m[3]}, "
                       f"{'true' if m[2] == 'ForTarget' else 'false'}, &single);"), body)
        assert replacements == 1, name
        body, replacements = re.subn(r"int32_t (\w+) = snapBase; snapEnd > \1;", r"int32_t \1 = 0; candidates.count > \1;", body)
        assert replacements == 1, name
        assert body.count("runner->instanceSnapshots[") == 1, name
        body = body.replace("runner->instanceSnapshots[", "candidates.items[")
        pop = "Runner_popInstanceSnapshot(runner, snapBase);"
        assert body.count(pop) == 1, name
        body = (body.replace(pop, "if (candidates.snapshot >= 0) Runner_popInstanceSnapshot(runner, candidates.snapshot);")
                if compare else re.sub(r"(?m)^ +" + re.escape(pop) + r"\n", "", body))
        code = code[:match.start()] + body + code[match.end():]
    return code
