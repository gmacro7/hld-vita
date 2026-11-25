"""Reuse the previous patched vertex program for an exact repeated layout."""

from runtime_verification_source import replace_once


VERTEX_PATCH_CACHE = r'''
#define HLD_VERTEX_CACHE_SIZE 64
#define HLD_VERTEX_CACHE_ATTRIBUTES 4

typedef struct {
    program *owner;
    SceGxmShaderPatcher *patcher;
    SceGxmShaderPatcherId shader;
    SceGxmVertexProgram *patched;
    unsigned count;
    SceGxmVertexAttribute attributes[HLD_VERTEX_CACHE_ATTRIBUTES];
    SceGxmVertexStream streams[HLD_VERTEX_CACHE_ATTRIBUTES];
} HldVertexLayout;

extern unsigned hldVertexPrograms;
static HldVertexLayout hldVertexLayouts[HLD_VERTEX_CACHE_SIZE];
static unsigned hldVertexRequests, hldVertexReuses;

static HldVertexLayout *hldVertexLayout(program *p) {
    const unsigned slot = (unsigned)(p - progs);
    return &hldVertexLayouts[(slot ^ (slot / HLD_VERTEX_CACHE_SIZE)) % HLD_VERTEX_CACHE_SIZE];
}

static void hldForgetVertexLayout(program *p) {
    HldVertexLayout *layout = hldVertexLayout(p);
    if (layout->owner == p) layout->patched = NULL;
}

static void hldPatchVertexProgram(program *p, const SceGxmVertexAttribute *attributes,
                                  const SceGxmVertexStream *streams) {
    ++hldVertexRequests;
    const unsigned count = p->attr_num;
    if (!hldVertexPrograms || count == 0 || count > HLD_VERTEX_CACHE_ATTRIBUTES) {
        patch_vertex_program(gxm_shader_patcher, p->vshader->id, attributes, count, streams, count, &p->vprog);
        return;
    }

    HldVertexLayout *layout = hldVertexLayout(p);
    const SceGxmShaderPatcherId shader = p->vshader->id;
    const size_t attributeBytes = count * sizeof(*attributes), streamBytes = count * sizeof(*streams);
    // Borrow only the program the driver still holds for this linked GL
    // program. Buffer addresses are supplied separately on every draw.
    if (layout->owner == p && layout->patcher == gxm_shader_patcher && layout->shader == shader &&
        layout->patched && layout->patched == p->vprog && layout->count == count &&
        !memcmp(layout->attributes, attributes, attributeBytes) && !memcmp(layout->streams, streams, streamBytes)) {
        ++hldVertexReuses;
        return;
    }

    const int error = sceGxmShaderPatcherCreateVertexProgram(gxm_shader_patcher, shader,
        attributes, count, streams, count, &p->vprog);
    if (error || !p->vprog) {
        layout->patched = NULL;
#ifdef LOG_ERRORS
        if (error) vgl_log("Vertex shader patching failed (%s) on shader 0x%X with %d attributes and %d streams.\n",
                           get_gxm_error_literal(error), shader, count, count);
#endif
        return;
    }
    layout->owner = p;
    layout->patcher = gxm_shader_patcher;
    layout->shader = shader;
    layout->patched = p->vprog;
    layout->count = count;
    memcpy(layout->attributes, attributes, attributeBytes);
    memcpy(layout->streams, streams, streamBytes);
}

void hldVglReadVertexPrograms(unsigned *requests, unsigned *reuses) {
    *requests = hldVertexRequests;
    *reuses = hldVertexReuses;
    hldVertexRequests = hldVertexReuses = 0;
}
'''


def with_vertex_program_cache(code):
    anchor = "static program progs[MAX_CUSTOM_PROGRAMS];"
    code = replace_once(code, anchor, anchor + "\n" + VERTEX_PATCH_CACHE)
    anchor = "\tpatch_vertex_program(gxm_shader_patcher, p->vshader->id, attributes, p->attr_num, streams, p->attr_num, &p->vprog);"
    assert code.count(anchor) == 3
    code = code.replace(anchor, "\thldPatchVertexProgram(p, attributes, streams);")
    for signature, argument in (("glDeleteProgram", "prog"), ("glLinkProgram", "progr")):
        anchor = (f"void {signature}(GLuint {argument}) {{\n\tTHREAD_SAFE()\n\n"
                  f"\t// Grabbing passed program\n\tprogram *p = &progs[{argument} - 1];")
        code = replace_once(code, anchor, anchor + "\n\thldForgetVertexLayout(p);")
    return code


def with_vertex_program_loop(code):
    anchor = "            hldReportAudioUpdate(runner->frameCount);"
    return replace_once(code, anchor, anchor + r'''
            if (runner->frameCount > 0 && runner->frameCount % 90 == 0) {
                extern unsigned hldVertexPrograms;
                extern void hldVglReadVertexPrograms(unsigned*, unsigned*);
                unsigned requests, reuses;
                hldVglReadVertexPrograms(&requests, &reuses);
                logInfo("Vita vertex patches frame=%d enabled=%u requests=%u reuses=%u\n",
                        runner->frameCount, hldVertexPrograms, requests, reuses);
            }''')
