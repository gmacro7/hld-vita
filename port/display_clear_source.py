import os
"""Submit a pending host clear when the display is first used."""

from pathlib import Path
import shlex
import shutil
import subprocess

from runtime_verification_source import replace_once
from full_viewport_mask_source import with_full_viewport_scissor
from fresh_scene_depth_source import with_initial_depth_clear, with_initial_depth_scene_generation


DECLARATIONS = r'''
void hldVglEmitDisplayClear(void);
void hldVglPrepareDisplay(void);
void hldVglRecordDisplayBegin(void);
'''

PENDING = r'''
static GLboolean hldDisplayClearPending;
static vector4f hldDisplayClearColor;
static unsigned hldClearQueued, hldClearEmitted, hldDisplayBegins;

void hldVglQueueDisplayClear(void) {
    THREAD_SAFE()
    // Clipped or masked clears keep their original timing.
    if (active_write_fb || scissor_test_state || blend_color_mask != SCE_GXM_COLOR_MASK_ALL ||
        hldDisplayClearPending) {
        glClear(GL_COLOR_BUFFER_BIT);
        return;
    }
    hldDisplayClearColor = clear_rgba_val;
    hldDisplayClearPending = GL_TRUE;
    __atomic_add_fetch(&hldClearQueued, 1, __ATOMIC_RELAXED);
}

void hldVglEmitDisplayClear(void) {
    if (!hldDisplayClearPending) return;
    hldDisplayClearPending = GL_FALSE;
    const GLboolean restoreScissor = scissor_test_state;
    if (restoreScissor) {
        scissor_test_state = GL_FALSE;
        update_scissor_test();
    }
    drawClear(GL_COLOR_BUFFER_BIT, &hldDisplayClearColor);
    if (restoreScissor) {
        scissor_test_state = GL_TRUE;
        update_scissor_test();
    }
    __atomic_add_fetch(&hldClearEmitted, 1, __ATOMIC_RELAXED);
}

// Display reads and an otherwise empty presentation can be the first users.
// Keep the caller's requested target even if the active GPU scene changes.
void hldVglPrepareDisplay(void) {
    if (!hldDisplayClearPending) return;
    framebuffer* write = active_write_fb;
    active_write_fb = NULL;
    scene_reset();
    active_write_fb = write;
}

void hldVglRecordDisplayBegin(void) {
    __atomic_add_fetch(&hldDisplayBegins, 1, __ATOMIC_RELAXED);
}

void hldVglReadDisplayClear(unsigned* queued, unsigned* emitted, unsigned* begins) {
    *queued = __atomic_exchange_n(&hldClearQueued, 0, __ATOMIC_RELAXED);
    *emitted = __atomic_exchange_n(&hldClearEmitted, 0, __ATOMIC_RELAXED);
    *begins = __atomic_exchange_n(&hldDisplayBegins, 0, __ATOMIC_RELAXED);
}
'''


def with_display_clear_misc(code):
    start = code.index("void glClear(GLbitfield mask) {")
    separator = code.index("\n\tscene_reset();", start) + len("\n\tscene_reset();")
    end = code.index("\nvoid glClearColor(", separator)
    wrapper = code[start:separator] + "\n\tdrawClear(mask, &clear_rgba_val);\n}\n"
    clear_body = replace_once(code[separator:end], "&clear_rgba_val.r", "&rgba->r")
    body = "static void drawClear(GLbitfield mask, const vector4f* rgba) {" + clear_body
    return code[:start] + body + wrapper + "\n" + PENDING + code[end:]


def with_display_clear_gxm(code):
    anchor = '#include "shared.h"\n'
    code = replace_once(code, anchor, anchor + DECLARATIONS)
    anchor = "\t\t\t\t&gxm_depth_stencil_surface);\n"
    code = replace_once(code, anchor, anchor + "\t\t\thldVglRecordDisplayBegin();\n")
    anchor = "\tif (dirty_scissor_state) {\n\t\tupdate_scissor_test();\n\t}\n}"
    code = replace_once(code, anchor, anchor[:-2] + "\n\tif (!active_write_fb) hldVglEmitDisplayClear();\n}")
    anchor = "void vglSwapBuffers(GLboolean has_commondialog) {\n\tTHREAD_SAFE()\n"
    code = replace_once(code, anchor, anchor + "\thldVglPrepareDisplay();\n")
    for operation in ("glFinish", "glFlush"):
        anchor = f"void {operation}(void) {{\n\tTHREAD_SAFE()\n"
        code = replace_once(code, anchor, anchor + "\thldVglPrepareDisplay();\n")
    return code


def with_display_clear_readback(code):
    anchor = '#include "shared.h"\n'
    code = replace_once(code, anchor, anchor + DECLARATIONS)
    for name in ("glReadPixels", "vglReadPixels", "glBlitNamedFramebuffer"):
        start = code.index(f"void {name}(")
        anchor_end = code.index("\n\tTHREAD_SAFE()\n", start) + len("\n\tTHREAD_SAFE()\n")
        is_display = "!readFramebuffer" if name == "glBlitNamedFramebuffer" else "!active_read_fb"
        code = code[:anchor_end] + f"\tif ({is_display}) hldVglPrepareDisplay();\n" + code[anchor_end:]
    return code


def build_display_clear_library(root: Path, out: Path, sdk: Path, *,
                                full_viewport_mask=False, initial_scene_depth=False):
    patches = {
        "misc": with_display_clear_misc,
        "gxm": with_display_clear_gxm,
        "framebuffers": with_display_clear_readback,
    }
    name = "display-clear"
    if os.environ.get("HLDC_RT_SCENES", "8") != "1":
        # Surfaces wider than 256 px get a render target of their own created with one scene per frame, so every
        # further pass into the same surface in a frame (HLD draws its 480x270 / 960x540 screen surfaces up to 5
        # times) blocks sceGxmEndScene until the GPU has finished the previous one. Give those targets 8 scenes.
        base_gxm = patches["gxm"]
        def with_render_target_scenes(code):
            code = base_gxm(code)
            old = "int r = setup_render_target(&rt_list[i].rt, w, h, rt_list[i].max_refs);"
            assert code.count(old) == 1
            code = code.replace(old, "int r = setup_render_target(&rt_list[i].rt, w, h, MAX_SCENES_PER_FRAME);")
            old = "int res = setup_render_target(&r->rt, w, h, r->max_refs);"
            assert code.count(old) == 1
            return code.replace(old, "int res = setup_render_target(&r->rt, w, h, MAX_SCENES_PER_FRAME);")
        patches["gxm"] = with_render_target_scenes
        name = "display-clear-rt8"
    if full_viewport_mask:
        patches["tests"] = with_full_viewport_scissor
        name = "display-clear-scissor"
    if initial_scene_depth:
        patches["misc"] = lambda code: with_initial_depth_clear(with_display_clear_misc(code))
        patches["gxm"] = lambda code: with_initial_depth_scene_generation(with_display_clear_gxm(code))
        name = "display-clear-initial-depth"
    return build_patched_vitagl_library(root, out, sdk, name, patches)


def build_patched_vitagl_library(root: Path, out: Path, sdk: Path, name: str, patches):
    dependencies = root / "deps"
    source = dependencies / "vitaGL" / "source"
    build_log = (dependencies / "vitagl-build.log").read_text().splitlines()
    directory = out / f"{name}-vitagl"
    directory.mkdir(exist_ok=True)
    objects = []
    for unit, patch in patches.items():
        target = directory / f"{unit}.c"
        obj = target.with_suffix(".o")
        command = next(line for line in build_log if line.endswith(f" -o source/{unit}.o source/{unit}.c"))
        args = shlex.split(command)
        args[0] = str(sdk / "arm-vita-eabi-gcc")
        # The installed archive uses the adapter in VitaSDK's GXM stubs.
        args.remove("-DHAVE_SOFTFP_ABI")
        args[args.index("-Isource")] = f"-I{source}"
        args[args.index("-o") + 1:] = [str(obj), str(target)]
        target.write_text(patch((source / f"{unit}.c").read_text()))
        subprocess.run(args, check=True)
        objects.append(obj)
    library = directory / f"libvitaGL-{name}.a"
    shutil.copyfile(dependencies / "softfp-target" / "lib" / "libvitaGL.a", library)
    subprocess.run([sdk / "arm-vita-eabi-gcc-ar", "r", library, *objects], check=True)
    return library


def with_display_clear_loop(code):
    anchor = "                    const uint64_t beforeHostClear = nowNanos();\n                    glClear(GL_COLOR_BUFFER_BIT);"
    code = replace_once(code, anchor, r'''                    const uint64_t beforeHostClear = nowNanos();
                    extern bool hldDisplayClear;
                    extern void hldVglQueueDisplayClear(void);
                    if (hldDisplayClear && runner->dataWin->gen8.gameID == 742741103 &&
                        runner->dataWin->gen8.wadVersion == 15 && *hostFramebuffer == 0)
                        hldVglQueueDisplayClear();
                    else
                        glClear(GL_COLOR_BUFFER_BIT);''')
    anchor = "            hldReportAudioUpdate(runner->frameCount);"
    return replace_once(code, anchor, anchor + r'''
            if (runner->frameCount > 0 && runner->frameCount % 90 == 0) {
                extern void hldVglReadDisplayClear(unsigned* queued, unsigned* emitted, unsigned* begins);
                unsigned queued, emitted, begins;
                hldVglReadDisplayClear(&queued, &emitted, &begins);
                logInfo("Vita display clears frame=%d queued=%u emitted=%u display_begins=%u\n",
                        runner->frameCount, queued, emitted, begins);
            }''')
