"""Package the experimental Vita presentation worker behind an opt-in marker."""
import argparse
import os
from pathlib import Path
import re
import shlex
import subprocess
import json as _json
HLDC_ENTRIES = _json.loads(os.environ.get('HLDC_ENTRIES', '[]'))
HLDC_NATIVE_SERIALIZE = os.environ.get('HLDC_NATIVE_SERIALIZE') == '1'
HLDC_NATIVE_SCRIPTS = os.environ.get('HLDC_NATIVE_SCRIPTS') == '1'
HLDC_NATIVE_ONLY = os.environ.get('HLDC_NATIVE_ONLY') == '1'
# Translated GML: a file listing code entries, or "all".
HLDC_TRANSLATE = os.environ.get('HLDC_TRANSLATE')
HLDC_PART_SOURCES = []


def portable_vm_source(generated):
    """vm.c as the port builds it: native serialization / native-only / translated GML / native scripts / compiled
    entries applied in the same order as the feedback build."""
    if HLDC_NATIVE_SERIALIZE:
        from native_serialize_source import with_native_serialize
        generated = with_native_serialize(generated)
    if HLDC_NATIVE_ONLY:
        from native_only_source import with_native_only_vm
        generated = with_native_only_vm(generated)
    if HLDC_TRANSLATE:
        from translated_gml_source import all_entries, with_translated_gml
        names = all_entries() if HLDC_TRANSLATE == "all" else \
            [line.strip() for line in Path(HLDC_TRANSLATE).read_text().splitlines() if line.strip()]
        generated, parts = with_translated_gml(generated, names, stats=os.environ.get("HLDC_STATS") == "1")
        HLDC_PART_SOURCES[:] = parts
    if HLDC_NATIVE_SCRIPTS:
        from native_scripts_source import with_native_scripts
        generated = with_native_scripts(generated)
    if HLDC_ENTRIES:
        from gml_compiler_source import with_compiled_gml
        generated = with_compiled_gml(generated, HLDC_ENTRIES)
    return generated
HLDC_PART_OBJECTS = []
import sys
from zipfile import ZipFile, ZIP_DEFLATED
from sort_source import with_fast_draw_sort
from incremental_draw_order_source import with_retained_draw_order
from single_draw_responder_source import with_single_draw_responder
from resolved_step_source import with_resolved_step
from classified_vm_entry_source import with_classified_vm_entry
from vm_source import with_fast_vm_values
from packed_source import with_packed_literals
from mask_source import with_mask_profile
from uniform_source import with_uniform_trial
from sprite_cache_source import with_sprite_cache
from room_image_cache_source import with_room_image_cache
from axis_draw_source import with_axis_sprite_draw
from transparent_composite_source import with_transparent_composite
from collision_query_source import with_collision_queries
from collision_bounds_source import with_collision_bounds_blocks, with_resumed_collision_blocks
from collision_target_views_source import with_collision_target_views, with_pinned_collision_targets
from persistent_terrain_bounds_source import (
    with_persistent_terrain_bounds, with_terrain_bounds_grid_hooks, with_terrain_bounds_sprite_hooks,
)
from premultiply_atlas_source import with_atlas_batch_upload_cache, with_premultiply_atlas_batches
from borrowed_queries_source import with_borrowed_queries
from placement_filters_source import with_placement_filters
from grid_refresh_source import with_grid_refresh
from bbox_cache_source import use_bbox_cache_refresh, with_bbox_cache
from mask_overlap_source import with_mask_overlap
from shape_candidate_cells_source import with_shape_candidate_cells
from vm_entry_profile_source import with_focused_step_samples, with_profiled_builtins, with_profiled_native_entry
from mine_entry_source import with_mine_entry, with_mine_entry_builtins, with_mine_entry_grid
from hologram_arrays_source import with_hologram_arrays
from hologram_args_source import with_hologram_args
from idle_scenery_source import with_idle_scenery
from scenery_stream_source import with_scenery_stream
from display_clear_source import build_display_clear_library, build_patched_vitagl_library, with_display_clear_loop
from surface_sprite_source import with_borrowed_surface_read, with_surface_sprite_renderer
from vertex_program_source import with_vertex_program_cache, with_vertex_program_loop
from walking_material_source import with_walking_material, with_walking_material_query
from mask_tiles_source import (with_mask_tiles_header, with_mask_tiles_builtins,
                               with_mask_tiles_runner, with_mask_tiles_renderer,
                               with_mask_line_tiles)
from grid_mask_memo_source import (with_builtin_mask_overlap_trial, with_dynamic_mask_memo_builtins,
                                   with_dynamic_mask_memo_renderer, with_grid_mask_memo,
                                   with_grid_mask_memo_runner)
from enemy_shadow_source import with_enemy_shadow, with_enemy_shadow_probes
from scenery_pass_source import with_scenery_pass
from scenery_draw_lists_source import with_scenery_draw_lists
from direct_instance_ids_source import with_direct_id_lookups, with_direct_id_runner
from shared_fields_runtime_source import (with_shared_field_iteration, with_shared_fields_hashmap,
                                          with_shared_fields_header, with_shared_fields_instance, with_shared_fields_runner)
from region_case_source import with_region_cases
from resting_scenery_source import with_resting_scenery
from active_case_source import with_active_case
from leaf_event_source import with_leaf_events
from array_pool_source import with_array_pool, with_array_pool_cleanup
from refined_runtime_source import with_refinement_gate, with_refinement_marker
from submission_rings_source import with_submission_rings, with_submission_trace
from sprite_worker_source import with_sprite_packets, with_sprite_worker
from graphics_stream_source import with_graphics_stream_lifecycle
from step_event_profile_source import with_step_event_profile, with_step_event_report
from frame_hitch_source import with_frame_hitch_profile, with_present_hitch_profile
from frame_log_source import with_frame_log_batch, with_frame_log_loop
from audio_start_profile_source import with_positional_play_profile, with_stream_start_profile
from archive_audio_source import with_archive_audio_reads, with_footstep_start_timings
from lazy_audio_length_source import with_lazy_audio_length
from ambient_memory_source import with_ambient_memory
from ambient_prefetch_source import with_ambient_prefetch_audio, with_ambient_prefetch_loop, with_constant_vorbis_crc
from frame_headroom_source import with_frame_headroom
from env_snapshot_pool_source import with_env_snapshot_pool
from scenery_snapshot_source import with_scenery_snapshot, with_scenery_snapshot_builtins, with_scenery_snapshot_runner
from packed_read_conversion_source import with_packed_read_conversions
from packed_slot_source import with_packed_slot_reads, with_packed_slot_writes
from shader_matrix_cache_source import with_shader_matrix_cache
from plain_shader_trial_source import with_plain_shader_trial
from vita_vm_feedback_source import ENGINE_PROFILE_UNITS, with_engine_profile_export, with_vm_profile_export
from scenery_cursor_source import with_scenery_cursor
from eventless_case_source import with_eventless_case
from native_scenery_body_source import with_native_scenery_body
from vm_density_source import with_compact_threaded_vm
from cpu_phase_profile_source import with_cpu_phase_profile
from scene_queries_source import with_scene_queries
from decoded_effect_cache_source import with_decoded_effect_cache, with_positional_effect_profile
from scenery_scalar_writes_source import with_scenery_scalar_writes
from graphics_commands_source import (SCALARS, EXTRA_SCALARS, VECTORS, graphics_command_source,
                                      with_graphics_command_loop, with_graphics_command_renderer, with_graphics_recording,
                                      with_render_tail_platform)
from capture_transport import with_compressed_capture
from collision_epoch_source import (with_collision_epoch_builtins, with_collision_epoch_runner,
                                    with_collision_epoch_vm)
from collision_target_span_source import with_collision_target_span
from script_call_source import with_script_call_buffers
from frame_deadline_source import with_frame_deadlines
from scalar_pair_source import with_scalar_pairs
from fixed_array_source import with_fixed_array_reads
from collision_grid_source import with_collision_grid
from shared_collision_grid_source import with_shared_collision_grid
from collision_phase_profile import with_collision_phase_profile
from wide_collision_source import with_wide_collision_blocks
from threaded_vm_source import with_threaded_vm
from switch_chain_source import with_switch_chains
from stable_case_source import with_stable_cases
from placement_cell_source import (with_placement_cell_grid, with_placement_cell_queries, with_placement_ids,
                                   with_point_query_cleanup)
from copy_block_source import with_copy_blocks
from idle_spawner_source import with_idle_spawners
from line_envelope_source import with_line_envelopes, with_line_envelope_cleanup, with_ordered_line_candidates
from line_chunk_source import with_line_chunk_bucket_hook, with_line_chunk_grid_hook, with_line_chunks
from line_terrain_index_source import with_line_terrain_index
from placement_probe_mask_source import with_placement_probe_masks
from frame_depth_clear_source import with_frame_depth_fold_loop, with_frame_depth_fold_renderer
from full_viewport_mask_source import (
    with_application_clear_profile, with_application_setup_profile, with_scissor_mask_report,
)
from fresh_scene_depth_source import with_initial_depth_profile_hook, with_initial_depth_report
from line_script_source import with_line_script
from palette_tail_source import with_palette_tail
from motion_repeat_source import with_motion_repeat
from scenery_skip_source import reuse_scenery_skip_guard, with_scenery_skip
from floor_pattern_source import with_floor_pattern
from phase_mine_source import with_phase_mine_collision, with_phase_mine_trigger
from phase_dormant_source import with_dormant_mines
from enemy_path_probe_source import with_enemy_path_probe
from line_mask_prep_source import with_line_mask_prep
from mantle_sweep_source import with_mantle_sweep_queries
from terrain_sweep_source import with_terrain_sweep_query, with_terrain_sweep_vm
from enemy_target_source import with_enemy_target
from scenery_batch_source import with_scenery_batches
from ascii_parser_source import with_ascii_parser
from pure_guard_source import with_pure_guards
from retained_sprite_source import with_retained_sprite_program
from flame_vent_source import with_flame_vent_guard, with_flame_vent_probes
from depth_helpers_source import with_depth_helpers
from recessing_guards_source import with_recessing_guards
from movement_helpers_source import with_movement_helpers
from zero_movement_source import with_zero_movement
from native_enemy_source import with_native_enemy_event
from compiled_events_source import with_compiled_events
from zero_alpha_quads_source import with_zero_alpha_quads
from sparse_composite_source import with_sparse_composite
from sparse_composite_trace import with_sparse_composite_trace
from runtime_verification_source import (with_runtime_verification_loop, with_room_phase_timings,
                                         with_distortion_reference, with_distortion_draw_trace,
                                         with_room_log_buffer_loop)
from text_fastpaths_source import with_text_fastpaths
from deferred_audio_source import (with_deferred_audio_builtins, with_deferred_audio_loop,
                                   with_deferred_audio_runner, with_deferred_audio_worker)

parser = argparse.ArgumentParser(description=__doc__)
modes = parser.add_mutually_exclusive_group()
modes.add_argument("--profile-vm", action="store_true", help="sample individual scripts and always use the worker after frame 180")
modes.add_argument("--value-check", action="store_true", help="use the worker after frame 180 without sampling scripts")
modes.add_argument("--profile-phases", action="store_true", help="break down time between Step and rendering")
modes.add_argument("--overlap-draw-list", action="store_true", help="compare overlapping draw-list preparation with audio maintenance")
modes.add_argument("--sort-draw-list", action="store_true", help="compare cached-key draw sorting with the original sort")
modes.add_argument("--pcm-town", action="store_true", help="try a predecoded copy of the original market audio loop")
modes.add_argument("--vm-values", action="store_true", help="try smaller frame setup and direct variable reads")
modes.add_argument("--packed-literals", action="store_true", help="try loading multi-instruction literal operations as one operation")
modes.add_argument("--overlap-audio", action="store_true", help="compare an audio worker with main-thread draw list preparation")
modes.add_argument("--mask-profile", action="store_true", help="time and verify a vector path for dynamic sprite collision masks")
modes.add_argument("--playable", action="store_true", help="package threaded presentation for an ordinary play session")
modes.add_argument("--packed-math", action="store_true", help="run pure scalar math directly on the VM's value stack")
modes.add_argument("--audio-affinity", action="store_true", help="compare audio-worker CPU scheduling and explicit separate cores")
modes.add_argument("--uniform-trial", action="store_true", help="skip already-current renderer uniforms across program switches")
modes.add_argument("--respawn-profile", action="store_true", help="profile the exact frames where the tutorial room rebuilds")
modes.add_argument("--compact-rvalues", action="store_true", help="package the separately built compact RValue runtime")
modes.add_argument("--sprite-cache", action="store_true", help="reuse the exact decoded pixels when a room reloads")
modes.add_argument("--deferred-audio", action="store_true", help="overlap audio and rendering with guarded audio access")
modes.add_argument("--packed-vars", action="store_true", help="bind regular variable slots before the interpreter runs")
modes.add_argument("--combined-perf", action="store_true", help="test fewer scalar dispatches and overlap audio")
modes.add_argument("--axis-draw", action="store_true", help="compare direct unrotated sprite geometry with the original")
modes.add_argument("--early-draw-list", action="store_true", help="prepare the draw list while the previous frame is still submitting")
modes.add_argument("--instance-properties", action="store_true", help="bind scalar built-in instance reads at load time")
modes.add_argument("--playable-combined", action="store_true", help="package the measured VM, display, audio and sprite-cache paths")
modes.add_argument("--playable-collision-order", action="store_true", help="package the loading screen and sorted collision target cache")
modes.add_argument("--playable-runtime", action="store_true", help="package grouped collision checks and direct bytecode dispatch")
modes.add_argument("--verify-runtime", action="store_true", help="verify active distortion and room-change times on the isolated title")
modes.add_argument("--playable-room-runtime", action="store_true", help="package the verified runtime with room-change log buffering")
modes.add_argument("--profile-runtime", action="store_true", help="sample script costs with the current playable runtime paths")
modes.add_argument("--profile-builtins", action="store_true", help="split sampled script costs into VM and built-in work")
modes.add_argument("--profile-integrated", action="store_true", help="profile the current collision and script paths together")
modes.add_argument("--profile-current-runtime", action="store_true", help="sample the measured runtime with the vent and depth improvements")
modes.add_argument("--profile-collision-phase", action="store_true", help="sample collision dispatch stages on the current runtime")
modes.add_argument("--mask-overlap", action="store_true", help="compare split-axis precise collision mask scans")
modes.add_argument("--verify-mask-overlap", action="store_true", help="compare physical captures with the new mask scan and distortion")
modes.add_argument("--room-updates", action="store_true", help="measure the direct scenery and shared enemy shadow paths")
modes.add_argument("--region-cases", action="store_true", help="measure shared empty-region checks in the case dispatcher")
modes.add_argument("--stack-register", action="store_true", help="measure keeping the operand-stack position local in the VM")
modes.add_argument("--grid-mask-overlap", action="store_true", help="compare word scans of grid-aligned collision masks")
modes.add_argument("--resting-scenery", action="store_true", help="compare direct resting scenery updates with the VM")
modes.add_argument("--active-cases", action="store_true", help="compare the direct paths before and after the original active case event")
modes.add_argument("--leaf-events", action="store_true", help="compare direct calls for inactive enemies and standard scenery draws")
modes.add_argument("--array-pool", action="store_true", help="compare recycling freed single-row legacy arrays")
modes.add_argument("--runtime-refinements", action="store_true", help="verify the combined measured runtime refinements")
modes.add_argument("--wide-collision", action="store_true", help="alternate grouped collision checks with wider bounds groups")
modes.add_argument("--resume-collision", action="store_true", help="compare refreshing grouped bounds after collision callbacks")
modes.add_argument("--borrowed-queries", action="store_true", help="compare direct instance lists with copied query snapshots")
modes.add_argument("--placement-filters", action="store_true", help="compare cached object matches and early misses in placement probes")
modes.add_argument("--grid-refresh", action="store_true", help="compare in-place grid refresh with removing and reinserting unchanged footprints")
modes.add_argument("--inline-bbox", action="store_true", help="compare split collision bounds cache hits and recomputation")
modes.add_argument("--collision-epoch", action="store_true", help="compare retaining collision bounds after proven non-mutating callbacks")
modes.add_argument("--playable-broadphase", action="store_true", help="package verified placement, bounds, and script argument paths")
modes.add_argument("--verify-broadphase", action="store_true", help="compare active distortion with the integrated broadphase runtime")
modes.add_argument("--playable-indexed-runtime", action="store_true", help="package measured placement and script indexing")
modes.add_argument("--verify-indexed-runtime", action="store_true", help="compare active distortion with measured placement and script indexing")
modes.add_argument("--axis-broadphase", action="store_true", help="compare a finer axis index on the integrated collision runtime")
modes.add_argument("--quantized-broadphase", action="store_true", help="compare eight-lane conservative collision culling")
modes.add_argument("--collision-cells", action="store_true", help="compare a two-dimensional collision grid")
modes.add_argument("--borrow-collision-targets", action="store_true", help="compare borrowing the dispatcher's frozen large target lists")
modes.add_argument("--script-call-buffers", action="store_true", help="compare reusing private scalar script argument buffers")
modes.add_argument("--frame-deadlines", action="store_true", help="compare bounded deadline-based pacing against relative delays")
modes.add_argument("--scalar-pairs", action="store_true", help="compare fusing adjacent scalar reads with numeric arithmetic or a branch")
modes.add_argument("--fixed-array-reads", action="store_true", help="bind constant array reads and compare direct viewport access")
modes.add_argument("--operand-dispatch", action="store_true", help="compare decoding operand widths in the threaded VM handlers")
modes.add_argument("--switch-chains", action="store_true", help="compare one numeric dispatch for repeated switch checks")
modes.add_argument("--stable-cases", action="store_true", help="compare direct returns for unchanged case checks")
modes.add_argument("--placement-cells", action="store_true", help="compare cached ordered placement candidates by cell and object")
modes.add_argument("--point-queries", action="store_true", help="compare candidate filtering for position_meeting")
modes.add_argument("--line-envelopes", action="store_true", help="compare early bounds checks for line collisions")
modes.add_argument("--scenery-batches", action="store_true", help="compare batching already skipped scenery updates")
modes.add_argument("--combined-simulation", action="store_true", help="measure the combined collision and script gains")
modes.add_argument("--ascii-parser", action="store_true", help="compare exact ASCII token parsing during room loads")
modes.add_argument("--pure-guards", action="store_true", help="compare direct branches for pause and viewport tests")
modes.add_argument("--retained-sprite-program", action="store_true", help="compare retaining the simple sprite shader between batches")
modes.add_argument("--flame-vent-probes", action="store_true", help="compare eliminating redundant Flame Vent collision probes")
modes.add_argument("--depth-helpers", action="store_true", help="compare native numeric depth updates on the measured runtime")
modes.add_argument("--recessing-guards", action="store_true", help="compare the original scenery terminal tests with fused branches")
modes.add_argument("--movement-helpers", action="store_true", help="compare native movement arithmetic, distance and instance ancestry")
modes.add_argument("--zero-movement", action="store_true", help="compare the native path when movement rounds to zero")
modes.add_argument("--native-enemy", action="store_true", help="compare the compiled enemy Step to the threaded VM")
modes.add_argument("--compact-enemy", action="store_true", help="compare compiling the frequently executed enemy instructions")
modes.add_argument("--compiled-events", action="store_true", help="compare five compiled hot events on the combined simulation runtime")
modes.add_argument("--playable-measured-runtime", action="store_true", help="package the measured simulation, startup and pacing changes")
modes.add_argument("--playable-split-masks", action="store_true", help="package the verified split-axis collision scan")
modes.add_argument("--verify-measured-runtime", action="store_true", help="compare physical captures with the measured runtime")
modes.add_argument("--zero-alpha-quads", action="store_true", help="compare omitting transparent quads from mixed overlay batches")
modes.add_argument("--skip-transparent-batches", action="store_true", help="compare skipping wholly transparent batches")
modes.add_argument("--sparse-composite", action="store_true", help="compare clipping the premultiplied overlay to covered regions")
modes.add_argument("--sparse-geometry", action="store_true", help="compare drawing covered overlay rectangles in one batch")
modes.add_argument("--trace-sparse-geometry", action="store_true", help="trace overlay eligibility on the Vita")
modes.add_argument("--sparse-transparent-coverage", action="store_true", help="exclude certified transparent quads from overlay coverage")
modes.add_argument("--shared-collision-grid", action="store_true", help="compare collision dispatch using the existing room grid")
modes.add_argument("--copy-blocks", action="store_true", help="compare predecoded instance-field copy blocks")
modes.add_argument("--idle-spawners", action="store_true", help="compare bypassing finished spawner steps with the same state writes")
modes.add_argument("--fast-text", action="store_true", help="compare borrowed string inputs and exact faster UTF-8 scans")
modes.add_argument("--transparent-composite", action="store_true", help="compare the same full composite with early zero-alpha discard")
modes.add_argument("--collision-query", action="store_true", help="compare reused collision query state with the original")
modes.add_argument("--combat-queries", action="store_true", help="compare early line rejection and measure dash and combat circle probes")
modes.add_argument("--collision-order", action="store_true", help="compare reused sorted collision target snapshots")
modes.add_argument("--collision-blocks", action="store_true", help="compare skipping blocks of spatially distant collision pairs")
modes.add_argument("--collision-grid", action="store_true", help="compare grouped and axis-indexed collision candidates")
modes.add_argument("--threaded-vm", action="store_true", help="compare direct bytecode dispatch with the equivalent switch interpreter")
modes.add_argument("--step-log-buffer", action="store_true", help="compare per-message and per-step Vita log writes")
modes.add_argument("--script-arguments", action="store_true", help="compare direct script argument reads and local assignments")
refinement_trials = parser.add_mutually_exclusive_group()
refinement_trials.add_argument("--submission-rings", action="store_true",
                               help="add the physical GXM ring-size comparison to the runtime verifier")
refinement_trials.add_argument("--bounded-round", action="store_true",
                               help="add the register-only coordinate rounding comparison")
refinement_trials.add_argument("--draw-order-repair", action="store_true",
                               help="repair cached depth order on the recorded routes")
refinement_trials.add_argument("--sprite-worker", action="store_true",
                               help="submit large sprite batches on the graphics worker")
refinement_trials.add_argument("--consolidated-runtime", action="store_true",
                               help="combine the measured leaf event and legacy array paths")
parser.add_argument("--route-profile", action="store_true", help="sample scripts during the East Lab movement replay")
parser.add_argument("--combat-route", action="store_true", help="test the graphics worker during a dash and sword replay")
consolidated_trials = parser.add_mutually_exclusive_group()
consolidated_trials.add_argument("--frame-hitch-profile", action="store_true", help="attribute the longest Vita frame in each sample")
consolidated_trials.add_argument("--scenery-draw-lists", action="store_true", help="compare native scenery draw-list construction")
consolidated_trials.add_argument("--audio-start-profile", action="store_true", help="time streamed audio startup on the Town route")
consolidated_trials.add_argument("--graphics-commands", action="store_true", help="submit groups of VitaGL commands on the graphics worker")
consolidated_trials.add_argument("--lazy-audio-length", action="store_true", help="defer the duration read on automatically streamed loops")
consolidated_trials.add_argument("--direct-instance-ids", action="store_true", help="index exact instance IDs")
consolidated_trials.add_argument("--combined-performance", action="store_true", help="combine the physically verified performance changes")
consolidated_trials.add_argument("--shared-instance-fields", action="store_true", help="compare compact scenery fields on the combined runtime")
consolidated_trials.add_argument("--memory-ambient", action="store_true", help="open short positional Vorbis loops from memory")
consolidated_trials.add_argument("--reduced-vm-work", action="store_true", help="compare reduced VM snapshot, conversion and scenery writes")
consolidated_trials.add_argument("--prefetch-ambience", action="store_true", help="prepare nearby ambience decoders off the game thread")
consolidated_trials.add_argument("--frame-headroom", action="store_true", help="benchmark room throughput with a 60 FPS pacing limit")
consolidated_trials.add_argument("--scenery-cursor", action="store_true", help="compare batched iteration in SCREEN's scenery loop")
consolidated_trials.add_argument("--simulation-profile", action="store_true", help="sample the latest runtime on the East Lab movement route")
consolidated_trials.add_argument("--eventless-case", action="store_true", help="compare direct linked cases with no user callback")
consolidated_trials.add_argument("--native-scenery-body", action="store_true", help="compare the lowered scenery update and append loop")
consolidated_trials.add_argument("--compact-vm-code", action="store_true", help="compare a smaller compiler layout of the threaded VM")
consolidated_trials.add_argument("--cpu-phase-profile", action="store_true", help="sample CPU thread scheduling on the East Lab replay")
consolidated_trials.add_argument("--scene-queries", action="store_true", help="compare specialized sprite geometry and movement queries")
consolidated_trials.add_argument("--step-spike-profile", action="store_true", help="trace scripts at repeatable East Lab Step spikes")
consolidated_trials.add_argument("--integrated-optimizations", action="store_true", help="compare verified native Step optimizations together")
consolidated_trials.add_argument("--sound-effect-cache", action="store_true", help="reuse decoded PCM for short positional effects")
consolidated_trials.add_argument("--grid-mask-memo", action="store_true", help="reuse precise overlap for immutable mask windows")
parser.add_argument("--graphics-framebuffer", action="store_true", help="read the recorded draw framebuffer without draining")
parser.add_argument("--graphics-render-tail", action="store_true", help="submit the final graphics commands with display swap")
parser.add_argument("--buffered-frame-reports", action="store_true", help="batch periodic timing lines per frame")
parser.add_argument("--survey-profile", action="store_true", help="sample scripts in the integrated survey build")
parser.add_argument("--archive-audio-reads", action="store_true", help="compare offset reads for embedded sound")
parser.add_argument("--quiet-shadow-log", action="store_true", help="suppress repeated shadow-search diagnostics")
parser.add_argument("--grouped-shadow", action="store_true", help="skip empty enemy shadow platform groups")
parser.add_argument("--ordered-line-candidates", action="store_true", help="compare a faster ordered line collision query")
parser.add_argument("--line-chunks", action="store_true", help="compare cached groups of terrain bounds")
parser.add_argument("--persistent-line-chunks", action="store_true", help="test persistent terrain bounds with the verified stack")
parser.add_argument("--native-line-script", action="store_true", help="test direct dispatch of the bound line query")
parser.add_argument("--line-script-arity", action="store_true", help="test the five- and seven-argument line call paths")
parser.add_argument("--palette-tail", action="store_true", help="test direct submission of the existing palette writes")
parser.add_argument("--optimized-profile", action="store_true", help="separate script and engine costs after the Big Bog optimizations")
parser.add_argument("--motion-repeat", action="store_true", help="measure fused movement repeats")
parser.add_argument("--scenery-skip", action="store_true", help="measure early skips for scenery that needs no update")
parser.add_argument("--scenery-guard", action="store_true", help="compare reuse of the SCREEN culling decision")
parser.add_argument("--hologram-arrays", action="store_true", help="compare lazy hologram offset arrays")
parser.add_argument("--hologram-args", action="store_true", help="compare native hologram argument setup")
parser.add_argument("--idle-scenery", action="store_true", help="compare skipping unchanged scenery scripts")
parser.add_argument("--display-clear", action="store_true", help="compare deferring the display clear until first use")
parser.add_argument("--walking-material", action="store_true", help="compare native walking material probes")
parser.add_argument("--placement-ids", action="store_true", help="measure direct placement queries for an explicit instance ID")
parser.add_argument("--floor-pattern", action="store_true", help="measure direct floor pattern rendering")
parser.add_argument("--case-checks", action="store_true", help="measure active and unchanged broken-link case paths")
parser.add_argument("--phase-mine-trigger", action="store_true", help="measure the combined mine trigger query")
parser.add_argument("--dormant-mine", action="store_true", help="measure native dormant mine updates")
parser.add_argument("--mine-entry", action="store_true", help="measure ready mine updates before VM frame allocation")
parser.add_argument("--mantle-sweep", action="store_true", help="measure native clear movement paths")
parser.add_argument("--vault-stack", action="store_true", help="combine Vault changes and include the line mask trial")
parser.add_argument("--vault-mask-layers", action="store_true", help="test native overlap and generated mask reuse")
parser.add_argument("--vault-path-probe", action="store_true", help="measure native enemy path visibility checks")
parser.add_argument("--vault-profile", action="store_true", help="sample the verified Vault simulation stack")
parser.add_argument("--native-entry-profile", action="store_true", help="attribute native entry time to the called script")
parser.add_argument("--shape-candidate-cells", action="store_true", help="measure indexed circle and ellipse queries")
parser.add_argument("--mask-tiles", action="store_true", help="measure the terrain mask tile index")
parser.add_argument("--mask-line-tiles", action="store_true", help="measure empty line rejection with the terrain mask index")
parser.add_argument("--verified-stack", action="store_true", help="combine the verified logging, collision and audio paths")
parser.add_argument("--scenery-stream", action="store_true", help="compare the fused settled scenery pass")
parser.add_argument("--terrain-sweep", action="store_true", help="compare stationary and ordinary stopped movement")
parser.add_argument("--enemy-target", action="store_true", help="compare direct enemy targeting")
parser.add_argument("--surface-sprites", action="store_true", help="compare faster dynamic sprites made from surfaces")
parser.add_argument("--vertex-programs", action="store_true", help="reuse exact patched vertex layouts")
parser.add_argument("--verified-runtime", action="store_true", help="compare the verified movement, scenery, mine and clear paths together")
parser.add_argument("--instance-slot-reads", action="store_true", help="cache exact slots at packed instance field reads")
parser.add_argument("--room-image-cache", action="store_true", help="cache the exact decoded room-sized sprites")
parser.add_argument("--collision-target-views", action="store_true", help="borrow collision targets and reuse their ordered membership")
parser.add_argument("--persistent-terrain-bounds", action="store_true", help="reuse collision dispatch bounds for unchanged terrain")
parser.add_argument("--atlas-batches", action="store_true", help="join successive premultiply batches on two static atlas textures")
parser.add_argument("--atlas-upload-cache", action="store_true", help="reuse the original values in the two-texture shader program")
profile_modes = parser.add_mutually_exclusive_group()
profile_modes.add_argument("--vm-training", action="store_true", help="collect VM compiler feedback on Vita")
profile_modes.add_argument("--vm-feedback", type=Path, metavar="PROFILE_DIR", help="compile the VM with collected Vita feedback")
engine_profile_modes = parser.add_mutually_exclusive_group()
engine_profile_modes.add_argument("--engine-training", action="store_true", help="collect compiler feedback for Runner and built-ins")
engine_profile_modes.add_argument("--engine-feedback", type=Path, metavar="PROFILE_DIR", help="compile Runner and built-ins with Vita feedback")
engine_profile_modes.add_argument("--retained-draw-order", action="store_true", help="reuse unchanged legacy draw sort keys")
engine_profile_modes.add_argument("--single-draw-responder", action="store_true", help="dispatch single-responder legacy draw events directly")
engine_profile_modes.add_argument("--classified-vm-entry", action="store_true", help="skip native entry checks for unbound scripts")
engine_profile_modes.add_argument("--render-order-stack", action="store_true", help="combine retained draw order and single-responder dispatch")
engine_profile_modes.add_argument("--line-terrain-index", action="store_true", help="index terrain for ordered line queries")
engine_profile_modes.add_argument("--placement-probe-masks", action="store_true", help="reuse empty-cell proofs for grouped collision probes")
engine_profile_modes.add_argument("--frame-depth-fold", action="store_true", help="combine overlapping application-frame depth clears")
engine_profile_modes.add_argument("--pinned-collision-targets", action="store_true", help="retain ordered collision lists through callbacks on the combined draw runtime")
engine_profile_modes.add_argument("--full-viewport-mask", action="store_true", help="skip the redundant first draw for full-target scissor masks")
engine_profile_modes.add_argument("--render-placement-stack", action="store_true", help="combine the verified draw order and placement probe improvements")
engine_profile_modes.add_argument("--draw-state-packets", action="store_true", help="record more adjacent draw commands with known state reads")
engine_profile_modes.add_argument("--expanded-draw-packets", action="store_true", help="submit smaller draw packets and profile required synchronous calls")
engine_profile_modes.add_argument("--matrix-array-packets", action="store_true", help="keep ordered shader matrix-array uploads in the graphics queue")
engine_profile_modes.add_argument("--graphics-stream", action="store_true", help="queue multiple ordered graphics packets between GL fences")
engine_profile_modes.add_argument("--stream-placement", action="store_true", help="combine streaming graphics and grouped placement proofs")
engine_profile_modes.add_argument("--arm-state-vm", action="store_true", help="compile the trained VM's hot paths in ARM instruction state")
engine_profile_modes.add_argument("--stream-enemy-target", action="store_true", help="train or use the streaming runtime with native enemy target resolution")
engine_profile_modes.add_argument("--focused-step-profile", action="store_true", help="sample nested work in the busiest measured Step event per room")
engine_profile_modes.add_argument("--obstacle-line-buckets", action="store_true", help="index ordered pathfinding and X-ray obstacle groups")
engine_profile_modes.add_argument("--stream-line-mask-tiles", action="store_true", help="reuse the precise line mask tile proof with the streaming obstacle index")
engine_profile_modes.add_argument("--stream-slot-obstacles", action="store_true", help="combine cached instance reads with the ordered obstacle index")
engine_profile_modes.add_argument("--stream-slot-reads", action="store_true", help="train or use cached instance reads on the ARM streaming runtime")
engine_profile_modes.add_argument("--stream-slot-writes", action="store_true", help="train or use cached instance reads and writes on the ARM streaming runtime")
engine_profile_modes.add_argument("--stream-matrix-cache", action="store_true", help="reuse exact game shader matrices on the ARM streaming runtime")
engine_profile_modes.add_argument("--plain-shader-trial", action="store_true", help="build an isolated switch for replacing game shaders with standard sprite rendering")
engine_profile_modes.add_argument("--stream-scenery-snapshot", action="store_true", help="retain ordered scenery snapshots on the ARM streaming runtime")
engine_profile_modes.add_argument("--resolved-step", action="store_true", help="reuse resolved Step handlers across frozen object buckets")
engine_profile_modes.add_argument("--initial-scene-depth", action="store_true", help="reuse the initialized depth of fresh offscreen scenes")
engine_profile_modes.add_argument("--mine-probe-mask", action="store_true", help="limit empty-cell proofs to the high-volume mine queries")
options = parser.parse_args()
options.obstacle_line_buckets |= options.stream_line_mask_tiles or options.stream_slot_obstacles
options.mask_line_tiles |= options.stream_line_mask_tiles
options.stream_slot_reads |= any((options.stream_slot_obstacles, options.stream_slot_writes,
                                 options.stream_matrix_cache, options.plain_shader_trial))
options.stream_placement |= any((options.arm_state_vm, options.stream_enemy_target, options.focused_step_profile,
                                 options.obstacle_line_buckets, options.stream_slot_reads, options.stream_scenery_snapshot))
options.enemy_target |= options.stream_enemy_target
options.instance_slot_reads |= options.stream_slot_reads
options.graphics_stream |= options.stream_placement
options.render_placement_stack |= options.stream_placement
options.matrix_array_packets |= options.graphics_stream
options.expanded_draw_packets |= options.matrix_array_packets
options.draw_state_packets |= options.expanded_draw_packets
if any((options.render_order_stack, options.pinned_collision_targets, options.full_viewport_mask,
        options.render_placement_stack, options.draw_state_packets, options.resolved_step,
        options.initial_scene_depth, options.mine_probe_mask)):
    options.retained_draw_order = options.single_draw_responder = True
if options.render_placement_stack or options.mine_probe_mask:
    options.placement_probe_masks = True
if options.draw_state_packets:
    options.graphics_commands = True
if options.atlas_upload_cache and not options.atlas_batches:
    parser.error("--atlas-upload-cache requires --atlas-batches")
if (options.vm_training or options.vm_feedback) and not options.verified_runtime:
    parser.error("VM compiler training and feedback require --verified-runtime")
if options.focused_step_profile and (not options.verified_runtime or not options.persistent_terrain_bounds or
                                     options.vm_training or options.vm_feedback or options.playable):
    parser.error("--focused-step-profile requires the verified terrain runtime, without compiler feedback or playable mode")
if options.vm_feedback:
    options.vm_feedback = options.vm_feedback.resolve()
    if not (options.vm_feedback / "vm.gcda").is_file():
        parser.error("--vm-feedback requires a directory containing vm.gcda")
if options.stream_slot_obstacles and not (options.vm_feedback or options.vm_training):
    parser.error("--stream-slot-obstacles requires the 02.74 instance-slot VM feedback")
if options.stream_matrix_cache and not (options.vm_feedback or options.vm_training):
    parser.error("--stream-matrix-cache requires the 02.74 instance-slot VM feedback")
if options.plain_shader_trial and (not options.vm_feedback or options.playable):
    parser.error("--plain-shader-trial requires the 02.74 VM feedback and an isolated test title")
new_vm_training = any((options.classified_vm_entry, options.stream_enemy_target, options.stream_slot_reads,
                       options.stream_scenery_snapshot))
feedback_terrain = bool(options.persistent_terrain_bounds and
                        (options.focused_step_profile or options.vm_feedback or (options.vm_training and new_vm_training)))
if (options.engine_training or options.engine_feedback) and not feedback_terrain:
    parser.error("engine feedback requires --verified-runtime --vm-feedback and --persistent-terrain-bounds")
if options.retained_draw_order and not feedback_terrain:
    parser.error("--retained-draw-order requires --verified-runtime --vm-feedback and --persistent-terrain-bounds")
if options.single_draw_responder and not feedback_terrain:
    parser.error("--single-draw-responder requires --verified-runtime --vm-feedback and --persistent-terrain-bounds")
if options.classified_vm_entry and not feedback_terrain:
    parser.error("--classified-vm-entry requires --verified-runtime, terrain bounds and VM training or feedback")
if options.line_terrain_index and not feedback_terrain:
    parser.error("--line-terrain-index requires --verified-runtime --vm-feedback and --persistent-terrain-bounds")
if options.placement_probe_masks and not feedback_terrain:
    parser.error("--placement-probe-masks requires --verified-runtime --vm-feedback and --persistent-terrain-bounds")
if options.frame_depth_fold and not feedback_terrain:
    parser.error("--frame-depth-fold requires --verified-runtime --vm-feedback and --persistent-terrain-bounds")
if options.pinned_collision_targets and not feedback_terrain:
    parser.error("--pinned-collision-targets requires --verified-runtime --vm-feedback and --persistent-terrain-bounds")
if options.full_viewport_mask and not feedback_terrain:
    parser.error("--full-viewport-mask requires --verified-runtime --vm-feedback and --persistent-terrain-bounds")
if options.draw_state_packets and not feedback_terrain:
    parser.error("--draw-state-packets requires --verified-runtime --vm-feedback and --persistent-terrain-bounds")
if options.resolved_step and not feedback_terrain:
    parser.error("--resolved-step requires --verified-runtime --vm-feedback and --persistent-terrain-bounds")
if options.initial_scene_depth and not feedback_terrain:
    parser.error("--initial-scene-depth requires --verified-runtime --vm-feedback and --persistent-terrain-bounds")
if options.engine_feedback:
    options.engine_feedback = options.engine_feedback.resolve()
    if not all((options.engine_feedback / f"{unit}.gcda").is_file() for unit in ENGINE_PROFILE_UNITS):
        parser.error("--engine-feedback requires the profile files for Runner and built-ins")
integrated_trials = (options.survey_profile, options.archive_audio_reads, options.quiet_shadow_log,
                     options.grouped_shadow, options.ordered_line_candidates, options.line_chunks,
                     options.verified_stack, options.persistent_line_chunks, options.native_line_script,
                     options.palette_tail, options.line_script_arity, options.optimized_profile, options.motion_repeat,
                     options.scenery_skip, options.placement_ids, options.floor_pattern, options.case_checks,
                     options.phase_mine_trigger, options.dormant_mine, options.mantle_sweep, options.vault_stack,
                     options.vault_mask_layers, options.vault_path_probe, options.vault_profile,
                     options.shape_candidate_cells, options.native_entry_profile, options.mask_tiles,
                     options.mask_line_tiles and not options.stream_line_mask_tiles,
                     options.mine_entry, options.scenery_guard, options.hologram_arrays,
                     options.hologram_args, options.idle_scenery, options.display_clear, options.walking_material,
                     options.scenery_stream, options.terrain_sweep,
                     options.enemy_target and not options.stream_enemy_target, options.surface_sprites,
                     options.vertex_programs, options.verified_runtime,
                     options.instance_slot_reads and not options.stream_slot_reads,
                     options.room_image_cache, options.collision_target_views, options.persistent_terrain_bounds,
                     options.atlas_batches)
if any(integrated_trials) and not (
        options.runtime_refinements and options.consolidated_runtime and options.integrated_optimizations):
    parser.error("survey, archive read and shadow log trials require the integrated verification build")
if sum(integrated_trials) > (2 if feedback_terrain else 1):
    parser.error("use separate integrated verification trials")
if options.atlas_batches:
    options.persistent_terrain_bounds = True
if options.persistent_terrain_bounds:
    options.collision_target_views = True
if options.verified_runtime:
    options.mine_entry = options.scenery_guard = options.idle_scenery = True
    options.display_clear = options.terrain_sweep = True
if options.scenery_stream:
    options.idle_scenery = options.scenery_guard = True
if options.optimized_profile:
    options.palette_tail = True
    options.line_script_arity = True
if options.native_entry_profile:
    options.vault_profile = options.vault_mask_layers = True
if options.hologram_args:
    options.hologram_arrays = True
if any((options.mask_line_tiles, options.mine_entry, options.scenery_guard, options.hologram_arrays,
        options.idle_scenery, options.display_clear, options.walking_material, options.terrain_sweep,
        options.enemy_target, options.surface_sprites, options.vertex_programs, options.instance_slot_reads,
        options.room_image_cache, options.collision_target_views)):
    options.mask_tiles = True
if options.shape_candidate_cells or options.mask_tiles:
    options.vault_mask_layers = True
if options.vault_mask_layers:
    options.vault_stack = options.grid_mask_memo = True
if options.vault_path_probe or options.vault_profile:
    options.vault_stack = True
if options.vault_stack:
    options.dormant_mine = options.mantle_sweep = True
if options.dormant_mine or options.mantle_sweep:
    options.phase_mine_trigger = True
if options.case_checks or options.phase_mine_trigger:
    options.floor_pattern = True
if options.placement_ids or options.floor_pattern:
    options.scenery_skip = True
if options.motion_repeat or options.scenery_skip:
    options.palette_tail = True
if options.palette_tail or options.line_script_arity:
    options.native_line_script = True
if options.native_line_script:
    options.persistent_line_chunks = True
if options.verified_stack or options.persistent_line_chunks:
    options.archive_audio_reads = True
    options.ordered_line_candidates = True
    options.grouped_shadow = True
    options.survey_profile = options.verified_stack or options.optimized_profile or options.vault_profile
if options.persistent_line_chunks:
    options.line_chunks = True
if options.line_chunks:
    options.ordered_line_candidates = True
if options.grouped_shadow or options.ordered_line_candidates:
    options.quiet_shadow_log = True
if options.grid_mask_memo and not (options.runtime_refinements and options.consolidated_runtime):
    parser.error("--grid-mask-memo requires --runtime-refinements and --consolidated-runtime")
if options.integrated_optimizations:
    options.eventless_case = True
    options.native_scenery_body = True
    options.scene_queries = True
if options.native_scenery_body:
    options.scenery_cursor = True
if options.scene_queries:
    options.movement_helpers = True
if (options.frame_headroom or options.scenery_cursor or options.simulation_profile
        or options.eventless_case or options.compact_vm_code or options.cpu_phase_profile or options.scene_queries
        or options.step_spike_profile or options.sound_effect_cache or options.grid_mask_memo):
    options.reduced_vm_work = True
if (options.simulation_profile or options.eventless_case or options.native_scenery_body
        or options.compact_vm_code or options.cpu_phase_profile or options.scene_queries or options.step_spike_profile
        or options.sound_effect_cache or options.grid_mask_memo):
    options.prefetch_ambience = True
if options.shared_instance_fields or options.memory_ambient or options.reduced_vm_work or options.prefetch_ambience:
    options.combined_performance = True
if options.combined_performance:
    options.scenery_draw_lists = True
    options.direct_instance_ids = True
    options.lazy_audio_length = True
    options.buffered_frame_reports = True
if (options.submission_rings or options.bounded_round or options.draw_order_repair or options.sprite_worker or
        options.consolidated_runtime) and not options.runtime_refinements:
    parser.error("refinement trials require --runtime-refinements")
if options.route_profile and not options.bounded_round:
    parser.error("--route-profile requires --bounded-round")
if options.combat_route and not options.sprite_worker:
    parser.error("--combat-route requires --sprite-worker")
if (options.frame_hitch_profile or options.scenery_draw_lists or options.audio_start_profile or
        options.graphics_commands or options.lazy_audio_length or options.direct_instance_ids) and not options.consolidated_runtime:
    parser.error("consolidated trials require --consolidated-runtime")
if options.graphics_framebuffer and not options.graphics_commands:
    parser.error("--graphics-framebuffer requires --graphics-commands")
if options.graphics_render_tail and not options.graphics_framebuffer:
    parser.error("--graphics-render-tail requires --graphics-framebuffer")
if options.buffered_frame_reports and not options.lazy_audio_length:
    parser.error("--buffered-frame-reports requires --lazy-audio-length")
profile_builtins = options.profile_builtins or options.profile_integrated or options.profile_current_runtime
profile_runtime = options.profile_runtime or profile_builtins
vector_broadphase = options.axis_broadphase or options.quantized_broadphase or options.collision_cells
mask_overlap = options.mask_overlap or options.verify_mask_overlap
always_split_masks = (options.room_updates or options.region_cases or options.playable_split_masks or
                      options.stack_register or options.grid_mask_overlap or options.resting_scenery or
                      options.active_cases or options.leaf_events or options.array_pool or options.runtime_refinements)
current_depth_helpers = (options.depth_helpers or options.profile_current_runtime or options.profile_collision_phase or
                         mask_overlap or always_split_masks)
measured_runtime = (options.playable_measured_runtime or options.verify_measured_runtime or
                    current_depth_helpers or options.recessing_guards)
flame_vent_reduction = options.flame_vent_probes or current_depth_helpers or options.recessing_guards
sparse_geometry = options.sparse_geometry or options.trace_sparse_geometry or options.sparse_transparent_coverage
sparse_compositing = options.sparse_composite or sparse_geometry
sparse_rendering = options.zero_alpha_quads or options.skip_transparent_batches or sparse_compositing
combined_runtime = (options.combined_simulation or options.compiled_events or measured_runtime or
                    sparse_rendering or options.shared_collision_grid)
indexed_runtime = (options.playable_indexed_runtime or options.verify_indexed_runtime or
                   combined_runtime or options.retained_sprite_program or options.flame_vent_probes or
                   options.movement_helpers or options.zero_movement or options.native_enemy or options.compact_enemy)
integrated_runtime = (indexed_runtime or options.playable_broadphase or options.verify_broadphase or options.axis_broadphase or
                      options.borrow_collision_targets or options.script_call_buffers or options.frame_deadlines or
                      options.scalar_pairs or options.quantized_broadphase or options.fixed_array_reads or
                      options.operand_dispatch or options.switch_chains or options.profile_integrated or options.stable_cases or
                      options.placement_cells or options.copy_blocks or options.idle_spawners or options.point_queries or
                      options.line_envelopes or options.scenery_batches or options.collision_cells or options.ascii_parser or
                      options.pure_guards)
verify_runtime = (options.verify_runtime or options.verify_broadphase or
                  options.verify_indexed_runtime or options.retained_sprite_program or options.verify_measured_runtime or
                  options.verify_mask_overlap or options.runtime_refinements)
bbox_runtime = options.inline_bbox or integrated_runtime
isolated_runtime = (verify_runtime or profile_runtime or options.wide_collision or
                    options.resume_collision or options.borrowed_queries or options.placement_filters or
                    options.grid_refresh or options.inline_bbox or options.collision_epoch or vector_broadphase or
                    options.borrow_collision_targets or options.script_call_buffers or options.frame_deadlines or
                    options.scalar_pairs or options.fixed_array_reads or options.operand_dispatch or
                    options.switch_chains or options.stable_cases or options.placement_cells or options.copy_blocks or
                    options.idle_spawners or options.point_queries or options.line_envelopes or options.scenery_batches or
                    options.combined_simulation or options.compiled_events or sparse_rendering or options.ascii_parser or
                    options.shared_collision_grid or options.depth_helpers or options.recessing_guards or
                    options.pure_guards or options.flame_vent_probes or options.profile_collision_phase or options.mask_overlap or
                    options.room_updates or options.region_cases or options.stack_register or options.grid_mask_overlap or
                    options.resting_scenery or options.active_cases or options.leaf_events or options.array_pool or
                    options.movement_helpers or options.zero_movement or options.native_enemy or options.compact_enemy)
buffer_room_logs = (indexed_runtime or options.playable_room_runtime or options.playable_broadphase or
                    (isolated_runtime and not verify_runtime))
uses_sprite_cache = (options.early_draw_list or options.instance_properties or options.playable_combined or
                     options.playable_collision_order or options.playable_runtime or options.playable_room_runtime or
                     integrated_runtime or isolated_runtime or
                     options.transparent_composite or options.collision_query or options.combat_queries or
                     options.collision_order or options.collision_blocks or options.collision_grid or
                     options.script_arguments or options.threaded_vm or options.step_log_buffer or options.fast_text)
if (options.playable_combined or options.playable_collision_order or options.playable_runtime or
        options.playable_room_runtime or integrated_runtime or isolated_runtime):
    options.playable = True
if uses_sprite_cache:
    options.combined_perf = True
if options.combined_perf:
    options.deferred_audio = True
proof = Path(__file__).resolve().parent
root = proof.parent
build = root / "build" / "engine-vita"  # with the sprite cache: build/engine-vita-sprite-cache (scripts/build.py configures it)
if uses_sprite_cache:
    build = build.with_name(build.name + "-sprite-cache")
source = root / "engine/src"
directory, version = "arm-fidelity", "00.51"
if options.profile_vm:
    directory, version = "arm-vm-profile", "00.52"
elif options.value_check:
    directory, version = "arm-value-check", "00.53"
elif options.profile_phases:
    directory, version = "arm-phase-profile", "00.54"
elif options.overlap_draw_list:
    directory, version = "arm-draw-list", "00.55"
elif options.sort_draw_list:
    directory, version = "arm-draw-sort", "00.56"
elif options.pcm_town:
    directory, version = "arm-pcm-town", "00.57"
elif options.vm_values:
    directory, version = "arm-vm-values", "00.58"
elif options.packed_literals:
    directory, version = "arm-packed-literals", "00.59"
elif options.overlap_audio:
    directory, version = "arm-audio-worker", "00.60"
elif options.mask_profile:
    directory, version = "arm-mask-profile", "00.61"
elif options.playable_combined:
    directory, version = "arm-playable-loading-vram", "00.83"
elif options.playable_collision_order:
    directory, version = "arm-playable-collision-order", "00.85"
elif options.playable_runtime:
    directory, version = "arm-playable-runtime", "00.89"
elif options.verify_runtime:
    directory, version = "arm-verify-runtime", "00.90"
elif options.collision_grid:
    directory, version = "arm-collision-grid", "00.91"
elif options.fast_text:
    directory, version = "arm-fast-text", "00.92"
elif options.playable_room_runtime:
    directory, version = "arm-playable-room-runtime", "00.93"
elif options.profile_runtime:
    directory, version = "arm-profile-runtime", "00.94"
elif options.wide_collision:
    directory, version = "arm-wide-collision", "00.95"
elif options.resume_collision:
    directory, version = "arm-resume-collision", "00.96"
elif options.profile_builtins:
    directory, version = "arm-profile-builtins", "00.97"
elif options.borrowed_queries:
    directory, version = "arm-borrowed-queries", "00.98"
elif options.placement_filters:
    directory, version = "arm-placement-filters", "00.99"
elif options.grid_refresh:
    directory, version = "arm-grid-refresh", "01.00"
elif options.inline_bbox:
    directory, version = "arm-inline-bbox", "01.01"
elif options.collision_epoch:
    directory, version = "arm-collision-epoch", "01.02"
elif options.playable_broadphase:
    directory, version = "arm-playable-broadphase", "01.03"
elif options.verify_broadphase:
    directory, version = "arm-verify-broadphase", "01.04"
elif options.axis_broadphase:
    directory, version = "arm-axis-broadphase", "01.05"
elif options.borrow_collision_targets:
    directory, version = "arm-borrow-collision-targets", "01.06"
elif options.script_call_buffers:
    directory, version = "arm-script-call-buffers", "01.07"
elif options.frame_deadlines:
    directory, version = "arm-frame-deadlines", "01.08"
elif options.scalar_pairs:
    directory, version = "arm-scalar-pairs", "01.09"
elif options.quantized_broadphase:
    directory, version = "arm-quantized-broadphase", "01.10"
elif options.fixed_array_reads:
    directory, version = "arm-fixed-array-reads", "01.11"
elif options.operand_dispatch:
    directory, version = "arm-operand-dispatch", "01.12"
elif options.switch_chains:
    directory, version = "arm-switch-chains", "01.13"
elif options.profile_integrated:
    directory, version = "arm-profile-integrated", "01.14"
elif options.profile_current_runtime:
    directory, version = "arm-profile-current-runtime", "01.46"
elif options.profile_collision_phase:
    directory, version = "arm-profile-collision-phase", "01.47"
elif options.mask_overlap:
    directory, version = "arm-mask-overlap", "01.48"
elif options.verify_mask_overlap:
    directory, version = "arm-verify-mask-overlap", "01.49"
elif options.room_updates:
    directory, version = "arm-room-updates", "01.50"
elif options.region_cases:
    directory, version = "arm-region-cases", "01.51"
elif options.playable_split_masks:
    directory, version = "arm-playable-split-masks", "01.52"
elif options.stack_register:
    directory, version = "arm-stack-register", "01.53"
elif options.grid_mask_overlap:
    directory, version = "arm-grid-mask-overlap", "01.54"
elif options.resting_scenery:
    directory, version = "arm-resting-scenery", "01.55"
elif options.active_cases:
    directory, version = "arm-active-cases", "01.56"
elif options.leaf_events:
    directory, version = "arm-leaf-events", "01.57"
elif options.array_pool:
    directory, version = "arm-array-pool", "01.58"
elif options.runtime_refinements:
    if options.engine_training:
        directory, version = "arm-engine-training", "02.47"
    elif options.engine_feedback:
        directory, version = "arm-engine-feedback", "02.48"
    elif options.render_order_stack:
        directory, version = "arm-render-order-stack", "02.53"
    elif options.line_terrain_index:
        directory, version = "arm-line-terrain-index", "02.54"
    elif options.arm_state_vm:
        directory, version = "arm-vm-arm-state", "02.68"
    elif options.stream_enemy_target:
        directory, version = (("arm-stream-enemy-training", "02.69") if options.vm_training else
                              ("arm-stream-enemy-target", "02.70"))
    elif options.focused_step_profile:
        directory, version = "arm-focused-step-profile", "02.71"
    elif options.stream_slot_obstacles:
        directory, version = os.environ.get("HLDC_DIR", "arm-hldc"), "02.78"
    elif options.stream_line_mask_tiles:
        directory, version = "arm-stream-line-mask-tiles", "02.77"
    elif options.obstacle_line_buckets:
        directory, version = "arm-obstacle-line-buckets", "02.72"
    elif options.plain_shader_trial:
        directory, version = "arm-plain-shader-trial", "02.82"
    elif options.stream_matrix_cache:
        directory, version = "arm-stream-matrix-cache", "02.81"
    elif options.stream_slot_writes:
        directory, version = (("arm-stream-slot-write-training", "02.79") if options.vm_training else
                              ("arm-stream-slot-writes", "02.80"))
    elif options.stream_slot_reads:
        directory, version = (("arm-shapes-training", "02.73") if options.vm_training else
                              ("arm-stream-slot-reads", "02.74"))
    elif options.stream_scenery_snapshot:
        directory, version = (("arm-stream-scenery-training", "02.75") if options.vm_training else
                              ("arm-stream-scenery-snapshot", "02.76"))
    elif options.stream_placement:
        directory, version = "arm-stream-placement", "02.67"
    elif options.render_placement_stack:
        directory, version = "arm-render-placement-stack", "02.59"
    elif options.mine_probe_mask:
        directory, version = "arm-mine-probe-mask", "02.63"
    elif options.placement_probe_masks:
        directory, version = "arm-placement-probe-masks", "02.55"
    elif options.frame_depth_fold:
        directory, version = "arm-frame-depth-fold", "02.56"
    elif options.pinned_collision_targets:
        directory, version = "arm-pinned-collision-targets", "02.57"
    elif options.full_viewport_mask:
        directory, version = "arm-full-viewport-mask", "02.58"
    elif options.graphics_stream:
        directory, version = "arm-graphics-stream", "02.66"
    elif options.matrix_array_packets:
        directory, version = "arm-matrix-array-packets", "02.65"
    elif options.expanded_draw_packets:
        directory, version = "arm-expanded-draw-packets", "02.64"
    elif options.draw_state_packets:
        directory, version = "arm-draw-state-packets", "02.60"
    elif options.resolved_step:
        directory, version = "arm-resolved-step", "02.61"
    elif options.initial_scene_depth:
        directory, version = "arm-initial-scene-depth", "02.62"
    elif options.retained_draw_order:
        directory, version = "arm-retained-draw-order", "02.49"
    elif options.single_draw_responder:
        directory, version = "arm-single-draw-responder", "02.50"
    elif options.classified_vm_entry:
        directory, version = (("arm-classified-vm-training", "02.51") if options.vm_training else
                              ("arm-classified-vm-feedback", "02.52"))
    elif feedback_terrain:
        directory, version = "arm-vm-feedback-terrain", "02.45"
    elif options.atlas_upload_cache:
        directory, version = "arm-atlas-upload-cache", "02.46"
    elif options.atlas_batches:
        directory, version = "arm-atlas-batches", "02.44"
    elif options.persistent_terrain_bounds:
        directory, version = "arm-persistent-terrain-bounds", "02.43"
    elif options.collision_target_views:
        directory, version = "arm-collision-target-views", "02.42"
    elif options.room_image_cache:
        directory, version = "arm-room-image-cache", "02.41"
    elif options.instance_slot_reads:
        directory, version = "arm-instance-slot-reads", "02.38"
    elif options.vm_training:
        directory, version = "arm-vm-training", "02.39"
    elif options.vm_feedback:
        directory, version = "arm-vm-feedback", "02.40"
    elif options.verified_runtime:
        directory, version = "arm-verified-runtime", "02.37"
    elif options.vertex_programs:
        directory, version = "arm-vertex-programs", "02.36"
    elif options.surface_sprites:
        directory, version = "arm-surface-sprites", "02.35"
    elif options.enemy_target:
        directory, version = "arm-enemy-target", "02.33"
    elif options.terrain_sweep:
        directory, version = "arm-terrain-sweep", "02.32"
    elif options.scenery_stream:
        directory, version = "arm-scenery-stream-recheck", "02.34"
    elif options.walking_material:
        directory, version = "arm-walking-material", "02.30"
    elif options.display_clear:
        directory, version = "arm-display-clear", "02.29"
    elif options.idle_scenery:
        directory, version = "arm-idle-scenery", "02.28"
    elif options.hologram_args:
        directory, version = "arm-hologram-args", "02.27"
    elif options.hologram_arrays:
        directory, version = "arm-hologram-arrays", "02.26"
    elif options.scenery_guard:
        directory, version = "arm-scenery-guard", "02.25"
    elif options.mine_entry:
        directory, version = "arm-mine-entry", "02.24"
    elif options.mask_line_tiles:
        directory, version = "arm-mask-line-tiles", "02.23"
    elif options.mask_tiles:
        directory, version = "arm-mask-tiles", "02.22"
    elif options.shape_candidate_cells:
        directory, version = "arm-shape-candidate-cells", "02.20"
    elif options.native_entry_profile:
        directory, version = "arm-native-entry-profile", "02.21"
    elif options.vault_profile:
        directory, version = "arm-vault-profile", "02.19"
    elif options.vault_path_probe:
        directory, version = "arm-vault-path-probe", "02.18"
    elif options.vault_mask_layers:
        directory, version = "arm-vault-mask-layers", "02.17"
    elif options.vault_stack:
        directory, version = "arm-vault-stack", "02.16"
    elif options.mantle_sweep:
        directory, version = "arm-mantle-sweep", "02.15"
    elif options.dormant_mine:
        directory, version = "arm-dormant-mine", "02.14"
    elif options.phase_mine_trigger:
        directory, version = "arm-phase-mine-trigger", "02.13"
    elif options.case_checks:
        directory, version = "arm-case-checks", "02.12"
    elif options.floor_pattern:
        directory, version = "arm-floor-pattern", "02.11"
    elif options.placement_ids:
        directory, version = "arm-placement-ids", "02.10"
    elif options.scenery_skip:
        directory, version = "arm-scenery-skip", "02.09"
    elif options.motion_repeat:
        directory, version = "arm-motion-repeat", "02.08"
    elif options.optimized_profile:
        directory, version = "arm-optimized-profile", "02.07"
    elif options.palette_tail:
        directory, version = "arm-palette-tail", "02.05"
    elif options.line_script_arity:
        directory, version = "arm-line-script-arity", "02.06"
    elif options.native_line_script:
        directory, version = "arm-native-line-script", "02.04"
    elif options.persistent_line_chunks:
        directory, version = "arm-persistent-line-chunks", "02.03"
    elif options.verified_stack:
        directory, version = "arm-verified-stack", "02.02"
    elif options.line_chunks:
        directory, version = "arm-line-chunks", "02.01"
    elif options.ordered_line_candidates:
        directory, version = "arm-ordered-line-candidates", "01.99"
    elif options.grouped_shadow:
        directory, version = "arm-grouped-shadow-bound", "02.00"
    elif options.quiet_shadow_log:
        directory, version = "arm-quiet-shadow-log", "01.97"
    elif options.archive_audio_reads:
        directory, version = "arm-archive-audio-reads", "01.96"
    elif options.survey_profile:
        directory, version = "arm-room-load-profile", "01.95"
    elif options.grid_mask_memo:
        directory, version = "arm-grid-mask-memo", "01.94"
    elif options.sound_effect_cache:
        directory, version = "arm-sound-effect-cache", "01.93"
    elif options.integrated_optimizations:
        directory, version = "arm-integrated-optimizations", "01.92"
    elif options.step_spike_profile:
        directory, version = "arm-step-spike-profile", "01.91"
    elif options.scene_queries:
        directory, version = "arm-scene-queries", "01.90"
    elif options.cpu_phase_profile:
        directory, version = "arm-thread-phase-profile", "01.89"
    elif options.compact_vm_code:
        directory, version = "arm-compact-vm-code", "01.87"
    elif options.native_scenery_body:
        directory, version = "arm-native-scenery-body", "01.86"
    elif options.eventless_case:
        directory, version = "arm-eventless-case", "01.85"
    elif options.simulation_profile:
        directory, version = "arm-simulation-profile", "01.84"
    elif options.scenery_cursor:
        directory, version = "arm-scenery-cursor", "01.83"
    elif options.frame_headroom:
        directory, version = "arm-frame-headroom", "01.82"
    elif options.prefetch_ambience:
        directory, version = "arm-prefetch-ambience", "01.81"
    elif options.reduced_vm_work:
        directory, version = "arm-reduced-vm-work", "01.80"
    elif options.memory_ambient:
        directory, version = "arm-memory-ambient", "01.79"
    elif options.shared_instance_fields:
        directory, version = "arm-shared-instance-fields-01.78", "01.78"
    elif options.combined_performance:
        directory, version = "arm-combined-performance", "01.76"
    elif options.frame_hitch_profile:
        directory, version = "arm-frame-hitch-profile", "01.67"
    elif options.graphics_commands:
        if options.graphics_render_tail:
            directory, version = "arm-graphics-render-tail", "01.73"
        elif options.graphics_framebuffer:
            directory, version = "arm-graphics-framebuffer", "01.72"
        else:
            directory, version = "arm-graphics-commands", "01.70"
    elif options.audio_start_profile:
        directory, version = "arm-audio-start-profile", "01.69"
    elif options.lazy_audio_length:
        directory, version = ("arm-buffered-frame-reports", "01.74") if options.buffered_frame_reports else (
            "arm-lazy-audio-length", "01.71")
    elif options.scenery_draw_lists:
        directory, version = "arm-scenery-draw-lists", "01.68"
    elif options.direct_instance_ids:
        directory, version = "arm-direct-instance-ids", "01.75"
    elif options.consolidated_runtime:
        directory, version = "arm-consolidated-runtime", "01.66"
    elif options.combat_route:
        directory, version = "arm-combat-route", "01.65"
    elif options.sprite_worker:
        directory, version = "arm-sprite-worker", "01.64"
    elif options.draw_order_repair:
        directory, version = "arm-draw-order-repair", "01.63"
    elif options.route_profile:
        directory, version = "arm-route-profile", "01.62"
    elif options.bounded_round:
        directory, version = "arm-bounded-round", "01.61"
    elif options.submission_rings:
        directory, version = "arm-submission-rings", "01.60"
    else:
        directory, version = "arm-runtime-refinements", "01.59"
elif options.stable_cases:
    directory, version = "arm-stable-cases", "01.15"
elif options.placement_cells:
    directory, version = "arm-placement-cells", "01.16"
elif options.copy_blocks:
    directory, version = "arm-copy-blocks", "01.17"
elif options.idle_spawners:
    directory, version = "arm-idle-spawners", "01.18"
elif options.point_queries:
    directory, version = "arm-point-queries", "01.19"
elif options.line_envelopes:
    directory, version = "arm-line-envelopes", "01.20"
elif options.playable_indexed_runtime:
    directory, version = "arm-playable-indexed-runtime", "01.21"
elif options.verify_indexed_runtime:
    directory, version = "arm-verify-indexed-runtime", "01.22"
elif options.scenery_batches:
    directory, version = "arm-scenery-batches", "01.23"
elif options.collision_cells:
    directory, version = "arm-collision-cells", "01.24"
elif options.combined_simulation:
    directory, version = "arm-combined-simulation", "01.25"
elif options.ascii_parser:
    directory, version = "arm-ascii-parser", "01.26"
elif options.pure_guards:
    directory, version = "arm-pure-guards", "01.27"
elif options.retained_sprite_program:
    directory, version = "arm-retained-sprite-program", "01.28"
elif options.flame_vent_probes:
    directory, version = "arm-flame-vent-probes", "01.29"
elif options.movement_helpers:
    directory, version = "arm-movement-helpers", "01.30"
elif options.zero_movement:
    directory, version = "arm-zero-movement", "01.31"
elif options.native_enemy:
    directory, version = "arm-native-enemy", "01.32"
elif options.compact_enemy:
    directory, version = "arm-compact-enemy", "01.33"
elif options.compiled_events:
    directory, version = "arm-compiled-events", "01.34"
elif options.playable_measured_runtime:
    directory, version = "arm-playable-measured-runtime", "01.35"
elif options.verify_measured_runtime:
    directory, version = "arm-verify-measured-runtime", "01.36"
elif options.zero_alpha_quads:
    directory, version = "arm-zero-alpha-quads", "01.37"
elif options.sparse_composite:
    directory, version = "arm-sparse-composite", "01.38"
elif options.sparse_geometry:
    directory, version = "arm-sparse-geometry", "01.39"
elif options.trace_sparse_geometry:
    directory, version = "arm-trace-sparse-geometry", "01.43"
elif options.sparse_transparent_coverage:
    directory, version = "arm-sparse-transparent-coverage", "01.44"
elif options.skip_transparent_batches:
    directory, version = "arm-skip-transparent-batches", "01.45"
elif options.shared_collision_grid:
    directory, version = "arm-shared-collision-grid", "01.40"
elif options.depth_helpers:
    directory, version = "arm-depth-helpers", "01.41"
elif options.recessing_guards:
    directory, version = "arm-recessing-guards", "01.42"
elif options.playable:
    directory, version = "arm-playable", "00.62"
elif options.packed_math:
    directory, version = "arm-packed-math", "00.63"
elif options.audio_affinity:
    directory, version = "arm-audio-affinity", "00.64"
elif options.uniform_trial:
    directory, version = "arm-uniform-trial", "00.65"
elif options.respawn_profile:
    directory, version = "arm-respawn-profile", "00.66"
elif options.compact_rvalues:
    directory, version = "arm-compact-rvalues", "00.67"
elif options.sprite_cache:
    directory, version = "arm-sprite-cache", "00.69"
elif options.early_draw_list:
    directory, version = "arm-early-draw-list", "00.75"
elif options.instance_properties:
    directory, version = "arm-instance-properties", "00.76"
elif options.transparent_composite:
    directory, version = "arm-transparent-composite", "00.78"
elif options.collision_query:
    directory, version = "arm-collision-query", "00.79"
elif options.combat_queries:
    directory, version = "arm-combat-queries", "00.82"
elif options.collision_order:
    directory, version = "arm-collision-order", "00.84"
elif options.collision_blocks:
    directory, version = "arm-collision-blocks", "00.86"
elif options.threaded_vm:
    directory, version = "arm-threaded-vm", "00.87"
elif options.step_log_buffer:
    directory, version = "arm-step-log-buffer", "00.88"
elif options.script_arguments:
    directory, version = "arm-script-arguments", "00.80"
elif options.combined_perf:
    directory, version = "arm-combined-perf", "00.73"
elif options.deferred_audio:
    directory, version = "arm-deferred-audio", "00.70"
elif options.packed_vars:
    directory, version = "arm-packed-vars", "00.71"
elif options.axis_draw:
    directory, version = "arm-axis-draw", "00.74"
out = proof / directory
out.mkdir(exist_ok=True)
if options.prefetch_ambience:
    vendor = root / "engine/vendor/stb/vorbis/stb_vorbis.c"
    (out / vendor.name).write_text(with_constant_vorbis_crc(vendor.read_text()))
commands = subprocess.check_output(["ninja", "-t", "commands", "Butterscotch.vpk"], cwd=build, text=True).splitlines()
link = shlex.split(next(line for line in commands if " -o butterscotch " in line))[2:]
link = link[:link.index("&&")]
sdk = Path(link[0]).parent
gcc = sdk / "arm-vita-eabi-gcc"

shared_field_transforms = {
    "instance.c": with_shared_fields_instance,
    "int_rvalue_hashmap.c": with_shared_fields_hashmap,
    "runner.c": lambda code: with_shared_fields_runner(code, launch_flag="hldSharedFieldsEnabled"),
    "vm_builtins.c": with_shared_field_iteration,
}
shared_field_sources = {*shared_field_transforms, "vm.c", "loop.c", "vita/main.c"}
if options.shared_instance_fields:
    field_header = out / "shared-fields-hashmap.h"
    field_header.write_text(with_shared_fields_header((source / "int_rvalue_hashmap.h").read_text()))


def replace_once(code, before, after):
    assert code.count(before) == 1, before
    return code.replace(before, after)


def compile_source(name, edit):
    path = source / name
    dst = out / path.name
    snapshot = proof / "native-platform-baseline.c"
    code = snapshot.read_text() if name == "vita/native_platform.c" and snapshot.exists() else path.read_text()
    code = edit(code)
    if options.direct_instance_ids:
        if name == "runner.c":
            code = with_direct_id_runner(code, launch_flag="hldDirectInstanceIdsEnabled")
        elif name in ("vm.c", "vm_builtins.c", "spatial_grid.c"):
            include = '#include "utils.h"' if name == "spatial_grid.c" else '#include "stb_ds.h"'
            code = with_direct_id_lookups(code, include)
    if options.shared_instance_fields and name in shared_field_transforms:
        code = shared_field_transforms[name](code)
    if options.grid_mask_memo and name == "runner.c":
        code = with_grid_mask_memo_runner(code, compare=True, dynamic=options.vault_mask_layers)
    if options.vault_mask_layers and name == "vm_builtins.c":
        code = ("#define HLD_SPLIT_MASK_OVERLAP\n#define HLD_BUILTIN_MASK_OVERLAP_TRIAL\n"
                "#define hldReportGridMaskOverlaps hldReportBuiltinGridMaskOverlaps\n"
                + with_dynamic_mask_memo_builtins(code, compare=True))
    if options.vault_mask_layers and name == "gl/gl_renderer.c":
        code = with_dynamic_mask_memo_renderer(code)
    if options.mask_tiles:
        if name == "runner.c":
            code = with_mask_tiles_runner(code)
        elif name == "vm_builtins.c":
            code = with_mask_tiles_builtins(code, launch_flag="hldMaskTiles")
            if options.mask_line_tiles:
                code = with_mask_line_tiles(code, checked=False, launch_flag="hldMaskLineTiles")
        elif name == "gl/gl_renderer.c":
            code = with_mask_tiles_renderer(code)
    if options.mine_entry:
        if name == "vm_builtins.c":
            code = with_mine_entry_builtins(code)
        elif name == "spatial_grid.c":
            code = with_mine_entry_grid(code)
    if options.collision_target_views and name == "runner.c":
        code = with_collision_target_views(code, launch_flag="hldCollisionTargetViews")
        if options.pinned_collision_targets:
            code = with_pinned_collision_targets(code)
    if options.persistent_terrain_bounds:
        if name == "runner.c":
            code = with_persistent_terrain_bounds(code, launch_flag="hldPersistentTerrainBounds")
        elif name == "spatial_grid.c":
            code = with_terrain_bounds_grid_hooks(code)
        elif name == "vm_builtins.c":
            code = with_terrain_bounds_sprite_hooks(code)
    if options.atlas_batches and name == "gl/gl_renderer.c":
        code = with_premultiply_atlas_batches(code, launch_flag="hldAtlasBatches")
        if options.atlas_upload_cache:
            code = with_atlas_batch_upload_cache(code)
    if options.classified_vm_entry and name == "vm.c":
        code = with_classified_vm_entry(code, windowed_report=True)
    if options.line_terrain_index and name == "vm_builtins.c":
        code = with_line_terrain_index(code)
    if options.placement_probe_masks and name == "vm_builtins.c":
        code = with_placement_probe_masks(code, windowed_report=True, mine_only=options.mine_probe_mask)
    if options.frame_depth_fold:
        if name == "gl/gl_renderer.c":
            code = with_frame_depth_fold_renderer(code, windowed_report=True)
        elif name == "loop.c":
            code = with_frame_depth_fold_loop(code)
        elif name == "vita/native_platform.c":
            code = with_present_hitch_profile(code)
    if options.full_viewport_mask or options.initial_scene_depth:
        if name == "gl/gl_renderer.c":
            code = with_application_clear_profile(code)
            if options.initial_scene_depth:
                code = with_initial_depth_profile_hook(code)
        elif name == "runner.c":
            code = with_application_setup_profile(code)
        elif name == "loop.c":
            code = with_initial_depth_report(code) if options.initial_scene_depth else with_scissor_mask_report(code)
        elif name == "vita/native_platform.c":
            code = with_present_hitch_profile(code)
    if options.stream_placement and name == "runner.c":
        code = with_step_event_profile(code)
    if HLDC_NATIVE_ONLY and name == "loop.c":
        from native_only_source import with_native_only_loop
        code = with_native_only_loop(code)
    if HLDC_TRANSLATE and name == "spatial_grid.c":
        from cell_retention_source import with_cell_retention_grid
        code = with_cell_retention_grid(code)
    if HLDC_TRANSLATE and name == "vm_builtins.c":
        from engine_speedups_source import with_engine_speedups_builtins
        code = with_engine_speedups_builtins(code)
    if (HLDC_TRANSLATE or os.environ.get("HLDC_MEMLOG")) and name == "loop.c":
        # Boot prefetch (texbin_fast_source): the opening room's and the UI pages are read on another core from here on.
        # Started once the VM exists, as the shaders begin compiling (~14 s of CPU, no card reads): earlier, during the
        # data.win parse, its reads competed with the parse's own.
        parsed = "        VitaBootProgress_set(VITA_BOOT_VM, 1, 1);\n"
        if os.environ.get("HLDC_BOOT_PREFETCH", "1") == "1" and os.environ.get("HLDC_TEXBIN_FAST", "1") == "1" and code.count(parsed) == 1:
            code = replace_once(code, parsed, parsed + "        { extern void hldBootPagePrefetch(DataWin*); hldBootPagePrefetch(dataWin); }\n")
        # After each room change, log the largest heap block and the total free heap (1 MiB steps).
        anchor = "                            runner->frameCount, runner->currentRoom->name, (nowNanos() - start) * 1e-6);"
        assert code.count(anchor) == 1
        # The periodic hooks' one-time marker lookups (card fopen, ~55 ms) also happen here, during the room load.
        code = code.replace(anchor, anchor + " { extern void hldLogMemoryHeadroom(void); hldLogMemoryHeadroom(); extern int hldPeriodicPrime; "
                            "hldPeriodicPrime = 1; extern void hldLogMemoryHeadroomPeriodic(void); hldLogMemoryHeadroomPeriodic(); "
                            "extern void hldRoomTourStep(Runner*); hldRoomTourStep(runner); hldPeriodicPrime = 0; }")
        # The GUI layer (map, dialogue, shops, pause) marks texture pages it loads as UI pages (engine_speedups_source).
        gui = "                Runner_drawGUI(runner, fbWidth, fbHeight, gameW, gameH);\n"
        if gui in code:
            code = replace_once(code, gui, "                { extern int hldGuiDrawing; hldGuiDrawing = 1; } Runner_drawGUI(runner, fbWidth, fbHeight, gameW, gameH); "
                                "{ extern int hldGuiDrawing; hldGuiDrawing = 0; }\n")
        # Test launches wipe the profile; with the test-has-map marker the isolated profile also gets the map, so a
        # route can open it (Select) to measure the map's page loads.
        pistol = '    if (!opening) Instance_setSelfVar(screen, VM_getOrAllocateVarID(vm, "givePistol"), RValue_makeInt32(1));\n'
        if pistol in code:
            code = replace_once(code, pistol, pistol +
                '    { FILE* hasMap = fopen("ux0:data/hld-vm/test-has-map", "r"); if (hasMap != NULL) { fclose(hasMap);\n'
                '      repeat(arrlen(runner->instances), i) { Instance* inst = runner->instances[i];\n'
                '        if (strcmp(vm->dataWin->objt.objects[inst->objectIndex].name, "DATA") == 0) {\n'
                '          Instance_setSelfVar(inst, VM_getOrAllocateVarID(vm, "playerHasMap"), RValue_makeInt32(1)); logInfo("HLD test launch: map granted\\n"); } } } }\n')
        # Also every 450 frames with the timing report, to show growth within a room.
        timing = "                    runner->vitaStepBeginTime * perFrameMs, runner->vitaStepEndTime * perFrameMs);"
        assert code.count(timing) == 1
        # The heap walk (mallinfo) and 16 MB probe stall a frame ~0.3 s, so mid-room reports need the memory-report marker.
        code = code.replace(timing, timing + " if (runner->frameCount % 450 == 0) { extern void hldLogMemoryHeadroomPeriodic(void); "
                            "hldLogMemoryHeadroomPeriodic(); extern void hldRoomTourStep(Runner*); hldRoomTourStep(runner); }")
        # Test mode: ux0:data/hld-vm/fight.txt ("<frame> <object:count,...>") spawns enemies in a ring around the
        # player on that frame and keeps the player's hp topped up, for a repeatable heavy fight.
        step = "                Runner_step(runner);"
        assert code.count(step) == 1
        pgo_hook = " extern void hldPgoFrame(Runner*); hldPgoFrame(runner);" if os.environ.get("HLDC_PART_PGO") == "train" else ""
        code = code.replace(step, "                { extern void hldUpdateDpadShoulders(Runner*); hldUpdateDpadShoulders(runner); extern void hldFightFixture(Runner*); hldFightFixture(runner); extern void hldLayerSpriteFrame(Runner*) "
                            "__attribute__((weak)); if (hldLayerSpriteFrame) hldLayerSpriteFrame(runner);" + pgo_hook + " } Runner_step(runner);")
        if os.environ.get("HLDC_PART_PGO") == "train":
            code += PGO_PART_EXPORT
        code += r"""
#include <math.h>
// D-pad left/right act as L1/R1 (heal / special) only in gameplay: not while a Menus instance exists or the SCREEN
// pause menu or inventory (map) is showing.
volatile int hldDpadShoulders;
void hldUpdateDpadShoulders(Runner* runner) {
    { extern void hldVitaButtonIcons(Renderer*); hldVitaButtonIcons(runner->renderer); }
    static int32_t menusObject = -2, screenObject = -1, pauseVar = -1, inventoryVar = -1;
    DataWin* dw = runner->dataWin;
    if (menusObject == -2) {
        menusObject = -1;
        for (uint32_t o = 0; o < dw->objt.count; ++o) {
            if (strcmp(dw->objt.objects[o].name, "Menus") == 0) menusObject = (int32_t) o;
            if (strcmp(dw->objt.objects[o].name, "SCREEN") == 0) screenObject = (int32_t) o;
        }
        for (uint32_t v = 0; v < dw->vari.variableCount; ++v) {
            const Variable* variable = &dw->vari.variables[v];
            if (variable->instanceType != INSTANCE_SELF || variable->varID < 0) continue;
            if (strcmp(variable->name, "pauseMenuShow") == 0) pauseVar = variable->varID;
            if (strcmp(variable->name, "inventoryShow") == 0) inventoryVar = variable->varID;
        }
    }
    bool menu = menusObject >= 0 && arrlen(runner->instancesByObject[menusObject]) > 0;
    if (!menu && screenObject >= 0 && arrlen(runner->instancesByObject[screenObject]) > 0) {
        Instance* screen = runner->instancesByObject[screenObject][0];
        if (pauseVar >= 0 && RValue_toReal(Instance_getSelfVar(screen, pauseVar)) > 0) menu = true;
        if (inventoryVar >= 0 && RValue_toReal(Instance_getSelfVar(screen, inventoryVar)) > 0) menu = true;
    }
    hldDpadShoulders = !menu;
}
void hldFightFixture(Runner* runner) {
    static int state = -1, spawnFrame = -1;
    static int32_t hpVar = -1, charObject = -1;
    static char spec[256];
    if (state == 0) return;
    if (state == -1) {
        state = 0;
        FILE* file = fopen("ux0:data/hld-vm/fight.txt", "r");
        if (file == NULL) return;
        if (fscanf(file, "%d %255s", &spawnFrame, spec) != 2) { fclose(file); return; }
        fclose(file);
        DataWin* dw = runner->dataWin;
        for (uint32_t v = 0; v < dw->vari.variableCount; ++v)
            if (dw->vari.variables[v].instanceType == INSTANCE_SELF && dw->vari.variables[v].varID >= 0 &&
                strcmp(dw->vari.variables[v].name, "hp") == 0) { hpVar = dw->vari.variables[v].varID; break; }
        for (uint32_t o = 0; o < dw->objt.count; ++o)
            if (strcmp(dw->objt.objects[o].name, "char") == 0) { charObject = (int32_t) o; break; }
        state = 1;
        logInfo("Vita fight fixture frame=%d spawn=%s hp_var=%d\n", spawnFrame, spec, (int) hpVar);
    }
    Instance* player = NULL;
    for (int i = 0; i < (int) arrlen(runner->instances); ++i)
        if (runner->instances[i]->objectIndex == charObject) { player = runner->instances[i]; break; }
    if (player == NULL) return;
    if (hpVar >= 0) Instance_setSelfVar(player, hpVar, RValue_makeReal(5.0));
    if (strncmp(spec, "cut", 3) == 0) {
        // "cut": hit every destructable within 260 px of the player (its sword-hit user event 5) on frames
        // spawnFrame, +6, +12, +18, like slicing the bushes around the player.
        const int offset = runner->frameCount - spawnFrame;
        if (offset < 0 || offset > 18 || offset % 6 != 0) return;
        static int32_t hitDirVar = -2;
        if (hitDirVar == -2) {
            hitDirVar = -1;
            DataWin* dw = runner->dataWin;
            for (uint32_t v = 0; v < dw->vari.variableCount; ++v)
                if (dw->vari.variables[v].instanceType == INSTANCE_SELF && dw->vari.variables[v].varID >= 0 &&
                    strcmp(dw->vari.variables[v].name, "hitDir") == 0) { hitDirVar = dw->vari.variables[v].varID; break; }
        }
        Instance** targets = NULL;
        for (int i = 0; i < (int) arrlen(runner->instances); ++i) {
            Instance* in = runner->instances[i];
            if (in->destroyed || strcmp(runner->dataWin->objt.objects[in->objectIndex].name, "destructable") != 0) continue;
            const double dx = in->x - player->x, dy = in->y - player->y;
            if (dx * dx + dy * dy <= 260.0 * 260.0) arrput(targets, in);
        }
        for (int i = 0; i < (int) arrlen(targets); ++i) {
            if (hitDirVar >= 0)
                Instance_setSelfVar(targets[i], hitDirVar, RValue_makeReal(atan2(-(targets[i]->y - player->y), targets[i]->x - player->x) * 57.29577951308232));
            Runner_executeEvent(runner, targets[i], EVENT_OTHER, OTHER_USER0 + 5);
        }
        logInfo("Vita cut fixture frame=%d hit=%d\n", runner->frameCount, (int) arrlen(targets));
        arrfree(targets);
        return;
    }
    if (runner->frameCount != spawnFrame) return;
    char list[256];
    int total = 0, placed = 0;
    snprintf(list, sizeof(list), "%s", spec);
    for (char* item = strtok(list, ","); item != NULL; item = strtok(NULL, ",")) {
        char* colon = strchr(item, ':');
        total += colon != NULL ? atoi(colon + 1) : 1;
    }
    snprintf(list, sizeof(list), "%s", spec);
    for (char* item = strtok(list, ","); item != NULL; item = strtok(NULL, ",")) {
        char* colon = strchr(item, ':');
        const int count = colon != NULL ? atoi(colon + 1) : 1;
        if (colon != NULL) *colon = 0;
        int32_t object = -1;
        for (uint32_t o = 0; o < runner->dataWin->objt.count; ++o)
            if (strcmp(runner->dataWin->objt.objects[o].name, item) == 0) { object = (int32_t) o; break; }
        for (int k = 0; object >= 0 && k < count; ++k, ++placed) {
            const double angle = 6.283185307179586 * placed / total;
            // Keep every spawn on screen: a tight ring around the player, clamped into the current view.
            double sx = player->x + 56.0 * cos(angle), sy = player->y + 40.0 * sin(angle);
            GMLCamera* camera = Runner_getCameraForView(runner, 0);
            if (camera != NULL) {
                const double left = camera->viewX + 24.0, right = camera->viewX + camera->viewWidth - 24.0;
                const double top = camera->viewY + 24.0, bottom = camera->viewY + camera->viewHeight - 24.0;
                sx = sx < left ? left : sx > right ? right : sx;
                sy = sy < top ? top : sy > bottom ? bottom : sy;
            }
            Runner_createInstance(runner, sx, sy, object);
        }
    }
    logInfo("Vita fight fixture spawned=%d frame=%d\n", placed, runner->frameCount);
}
"""
        # Test mode: ux0:data/hld-vm/room-tour.txt lists rooms to visit in turn, one every 450 frames.
        code += r"""
void hldRoomTourStep(Runner* runner) {
    static int state = -1, count = 0, next = 0;
    static int32_t rooms[64];
    if (state == -1) {
        state = 0;
        FILE* file = fopen("ux0:data/hld-vm/room-tour.txt", "r");
        if (file == NULL) return;
        char line[128];
        while (count < 64 && fgets(line, sizeof(line), file) != NULL) {
            line[strcspn(line, "\r\n ")] = '\0';
            for (uint32_t i = 0; i < runner->dataWin->room.count; ++i) {
                if (strcmp(runner->dataWin->room.rooms[i].name, line) == 0) { rooms[count++] = (int32_t) i; break; }
            }
        }
        fclose(file);
        state = 1;
        logInfo("Vita room tour rooms=%d\n", count);
    }
    // Frame 2 (still loading) only opens the files, so the first periodic call does no card lookups mid-play.
    extern int hldPeriodicPrime;
    if (hldPeriodicPrime) return;
    if (state != 1 || next >= count) return;
    logInfo("Vita room tour step=%d room=%s\n", next, runner->dataWin->room.rooms[rooms[next]].name);
    runner->pendingRoom = rooms[next++];
}
"""
        code += r"""
#include <psp2/kernel/sysmem.h>
#include <malloc.h>
#include <vitaGL.h>
unsigned int _get_vita_heap_size(void);
void hldLogMemoryHeadroom(void);
int hldPeriodicPrime;
void hldLogMemoryHeadroomPeriodic(void) {
    static int enabled = -1;
    if (enabled < 0) {
        FILE* marker = fopen("ux0:data/hld-vm/memory-report", "r");
        enabled = marker != NULL;
        if (marker != NULL) fclose(marker);
    }
    if (enabled && !hldPeriodicPrime) hldLogMemoryHeadroom();
}
void hldLogMemoryHeadroom(void) {
    SceKernelFreeMemorySizeInfo info = {.size = sizeof(info)};
    sceKernelGetFreeMemorySize(&info);
    const struct mallinfo heap = mallinfo();
    const double size = _get_vita_heap_size() / 1048576.0;
    // Headroom: heap not yet taken from the system plus free space inside the taken part.
    void* probe = malloc((size_t) 16 << 20);
    const bool fits16 = probe != NULL;
    free(probe);
    logInfo("Vita memory heap_mb=%.1f taken_mb=%.1f inside_free_mb=%.1f headroom_mb=%.1f user_free_mb=%.1f "
            "malloc16=%d vgl_vram_mb=%.1f vgl_ram_mb=%.1f vgl_phycont_mb=%.1f vgl_budget_mb=%.1f\n", size,
            heap.arena / 1048576.0, heap.fordblks / 1048576.0, size - heap.arena / 1048576.0 + heap.fordblks / 1048576.0,
            info.size_user / 1048576.0, (int) fits16, vglMemFree(VGL_MEM_VRAM) / 1048576.0,
            vglMemFree(VGL_MEM_RAM) / 1048576.0, vglMemFree(VGL_MEM_PHYCONT) / 1048576.0, vglMemFree(VGL_MEM_BUDGET) / 1048576.0);
}
"""
    if os.environ.get("HLD_BOOT_DIAG"):
        # Boot hang diagnostics: async log lines at page loads, surface creation and the first placement-cell calls.
        if name == "gl/gl_renderer.c":
            a = "static bool loadTexturePage(GLRenderer* gl, uint32_t pageId) {"
            assert code.count(a) == 1
            code = code.replace(a, a + '\n    logInfo("HLDDIAG page %u begin\\n", pageId);')
            a = "static int32_t glCreateSurface(Renderer* renderer, int32_t width, int32_t height) {"
            assert code.count(a) == 1
            code = code.replace(a, a + '\n    logInfo("HLDDIAG surface %dx%d begin\\n", width, height);')
        if name == "vm_builtins.c":
            a = "static Instance** placementCellTargets(Runner* runner, Instance** instances, int32_t cell, int32_t target) {"
            assert code.count(a) == 1
            code = code.replace(a, a + '\n    { static unsigned n; ++n; if (n <= 300 || n % 5000 == 0) logInfo("HLDDIAG cell n=%u cell=%d target=%d len=%d kept=%u rebuilt=%u\\n", n, cell, target, (int) arrlen(instances), placementCellKept, placementCellRebuilt); }')
    if HLDC_TRANSLATE and os.environ.get("HLDC_VITA_ROWS", "1") == "1" and name in ("vm_builtins.c", "gl/gl_renderer.c"):
        # Full-height 960x544 picture (vita_rows_source): four more rows of the world instead of black edges.
        from vita_rows_source import with_vita_rows_builtins, with_vita_rows_renderer
        code = with_vita_rows_builtins(code) if name == "vm_builtins.c" else with_vita_rows_renderer(code)
    dst.write_text(code)
    argv = shlex.split(next(line for line in commands if " -c " in line and line.endswith(str(path))))
    obj = dst.with_suffix(".obj")
    original = argv[argv.index("-o") + 1]
    argv[argv.index("-o") + 1] = str(obj)
    argv[argv.index("-MF") + 1] = str(obj.with_suffix(".d"))
    argv[-1] = str(dst)
    if options.shared_instance_fields and name in shared_field_sources:
        argv[1:1] = ["-include", str(field_header), "-iquote", str(proof)]
    compile_directory = build
    vm_profile_unit = name == "vm.c" and (options.vm_training or options.vm_feedback)
    engine_profile_unit = path.stem in ENGINE_PROFILE_UNITS and (options.engine_training or options.engine_feedback)
    if name == "vm.c" and options.focused_step_profile:
        argv = [flag for flag in argv if not flag.startswith("-flto")] + ["-fno-lto", "-marm"]
    if vm_profile_unit or engine_profile_unit:
        # A relative object name gives both builds the same profile key.
        # Keep the rest of the program on the existing LTO configuration.
        argv = [flag for flag in argv if not flag.startswith("-flto")]
        argv[argv.index("-o") + 1] = obj.name
        profile_input = options.vm_feedback if vm_profile_unit else options.engine_feedback
        if vm_profile_unit and os.environ.get("HLDC_PORTABLE_PROFILE") == "1":
            # GCC keys each function's profile to the file names it sees (the source and every header). Training and
            # feedback builds both compile the same generated vm-portable.c from the build directory, with every path
            # inside this checkout written relative to it, so a profile recorded in one checkout matches any other.
            generated = portable_vm_source(dst.read_text())
            (out / "vm-portable.c").write_text(generated)
            argv[-1] = "vm-portable.c"
            argv = [arg if arg.startswith("-fprofile") else arg.replace(str(root), os.path.relpath(root, out))
                    for arg in argv]
        elif profile_input:
            training_directory = "arm-vm-training" if vm_profile_unit else "arm-engine-training"
            if options.classified_vm_entry:
                training_directory = "arm-classified-vm-training"
            elif options.stream_enemy_target:
                training_directory = "arm-stream-enemy-training"
            elif options.stream_slot_writes:
                training_directory = "arm-stream-slot-write-training"
            elif options.stream_slot_reads:
                training_directory = "arm-shapes-training"
            elif options.stream_scenery_snapshot:
                training_directory = "arm-stream-scenery-training"
            training_source = proof / training_directory / path.name
            if not training_source.is_file() or training_source.read_bytes() != dst.read_bytes():
                raise RuntimeError(f"The generated {path.name} differs from its hardware training input")
            # GCC also records the source filename in each function's
            # location checksum. Use the identical training input path.
            argv[-1] = str(training_source)
            if name == "vm.c" and (HLDC_ENTRIES or HLDC_NATIVE_SERIALIZE or HLDC_NATIVE_SCRIPTS or HLDC_NATIVE_ONLY or HLDC_TRANSLATE):
                generated = dst.read_text()
                if HLDC_NATIVE_SERIALIZE:
                    from native_serialize_source import with_native_serialize
                    generated = with_native_serialize(generated)
                if HLDC_NATIVE_ONLY:
                    from native_only_source import with_native_only_vm
                    generated = with_native_only_vm(generated)
                if HLDC_TRANSLATE:
                    from translated_gml_source import all_entries, with_translated_gml
                    names = all_entries() if HLDC_TRANSLATE == "all" else \
                        [line.strip() for line in Path(HLDC_TRANSLATE).read_text().splitlines() if line.strip()]
                    generated, parts = with_translated_gml(generated, names, stats=os.environ.get("HLDC_STATS") == "1")
                    HLDC_PART_SOURCES[:] = parts
                if HLDC_NATIVE_SCRIPTS:
                    # After translation, as on the Mac: native scripts register for direct translated calls.
                    from native_scripts_source import with_native_scripts
                    generated = with_native_scripts(generated)
                if HLDC_ENTRIES:
                    from gml_compiler_source import with_compiled_gml
                    generated = with_compiled_gml(generated, HLDC_ENTRIES)
                compiled = out / "vm-hldc.c"
                compiled.write_text(f'#line 1 "{training_source}"\n' + generated)
                argv[-1] = str(compiled)
        argv += ["-fno-lto", "-fno-profile-values", f"-fprofile-prefix-path={out}"]
        if vm_profile_unit and any((options.arm_state_vm, options.obstacle_line_buckets, options.stream_slot_reads,
                                    options.stream_scenery_snapshot)):
            argv.append("-marm")
        compile_directory = out
        if not profile_input:
            argv += ["-fprofile-generate", "-fprofile-info-section=hld_gcov",
                     f"-fprofile-dir={out / 'profiles'}"]
        elif engine_profile_unit or new_vm_training:
            argv += [f"-fprofile-use={profile_input}", "-fprofile-partial-training",
                     "-Werror=missing-profile", "-Werror=coverage-mismatch"]
            if (name == "vm.c" and (HLDC_ENTRIES or HLDC_NATIVE_SERIALIZE or HLDC_NATIVE_SCRIPTS or HLDC_NATIVE_ONLY or HLDC_TRANSLATE)) or \
                    (name == "vm_builtins.c" and HLDC_TRANSLATE) or (name in ("loop.c", "gl/gl_renderer.c") and (HLDC_TRANSLATE or os.environ.get("HLDC_MEMLOG"))):
                # Only the compiled entries, hldcBind and the two hooked functions lack matching feedback.
                argv[-2:] = ["-Wno-error=missing-profile", "-Wno-error=coverage-mismatch"]
        else:
            argv += [f"-fprofile-use={profile_input}", "-fprofile-partial-training",
                     "-Wno-error=missing-profile", "-Wno-error=coverage-mismatch"]
    no_compiler_feedback = os.environ.get("HLDC_NO_COMPILER_FEEDBACK") == "1" and vm_profile_unit
    if no_compiler_feedback:
        # Same VM source, built without the recorded GCC profile: GCC keys that profile to the absolute path it was
        # recorded at, so a checkout elsewhere cannot use it (scripts/build.py sets this).
        argv = [a for a in argv if not a.startswith(("-fprofile-use", "-fprofile-partial-training"))
                and "missing-profile" not in a and "coverage-mismatch" not in a]
    portable_feedback = (vm_profile_unit and options.vm_feedback and not no_compiler_feedback
                         and os.environ.get("HLDC_PORTABLE_PROFILE") == "1")
    if portable_feedback:
        argv = [a.replace("-Werror=missing-profile", "-Wno-error=missing-profile")
                 .replace("-Werror=coverage-mismatch", "-Wno-error=coverage-mismatch") for a in argv]
        compiler = subprocess.run(argv, cwd=compile_directory, stderr=subprocess.PIPE, text=True,
                                  env={**os.environ, "LC_ALL": "C"})
        print(compiler.stderr, file=sys.stderr, end="")
        compiler.check_returncode()
        unmatched = re.findall(r"warning: (.+) \[-W(coverage-mismatch|missing-profile)\]$", compiler.stderr, re.M)
        print(f"VM compiler feedback (portable profile): {len(unmatched)} functions without matching counters",
              flush=True)
    elif name == "vm.c" and options.vm_feedback and not no_compiler_feedback:
        # The SDK discards mismatched counters and estimates those functions.
        # Require valid feedback everywhere except the observed GCC 15.2
        # graph differences in this exact VM source.
        expected = {
            ("number of counters in profile data for function 'VM_free' does not match its profile data "
             "(counter 'arcs', expected 61 and have 60)", "coverage-mismatch"),
            ("number of counters in profile data for function 'handleCall' does not match its profile data "
             "(counter 'arcs', expected 41 and have 47)", "coverage-mismatch"),
            ("profile for function 'VM_createDedupKey' not found in profile data", "missing-profile"),
        }
        compiler = subprocess.run(argv, cwd=compile_directory, stderr=subprocess.PIPE, text=True,
                                  env={**os.environ, "LC_ALL": "C"})
        print(compiler.stderr, file=sys.stderr, end="")
        compiler.check_returncode()
        discrepancies = set(re.findall(r"warning: (.+) \[-W(coverage-mismatch|missing-profile)\]$",
                                       compiler.stderr, re.M))
        allowed = ("executeBytecodeInstructions", "VM_create", "VM_executeCode", "hldc", "hldSerialize", "hldAscii", "hldNative", "hldOnscreen", "hldApproach", "hldDistance", "hldIsAny", "hldIsType", "hldPalette", "hldObjectParent", "hldExisting", "hldViewRect", "hldNumber", "hldCompare", "hldScriptResult", "hldNativeOnly", "hldNativeScreen", "hldSpriteIndex", "hldDrawSprite", "hldReportNativeOnly")
        if HLDC_NATIVE_ONLY:
            # Native game code is new and has no feedback yet.
            allowed = allowed + ("hld",)
        if HLDC_TRANSLATE:
            allowed = allowed + ("hld", "resolveVariableWriteAccess", "VM_isObjectOrDescendant")
        discrepancies = {d for d in discrepancies if not ((HLDC_ENTRIES or HLDC_NATIVE_SERIALIZE or HLDC_NATIVE_SCRIPTS or HLDC_NATIVE_ONLY or HLDC_TRANSLATE) and any(f"'{a}" in d[0] for a in allowed))}
        if discrepancies - expected:
            raise RuntimeError(f"Unexpected VM feedback diagnostics: {sorted(discrepancies - expected)}")
        print(f"VM compiler feedback accepted with {len(discrepancies)} known fallback functions", flush=True)
    else:
        subprocess.run(argv, cwd=compile_directory, check=True)
    if name == "vm.c" and HLDC_PART_SOURCES:
        # Translated GML parts: vm.c's flags without its profile feedback, compiled in parallel.
        profile = ("-fprofile-use", "-fprofile-partial-training", "-Werror=missing-profile", "-Werror=coverage-mismatch",
                   "-Wno-error=missing-profile", "-Wno-error=coverage-mismatch", "-fprofile-generate",
                   "-fprofile-info-section", "-fprofile-dir", "-fprofile-prefix-path")
        base = [flag for flag in argv if not flag.startswith(profile)]
        # HLDC_PART_OPT (e.g. -Os) replaces the optimization level of the translated parts only.
        if os.environ.get("HLDC_PART_OPT"):
            base = [os.environ["HLDC_PART_OPT"] if flag in ("-O1", "-O2", "-O3", "-Os") else flag for flag in base]
        # HLDC_PART_PGO=train instruments the parts (counters exported to ux0:data/hld-vm/pgo/ every 900 frames);
        # HLDC_PART_PGO=<dir> compiles them with that feedback.
        pgo = os.environ.get("HLDC_PART_PGO")
        if pgo == "train":
            base += ["-fprofile-generate", "-fprofile-info-section=hld_gcov", "-fprofile-update=single",
                     f"-fprofile-prefix-path={out}", f"-fprofile-dir={out / 'profiles'}"]
        elif pgo:
            base += [f"-fprofile-use={Path(pgo).resolve()}", "-fprofile-partial-training", f"-fprofile-prefix-path={out}",
                     "-Wno-missing-profile", "-Wno-coverage-mismatch"]
        running = []
        for number, part_source in enumerate(HLDC_PART_SOURCES):
            part = out / f"hldc-part-{number}.c"
            part.write_text(part_source)
            target = part.with_suffix(".obj")
            part_argv = list(base)
            # Replace vm.c's source argument (flags were appended after it).
            vm_source = next(i for i, flag in enumerate(part_argv) if flag.endswith((".c", ".C")) and not flag.startswith("-"))
            part_argv[vm_source] = str(part)
            part_argv[part_argv.index("-o") + 1] = str(target)
            part_argv[part_argv.index("-MF") + 1] = str(target.with_suffix(".d"))
            HLDC_PART_OBJECTS.append(str(target))
            running.append(subprocess.Popen(part_argv, cwd=compile_directory))
            if len(running) >= (os.cpu_count() or 4):
                if running.pop(0).wait() != 0:
                    raise RuntimeError("translated part failed to compile")
        for process in running:
            if process.wait() != 0:
                raise RuntimeError("translated part failed to compile")
    return original, str(obj)


if bbox_runtime:
    collision_header = with_bbox_cache((source / "collision.h").read_text(), compare=options.inline_bbox)
    if mask_overlap:
        collision_header = with_mask_overlap(collision_header, compare=True, reference_marker=options.verify_mask_overlap)
    elif always_split_masks:
        grid = "compare" if options.grid_mask_overlap or options.runtime_refinements else None
        collision_header = with_mask_overlap(collision_header, grid=grid)
        if options.runtime_refinements:
            collision_header = with_refinement_gate(collision_header, "useGridMaskOverlap")
    if options.grid_mask_memo:
        collision_header = with_grid_mask_memo(collision_header, checked=False, compare=True,
                                               dynamic=options.vault_mask_layers)
    if options.vault_mask_layers:
        collision_header = with_builtin_mask_overlap_trial(collision_header)
    if options.mask_tiles:
        collision_header = with_mask_tiles_header(collision_header, launch_flag="hldMaskTiles")
    (out / "collision.h").write_text(collision_header)


def platform(code):
    if os.environ.get("HLDC_DRAWSTATS") == "1" and "if (measuringSwap) finalSceneNanos += nowNanos() - start;" in code:
        code = code.replace("    uint64_t start = measuringSwap ? nowNanos() : 0;\n    int result = __real_sceGxmEndScene(",
                            "    uint64_t start = nowNanos();\n    int result = __real_sceGxmEndScene(", 1)
        code = code.replace("    if (measuringSwap) finalSceneNanos += nowNanos() - start;",
                            "    { extern uint64_t hldGxmEndNs; extern void hldSceneEnded(uint64_t); hldGxmEndNs += nowNanos() - start; hldSceneEnded(nowNanos() - start); } "
                            "if (measuringSwap) finalSceneNanos += nowNanos() - start;", 1)
    code = replace_once(code, "#include <stdio.h>",
                        '#include <stdio.h>\n#include <stdlib.h>\n#include <runner.h>\n#include "vita/boot_progress.h"')
    swap_worker = (proof / "swap-worker.inc").read_text()
    if options.playable:
        swap_worker = replace_once(swap_worker, '''    FILE* flag = fopen("ux0:data/hld-vm/async-present-ab", "r");
    if (flag == NULL) return;
    fclose(flag);''', '''    FILE* flag = fopen("ux0:data/hld-vm/serial-present", "r");
    if (flag != NULL) {
        fclose(flag);
        return;
    }''')
        swap_worker = replace_once(swap_worker, 'if (swapStop) break;',
                                   'if (__atomic_load_n(&swapStop, __ATOMIC_ACQUIRE)) break;')
        swap_worker = replace_once(swap_worker, '    swapStop = true;',
                                   '    __atomic_store_n(&swapStop, true, __ATOMIC_RELEASE);')
    code = replace_once(code, "static bool rearHealPressed(void) {",
                        swap_worker + "\nstatic bool rearHealPressed(void) {")
    if options.active_cases:
        code = replace_once(code, "#include <stdio.h>", "#include <stdio.h>\n#include <psp2/power.h>")
        anchor = "static bool rearHealPressed(void) {"
        code = replace_once(code, anchor, (proof / "gpu-clock-trial.inc").read_text() + "\n" + anchor)
    code = replace_once(code, '    logInfo("Native Vita display backend ready\\n");',
                        '    openSwapWorker();\n    logInfo("Native Vita display backend ready\\n");')
    code = replace_once(code, 'void platformExit(void) {\n',
                        'void platformExit(void) {\n    closeSwapWorker();\n')
    if options.active_cases:
        anchor = "void platformExit(void) {\n    closeSwapWorker();"
        code = replace_once(code, anchor, anchor + "\n    hldRestoreGpuClock(-1);")
    if options.overlap_draw_list:
        code = replace_once(code, 'static bool rearHealPressed(void) {',
                            (proof / "draw-list-worker.inc").read_text() + '\nstatic bool rearHealPressed(void) {')
        code = replace_once(code, '    openSwapWorker();', '    openSwapWorker();\n    openDrawListWorker();')
        code = replace_once(code, 'void platformExit(void) {\n    closeSwapWorker();',
                            'void platformExit(void) {\n    closeDrawListWorker();\n    closeSwapWorker();')
    if options.overlap_audio or options.audio_affinity or options.deferred_audio:
        audio_worker = (proof / "audio-worker.inc").read_text()
        if options.deferred_audio:
            audio_worker = with_deferred_audio_worker(audio_worker)
        if uses_sprite_cache:
            audio_worker = replace_once(audio_worker, " || ((frame - 1) / 180) % 2 == 0", "")
            audio_worker = replace_once(audio_worker, "Vita audio worker active, alternating 180-frame windows",
                                        "Vita audio worker enabled after frame 180")
        if options.audio_affinity:
            anchor = 'bool hldQueueAudioUpdate(Runner* runner, float dt) {'
            audio_worker = replace_once(audio_worker, anchor, (proof / "audio-affinity.inc").read_text() + '\n' + anchor)
            audio_worker = replace_once(audio_worker, ' || ((frame - 1) / 180) % 2 == 0) return false;',
                                        ') return false;\n    if (frame % 180 == 1) switchAudioAffinity(((frame - 1) / 180) % 2 != 0);')
            audio_worker = replace_once(audio_worker, '    if (audioUpdateWorker >= 0) {\n',
                                        '    if (audioUpdateWorker >= 0) {\n        switchAudioAffinity(false);\n')
            audio_worker = replace_once(audio_worker, "Vita audio worker active, alternating 180-frame windows",
                                        "Vita audio worker active, alternating CPU affinity every 180 frames")
        code = replace_once(code, 'static bool rearHealPressed(void) {',
                            audio_worker + '\nstatic bool rearHealPressed(void) {')
        code = replace_once(code, '    openSwapWorker();', '    openSwapWorker();\n    openAudioUpdateWorker();')
        code = replace_once(code, 'void platformExit(void) {\n    closeSwapWorker();',
                            'void platformExit(void) {\n    closeAudioUpdateWorker();\n    closeSwapWorker();')
    code = replace_once(code, 'void platformSwapBuffers(void) {', 'static void swapNow(void) {')
    code = replace_once(code, '    static unsigned renderedFrames;\n    measuringSwap', '    measuringSwap')
    code = replace_once(code, '    measuringSwap = false;\n    if (++renderedFrames',
                        '    measuringSwap = false;\n}\n\nstatic void reportFinishedSwap(void) {\n'
                        '    VitaBootProgress_gamePresented();\n    static unsigned renderedFrames;\n    if (++renderedFrames')
    if not options.playable or verify_runtime or sparse_rendering:
        capture_frame = "630" if options.transparent_composite or sparse_rendering else "300"
        late_capture = ""
        if options.runtime_refinements:
            captures = (450, 720, 1080) if options.combat_route or options.combined_performance else (720, 1080)
            late_capture = "".join(
                f'    if (swapFrame == {frame}) exact = sceIoRemove("ux0:data/hld-vm/capture-exact-{frame}") >= 0;\n'
                for frame in captures
            )
        code = replace_once(code, '''    if (++frames != 90) return;
    frames = 0;
    const char* request = "ux0:data/hld-vm/capture-request";
    if (sceIoRemove(request) < 0) return;''',
        f'    bool exact = swapFrame == {capture_frame} && sceIoRemove("ux0:data/hld-vm/capture-exact-{capture_frame}") >= 0;\n' +
        late_capture +
        '''    if (++frames == 90) {
        frames = 0;
        if (!exact && sceIoRemove("ux0:data/hld-vm/capture-request") < 0) return;
    } else if (!exact) {
        return;
    }''')
        code = replace_once(code, 'logInfo("Vita frame captured\\n");',
                            'logInfo("Vita frame captured on runner frame %d\\n", swapFrame);')
    if verify_runtime or sparse_rendering:
        code = with_compressed_capture(code)
    extension = r"""
void platformSwapBuffers(void) {
    hldWaitForSwap();
    swapFrame = activeRunner != nullptr ? activeRunner->frameCount : -1;
    if (swapWorker < 0 || activeRunner == nullptr) {
        swapNow();
        reportFinishedSwap();
        return;
    }
    const int frame = activeRunner->frameCount;
    if (frame > 0 && frame % 90 == 0) {
        logInfo("Vita async frames=%d submitted=%u waited=%u barrier_ms=%.3f\n",
            frame, swapSubmitted, swapWaited, swapWaitNanos / (90.0 * 1e6));
        swapSubmitted = swapWaited = 0;
        swapWaitNanos = 0;
    }
    if (frame <= 180 || ((frame - 1) / 180) % 2 == 0) {
        swapNow();
        reportFinishedSwap();
        return;
    }
    hldSwapPending = 1;
    int result = sceKernelSignalSema(swapStart, 1);
    if (result < 0) {
        hldSwapPending = 0;
        logWarn("Vita async submission failed: %08x\n", (unsigned)result);
        swapNow();
        reportFinishedSwap();
        return;
    }
    ++swapSubmitted;
}
"""
    if version != "00.51":
        extension = replace_once(extension, "frame <= 180 || ((frame - 1) / 180) % 2 == 0", "frame <= 180")
        code = replace_once(code, "Vita async presentation test active, alternating 180-frame windows",
                            "Vita threaded display submission enabled" if options.playable else
                            "Vita async presentation test active, enabled after frame 180")
    if options.submission_rings:
        code = with_submission_trace(code)
    if options.sprite_worker:
        code = with_sprite_worker(code)
    if options.graphics_commands:
        code = with_sprite_worker(code).replace("native-sprite-worker", "native-graphics-commands")
        if options.graphics_stream:
            code = with_graphics_stream_lifecycle(code)
        if options.graphics_render_tail:
            code, extension = with_render_tail_platform(code, extension)
        extension += "\n" + graphics_command_source(framebuffer_shadow=options.graphics_framebuffer,
                                                     render_tail=options.graphics_render_tail,
                                                     draw_state=options.draw_state_packets,
                                                     fence_apis=api if options.expanded_draw_packets else None,
                                                     matrix_arrays=options.matrix_array_packets,
                                                     stream=options.graphics_stream)
    if options.bounded_round or options.draw_order_repair or options.sprite_worker or options.consolidated_runtime:
        start = "static void pollVitaGamepad(RunnerGamepadState* pads) {"
        code = replace_once(code, start, (proof / "vita-test-route.inc").read_text() + "\n" + start)
        connected = "    bool connected = sceCtrlPeekBufferPositive(0, &pad, 1) > 0;"
        code = replace_once(code, connected, connected + "\n    const bool replay = applyTestRoute(&pad);\n"
                            "    connected |= replay;")
        code = replace_once(code, "if (connected && rearHealPressed())",
                            "if (connected && !replay && rearHealPressed())")
        active = "    activeRunner = runner;"
        code = replace_once(code, active, active + "\n    loadTestRoute();")
    if HLDC_TRANSLATE:
        # Vita controls: the handheld has no L1/R1 (its shoulders are the triggers), so D-pad left/right are the
        # controller's L1/R1 everywhere: heal / special in play, page switching on the map and inventory. The game
        # reads D-pad left/right only for developer cheats, so nothing else changes.
        code = replace_once(code, "SCE_CTRL_L1, 0, SCE_CTRL_LTRIGGER", "SCE_CTRL_L1, SCE_CTRL_R1, SCE_CTRL_LTRIGGER")
        heal = "rearHealPressed()) buttons |= SCE_CTRL_L1;"
        code = replace_once(code, heal, heal + " { extern volatile int hldDpadShoulders; if (connected) { "
                            "if (buttons & SCE_CTRL_LEFT) buttons |= SCE_CTRL_L1; if (buttons & SCE_CTRL_RIGHT) buttons |= SCE_CTRL_R1; } }")
    if HLDC_TRANSLATE:
        # The capture markers were polled on the card in normal play (sceIoRemove at frames 300/450/720/1080 and every
        # 90 frames, ~80-100 ms each on the present path). Look once at startup; with no capture file then, skip them.
        first = "static void captureVitaFrame(void) {\n    static unsigned frames;\n"
        if first in code:
            code = "#include <psp2/io/stat.h>\n" + code
            code = replace_once(code, first, first +
                '    static int hldCaptureFiles = -1;\n'
                '    if (hldCaptureFiles < 0) {\n'
                '        static const char* const names[] = {"capture-request", "capture-exact-300", "capture-exact-450", "capture-exact-720",\n'
                '                                            "capture-exact-1080", "capture-burst"};\n'
                '        hldCaptureFiles = 0;\n'
                '        for (unsigned i = 0; i < sizeof(names) / sizeof(*names); ++i) {\n'
                '            char path[64]; snprintf(path, sizeof path, "ux0:data/hld-vm/%s", names[i]);\n'
                '            SceIoStat stat; if (sceIoGetstat(path, &stat) >= 0) hldCaptureFiles = 1;\n'
                '        }\n'
                '    }\n'
                '    if (!hldCaptureFiles) return;\n')
    if HLDC_TRANSLATE or os.environ.get("HLDC_MEMLOG"):
        # Test mode: ux0:data/hld-vm/capture-burst ("<start> <count>") saves each of those frames to frame-<n>.rgba.
        last = '    if (swapFrame == 1080) exact = sceIoRemove("ux0:data/hld-vm/capture-exact-1080") >= 0;'
        if last in code:
            code = replace_once(code, last, last + ' static int burstStart = -2, burstCount = 0; if (burstStart == -2) { burstStart = -1; '
                                'FILE* burstFile = fopen("ux0:data/hld-vm/capture-burst", "r"); if (burstFile != NULL) { if (fscanf(burstFile, "%d %d", '
                                '&burstStart, &burstCount) != 2) burstStart = -1; fclose(burstFile); } } const bool burst = burstStart >= 0 && '
                                'swapFrame >= burstStart && swapFrame < burstStart + burstCount; if (burst) exact = true;')
            code = replace_once(code, '    const char* ready = "ux0:data/hld-vm/frame.rgba";',
                                '    char readyName[64]; snprintf(readyName, sizeof(readyName), burst ? "ux0:data/hld-vm/frame-%d.rgba" : '
                                '"ux0:data/hld-vm/frame.rgba", swapFrame); const char* ready = readyName;')
    return code + extension


PGO_PART_EXPORT = r"""
// Translated-part compiler feedback: every gcov unit in the hld_gcov section is written to ux0:data/hld-vm/pgo/.
struct gcov_info;
extern const struct gcov_info* const __start_hld_gcov[];
extern const struct gcov_info* const __stop_hld_gcov[];
extern void __gcov_info_to_gcda(const struct gcov_info*, void (*filename)(const char*, void*),
                               void (*dump)(const void*, unsigned, void*), void* (*allocate)(unsigned, void*), void*);
typedef struct { FILE* file; unsigned bytes; void* blocks[256]; unsigned blockCount; } HldPgoOut;
static void hldPgoFilename(const char* name, void* opaque) {
    HldPgoOut* out = opaque;
    const char* base = strrchr(name, '/');
    base = base != NULL ? base + 1 : name;
    char path[512];
    snprintf(path, sizeof(path), "ux0:data/hld-vm/pgo/%s", base);
    out->file = fopen(path, "wb");
    static int reported;
    if (reported++ < 4) logInfo("PGO unit %s -> %s\n", name, path);
}
static void hldPgoDump(const void* bytes, unsigned count, void* opaque) {
    HldPgoOut* out = opaque;
    if (out->file != NULL) out->bytes += (unsigned) fwrite(bytes, 1, count, out->file);
}
static void* hldPgoAllocate(unsigned bytes, void* opaque) {
    HldPgoOut* out = opaque;
    void* block = malloc(bytes);
    if (out->blockCount < 256) out->blocks[out->blockCount++] = block;
    return block;
}
int sceIoMkdir(const char*, int);
void hldPgoFrame(Runner* runner) {
    if (runner->frameCount == 0 || runner->frameCount % 900 != 0) return;
    sceIoMkdir("ux0:data/hld-vm/pgo", 0777);
    unsigned units = 0, bytes = 0;
    for (const struct gcov_info* const* info = __start_hld_gcov; info < __stop_hld_gcov; ++info) {
        HldPgoOut out = {0};
        __gcov_info_to_gcda(*info, hldPgoFilename, hldPgoDump, hldPgoAllocate, &out);
        if (out.file != NULL) fclose(out.file);
        for (unsigned i = 0; i < out.blockCount; ++i) free(out.blocks[i]);
        ++units; bytes += out.bytes;
    }
    logInfo("PGO exported frame=%d units=%u bytes=%u\n", runner->frameCount, units, bytes);
}
"""


def main(code):
    if os.environ.get("HLDC_FBO_SCENES"):
        # Scenes per frame for framebuffer render targets (vitaGL allows up to 8): fewer waits when a surface is
        # rendered to many times per frame.
        call = "    vglSetupRenderTargetScenesNum(4, 4);"
        assert code.count(call) == 1
        code = code.replace(call, f"    vglSetupRenderTargetScenesNum(4, {int(os.environ['HLDC_FBO_SCENES'])});")
    if os.environ.get("HLDC_GXM_RINGS") == "1":
        # Larger sceGxm ring and parameter buffers: the CPU blocks in scene begin/end when one fills before the GPU
        # catches up.
        call = "    vglSetParamBufferSize(6 * 1024 * 1024);"
        assert code.count(call) == 1
        code = code.replace(call, "    vglSetParamBufferSize(16 * 1024 * 1024); vglSetVDMBufferSize(512 * 1024); "
                            "vglSetVertexBufferSize(4 * 1024 * 1024); vglSetFragmentBufferSize(2 * 1024 * 1024); "
                            "vglSetUSSEBufferSize(64 * 1024);")
    if os.environ.get("HLDC_KEEP_CLOCK") == "1":
        # Leave the CPU/bus/GPU clocks to the system (e.g. an overclock plugin) instead of setting 444/222/222.
        for call in ("    scePowerSetArmClockFrequency(444);", "    scePowerSetBusClockFrequency(222);",
                     "    scePowerSetGpuClockFrequency(222);"):
            assert code.count(call) == 1, call
            code = code.replace(call, "    /* clock left to the system */")
    return main_original(code)


def main_original(code):
    code = replace_once(code, "HLD 2016 00.49 starting", f"HLD 2016 {version} starting")
    if options.compact_rvalues or options.deferred_audio:
        code = replace_once(code, '#include <loop.h>', '#include <loop.h>\n#include <rvalue.h>\n#include <int_rvalue_hashmap.h>')
        code = replace_once(code, '    mainThread = sceKernelGetThreadId();',
                            '    logInfo("Vita compact values bytes=%u map_entry=%u\\n", (unsigned)sizeof(RValue), '
                            '(unsigned)sizeof(IntRValueEntry));\n    mainThread = sceKernelGetThreadId();')
    if options.runtime_refinements:
        code = with_refinement_marker(code)
    if options.submission_rings:
        code = with_submission_rings(code)
    if options.bounded_round:
        code = replace_once(code, "bool hldRefinedRuntime;", "bool hldRefinedRuntime;\nbool hldBoundedRound;")
        anchor = "    mainThread = sceKernelGetThreadId();"
        code = replace_once(code, anchor, r'''    FILE* roundingFlag = fopen("ux0:data/hld-vm/native-bounded-round", "r");
    hldBoundedRound = roundingFlag != NULL;
    if (roundingFlag != NULL) fclose(roundingFlag);
    logInfo("Vita bounded rounding enabled=%d\n", (int)hldBoundedRound);
''' + anchor)
    if options.mask_line_tiles:
        code = replace_once(code, "bool hldRefinedRuntime;", "bool hldRefinedRuntime;\nbool hldMaskLineTiles;")
        anchor = "    mainThread = sceKernelGetThreadId();"
        code = replace_once(code, anchor, r'''    FILE* maskLineTilesFlag = fopen("ux0:data/hld-vm/mask-line-tiles", "r");
    hldMaskLineTiles = maskLineTilesFlag != NULL;
    if (maskLineTilesFlag != NULL) fclose(maskLineTilesFlag);
    logInfo("Vita mask tile lines enabled=%d\n", (int)hldMaskLineTiles);
''' + anchor)
    if options.draw_order_repair:
        code = replace_once(code, "bool hldRefinedRuntime;", "bool hldRefinedRuntime;\nbool hldDrawOrderRepair;")
        anchor = "    mainThread = sceKernelGetThreadId();"
        code = replace_once(code, anchor, r'''    FILE* sortFlag = fopen("ux0:data/hld-vm/native-draw-order-repair", "r");
    hldDrawOrderRepair = sortFlag != NULL;
    if (sortFlag != NULL) fclose(sortFlag);
''' + anchor)
    if options.consolidated_runtime:
        code = replace_once(code, "bool hldRefinedRuntime;", "bool hldRefinedRuntime;\nbool hldConsolidatedRuntime;")
        anchor = "    mainThread = sceKernelGetThreadId();"
        code = replace_once(code, anchor, r'''    FILE* runtimeFlag = fopen("ux0:data/hld-vm/native-consolidated-runtime", "r");
    hldConsolidatedRuntime = runtimeFlag != NULL;
    if (runtimeFlag != NULL) fclose(runtimeFlag);
''' + anchor)
    if options.scenery_draw_lists:
        code = replace_once(code, "bool hldRefinedRuntime;", "bool hldRefinedRuntime;\nbool hldSceneryDrawLists;")
        anchor = "    mainThread = sceKernelGetThreadId();"
        code = replace_once(code, anchor, r'''    FILE* drawListsFlag = fopen("ux0:data/hld-vm/native-scenery-draw-lists", "r");
    hldSceneryDrawLists = drawListsFlag != NULL;
    if (drawListsFlag != NULL) fclose(drawListsFlag);
''' + anchor)
    if options.lazy_audio_length:
        code = replace_once(code, "bool hldRefinedRuntime;", "bool hldRefinedRuntime;\nbool hldDeferLoopLength;")
        anchor = "    mainThread = sceKernelGetThreadId();"
        code = replace_once(code, anchor, r'''    FILE* lengthFlag = fopen("ux0:data/hld-vm/defer-loop-length", "r");
    hldDeferLoopLength = lengthFlag != NULL;
    if (lengthFlag != NULL) fclose(lengthFlag);
    logInfo("Vita loop duration deferred=%d\n", (int)hldDeferLoopLength);
''' + anchor)
    if options.graphics_render_tail:
        code = replace_once(code, "bool hldRefinedRuntime;", "bool hldRefinedRuntime;\nbool hldRenderTailEnabled;")
        anchor = "    mainThread = sceKernelGetThreadId();"
        code = replace_once(code, anchor, r'''    FILE* tailFlag = fopen("ux0:data/hld-vm/native-graphics-render-tail", "r");
    hldRenderTailEnabled = tailFlag != NULL;
    if (tailFlag != NULL) fclose(tailFlag);
''' + anchor)
    if options.direct_instance_ids:
        code = replace_once(code, "bool hldRefinedRuntime;",
                            "bool hldRefinedRuntime;\nbool hldDirectInstanceIdsEnabled;")
        anchor = "    mainThread = sceKernelGetThreadId();"
        code = replace_once(code, anchor, r'''    FILE* idFlag = fopen("ux0:data/hld-vm/native-direct-instance-ids", "r");
    hldDirectInstanceIdsEnabled = idFlag != NULL;
    if (idFlag != NULL) fclose(idFlag);
''' + anchor)
    if options.shared_instance_fields:
        code = replace_once(code, "bool hldRefinedRuntime;",
                            "bool hldRefinedRuntime;\nbool hldSharedFieldsEnabled;")
        anchor = "    mainThread = sceKernelGetThreadId();"
        code = replace_once(code, anchor, r'''    FILE* fieldsFlag = fopen("ux0:data/hld-vm/shared-instance-fields", "r");
    hldSharedFieldsEnabled = fieldsFlag != NULL;
    if (fieldsFlag != NULL) fclose(fieldsFlag);
    logInfo("Vita shared instance fields enabled=%d\n", (int)hldSharedFieldsEnabled);
''' + anchor)
    if options.memory_ambient:
        code = replace_once(code, "bool hldRefinedRuntime;", "bool hldRefinedRuntime;\nbool hldMemoryAmbient;")
        anchor = "    mainThread = sceKernelGetThreadId();"
        code = replace_once(code, anchor, r'''    FILE* ambientFlag = fopen("ux0:data/hld-vm/memory-ambient", "r");
    hldMemoryAmbient = ambientFlag != NULL;
    if (ambientFlag != NULL) fclose(ambientFlag);
    logInfo("Vita memory ambience enabled=%d\n", (int)hldMemoryAmbient);
''' + anchor)
    if options.reduced_vm_work:
        code = replace_once(code, "bool hldRefinedRuntime;", "bool hldRefinedRuntime;\nbool hldReducedVMWork;")
        anchor = "    mainThread = sceKernelGetThreadId();"
        code = replace_once(code, anchor, r'''    FILE* workFlag = fopen("ux0:data/hld-vm/reduced-vm-work", "r");
    hldReducedVMWork = workFlag != NULL;
    if (workFlag != NULL) fclose(workFlag);
    logInfo("Vita reduced VM work enabled=%d\n", (int)hldReducedVMWork);
''' + anchor)
    if options.prefetch_ambience:
        code = replace_once(code, "bool hldRefinedRuntime;", "bool hldRefinedRuntime;\nbool hldPrefetchAmbience;")
        anchor = "    mainThread = sceKernelGetThreadId();"
        code = replace_once(code, anchor, r'''    FILE* ambienceFlag = fopen("ux0:data/hld-vm/prefetch-ambience", "r");
    hldPrefetchAmbience = ambienceFlag != NULL;
    if (ambienceFlag != NULL) fclose(ambienceFlag);
    logInfo("Vita ambience prefetch enabled=%d\n", (int)hldPrefetchAmbience);
''' + anchor)
    if options.frame_headroom:
        code = replace_once(code, "bool hldRefinedRuntime;", "bool hldRefinedRuntime;\nbool hldHigherPacingLimit;")
        anchor = "    mainThread = sceKernelGetThreadId();"
        code = replace_once(code, anchor, r'''    FILE* headroomFlag = fopen("ux0:data/hld-vm/benchmark-60-limit", "r");
    hldHigherPacingLimit = headroomFlag != NULL;
    if (headroomFlag != NULL) fclose(headroomFlag);
    logInfo("Vita higher pacing benchmark enabled=%d\n", (int)hldHigherPacingLimit);
''' + anchor)
    if options.scenery_cursor:
        code = replace_once(code, "bool hldRefinedRuntime;", "bool hldRefinedRuntime;\nbool hldSceneryCursor;")
        anchor = "    mainThread = sceKernelGetThreadId();"
        code = replace_once(code, anchor, r'''    FILE* sceneryCursorFlag = fopen("ux0:data/hld-vm/scenery-cursor", "r");
    hldSceneryCursor = sceneryCursorFlag != NULL;
    if (sceneryCursorFlag != NULL) fclose(sceneryCursorFlag);
    logInfo("Vita scenery cursor enabled=%d\n", (int)hldSceneryCursor);
''' + anchor)
    if options.eventless_case:
        code = replace_once(code, "bool hldRefinedRuntime;", "bool hldRefinedRuntime;\nbool hldEventlessCase;")
        anchor = "    mainThread = sceKernelGetThreadId();"
        code = replace_once(code, anchor, r'''    FILE* eventlessFlag = fopen("ux0:data/hld-vm/eventless-case", "r");
    hldEventlessCase = eventlessFlag != NULL;
    if (eventlessFlag != NULL) fclose(eventlessFlag);
    logInfo("Vita eventless linked cases enabled=%d\n", (int)hldEventlessCase);
''' + anchor)
    if options.native_scenery_body:
        code = replace_once(code, "bool hldRefinedRuntime;", "bool hldRefinedRuntime;\nbool hldNativeSceneryBody;")
        anchor = "    mainThread = sceKernelGetThreadId();"
        code = replace_once(code, anchor, r'''    FILE* nativeBodyFlag = fopen("ux0:data/hld-vm/native-scenery-body", "r");
    hldNativeSceneryBody = nativeBodyFlag != NULL;
    if (nativeBodyFlag != NULL) fclose(nativeBodyFlag);
    logInfo("Vita native scenery body enabled=%d\n", (int)hldNativeSceneryBody);
''' + anchor)
    if options.compact_vm_code:
        code = replace_once(code, "bool hldRefinedRuntime;", "bool hldRefinedRuntime;\nbool hldCompactVM;")
        anchor = "    mainThread = sceKernelGetThreadId();"
        code = replace_once(code, anchor, r'''    FILE* compactVMFlag = fopen("ux0:data/hld-vm/compact-vm-code", "r");
    hldCompactVM = compactVMFlag != NULL;
    if (compactVMFlag != NULL) fclose(compactVMFlag);
    logInfo("Vita compact VM code enabled=%d\n", (int)hldCompactVM);
''' + anchor)
    if options.scene_queries:
        code = replace_once(code, "bool hldRefinedRuntime;", "bool hldRefinedRuntime;\nbool hldSceneQueries;")
        anchor = "    mainThread = sceKernelGetThreadId();"
        code = replace_once(code, anchor, r'''    FILE* sceneQueriesFlag = fopen("ux0:data/hld-vm/scene-queries", "r");
    hldSceneQueries = sceneQueriesFlag != NULL;
    if (sceneQueriesFlag != NULL) fclose(sceneQueriesFlag);
    logInfo("Vita scene queries enabled=%d\n", (int)hldSceneQueries);
''' + anchor)
    if options.grid_mask_memo:
        code = replace_once(code, "bool hldRefinedRuntime;", "bool hldRefinedRuntime;\nbool hldGridMaskMemo;")
        anchor = "    mainThread = sceKernelGetThreadId();"
        code = replace_once(code, anchor, r'''    FILE* maskMemoFlag = fopen("ux0:data/hld-vm/grid-mask-memo", "r");
    hldGridMaskMemo = maskMemoFlag != NULL;
    if (maskMemoFlag != NULL) fclose(maskMemoFlag);
    logInfo("Vita grid mask memo enabled=%d\n", (int)hldGridMaskMemo);
''' + anchor)
    if options.vault_mask_layers:
        code = replace_once(code, "bool hldRefinedRuntime;", "bool hldRefinedRuntime;\nbool hldBuiltinMaskOverlap;")
        anchor = "    mainThread = sceKernelGetThreadId();"
        code = replace_once(code, anchor, r'''    FILE* overlapFlag = fopen("ux0:data/hld-vm/builtin-mask-overlap", "r");
    hldBuiltinMaskOverlap = overlapFlag != NULL;
    if (overlapFlag != NULL) fclose(overlapFlag);
    logInfo("Vita builtin split masks enabled=%d\n", (int)hldBuiltinMaskOverlap);
''' + anchor)
    if options.shape_candidate_cells:
        code = replace_once(code, "bool hldRefinedRuntime;", "bool hldRefinedRuntime;\nbool hldShapeCandidateCells;")
        anchor = "    mainThread = sceKernelGetThreadId();"
        code = replace_once(code, anchor, r'''    FILE* shapeCellsFlag = fopen("ux0:data/hld-vm/shape-candidate-cells", "r");
    hldShapeCandidateCells = shapeCellsFlag != NULL;
    if (shapeCellsFlag != NULL) fclose(shapeCellsFlag);
    logInfo("Vita indexed shape cells enabled=%d\n", (int)hldShapeCandidateCells);
''' + anchor)
    if options.mask_tiles:
        code = replace_once(code, "bool hldRefinedRuntime;", "bool hldRefinedRuntime;\nbool hldMaskTiles;")
        anchor = "    mainThread = sceKernelGetThreadId();"
        code = replace_once(code, anchor, r'''    FILE* maskTilesFlag = fopen("ux0:data/hld-vm/mask-tiles", "r");
    hldMaskTiles = maskTilesFlag != NULL;
    if (maskTilesFlag != NULL) fclose(maskTilesFlag);
    logInfo("Vita mask tile index enabled=%d\n", (int)hldMaskTiles);
''' + anchor)
    if options.mine_entry:
        code = replace_once(code, "bool hldRefinedRuntime;", "bool hldRefinedRuntime;\nbool hldMineEntry;")
        anchor = "    mainThread = sceKernelGetThreadId();"
        code = replace_once(code, anchor, r'''    FILE* mineEntryFlag = fopen("ux0:data/hld-vm/mine-entry", "r");
    hldMineEntry = mineEntryFlag != NULL;
    if (mineEntryFlag != NULL) fclose(mineEntryFlag);
    logInfo("Vita dormant mine entries enabled=%d\n", (int)hldMineEntry);
''' + anchor)
    if options.archive_audio_reads:
        code = replace_once(code, "bool hldRefinedRuntime;", "bool hldRefinedRuntime;\nbool hldArchiveAudioReads;")
        anchor = "    mainThread = sceKernelGetThreadId();"
        code = replace_once(code, anchor, r'''    FILE* archiveAudioFlag = fopen("ux0:data/hld-vm/archive-audio-reads", "r");
    hldArchiveAudioReads = archiveAudioFlag != NULL;
    if (archiveAudioFlag != NULL) fclose(archiveAudioFlag);
    logInfo("Vita archive audio reads enabled=%d\n", (int)hldArchiveAudioReads);
''' + anchor)
    if options.quiet_shadow_log:
        code = replace_once(code, "bool hldRefinedRuntime;", "bool hldRefinedRuntime;\nbool hldQuietShadowLog;")
        anchor = "    mainThread = sceKernelGetThreadId();"
        code = replace_once(code, anchor, r'''    FILE* quietShadowFlag = fopen("ux0:data/hld-vm/quiet-shadow-log", "r");
    hldQuietShadowLog = quietShadowFlag != NULL;
    if (quietShadowFlag != NULL) fclose(quietShadowFlag);
    logInfo("Vita quiet shadow log enabled=%d\n", (int)hldQuietShadowLog);
''' + anchor)
    if options.ordered_line_candidates:
        code = replace_once(code, "bool hldRefinedRuntime;", "bool hldRefinedRuntime;\nbool hldOrderedLineCandidates;")
        anchor = "    mainThread = sceKernelGetThreadId();"
        code = replace_once(code, anchor, r'''    FILE* orderedLineFlag = fopen("ux0:data/hld-vm/ordered-line-candidates", "r");
    hldOrderedLineCandidates = orderedLineFlag != NULL;
    if (orderedLineFlag != NULL) fclose(orderedLineFlag);
    logInfo("Vita ordered line candidates enabled=%d\n", (int)hldOrderedLineCandidates);
''' + anchor)
    if options.line_chunks:
        code = replace_once(code, "bool hldRefinedRuntime;", "bool hldRefinedRuntime;\nbool hldLineChunks;")
        anchor = "    mainThread = sceKernelGetThreadId();"
        code = replace_once(code, anchor, r'''    FILE* lineChunksFlag = fopen("ux0:data/hld-vm/line-chunks", "r");
    hldLineChunks = lineChunksFlag != NULL;
    if (lineChunksFlag != NULL) fclose(lineChunksFlag);
    logInfo("Vita line chunks enabled=%d\n", (int)hldLineChunks);
''' + anchor)
    if options.native_line_script:
        code = replace_once(code, "bool hldRefinedRuntime;", "bool hldRefinedRuntime;\nbool hldNativeLineScript;")
        anchor = "    mainThread = sceKernelGetThreadId();"
        code = replace_once(code, anchor, r'''    FILE* nativeLineScriptFlag = fopen("ux0:data/hld-vm/native-line-script", "r");
    hldNativeLineScript = nativeLineScriptFlag != NULL;
    if (nativeLineScriptFlag != NULL) fclose(nativeLineScriptFlag);
    logInfo("Vita native line script enabled=%d\n", (int)hldNativeLineScript);
''' + anchor)
    if options.palette_tail:
        code = replace_once(code, "bool hldRefinedRuntime;", "bool hldRefinedRuntime;\nbool hldPaletteTail;")
        anchor = "    mainThread = sceKernelGetThreadId();"
        code = replace_once(code, anchor, r'''    FILE* paletteTailFlag = fopen("ux0:data/hld-vm/palette-tail", "r");
    hldPaletteTail = paletteTailFlag != NULL;
    if (paletteTailFlag != NULL) fclose(paletteTailFlag);
    logInfo("Vita palette upload enabled=%d\n", (int)hldPaletteTail);
''' + anchor)
    if options.motion_repeat:
        code = replace_once(code, "bool hldRefinedRuntime;", "bool hldRefinedRuntime;\nbool hldMotionRepeat;")
        anchor = "    mainThread = sceKernelGetThreadId();"
        code = replace_once(code, anchor, r'''    FILE* motionRepeatFlag = fopen("ux0:data/hld-vm/motion-repeat", "r");
    hldMotionRepeat = motionRepeatFlag != NULL;
    if (motionRepeatFlag != NULL) fclose(motionRepeatFlag);
    logInfo("Vita movement repeats enabled=%d\n", (int)hldMotionRepeat);
''' + anchor)
    if options.scenery_skip:
        code = replace_once(code, "bool hldRefinedRuntime;", "bool hldRefinedRuntime;\nbool hldScenerySkip;")
        anchor = "    mainThread = sceKernelGetThreadId();"
        code = replace_once(code, anchor, r'''    FILE* scenerySkipFlag = fopen("ux0:data/hld-vm/scenery-skip", "r");
    hldScenerySkip = scenerySkipFlag != NULL;
    if (scenerySkipFlag != NULL) fclose(scenerySkipFlag);
    logInfo("Vita scenery skips enabled=%d\n", (int)hldScenerySkip);
''' + anchor)
    if options.scenery_guard:
        code = replace_once(code, "bool hldRefinedRuntime;", "bool hldRefinedRuntime;\nbool hldSceneryGuard;")
        anchor = "    mainThread = sceKernelGetThreadId();"
        code = replace_once(code, anchor, r'''    FILE* sceneryGuardFlag = fopen("ux0:data/hld-vm/scenery-guard", "r");
    hldSceneryGuard = sceneryGuardFlag != NULL;
    if (sceneryGuardFlag != NULL) fclose(sceneryGuardFlag);
    logInfo("Vita scenery guard enabled=%d\n", (int)hldSceneryGuard);
''' + anchor)
    if options.hologram_arrays:
        code = replace_once(code, "bool hldRefinedRuntime;", "bool hldRefinedRuntime;\nbool hldHologramArrays;")
        anchor = "    mainThread = sceKernelGetThreadId();"
        code = replace_once(code, anchor, r'''    FILE* hologramArraysFlag = fopen("ux0:data/hld-vm/hologram-arrays", "r");
    hldHologramArrays = hologramArraysFlag != NULL;
    if (hologramArraysFlag != NULL) fclose(hologramArraysFlag);
    logInfo("Vita hologram arrays enabled=%d\n", (int)hldHologramArrays);
''' + anchor)
    if options.hologram_args:
        code = replace_once(code, "bool hldRefinedRuntime;", "bool hldRefinedRuntime;\nbool hldHologramArgs;")
        anchor = "    mainThread = sceKernelGetThreadId();"
        code = replace_once(code, anchor, r'''    FILE* hologramArgsFlag = fopen("ux0:data/hld-vm/hologram-args", "r");
    hldHologramArgs = hologramArgsFlag != NULL;
    if (hologramArgsFlag != NULL) fclose(hologramArgsFlag);
    logInfo("Vita hologram arguments enabled=%d\n", (int)hldHologramArgs);
''' + anchor)
    if options.collision_target_views:
        code = replace_once(code, "bool hldRefinedRuntime;", "bool hldRefinedRuntime;\nbool hldCollisionTargetViews;")
        anchor = "    mainThread = sceKernelGetThreadId();"
        code = replace_once(code, anchor, r'''    FILE* collisionTargetViewsFlag = fopen("ux0:data/hld-vm/collision-target-views", "r");
    hldCollisionTargetViews = collisionTargetViewsFlag != NULL;
    if (collisionTargetViewsFlag != NULL) fclose(collisionTargetViewsFlag);
    logInfo("Vita collision target views enabled=%d\n", (int)hldCollisionTargetViews);
''' + anchor)
    if options.room_image_cache:
        code = replace_once(code, "bool hldRefinedRuntime;", "bool hldRefinedRuntime;\nbool hldRoomImageCache;")
        anchor = "    mainThread = sceKernelGetThreadId();"
        code = replace_once(code, anchor, r'''    FILE* roomImageCacheFlag = fopen("ux0:data/hld-vm/room-image-cache", "r");
    hldRoomImageCache = roomImageCacheFlag != NULL;
    if (roomImageCacheFlag != NULL) fclose(roomImageCacheFlag);
    logInfo("Vita room image cache enabled=%d\n", (int)hldRoomImageCache);
''' + anchor)
    if options.persistent_terrain_bounds:
        code = replace_once(code, "bool hldRefinedRuntime;", "bool hldRefinedRuntime;\nbool hldPersistentTerrainBounds;")
        anchor = "    mainThread = sceKernelGetThreadId();"
        code = replace_once(code, anchor, r'''    FILE* terrainBoundsFlag = fopen("ux0:data/hld-vm/persistent-terrain-bounds", "r");
    hldPersistentTerrainBounds = terrainBoundsFlag != NULL && hldCollisionTargetViews;
    if (terrainBoundsFlag != NULL) fclose(terrainBoundsFlag);
    logInfo("Vita persistent terrain bounds enabled=%d\n", (int)hldPersistentTerrainBounds);
''' + anchor)
    if options.atlas_batches:
        code = replace_once(code, "bool hldRefinedRuntime;", "bool hldRefinedRuntime;\nbool hldAtlasBatches;")
        anchor = "    mainThread = sceKernelGetThreadId();"
        code = replace_once(code, anchor, r'''    FILE* atlasBatchesFlag = fopen("ux0:data/hld-vm/atlas-batches", "r");
    hldAtlasBatches = atlasBatchesFlag != NULL;
    if (atlasBatchesFlag != NULL) fclose(atlasBatchesFlag);
    logInfo("Vita atlas batches enabled=%d\n", (int)hldAtlasBatches);
''' + anchor)
    if options.instance_slot_reads:
        code = replace_once(code, "bool hldRefinedRuntime;", "bool hldRefinedRuntime;\nbool hldInstanceSlotReads;")
        anchor = "    mainThread = sceKernelGetThreadId();"
        code = replace_once(code, anchor, r'''    FILE* instanceSlotReadsFlag = fopen("ux0:data/hld-vm/instance-slot-reads", "r");
    hldInstanceSlotReads = instanceSlotReadsFlag != NULL;
    if (instanceSlotReadsFlag != NULL) fclose(instanceSlotReadsFlag);
    logInfo("Vita instance slot reads enabled=%d\n", (int)hldInstanceSlotReads);
''' + anchor)
    if options.vertex_programs:
        code = replace_once(code, "bool hldRefinedRuntime;", "bool hldRefinedRuntime;\nunsigned hldVertexPrograms;")
        anchor = "    mainThread = sceKernelGetThreadId();"
        code = replace_once(code, anchor, r'''    FILE* vertexProgramsFlag = fopen("ux0:data/hld-vm/vertex-programs", "r");
    hldVertexPrograms = vertexProgramsFlag != NULL;
    if (vertexProgramsFlag != NULL) fclose(vertexProgramsFlag);
    logInfo("Vita vertex programs enabled=%u\n", hldVertexPrograms);
''' + anchor)
    if options.surface_sprites:
        code = replace_once(code, "bool hldRefinedRuntime;", "bool hldRefinedRuntime;\nbool hldSurfaceSprites;")
        anchor = "    mainThread = sceKernelGetThreadId();"
        code = replace_once(code, anchor, r'''    FILE* surfaceSpritesFlag = fopen("ux0:data/hld-vm/surface-sprites", "r");
    hldSurfaceSprites = surfaceSpritesFlag != NULL;
    if (surfaceSpritesFlag != NULL) fclose(surfaceSpritesFlag);
    logInfo("Vita surface sprites enabled=%d\n", (int)hldSurfaceSprites);
''' + anchor)
    if options.enemy_target:
        code = replace_once(code, "bool hldRefinedRuntime;", "bool hldRefinedRuntime;\nbool hldEnemyTarget;")
        anchor = "    mainThread = sceKernelGetThreadId();"
        code = replace_once(code, anchor, r'''    FILE* enemyTargetFlag = fopen("ux0:data/hld-vm/enemy-target", "r");
    hldEnemyTarget = enemyTargetFlag != NULL;
    if (enemyTargetFlag != NULL) fclose(enemyTargetFlag);
    logInfo("Vita enemy targets enabled=%d\n", (int)hldEnemyTarget);
''' + anchor)
    if options.scenery_stream:
        code = replace_once(code, "bool hldRefinedRuntime;", "bool hldRefinedRuntime;\nbool hldSceneryStream;")
        anchor = "    mainThread = sceKernelGetThreadId();"
        code = replace_once(code, anchor, r'''    FILE* sceneryStreamFlag = fopen("ux0:data/hld-vm/scenery-stream", "r");
    hldSceneryStream = sceneryStreamFlag != NULL;
    if (sceneryStreamFlag != NULL) fclose(sceneryStreamFlag);
    logInfo("Vita scenery stream enabled=%d\n", (int)hldSceneryStream);
''' + anchor)
    if options.idle_scenery:
        code = replace_once(code, "bool hldRefinedRuntime;", "bool hldRefinedRuntime;\nbool hldIdleScenery;")
        anchor = "    mainThread = sceKernelGetThreadId();"
        code = replace_once(code, anchor, r'''    FILE* idleSceneryFlag = fopen("ux0:data/hld-vm/idle-scenery", "r");
    hldIdleScenery = idleSceneryFlag != NULL;
    if (idleSceneryFlag != NULL) fclose(idleSceneryFlag);
    logInfo("Vita idle scenery enabled=%d\n", (int)hldIdleScenery);
''' + anchor)
    if options.display_clear:
        code = replace_once(code, "bool hldRefinedRuntime;", "bool hldRefinedRuntime;\nbool hldDisplayClear;")
        anchor = "    mainThread = sceKernelGetThreadId();"
        code = replace_once(code, anchor, r'''    FILE* displayClearFlag = fopen("ux0:data/hld-vm/display-clear", "r");
    hldDisplayClear = displayClearFlag != NULL;
    if (displayClearFlag != NULL) fclose(displayClearFlag);
    logInfo("Vita display clear enabled=%d\n", (int)hldDisplayClear);
''' + anchor)
    if options.walking_material:
        code = replace_once(code, "bool hldRefinedRuntime;", "bool hldRefinedRuntime;\nbool hldWalkingMaterial;")
        anchor = "    mainThread = sceKernelGetThreadId();"
        code = replace_once(code, anchor, r'''    FILE* walkingMaterialFlag = fopen("ux0:data/hld-vm/walking-material", "r");
    hldWalkingMaterial = walkingMaterialFlag != NULL;
    if (walkingMaterialFlag != NULL) fclose(walkingMaterialFlag);
    logInfo("Vita walking material enabled=%d\n", (int)hldWalkingMaterial);
''' + anchor)
    if options.placement_ids:
        code = replace_once(code, "bool hldRefinedRuntime;", "bool hldRefinedRuntime;\nbool hldPlacementIds;")
        anchor = "    mainThread = sceKernelGetThreadId();"
        code = replace_once(code, anchor, r'''    FILE* placementIdsFlag = fopen("ux0:data/hld-vm/placement-ids", "r");
    hldPlacementIds = placementIdsFlag != NULL;
    if (placementIdsFlag != NULL) fclose(placementIdsFlag);
    logInfo("Vita instance placement enabled=%d\n", (int)hldPlacementIds);
''' + anchor)
    if options.floor_pattern:
        code = replace_once(code, "bool hldRefinedRuntime;", "bool hldRefinedRuntime;\nbool hldFloorPattern;")
        anchor = "    mainThread = sceKernelGetThreadId();"
        code = replace_once(code, anchor, r'''    FILE* floorFlag = fopen("ux0:data/hld-vm/floor-pattern", "r");
    hldFloorPattern = floorFlag != NULL;
    if (floorFlag != NULL) fclose(floorFlag);
    logInfo("Vita floor pattern enabled=%d\n", (int)hldFloorPattern);
''' + anchor)
    if options.case_checks:
        code = replace_once(code, "bool hldRefinedRuntime;", "bool hldRefinedRuntime;\nbool hldCaseChecks;")
        anchor = "    mainThread = sceKernelGetThreadId();"
        code = replace_once(code, anchor, r'''    FILE* caseFlag = fopen("ux0:data/hld-vm/case-checks", "r");
    hldCaseChecks = caseFlag != NULL;
    if (caseFlag != NULL) fclose(caseFlag);
    logInfo("Vita case checks enabled=%d\n", (int)hldCaseChecks);
''' + anchor)
    if options.phase_mine_trigger:
        code = replace_once(code, "bool hldRefinedRuntime;", "bool hldRefinedRuntime;\nbool hldPhaseMineQuery;")
        anchor = "    mainThread = sceKernelGetThreadId();"
        code = replace_once(code, anchor, r'''    FILE* mineFlag = fopen("ux0:data/hld-vm/phase-mine-trigger", "r");
    hldPhaseMineQuery = mineFlag != NULL;
    if (mineFlag != NULL) fclose(mineFlag);
    logInfo("Vita phase mine triggers enabled=%d\n", (int)hldPhaseMineQuery);
''' + anchor)
    if options.dormant_mine:
        code = replace_once(code, "bool hldRefinedRuntime;", "bool hldRefinedRuntime;\nbool hldDormantMines;")
        anchor = "    mainThread = sceKernelGetThreadId();"
        code = replace_once(code, anchor, r'''    FILE* dormantFlag = fopen("ux0:data/hld-vm/dormant-mine", "r");
    hldDormantMines = dormantFlag != NULL;
    if (dormantFlag != NULL) fclose(dormantFlag);
    logInfo("Vita dormant mines enabled=%d\n", (int)hldDormantMines);
''' + anchor)
    if options.vault_path_probe:
        code = replace_once(code, "bool hldRefinedRuntime;", "bool hldRefinedRuntime;\nbool hldEnemyPathProbe;")
        anchor = "    mainThread = sceKernelGetThreadId();"
        code = replace_once(code, anchor, r'''    FILE* pathProbeFlag = fopen("ux0:data/hld-vm/enemy-path-probe", "r");
    hldEnemyPathProbe = pathProbeFlag != NULL;
    if (pathProbeFlag != NULL) fclose(pathProbeFlag);
    logInfo("Vita enemy path probes enabled=%d\n", (int)hldEnemyPathProbe);
''' + anchor)
    if options.vault_stack:
        code = replace_once(code, "bool hldRefinedRuntime;", "bool hldRefinedRuntime;\nbool hldPreparedLineMasks;")
        anchor = "    mainThread = sceKernelGetThreadId();"
        code = replace_once(code, anchor, r'''    FILE* lineMaskFlag = fopen("ux0:data/hld-vm/line-mask-prep", "r");
    hldPreparedLineMasks = lineMaskFlag != NULL;
    if (lineMaskFlag != NULL) fclose(lineMaskFlag);
    logInfo("Vita prepared line masks enabled=%d\n", (int)hldPreparedLineMasks);
''' + anchor)
    if options.verified_stack or options.optimized_profile or options.vault_profile:
        code = replace_once(code, "bool hldRefinedRuntime;", "bool hldRefinedRuntime;\nbool hldStackProfile;")
        anchor = "    mainThread = sceKernelGetThreadId();"
        code = replace_once(code, anchor, r'''    FILE* stackProfileFlag = fopen("ux0:data/hld-vm/stack-profile", "r");
    hldStackProfile = stackProfileFlag != NULL;
    if (stackProfileFlag != NULL) fclose(stackProfileFlag);
    logInfo("Vita stack profile enabled=%d\n", (int)hldStackProfile);
''' + anchor)
    if options.sound_effect_cache:
        code = replace_once(code, "bool hldRefinedRuntime;", "bool hldRefinedRuntime;\nbool hldCacheSoundEffects;")
        anchor = "    mainThread = sceKernelGetThreadId();"
        code = replace_once(code, anchor, r'''    FILE* effectCacheFlag = fopen("ux0:data/hld-vm/sound-effect-cache", "r");
    hldCacheSoundEffects = effectCacheFlag != NULL;
    if (effectCacheFlag != NULL) fclose(effectCacheFlag);
    logInfo("Vita sound effect cache enabled=%d\n", (int)hldCacheSoundEffects);
''' + anchor)
    if options.playable:
        return code
    return replace_once(code, "args.exitAtFrame = -1;", "args.exitAtFrame = 1260;")


def vm_profile(code):
    marker = "RValue VM_executeCode(VMContext* ctx, int32_t codeIndex,"
    sample = (proof / "vm-profile.inc").read_text()
    if options.verified_stack or options.optimized_profile or options.vault_profile:
        anchor = "static VMSampleScope beginVMSample(VMContext* ctx) {"
        sample = replace_once(sample, anchor, "extern bool hldStackProfile;\n\n" + anchor +
                              "\n    if (!hldStackProfile) return (VMSampleScope){0};")
    profile_route = options.route_profile or options.simulation_profile or options.step_spike_profile or options.survey_profile
    if options.profile_current_runtime or profile_route:
        sample = "#define HLD_PROFILE_CURRENT_RUNTIME\n" + sample
    if profile_route:
        sample = "#define HLD_PROFILE_VM_DISPATCH\n" + sample
    if options.respawn_profile:
        sample = replace_once(sample, "    if (frame < 550 || frame % 33 != 9) return (VMSampleScope){0};", """    if (frame != vmSampleLastFrame && vmSampleFrames > 0) reportVMSample(ctx);
    if (frame != 8 && frame != 513 && frame != 956) return (VMSampleScope){0};""")
        sample = replace_once(sample, "const int window = (frame - 550) / 220;", "const int window = frame;")
    if profile_runtime:
        sample = replace_once(sample, "if (frame < 550 || frame % 33 != 9)",
                              "if (frame < 250 || frame % 33 != 9)")
        sample = replace_once(sample, "const int window = (frame - 550) / 220;",
                              "const int window = (frame - 250) / 220;")
    if profile_route:
        if options.step_spike_profile:
            sample = replace_once(sample, "    if (frame < 550 || frame % 33 != 9) return (VMSampleScope){0};", """    if (frame != vmSampleLastFrame && vmSampleFrames > 0) reportVMSample(ctx);
    if (frame != 592 && frame != 595 && frame != 600 &&
        frame != 630 && frame != 633 && frame != 639) return (VMSampleScope){0};""")
            sample = replace_once(sample, "const int window = (frame - 550) / 220;", "const int window = frame;")
        else:
            sample = replace_once(sample, "if (frame < 550 || frame % 33 != 9)",
                                  "if (frame < 450 || frame >= 735 || frame % 15 != 6)")
            sample = replace_once(sample, "const int window = (frame - 550) / 220;",
                                  "const int window = (frame - 450) / 90;")
        sample = replace_once(sample, "    vmSampleFrames = 0;",
                              '    logInfo("Vita VM route sample complete frame=%d\\n", vmSampleLastFrame);\n'
                              "    vmSampleFrames = 0;")
    if profile_builtins or profile_route:
        code, sample = with_profiled_builtins(code, sample)
    if options.native_entry_profile:
        sample = "#define HLD_PROFILE_NATIVE_ENTRY\n" + sample
    code = replace_once(code, marker, sample + "\n" + marker)
    if options.native_entry_profile:
        return with_profiled_native_entry(code)
    anchor = ("    // Try the bound line query.\n" if options.optimized_profile
              else "    // Create a CallFrame for this script\n")
    code = replace_once(code, anchor, "    VMSampleScope sample = beginVMSample(ctx);\n\n" + anchor)
    code = replace_once(code, "    unwindVMStack(ctx, stackTopWhenTheCallFrameWasCreated);\n\n    return result;",
                        "    unwindVMStack(ctx, stackTopWhenTheCallFrameWasCreated);\n\n"
                        "    endVMSample(sample, codeIndex, eventType, eventSubtype);\n    return result;")
    return code


def step_log_buffer(code, phase="step", compare=True):
    code = replace_once(code, '#include <stdio.h>', '#include <stdio.h>\n#include <stdbool.h>')
    code = replace_once(code, 'static FILE* vitaLog;', '''static FILE* vitaLog;
static char stepLogStorage[8192];
static bool bufferLogDuringStep;
static bool batchingStepLog;

void vitaBeginStepLog(void) {
    static bool reported;
    if (!reported) {
        logInfo("Vita step log buffered=%d\\n", (int)bufferLogDuringStep);
        reported = true;
    }
    if (bufferLogDuringStep) __atomic_store_n(&batchingStepLog, true, __ATOMIC_RELEASE);
}

void vitaEndStepLog(void) {
    if (!bufferLogDuringStep) return;
    __atomic_store_n(&batchingStepLog, false, __ATOMIC_RELEASE);
    if (vitaLog != NULL) fflush(vitaLog);
}''')
    code = replace_once(code, '    vitaLog = fopen("ux0:data/hld-vm/runtime.log", "w");', '''    FILE* flag = fopen("ux0:data/hld-vm/buffered-step-log", "r");
    bufferLogDuringStep = flag != NULL;
    if (flag != NULL) fclose(flag);
    vitaLog = fopen("ux0:data/hld-vm/runtime.log", "w");
    if (vitaLog != NULL && bufferLogDuringStep &&
        setvbuf(vitaLog, stepLogStorage, _IOFBF, sizeof(stepLogStorage)) != 0) {
        bufferLogDuringStep = false;
    }''')
    code = replace_once(code, '    vfprintf(vitaLog, format, va);\n    fflush(vitaLog);',
                        '''    vfprintf(vitaLog, format, va);
    if (type == LOG_TYPE_ERROR || !__atomic_load_n(&batchingStepLog, __ATOMIC_ACQUIRE))
        fflush(vitaLog);''')
    if not compare:
        code = replace_once(code, '''    FILE* flag = fopen("ux0:data/hld-vm/buffered-step-log", "r");
    bufferLogDuringStep = flag != NULL;
    if (flag != NULL) fclose(flag);''', "    bufferLogDuringStep = true;")
    if phase == "room":
        code = code.replace("StepLog", "RoomLog").replace("stepLogStorage", "roomLogStorage")
        code = code.replace("bufferLogDuringStep", "bufferLogDuringRoomChange")
        code = code.replace("buffered-step-log", "buffered-room-log").replace("Vita step log", "Vita room log")
    return code


def phase_profile(code):
    code = replace_once(code, "        unsigned sampledFrames = 0, presentedFrames = 0;",
                       "        unsigned sampledFrames = 0, presentedFrames = 0;\n"
                       "        uint64_t inputTotal = 0, audioTotal = 0, setupTotal = 0, preTotal = 0, beginTotal = 0;\n")
    code = replace_once(code, "            uint64_t stepStart = nowNanos();",
                       "            uint64_t stepStart = nowNanos();\n"
                       "            inputTotal += stepStart - frameStartNow;")
    code = replace_once(code, "            stepTotal += nowNanos() - stepStart;",
                       "            uint64_t stepEnd = nowNanos();\n            stepTotal += stepEnd - stepStart;")
    code = replace_once(code, "                runner->audioSystem->vtable->update(runner->audioSystem, dt);",
                       "                uint64_t audioStart = nowNanos();\n"
                       "                runner->audioSystem->vtable->update(runner->audioSystem, dt);\n"
                       "                audioTotal += nowNanos() - audioStart;")
    code = replace_once(code, "                Runner_drawPre(runner, fbWidth, fbHeight);",
                       "                const uint64_t preStart = nowNanos();\n"
                       "                Runner_drawPre(runner, fbWidth, fbHeight);\n"
                       "                const uint64_t preEnd = nowNanos();\n"
                       "                setupTotal += preStart - stepEnd;\n"
                       "                preTotal += preEnd - preStart;")
    code = replace_once(code, "                uint64_t drawViewStart = nowNanos();",
                       "                uint64_t drawViewStart = nowNanos();\n"
                       "                beginTotal += drawViewStart - preEnd;")
    code = replace_once(code, "                stepTotal = workTotal = swapTotal = viewTotal = guiTotal = worstInterval = 0;",
                       "                logInfo(\"Vita phases frame=%d input_ms=%.3f audio_ms=%.3f setup_ms=%.3f pre_ms=%.3f begin_ms=%.3f\\n\",\n"
                       "                    runner->frameCount, inputTotal * perFrameMs, audioTotal * perFrameMs,\n"
                       "                    setupTotal * perFrameMs, preTotal * perFrameMs, beginTotal * perFrameMs);\n"
                       "                inputTotal = audioTotal = setupTotal = preTotal = beginTotal = 0;\n"
                       "                stepTotal = workTotal = swapTotal = viewTotal = guiTotal = worstInterval = 0;")
    return code


def sprite_load_profile(code):
    lo = code.index("static RValue builtin_sprite_add(")
    hi = code.index("static RValue builtin_sprite_collision_mask(", lo)
    region = code[lo:hi]
    region = replace_once(region, "    uint8_t* bytes = nullptr;", "    const uint64_t begin = nowNanos();\n    uint8_t* bytes = nullptr;")
    region = replace_once(region, "    int w, h;", "    const uint64_t afterRead = nowNanos();\n    int w, h;")
    region = replace_once(region, "    if (RValue_toBool(args[2])) {", "    const uint64_t afterDecode = nowNanos();\n    if (RValue_toBool(args[2])) {")
    region = replace_once(region, "    int32_t sprite = renderer->vtable->createSpriteFromPixels(",
                          "    const uint64_t afterAlpha = nowNanos();\n    int32_t sprite = renderer->vtable->createSpriteFromPixels(")
    region = replace_once(region, "    return RValue_makeInt32(sprite);", """    const char* filename = strrchr(args[0].string, '/');
    logInfo("Vita sprite load frame=%d file=%s bytes=%d read_ms=%.3f decode_ms=%.3f alpha_ms=%.3f upload_mask_ms=%.3f\\n",
            runner->frameCount, filename != nullptr ? filename + 1 : args[0].string, size,
            (afterRead - begin) * 1e-6, (afterDecode - afterRead) * 1e-6, (afterAlpha - afterDecode) * 1e-6,
            (nowNanos() - afterAlpha) * 1e-6);
    return RValue_makeInt32(sprite);""")
    return code[:lo] + region + code[hi:]


def draw_list_loop(code):
    code = phase_profile(code)
    code = replace_once(code, "                uint64_t audioStart = nowNanos();",
                        "                extern void hldQueueDrawList(Runner* runner);\n"
                        "                extern void hldFinishDrawList(int frame);\n"
                        "                if (shouldRender) hldQueueDrawList(runner);\n"
                        "                uint64_t audioStart = nowNanos();")
    return replace_once(code, "                audioTotal += nowNanos() - audioStart;",
                        "                audioTotal += nowNanos() - audioStart;\n"
                        "                if (shouldRender) hldFinishDrawList(runner->frameCount);")


def pcm_town_audio(code):
    anchor = "        stb_vorbis* v = nullptr;\n        if (wavFile == nullptr) {"
    return replace_once(code, anchor, """        if (wavFile == nullptr && assetStreamPath != nullptr && sound != nullptr &&
            strcmp(sound->name, "MUS_AMB_C_MARKET_1") == 0) {
            wavFile = openWavStream("ux0:data/hld-vm/test-market-pcm.wav", &wavChannels,
                                    &wavSampleRate, &wavDataStart, &wavDataBytes);
            if (wavFile != nullptr) logInfo("Vita PCM arrangement: Original market loop loaded from sidecar\\n");
        }

""" + anchor)


def audio_worker_loop(code):
    code = phase_profile(code)
    original = """                uint64_t audioStart = nowNanos();
                runner->audioSystem->vtable->update(runner->audioSystem, dt);
                audioTotal += nowNanos() - audioStart;"""
    return replace_once(code, original, """                extern bool hldQueueAudioUpdate(Runner* runner, float dt);
                extern uint64_t hldFinishAudioUpdate(void);
                extern void hldReportAudioUpdate(int frame);
                extern void hldPrepareDrawList(Runner* runner);
                if (shouldRender && hldQueueAudioUpdate(runner, dt)) {
                    hldPrepareDrawList(runner);
                    audioTotal += hldFinishAudioUpdate();
                } else {
                    uint64_t audioStart = nowNanos();
                    runner->audioSystem->vtable->update(runner->audioSystem, dt);
                    audioTotal += nowNanos() - audioStart;
                }
                hldReportAudioUpdate(runner->frameCount);""")


objects = [token for token in link if token.startswith("CMakeFiles/butterscotch.dir/") and token.endswith(".obj")]
nm = subprocess.run([sdk / "arm-vita-eabi-gcc-nm", "-u", *objects], cwd=build, text=True, capture_output=True, check=True)
api = set(re.findall(r"\bU ((?:gl|vgl|sceGxm)[A-Z]\w*)$", nm.stdout, re.M))
api -= {"vglSwapBuffers", "sceGxmEndScene", "sceGxmDisplayQueueAddEntry", "sceGxmDisplayQueueFinish"}
assert {"glBindFramebuffer", "glDrawElements", "glReadPixels", "vglBufferData"} <= api, sorted(api)
if options.graphics_commands:
    assert set(SCALARS) | set(VECTORS) | {"glDrawArrays", "vglAllocFromScratch"} <= api, sorted(api)
if options.draw_state_packets:
    assert {"glGetBooleanv", "glGetIntegerv", "glIsEnabled"} <= api, sorted(api)
if options.expanded_draw_packets:
    assert set(EXTRA_SCALARS) <= api and len(api) <= 256, sorted(api)
api = sorted(api)
bridge = out / "graphics-barriers.S"
assembly = [".syntax unified", ".arm", ".text", ".p2align 2"]
for call_id, name in enumerate(api):
    assembly += [
        f".global __wrap_{name}", f".type __wrap_{name}, %function",
        f"__wrap_{name}:",
    ]
    if options.graphics_commands:
        assembly += [
            "    ldr r12, =hldVitaGraphicsRecording",
            "    ldrb r12, [r12]",
            "    cmp r12, #0",
            "    beq 1f",
            "    push {r0-r3,r12,lr}",
        ]
        if options.expanded_draw_packets:
            assembly += [f"    mov r0, #{call_id}", "    bl hldDrainVitaGraphicsForCall"]
        else:
            assembly += ["    bl hldDrainVitaGraphics"]
        assembly += [
            "    pop {r0-r3,r12,lr}",
            f"    b __real_{name}",
            "1:",
        ]
    assembly += [
        "    ldr r12, =hldSwapPending",
        "    ldr r12, [r12]",
        "    cmp r12, #0",
        f"    beq __real_{name}",
        "    push {r0-r3,r12,lr}",
        "    bl hldWaitForSwap",
        "    pop {r0-r3,r12,lr}",
        f"    b __real_{name}",
        f".size __wrap_{name}, . - __wrap_{name}", ".ltorg", "",
    ]
assembly += ['.section .note.GNU-stack,"",%progbits']
bridge.write_text("\n".join(assembly) + "\n")
bridge_obj = bridge.with_suffix(".o")
subprocess.run([gcc, "-c", "-marm", bridge, "-o", bridge_obj], check=True)
replacements = dict([compile_source("vita/native_platform.c", platform), compile_source("vita/main.c", main)])
if HLDC_TRANSLATE and os.environ.get("HLDC_TEXBIN_FAST", "1") == "1":
    from texbin_fast_source import with_fast_texbin
    replacements.update([compile_source("vita/vita_textures.c", with_fast_texbin)])
if options.shared_instance_fields:
    for filename in ("instance.c", "int_rvalue_hashmap.c"):
        replacements.update([compile_source(filename, lambda code: code)])
if options.profile_vm:
    replacements.update([compile_source("vm.c", vm_profile)])
elif options.profile_phases:
    replacements.update([compile_source("loop.c", phase_profile)])
elif options.overlap_draw_list:
    replacements.update([compile_source("loop.c", draw_list_loop), compile_source("runner.c", lambda code: code +
        "\nvoid hldPrepareDrawList(Runner* runner) { rebuildDrawableCacheIfDirty(runner); }\n")])
elif options.sort_draw_list:
    replacements.update([compile_source("loop.c", phase_profile), compile_source("runner.c", with_fast_draw_sort)])
elif options.pcm_town:
    replacements.update([compile_source("loop.c", phase_profile),
                         compile_source("runner.c", lambda code: with_fast_draw_sort(code, always=True)),
                         compile_source("audio/openal/al_audio_system.c", pcm_town_audio)])
elif options.vm_values:
    replacements.update([compile_source("loop.c", phase_profile),
                         compile_source("runner.c", lambda code: with_fast_draw_sort(code, always=True)),
                         compile_source("vm.c", with_fast_vm_values)])
elif options.packed_literals:
    replacements.update([compile_source("loop.c", phase_profile),
                         compile_source("runner.c", lambda code: with_fast_draw_sort(code, always=True)),
                         compile_source("vm.c", lambda code: with_packed_literals(with_fast_vm_values(code)))])
elif options.overlap_audio or options.audio_affinity:
    replacements.update([compile_source("loop.c", audio_worker_loop),
                         compile_source("runner.c", lambda code: with_fast_draw_sort(code, always=True) +
                             "\nvoid hldPrepareDrawList(Runner* runner) { rebuildDrawableCacheIfDirty(runner); }\n"),
                         compile_source("vm.c", lambda code: with_packed_literals(with_fast_vm_values(code)))])
elif options.mask_profile:
    replacements.update([compile_source("loop.c", phase_profile), compile_source("gl/gl_renderer.c", with_mask_profile)])
elif options.packed_math:
    replacements.update([compile_source("loop.c", phase_profile),
                         compile_source("runner.c", lambda code: with_fast_draw_sort(code, always=True)),
                         compile_source("vm.c", lambda code: with_packed_literals(with_fast_vm_values(code), math=True))])
elif options.uniform_trial:
    replacements.update([compile_source("loop.c", phase_profile), compile_source("gl/gl_renderer.c", with_uniform_trial)])
elif options.respawn_profile:
    replacements.update([compile_source("loop.c", phase_profile), compile_source("vm.c", vm_profile),
                         compile_source("vm_builtins.c", sprite_load_profile)])
elif options.compact_rvalues:
    replacements.update([compile_source("loop.c", phase_profile),
                         compile_source("runner.c", lambda code: with_fast_draw_sort(code, always=True))])
elif options.sprite_cache:
    replacements.update([compile_source("loop.c", phase_profile), compile_source("vm_builtins.c", with_sprite_cache)])
elif options.deferred_audio:
    def prepare_loop(code):
        code = with_deferred_audio_loop(phase_profile(code))
        if options.prefetch_ambience:
            code = with_ambient_prefetch_loop(code)
        if options.frame_hitch_profile or options.audio_start_profile or options.lazy_audio_length:
            code = with_frame_hitch_profile(code)
        if options.display_clear:
            code = with_display_clear_loop(code)
        if options.vertex_programs:
            code = with_vertex_program_loop(code)
        if options.buffered_frame_reports:
            code = with_frame_log_loop(code)
        if options.vm_training:
            code = with_vm_profile_export(code)
        elif options.engine_training:
            code = with_engine_profile_export(code)
        if options.reduced_vm_work:
            anchor = "            hldReportAudioUpdate(runner->frameCount);"
            code = replace_once(code, anchor, anchor + "\n" + "\n".join(
                f"            extern void {report}(int frame);\n            {report}(runner->frameCount);"
                for report in ("hldReportEnvSnapshotPool", "hldReportConvertedReads", "hldReportSceneryScalarWrites")))
        if options.graphics_commands:
            code = with_graphics_command_loop(code, render_tail=options.graphics_render_tail)
        if options.stream_placement:
            code = with_step_event_report(code)
        if options.frame_deadlines or measured_runtime:
            code = with_frame_deadlines(code, compare=options.frame_deadlines)
        if options.frame_headroom:
            code = with_frame_headroom(code)
        if verify_runtime:
            code = with_runtime_verification_loop(code)
        elif buffer_room_logs:
            code = with_room_log_buffer_loop(code, measure=options.ascii_parser or measured_runtime)
        if options.step_log_buffer:
            code = replace_once(code, "                Runner_step(runner);",
                                """                extern void vitaBeginStepLog(void);
                extern void vitaEndStepLog(void);
                vitaBeginStepLog();
                Runner_step(runner);
                vitaEndStepLog();""")
        if options.early_draw_list:
            anchor = "                // Clear the default framebuffer (window background) to black"
            code = replace_once(code, anchor, """                extern void hldPrepareDrawList(Runner* runner);
                if (runner->frameCount > 180 && ((runner->frameCount - 1) / 180) % 2 != 0)
                    hldPrepareDrawList(runner);

""" + anchor)
        if options.collision_query or options.combat_queries:
            anchor = "            hldReportAudioUpdate(runner->frameCount);"
            code = replace_once(code, anchor, anchor + "\n            extern void hldReportCollisionQueries(int frame);\n"
                                "            hldReportCollisionQueries(runner->frameCount);")
        if options.collision_order:
            anchor = "            hldReportAudioUpdate(runner->frameCount);"
            code = replace_once(code, anchor, anchor + "\n            extern void hldReportCollisionOrder(int frame);\n"
                                "            hldReportCollisionOrder(runner->frameCount);")
        if options.collision_blocks:
            anchor = "            hldReportAudioUpdate(runner->frameCount);"
            code = replace_once(code, anchor, anchor + "\n            extern void hldReportCollisionBlocks(int frame);\n"
                                "            hldReportCollisionBlocks(runner->frameCount);")
        if options.collision_grid or vector_broadphase:
            anchor = "            hldReportAudioUpdate(runner->frameCount);"
            code = replace_once(code, anchor, anchor + "\n            extern void hldReportCollisionGrid(int frame);\n"
                                "            hldReportCollisionGrid(runner->frameCount);")
        if options.wide_collision:
            anchor = "            hldReportAudioUpdate(runner->frameCount);"
            code = replace_once(code, anchor, anchor + "\n            extern void hldReportWideCollision(int frame);\n"
                                "            hldReportWideCollision(runner->frameCount);")
        if options.resume_collision:
            anchor = "            hldReportAudioUpdate(runner->frameCount);"
            code = replace_once(code, anchor, anchor + "\n            extern void hldReportCollisionRefresh(int frame);\n"
                                "            hldReportCollisionRefresh(runner->frameCount);")
        if options.borrowed_queries:
            anchor = "            hldReportAudioUpdate(runner->frameCount);"
            code = replace_once(code, anchor, anchor + "\n            extern void hldReportReadOnlyInstances(int frame);\n"
                                "            hldReportReadOnlyInstances(runner->frameCount);")
        if options.placement_filters:
            anchor = "            hldReportAudioUpdate(runner->frameCount);"
            code = replace_once(code, anchor, anchor + "\n            extern void hldReportPlacementFilters(int frame);\n"
                                "            hldReportPlacementFilters(runner->frameCount);")
        if options.grid_refresh:
            anchor = "            hldReportAudioUpdate(runner->frameCount);"
            code = replace_once(code, anchor, anchor + "\n            extern void hldReportGridRefresh(int frame);\n"
                                "            hldReportGridRefresh(runner->frameCount);")
        if options.inline_bbox:
            anchor = "            hldReportAudioUpdate(runner->frameCount);"
            code = replace_once(code, anchor, anchor + "\n            extern void hldReportInlineBBox(Runner* runner);\n"
                                "            hldReportInlineBBox(runner);")
        if options.collision_epoch:
            anchor = "            hldReportAudioUpdate(runner->frameCount);"
            code = replace_once(code, anchor, anchor + "\n            extern void hldReportCollisionEpoch(int frame);\n"
                                "            hldReportCollisionEpoch(runner->frameCount);")
        if options.borrow_collision_targets:
            anchor = "            hldReportAudioUpdate(runner->frameCount);"
            code = replace_once(code, anchor, anchor + "\n            extern void hldReportCollisionTargets(int frame);\n"
                                "            hldReportCollisionTargets(runner->frameCount);")
        if options.script_call_buffers:
            anchor = "            hldReportAudioUpdate(runner->frameCount);"
            code = replace_once(code, anchor, anchor + "\n            extern void hldReportScriptCallBuffers(int frame);\n"
                                "            hldReportScriptCallBuffers(runner->frameCount);")
        if options.scalar_pairs:
            anchor = "            hldReportAudioUpdate(runner->frameCount);"
            code = replace_once(code, anchor, anchor + "\n            extern void hldReportScalarPairs(int frame);\n"
                                "            hldReportScalarPairs(runner->frameCount);")
        if options.fixed_array_reads:
            anchor = "            hldReportAudioUpdate(runner->frameCount);"
            code = replace_once(code, anchor, anchor + "\n            extern void hldReportFixedViews(int frame);\n"
                                "            hldReportFixedViews(runner->frameCount);")
        if options.operand_dispatch:
            anchor = "            hldReportAudioUpdate(runner->frameCount);"
            code = replace_once(code, anchor, anchor + "\n            extern void hldReportOperandHandlers(int frame);\n"
                                "            hldReportOperandHandlers(runner->frameCount);")
        if options.switch_chains:
            anchor = "            hldReportAudioUpdate(runner->frameCount);"
            code = replace_once(code, anchor, anchor + "\n            extern void hldReportSwitchChains(int frame);\n"
                                "            hldReportSwitchChains(runner->frameCount);")
        if options.stable_cases:
            anchor = "            hldReportAudioUpdate(runner->frameCount);"
            code = replace_once(code, anchor, anchor + "\n            extern void hldReportStableCases(int frame);\n"
                                "            hldReportStableCases(runner->frameCount);")
        if options.placement_cells or options.point_queries:
            anchor = "            hldReportAudioUpdate(runner->frameCount);"
            code = replace_once(code, anchor, anchor + "\n            extern void hldReportPlacementCells(int frame);\n"
                                "            hldReportPlacementCells(runner->frameCount);")
        if options.copy_blocks:
            anchor = "            hldReportAudioUpdate(runner->frameCount);"
            code = replace_once(code, anchor, anchor + "\n            extern void hldReportCopyBlocks(int frame);\n"
                                "            hldReportCopyBlocks(runner->frameCount);")
        if options.idle_spawners:
            anchor = "            hldReportAudioUpdate(runner->frameCount);"
            code = replace_once(code, anchor, anchor + "\n            extern void hldReportIdleSpawners(int frame);\n"
                                "            hldReportIdleSpawners(runner->frameCount);")
        if options.line_envelopes or options.ordered_line_candidates:
            anchor = "            hldReportAudioUpdate(runner->frameCount);"
            code = replace_once(code, anchor, anchor + "\n            extern void hldReportLineEnvelopes(int frame);\n"
                                "            hldReportLineEnvelopes(runner->frameCount);")
        if options.scenery_batches:
            anchor = "            hldReportAudioUpdate(runner->frameCount);"
            code = replace_once(code, anchor, anchor + "\n            extern void hldReportSceneryBatches(int frame);\n"
                                "            hldReportSceneryBatches(runner->frameCount);")
        if options.ascii_parser:
            anchor = "            hldReportAudioUpdate(runner->frameCount);"
            code = replace_once(code, anchor, anchor + "\n            extern void hldReportAsciiParser(int frame);\n"
                                "            hldReportAsciiParser(runner->frameCount);")
        if options.pure_guards:
            anchor = "            hldReportAudioUpdate(runner->frameCount);"
            code = replace_once(code, anchor, anchor + "\n            extern void hldReportPureGuards(int frame);\n"
                                "            hldReportPureGuards(runner->frameCount);")
        if options.retained_sprite_program:
            anchor = "            hldReportAudioUpdate(runner->frameCount);"
            code = replace_once(code, anchor, anchor + "\n            extern void hldReportRetainedSpriteProgram(int frame);\n"
                                "            hldReportRetainedSpriteProgram(runner->frameCount);")
        if options.flame_vent_probes:
            anchor = "            hldReportAudioUpdate(runner->frameCount);"
            code = replace_once(code, anchor, anchor + "\n            extern void hldReportFlameVentGuard(int frame);\n"
                                "            hldReportFlameVentGuard(runner->frameCount);")
        if options.depth_helpers:
            anchor = "            hldReportAudioUpdate(runner->frameCount);"
            code = replace_once(code, anchor, anchor + "\n            extern void hldReportDepthHelpers(int frame);\n"
                                "            hldReportDepthHelpers(runner->frameCount);")
        if mask_overlap:
            anchor = "            hldReportAudioUpdate(runner->frameCount);"
            code = replace_once(code, anchor, anchor + "\n            extern void hldReportMaskOverlaps(int frame);\n"
                                "            hldReportMaskOverlaps(runner->frameCount);")
        if options.room_updates:
            anchor = "            hldReportAudioUpdate(runner->frameCount);"
            code = replace_once(code, anchor, anchor + "\n            extern void hldReportSceneryPass(int frame);\n"
                                "            extern void hldReportEnemyShadow(int frame);\n"
                                "            hldReportSceneryPass(runner->frameCount);\n"
                                "            hldReportEnemyShadow(runner->frameCount);")
        if options.region_cases:
            anchor = "            hldReportAudioUpdate(runner->frameCount);"
            code = replace_once(code, anchor, anchor + "\n            extern void hldReportRegionCases(int frame);\n"
                                "            hldReportRegionCases(runner->frameCount);")
        if options.stack_register:
            anchor = "            hldReportAudioUpdate(runner->frameCount);"
            code = replace_once(code, anchor, anchor + "\n            extern void hldReportStackRegister(int frame);\n"
                                "            hldReportStackRegister(runner->frameCount);")
        if options.grid_mask_overlap:
            anchor = "            hldReportAudioUpdate(runner->frameCount);"
            code = replace_once(code, anchor, anchor + "\n            extern void hldReportGridMaskOverlaps(int frame);\n"
                                "            hldReportGridMaskOverlaps(runner->frameCount);")
        if options.resting_scenery:
            anchor = "            hldReportAudioUpdate(runner->frameCount);"
            code = replace_once(code, anchor, anchor + "\n            extern void hldReportRestingScenery(int frame);\n"
                                "            hldReportRestingScenery(runner->frameCount);")
        if options.active_cases:
            anchor = "            uint64_t frameStartNow = nowNanos();"
            code = replace_once(code, anchor, anchor + "\n            extern void hldGpuClockTrial(int frame);\n"
                                "            hldGpuClockTrial(runner->frameCount);")
            anchor = "            hldReportAudioUpdate(runner->frameCount);"
            code = replace_once(code, anchor, anchor + "\n            extern void hldReportActiveCase(int frame);\n"
                                "            hldReportActiveCase(runner->frameCount);")
        if options.leaf_events or options.consolidated_runtime:
            anchor = "            hldReportAudioUpdate(runner->frameCount);"
            code = replace_once(code, anchor, anchor + "\n            extern void hldReportLeafEvents(int frame);\n"
                                "            hldReportLeafEvents(runner->frameCount);")
        if options.scenery_draw_lists:
            anchor = "            hldReportAudioUpdate(runner->frameCount);"
            code = replace_once(code, anchor, anchor + "\n            extern void hldReportSceneryDrawLists(int frame);\n"
                                "            hldReportSceneryDrawLists(runner->frameCount);")
        if options.scenery_cursor:
            anchor = "            hldReportAudioUpdate(runner->frameCount);"
            code = replace_once(code, anchor, anchor + "\n            extern void hldReportSceneryCursor(int frame);\n"
                                "            hldReportSceneryCursor(runner->frameCount);")
        if options.eventless_case:
            anchor = "            hldReportAudioUpdate(runner->frameCount);"
            code = replace_once(code, anchor, anchor + "\n            extern void hldReportEventlessCase(int frame);\n"
                                "            hldReportEventlessCase(runner->frameCount);")
        if options.native_scenery_body:
            anchor = "            hldReportAudioUpdate(runner->frameCount);"
            code = replace_once(code, anchor, anchor + "\n            extern void hldReportNativeSceneryBody(int frame);\n"
                                "            hldReportNativeSceneryBody(runner->frameCount);")
        if options.direct_instance_ids:
            anchor = "            hldReportAudioUpdate(runner->frameCount);"
            code = replace_once(code, anchor, anchor + "\n            extern void hldReportDirectIds(int frame);\n"
                                "            hldReportDirectIds(runner->frameCount);")
        if options.array_pool or options.consolidated_runtime:
            anchor = "            uint64_t frameStartNow = nowNanos();"
            code = replace_once(code, anchor, anchor + "\n            extern void hldUpdateArrayPool(int frame);\n"
                                "            hldUpdateArrayPool(runner->frameCount);")
            anchor = "            hldReportAudioUpdate(runner->frameCount);"
            code = replace_once(code, anchor, anchor + "\n            extern void hldReportArrayPool(int frame);\n"
                                "            hldReportArrayPool(runner->frameCount);")
        if options.runtime_refinements:
            anchor = "            hldReportAudioUpdate(runner->frameCount);"
            reports = ("hldReportGridMaskOverlaps", "hldReportRegionCases",
                       "hldReportRestingScenery", "hldReportSceneryPass")
            if options.grid_mask_memo:
                reports += ("hldReportGridMaskMemo",)
            if options.vault_mask_layers:
                reports += ("hldReportBuiltinMaskOverlap",)
            if options.shape_candidate_cells:
                reports += ("hldReportShapeCandidateCells",)
            if options.mask_tiles:
                reports += ("hldReportMaskTiles",)
            if options.collision_target_views:
                reports += ("hldReportCollisionTargetViews",)
            if options.persistent_terrain_bounds:
                reports += ("hldReportTerrainDispatchBounds",)
            if options.retained_draw_order:
                reports += ("hldReportRetainedDrawOrder",)
            if options.single_draw_responder:
                reports += ("hldReportSingleDrawResponder",)
            if options.resolved_step:
                reports += ("hldReportResolvedStep",)
            if options.classified_vm_entry:
                reports += ("hldReportClassifiedEntries",)
            if options.placement_probe_masks:
                reports += ("hldReportPlacementProbeMasks",)
            if options.frame_depth_fold:
                reports += ("hldReportFrameDepthFold",)
            if options.mask_line_tiles:
                reports += ("hldReportLineMaskTiles",)
            if options.quiet_shadow_log:
                reports += ("hldReportQuietShadowLog",)
            if options.grouped_shadow:
                reports += ("hldReportEnemyShadow",)
            if options.line_chunks:
                reports += ("hldReportLineChunks",)
            if options.native_line_script:
                reports += ("hldReportLineScript",)
            if options.palette_tail:
                reports += ("hldReportPaletteTail",)
            if options.motion_repeat:
                reports += ("hldReportMotionRepeat",)
            if options.scenery_skip:
                reports += ("hldReportScenerySkip",)
            if options.scenery_guard:
                reports += ("hldReportScenerySkipGuard",)
            if options.stream_scenery_snapshot:
                reports += ("hldReportScenerySnapshots",)
            if options.hologram_args:
                reports += ("hldReportHologramArgs",)
            if options.idle_scenery:
                reports += ("hldReportIdleScenery",)
            if options.scenery_stream:
                reports += ("hldReportSceneryStream",)
            if options.terrain_sweep:
                reports += ("hldReportTerrainSweep",)
            if options.enemy_target:
                reports += ("hldReportEnemyTarget",)
            if options.walking_material:
                reports += ("hldReportWalkingMaterial",)
            if options.placement_ids:
                reports += ("hldReportPlacementIds",)
            if options.floor_pattern:
                reports += ("hldReportFloorPattern",)
            if options.case_checks:
                reports += ("hldReportCaseChecks",)
            if options.phase_mine_trigger:
                reports += ("hldReportPhaseMineTrigger",)
            if options.dormant_mine:
                reports += ("hldReportDormantMines",)
            if options.mine_entry:
                reports += ("hldReportMineEntry",)
            if options.vault_path_probe:
                reports += ("hldReportEnemyPathProbe",)
            if options.vault_stack:
                reports += ("hldReportLineMaskPrep",)
            for report in reports:
                code = replace_once(code, anchor,
                                    anchor + f"\n            extern void {report}(int frame);\n"
                                    f"            {report}(runner->frameCount);")
        if options.draw_order_repair:
            anchor = "            hldReportAudioUpdate(runner->frameCount);"
            code = replace_once(code, anchor, anchor + "\n            extern void hldReportDrawOrderRepair(int frame);\n"
                                "            hldReportDrawOrderRepair(runner->frameCount);")
        if options.sprite_worker:
            anchor = "            hldReportAudioUpdate(runner->frameCount);"
            code = replace_once(code, anchor, anchor + "\n            extern void hldReportVitaBatchWorker(int frame);\n"
                                "            hldReportVitaBatchWorker(runner->frameCount);")
        if options.recessing_guards:
            anchor = "            hldReportAudioUpdate(runner->frameCount);"
            code = replace_once(code, anchor, anchor + "\n            extern void hldReportRecessingGuards(int frame);\n"
                                "            hldReportRecessingGuards(runner->frameCount);")
        if options.movement_helpers:
            anchor = "            hldReportAudioUpdate(runner->frameCount);"
            code = replace_once(code, anchor, anchor + "\n            extern void hldReportMovementHelpers(int frame);\n"
                                "            hldReportMovementHelpers(runner->frameCount);")
        if options.scene_queries:
            anchor = "            hldReportAudioUpdate(runner->frameCount);"
            code = replace_once(code, anchor, anchor + "\n            extern void hldReportSceneQueries(int frame);\n"
                                "            hldReportSceneQueries(runner->frameCount);")
        if options.sound_effect_cache:
            anchor = "            hldReportAudioUpdate(runner->frameCount);"
            code = replace_once(code, anchor, anchor + "\n            extern void hldReportDecodedEffects(int frame);\n"
                                "            hldReportDecodedEffects(runner->frameCount);")
        if options.zero_movement or options.mantle_sweep:
            anchor = "            hldReportAudioUpdate(runner->frameCount);"
            code = replace_once(code, anchor, anchor + "\n            extern void hldReportZeroMovement(int frame);\n"
                                "            hldReportZeroMovement(runner->frameCount);")
        if options.native_enemy or options.compact_enemy:
            anchor = "            hldReportAudioUpdate(runner->frameCount);"
            code = replace_once(code, anchor, anchor + "\n            extern void hldReportNativeEnemy(int frame);\n"
                                "            hldReportNativeEnemy(runner->frameCount);")
        if options.compiled_events:
            anchor = "            hldReportAudioUpdate(runner->frameCount);"
            code = replace_once(code, anchor, anchor + "\n            extern void hldReportCompiledEvents(int frame);\n"
                                "            hldReportCompiledEvents(runner->frameCount);")
        if options.zero_alpha_quads:
            anchor = "            hldReportAudioUpdate(runner->frameCount);"
            code = replace_once(code, anchor, anchor + "\n            extern void hldReportZeroAlphaQuads(int frame);\n"
                                "            hldReportZeroAlphaQuads(runner->frameCount);")
        if options.skip_transparent_batches:
            anchor = "            hldReportAudioUpdate(runner->frameCount);"
            code = replace_once(code, anchor, anchor + "\n            extern void hldReportTransparentBatches(int frame);\n"
                                "            hldReportTransparentBatches(runner->frameCount);")
        if sparse_compositing:
            anchor = "            hldReportAudioUpdate(runner->frameCount);"
            code = replace_once(code, anchor, anchor + "\n            extern void hldReportSparseComposite(int frame);\n"
                                "            hldReportSparseComposite(runner->frameCount);")
        if options.shared_collision_grid:
            anchor = "            hldReportAudioUpdate(runner->frameCount);"
            code = replace_once(code, anchor, anchor + "\n            extern void hldReportSharedCollisionGrid(int frame);\n"
                                "            hldReportSharedCollisionGrid(runner->frameCount);")
        if options.cpu_phase_profile:
            code = with_cpu_phase_profile(code)
        return code

    def prepare_runner(code):
        if options.persistent_line_chunks:
            code = with_line_chunk_bucket_hook(code)
        if options.stream_scenery_snapshot:
            code = with_scenery_snapshot_runner(code)
        code = with_fast_draw_sort(with_deferred_audio_runner(code), always=True)
        if options.retained_draw_order:
            code = with_retained_draw_order(code, windowed_report=True)
        if options.single_draw_responder:
            code = with_single_draw_responder(code, windowed_report=True)
        if options.resolved_step:
            code = with_resolved_step(code, windowed_report=True)
        if options.draw_order_repair:
            code = "#define HLD_DRAW_ORDER_REPAIR\n#define HLD_COMPARE_DRAW_ORDER_REPAIR\n" + code
        if options.bounded_round:
            code = "#define HLD_COMPARE_BOUNDED_ROUND\n" + code
        if mask_overlap or always_split_masks:
            code = "#define HLD_SPLIT_MASK_OVERLAP\n" + code
        if not integrated_runtime and (options.collision_blocks or options.playable_runtime or options.verify_runtime or
                options.playable_room_runtime or profile_runtime or options.borrowed_queries or
                options.placement_filters or options.grid_refresh or options.inline_bbox or options.collision_epoch):
            code = with_collision_bounds_blocks(code, compare=options.collision_blocks)
        if integrated_runtime:
            code = with_wide_collision_blocks(code)
        if options.borrow_collision_targets:
            code = with_collision_target_span(code, compare=True)
        if vector_broadphase:
            code = with_collision_grid(code, compare=True, use_existing_blocks=True, alternating=True,
                                       cells=32, quantized=options.quantized_broadphase, cell_grid=options.collision_cells)
        if options.collision_epoch or integrated_runtime:
            code = with_collision_epoch_runner(code, compare=options.collision_epoch)
        if options.shared_collision_grid:
            code = with_shared_collision_grid(code, compare=True)
        if options.profile_collision_phase:
            code = with_collision_phase_profile(code)
        if bbox_runtime:
            code = use_bbox_cache_refresh(code)
        if options.inline_bbox:
            code += r'''
void hldReportInlineBBox(Runner* runner) {
    const int frame = runner->frameCount;
    if (frame <= 0 || frame % 90 != 0) return;
    logInfo("Vita inline bounds frame=%d enabled=%d\n", frame, (int)Collision_useInlineBBox(frame - 1));
}
'''
        if options.collision_grid:
            code = with_collision_grid(code, compare=True)
        if options.wide_collision:
            code = with_wide_collision_blocks(code, compare=True)
        if options.resume_collision:
            code = with_resumed_collision_blocks(code, compare=True)
        if verify_runtime:
            code = with_room_phase_timings(code)
        if options.collision_order:
            anchor = "static int32_t pushSortedCollisionTargets(Runner* runner, int32_t target) {"
            code = replace_once(code, anchor, """static bool reuseCollisionOrder;
static bool collisionOrderInitialized;
static unsigned collisionOrderRequests;
static unsigned collisionOrderSorts;

void hldReportCollisionOrder(int frame) {
    if (frame <= 0 || frame % 90 != 0) return;
    logInfo("Vita collision order frame=%d reuse=%d requests=%u sorts=%u\\n",
            frame, (int)reuseCollisionOrder, collisionOrderRequests, collisionOrderSorts);
    collisionOrderRequests = collisionOrderSorts = 0;
}

""" + anchor + """
    if (!collisionOrderInitialized) {
        FILE* flag = fopen("ux0:data/hld-vm/collision-order-cache", "r");
        reuseCollisionOrder = flag != nullptr;
        if (flag != nullptr) fclose(flag);
        collisionOrderInitialized = true;
    }
    ++collisionOrderRequests;
    if (!reuseCollisionOrder) {
        int32_t base = Runner_pushInstancesOfObject(runner, target);
        size_t count = arrlenu(runner->instanceSnapshots) - base;
        if (count > 1) {
            ++collisionOrderSorts;
            qsort(runner->instanceSnapshots + base, count, sizeof(Instance*),
                  sortInstancesByObjectIndexThenInstanceIdAscending);
        }
        return base;
    }""")
            anchor = "            qsort(entry->sorted, count, sizeof(Instance*), sortInstancesByObjectIndexThenInstanceIdAscending);"
            code = replace_once(code, anchor, "            ++collisionOrderSorts;\n" + anchor)
        if options.early_draw_list:
            code += "\nvoid hldPrepareDrawList(Runner* runner) { rebuildDrawableCacheIfDirty(runner); }\n"
        return code

    def prepare_builtins(code):
        if options.stream_scenery_snapshot:
            code = with_scenery_snapshot_builtins(code)
        if options.walking_material:
            code = with_walking_material_query(code)
        code = with_deferred_audio_builtins(code)
        if options.quiet_shadow_log:
            anchor = "static RValue builtin_show_debug_message("
            code = replace_once(code, anchor, r'''extern bool hldQuietShadowLog;
static uint32_t hldShadowLogCount;

void hldReportQuietShadowLog(int frame) {
    if (frame <= 0 || frame % 90 != 0) return;
    logInfo("Vita shadow diagnostics frame=%d quiet=%d messages=%u\n", frame,
            (int)hldQuietShadowLog, hldShadowLogCount);
    hldShadowLogCount = 0;
}

''' + anchor)
            anchor = '    logInfo("Game: %s\\n", val);'
            code = replace_once(code, anchor, r'''    static const char shadowDiagnostic[] = "Found an object in shadow search-- i=";
    const bool shadowSearch = strncmp(val, shadowDiagnostic, sizeof(shadowDiagnostic) - 1) == 0;
    hldShadowLogCount += shadowSearch;
    if (!shadowSearch || !hldQuietShadowLog) logInfo("Game: %s\n", val);''')
        if options.audio_start_profile or options.lazy_audio_length:
            code = with_positional_play_profile(code)
        if options.sound_effect_cache:
            code = with_positional_effect_profile(code)
        if options.bounded_round:
            code = "#define HLD_COMPARE_BOUNDED_ROUND\n" + code
        if options.collision_epoch or integrated_runtime:
            code = with_collision_epoch_builtins(code)
        if options.borrowed_queries:
            return with_borrowed_queries(code, compare=True)
        if options.placement_filters or integrated_runtime:
            code = with_placement_filters(code, compare=options.placement_filters)
            if options.placement_cells or options.point_queries or indexed_runtime:
                code = with_placement_cell_queries(code, compare=not indexed_runtime, point=options.point_queries)
            if options.shape_candidate_cells:
                code = with_shape_candidate_cells(code, launch_flag="hldShapeCandidateCells")
            if flame_vent_reduction:
                code = with_flame_vent_probes(code)
            if options.room_updates or options.grouped_shadow:
                code = with_enemy_shadow_probes(code, grouped=options.grouped_shadow)
            if options.phase_mine_trigger:
                code = with_phase_mine_collision(code, launch_flag="hldPhaseMineQuery")
            if options.mantle_sweep:
                code = with_mantle_sweep_queries(code)
                if options.terrain_sweep:
                    code = with_terrain_sweep_query(code)
            if options.line_envelopes:
                code = with_line_envelopes(code, compare=True)
            elif options.ordered_line_candidates:
                code = with_ordered_line_candidates(
                    with_line_envelopes(code, compare=True, launch_flag="hldOrderedLineCandidates"))
                if options.line_chunks:
                    code = with_line_chunks(code, launch_flag="hldLineChunks",
                                            persistent=options.persistent_line_chunks,
                                            obstacle_buckets=options.obstacle_line_buckets)
            if options.placement_ids:
                code = with_placement_ids(code, launch_flag="hldPlacementIds")
            if options.vault_stack:
                code = with_line_mask_prep(code, launch_flag="hldPreparedLineMasks")
            return code
        if options.fast_text:
            return with_text_fastpaths(code, compare=True)
        if options.combat_queries:
            return with_collision_queries(code, compare=True, reject_bounds=True)
        return with_collision_queries(code, compare=True) if options.collision_query else code

    def prepare_vm(code):
        code = with_packed_literals(with_fast_vm_values(code), math=True,
                                    variables=options.combined_perf, branches=options.combined_perf,
                                    properties=options.instance_properties,
                                    arguments=options.script_arguments or integrated_runtime,
                                    argument_marker=not indexed_runtime)
        if options.collision_epoch or integrated_runtime:
            code = with_collision_epoch_vm(code)
        if options.script_call_buffers:
            code = with_script_call_buffers(code, compare=True)
        if options.scalar_pairs:
            code = with_scalar_pairs(code, compare=True)
        if options.fixed_array_reads:
            code = with_fixed_array_reads(code, compare=True)
        if options.switch_chains or indexed_runtime:
            code = with_switch_chains(code, compare=options.switch_chains)
        if options.stable_cases or combined_runtime:
            code = with_stable_cases(code, compare=options.stable_cases,
                                     link_checks=options.case_checks, case_check_launch=options.case_checks)
        if options.copy_blocks or options.idle_spawners or combined_runtime:
            code = with_copy_blocks(code, compare=options.copy_blocks)
        if options.idle_spawners or combined_runtime:
            code = with_idle_spawners(code, compare=options.idle_spawners)
        if options.point_queries:
            code = with_point_query_cleanup(code)
        if options.line_envelopes or options.ordered_line_candidates:
            code = with_line_envelope_cleanup(code)
        if options.scenery_batches:
            code = with_scenery_batches(code, compare=True)
        if options.ascii_parser or measured_runtime:
            code = with_ascii_parser(code, compare=options.ascii_parser)
        if options.pure_guards or measured_runtime:
            code = with_pure_guards(code, compare=options.pure_guards)
        if flame_vent_reduction:
            code = with_flame_vent_guard(code, compare=options.flame_vent_probes)
        if current_depth_helpers:
            code = with_depth_helpers(code, compare=options.depth_helpers)
        if options.room_updates:
            code = with_scenery_pass(with_enemy_shadow(code, compare=True), compare=True)
        if options.grouped_shadow:
            code = with_enemy_shadow(code, compare=True, grouped=True)
        if options.region_cases or options.resting_scenery or options.active_cases or options.runtime_refinements:
            code = with_region_cases(code, compare=options.region_cases or options.runtime_refinements)
        if options.resting_scenery or options.runtime_refinements:
            code = with_resting_scenery(code, compare=True)
        if options.runtime_refinements:
            code = with_refinement_gate(with_scenery_pass(code, compare=True), "useSceneryPass",
                                        "useRegionCases", "useRestingScenery")
        if options.active_cases or options.case_checks:
            code = with_active_case(code, compare=options.active_cases)
        if options.leaf_events or options.consolidated_runtime:
            code = with_leaf_events(code, compare=True)
            if options.consolidated_runtime:
                code = with_refinement_gate(code, "useLeafEvents", flag="hldConsolidatedRuntime")
        if options.array_pool or options.consolidated_runtime:
            code = with_array_pool_cleanup(code)
        if options.recessing_guards:
            code = with_recessing_guards(code, compare=True)
        if options.movement_helpers:
            code = with_movement_helpers(code, compare=True)
        if options.scene_queries:
            code = with_scene_queries(code, launch_flag="hldSceneQueries")
        if options.native_line_script:
            code = with_line_script(code, launch_flag="hldNativeLineScript", extra_args=options.line_script_arity,
                                    profile=options.optimized_profile)
        if options.palette_tail:
            code = with_palette_tail(code, launch_flag="hldPaletteTail")
        if options.floor_pattern:
            code = with_floor_pattern(code, launch_flag="hldFloorPattern")
        if options.phase_mine_trigger:
            code = with_phase_mine_trigger(code, launch_flag="hldPhaseMineQuery")
        if options.dormant_mine:
            code = with_dormant_mines(code, launch_flag="hldDormantMines")
        if options.mine_entry:
            code = with_mine_entry(code, checked=False, launch_flag="hldMineEntry")
        if options.vault_path_probe:
            code = with_enemy_path_probe(code, launch_flag="hldEnemyPathProbe")
        if options.motion_repeat:
            code = with_motion_repeat(code, launch_flag="hldMotionRepeat")
        if options.zero_movement or options.mantle_sweep:
            code = with_zero_movement(code, compare=True, sweep=options.mantle_sweep)
            if options.terrain_sweep:
                code = with_terrain_sweep_vm(code, compare=True)
        if options.reduced_vm_work:
            code = with_env_snapshot_pool(code, launch_flag="hldReducedVMWork")
            code = with_packed_read_conversions(code, launch_flag="hldReducedVMWork")
            code = with_scenery_scalar_writes(code, launch_flag="hldReducedVMWork")
        if options.stream_scenery_snapshot:
            code = with_scenery_snapshot(code, pool_flag="hldReducedVMWork")
        if options.instance_slot_reads:
            code = with_packed_slot_reads(code, launch_flag="hldInstanceSlotReads")
            if options.stream_slot_writes:
                code = with_packed_slot_writes(code)
        if verify_runtime:
            code = with_distortion_reference(code)
        if options.hologram_args:
            code = with_hologram_args(code, launch_flag="hldHologramArgs")
        if options.hologram_arrays:
            code = with_hologram_arrays(code, launch_flag="hldHologramArrays")
        if options.enemy_target:
            code = with_enemy_target(code, launch_flag="hldEnemyTarget")
        if options.idle_scenery:
            code = with_idle_scenery(code, launch_flag="hldIdleScenery")
        if options.walking_material:
            code = with_walking_material(code, launch_flag="hldWalkingMaterial")
        if (options.threaded_vm or options.playable_runtime or options.collision_grid or
                options.playable_room_runtime or integrated_runtime or isolated_runtime):
            code = with_threaded_vm(code, compare=options.threaded_vm, decode_at_handler=options.operand_dispatch,
                                    alternate_operands=options.operand_dispatch, cache_stack=options.stack_register,
                                    compare_cached_stack=options.stack_register)
        if options.scenery_draw_lists:
            code = with_scenery_draw_lists(code, launch_flag="hldSceneryDrawLists")
        if options.native_scenery_body:
            code = with_native_scenery_body(code, launch_flag="hldNativeSceneryBody")
            if options.scenery_skip:
                code = with_scenery_skip(code, launch_flag="hldScenerySkip")
                if options.scenery_guard:
                    code = reuse_scenery_skip_guard(code, launch_flag="hldScenerySkip", guard_flag="hldSceneryGuard")
                    if options.scenery_stream:
                        code = with_scenery_stream(code, launch_flag="hldSceneryStream")
        elif options.scenery_cursor:
            code = with_scenery_cursor(code)
        if options.eventless_case:
            code = with_eventless_case(code, launch_flag="hldEventlessCase")
        if options.compact_vm_code:
            code = with_compact_threaded_vm(code)
        if options.native_enemy or options.compact_enemy:
            code = with_native_enemy_event(code, compare=True, compact=options.compact_enemy)
        if options.compiled_events:
            code = with_compiled_events(code, compare=True)
        if options.focused_step_profile:
            code = with_focused_step_samples(code)
        elif profile_runtime or options.route_profile or options.simulation_profile or options.step_spike_profile or options.survey_profile:
            code = vm_profile(code)
        return code

    replacements.update([compile_source("loop.c", prepare_loop),
                         compile_source("runner.c", prepare_runner),
                         compile_source("vm.c", prepare_vm),
                         compile_source("vm_builtins.c", prepare_builtins)])
    if HLDC_TRANSLATE or os.environ.get("HLDC_SPLASH"):
        from splash_boot_source import with_splash_boot
        replacements.update([compile_source("vita/boot_progress.c", with_splash_boot)])
    if options.room_image_cache:
        replacements.update([compile_source("image/sprite_decode_cache.c",
                                            lambda code: with_room_image_cache(code, launch_flag="hldRoomImageCache"))])
    if options.array_pool or options.consolidated_runtime:
        launch_flag = "hldConsolidatedRuntime" if options.consolidated_runtime else None
        replacements.update([compile_source("gml_array.c", lambda code: with_array_pool(
            code, compare=True, launch_flag=launch_flag))])
    if options.audio_start_profile:
        replacements.update([compile_source("audio/openal/al_audio_system.c", with_stream_start_profile)])
    if options.lazy_audio_length:
        def prepare_audio(code):
            code = with_lazy_audio_length(with_stream_start_profile(code))
            if options.prefetch_ambience:
                code = with_ambient_prefetch_audio(code)
            elif options.memory_ambient:
                code = with_ambient_memory(code)
            if options.sound_effect_cache:
                code = with_decoded_effect_cache(code)
            if options.archive_audio_reads:
                code = with_footstep_start_timings(code)
            if HLDC_TRANSLATE:
                from effect_buffer_cache_source import with_effect_buffer_cache
                code = with_effect_buffer_cache(code)
            return code

        replacements.update([compile_source("audio/openal/al_audio_system.c", prepare_audio)])
    if HLDC_TRANSLATE:
        def prepare_overlay_fs(code):
            # A failed open of an existing file (e.g. out of handles) used to make LevelLoad build an empty room.
            # Log it with errno, let the audio code close its optional prefetched streams, and retry once.
            code = replace_once(code, '#include "overlay_file_system.h"', '#include "overlay_file_system.h"\n#include <errno.h>')
            opened = '    FILE* f = fopen(fullPath, "rb");\n    free(fullPath);'
            assert code.count(opened) == 2
            code = code.replace(opened, '    FILE* f = fopen(fullPath, "rb"); if (f == nullptr && errno != ENOENT) { '
                                'extern void hldReleaseOptionalFiles(void) __attribute__((weak)); const int firstError = errno; '
                                'if (hldReleaseOptionalFiles) hldReleaseOptionalFiles(); f = fopen(fullPath, "rb"); '
                                'logWarn("FS: open of %s failed (errno %d), retry %s\\n", fullPath, firstError, f != nullptr ? "worked" : "failed"); }\n'
                                '    free(fullPath);')
            # Whole-file reads (levels, room layer PNGs, sounds) went through fread, ~3 MB/s from the card. Read them with
            # sceIoRead instead (~3x faster); fread stays as the fallback. fopen keeps the error handling above.
            code = code.replace('#include <errno.h>', '#include <errno.h>\n#include <psp2/io/fcntl.h>\n'
                                'static size_t hldWholeRead(const char* path, void* buffer, size_t size) {\n'
                                '    const SceUID fd = sceIoOpen(path, SCE_O_RDONLY, 0);\n'
                                '    if (fd < 0) return (size_t) -1;\n'
                                '    size_t done = 0;\n'
                                '    while (done < size) {\n'
                                '        const size_t want = size - done > ((size_t) 4 << 20) ? ((size_t) 4 << 20) : size - done;\n'
                                '        const int got = sceIoRead(fd, (uint8_t*) buffer + done, want);\n'
                                '        if (got <= 0) break;\n'
                                '        done += (size_t) got;\n'
                                '    }\n'
                                '    sceIoClose(fd);\n'
                                '    return done == size ? done : (size_t) -1;\n'
                                '}\n', 1)
            def scoped(code, signature, edits):
                start = code.index(signature)
                end = code.index("\n}\n", start)
                body = code[start:end]
                for before, after in edits:
                    body = replace_once(body, before, after)
                return code[:start] + body + code[end:]
            code = scoped(code, "static bool overlayReadFileBinary(", [
                ("\n    free(fullPath);\n    if (f == nullptr) return false;\n", "\n    if (f == nullptr) { free(fullPath); return false; }\n"),
                ("    size_t bytesRead = fread(data, 1, (size_t) size, f);\n    fclose(f);\n",
                 "    size_t bytesRead = hldWholeRead(fullPath, data, (size_t) size);\n"
                 "    if (bytesRead != (size_t) size) bytesRead = fread(data, 1, (size_t) size, f);\n    fclose(f);\n    free(fullPath);\n")])
            code = scoped(code, "static char* overlayReadFileText(", [
                ("\n    free(fullPath);\n    if (f == nullptr) return nullptr;\n", "\n    if (f == nullptr) { free(fullPath); return nullptr; }\n"),
                ("    size_t bytesRead = fread(content, 1, (size_t) size, f);\n",
                 "    size_t bytesRead = hldWholeRead(fullPath, content, (size_t) size);\n"
                 "    if (bytesRead != (size_t) size) bytesRead = fread(content, 1, (size_t) size, f);\n    free(fullPath);\n")])
            if os.environ.get("HLDC_FAST_DIRLIST", "1") == "1":
                # file_find_* listings: entry type from readdir's own stat (fast_dirlist_source), ~73 s off the boot.
                from fast_dirlist_source import with_fast_dirlist
                code = with_fast_dirlist(code)
            return code
        replacements.update([compile_source("overlay_file_system.c", prepare_overlay_fs)])

    if options.archive_audio_reads:
        replacements.update([compile_source("data_win.c", with_archive_audio_reads)])
    if options.step_log_buffer:
        replacements.update([compile_source("vita/log.c", step_log_buffer)])
    if verify_runtime:
        def prepare_verification_renderer(code):
            if os.environ.get("HLDC_NO_BACKGROUND_PAGES") == "1":
                code = "#define HLD_NO_BACKGROUND_PAGES 1\n" + code
            if os.environ.get("HLDC_NO_UNIFORM_FLUSH") == "1":
                # Diagnostic: the renderer before uniform setters flushed the sprite batch.
                fix = " flushBatch((GLRenderer*) renderer); // GameMaker draws queued sprites before a uniform changes"
                assert code.count(fix) == 3
                code = code.replace(fix, "")
            if HLDC_TRANSLATE or os.environ.get("HLDC_MEMLOG"):
                from engine_speedups_source import with_texture_flush_renderer
                code = with_texture_flush_renderer(code)
            if options.retained_sprite_program:
                code = with_retained_sprite_program(code)
            if options.sprite_worker:
                code = with_sprite_packets(code)
            if options.graphics_commands:
                code = with_graphics_command_renderer(code, framebuffer_shadow=options.graphics_framebuffer,
                                                      draw_state=options.draw_state_packets,
                                                      extended=options.expanded_draw_packets)
            if options.surface_sprites:
                code = with_surface_sprite_renderer(code)
            if options.stream_matrix_cache:
                code = with_shader_matrix_cache(code)
            if options.plain_shader_trial:
                code = with_plain_shader_trial(code)
            return with_distortion_draw_trace(code)

        def prepare_log(code):
            code = step_log_buffer(code, "room", compare=options.verify_runtime)
            code = with_frame_log_batch(code) if options.buffered_frame_reports else code
            if HLDC_TRANSLATE and os.environ.get("HLDC_SYNC_LOG") != "1":
                from async_log_source import with_async_log
                code = with_async_log(code)
            return code

        replacements.update([compile_source("vita/log.c", prepare_log),
                             compile_source("gl/gl_renderer.c", prepare_verification_renderer)])
    elif buffer_room_logs:
        replacements.update([compile_source("vita/log.c", lambda code: step_log_buffer(code, "room", compare=False))])
    if options.grid_refresh:
        replacements.update([compile_source("spatial_grid.c", lambda code: with_grid_refresh(code, compare=True))])
    if bbox_runtime:
        def prepare_grid(code):
            if options.placement_cells or options.point_queries or indexed_runtime:
                code = with_placement_cell_grid(code)
            return with_line_chunk_grid_hook(code, persistent=options.persistent_line_chunks) if options.line_chunks else code

        replacements.update([compile_source("spatial_grid.c", prepare_grid)])
    if options.transparent_composite:
        replacements.update([compile_source("gl/gl_renderer.c", lambda code: with_transparent_composite(code, compare=True))])
    if options.zero_alpha_quads:
        replacements.update([compile_source("gl/gl_renderer.c", lambda code: with_zero_alpha_quads(code, compare=True))])
    if options.skip_transparent_batches:
        replacements.update([compile_source("gl/gl_renderer.c", lambda code: with_zero_alpha_quads(
            code, transparent_batches=True))])
    if sparse_compositing:
        def prepare_sparse_renderer(code):
            code = with_sparse_composite(code, compare=True, geometry=sparse_geometry,
                                         transparent_coverage=options.sparse_transparent_coverage)
            if options.trace_sparse_geometry or options.sparse_transparent_coverage:
                code = with_sparse_composite_trace(code, source_quads=options.sparse_transparent_coverage)
            return code
        replacements.update([compile_source("gl/gl_renderer.c", prepare_sparse_renderer)])
elif options.packed_vars:
    replacements.update([compile_source("loop.c", phase_profile),
                         compile_source("runner.c", lambda code: with_fast_draw_sort(code, always=True)),
                         compile_source("vm.c", lambda code: with_packed_literals(with_fast_vm_values(code), math=True, variables=True))])
elif options.axis_draw:
    def axis_loop(code):
        return replace_once(phase_profile(code),
            "            if (args.exitAtFrame >= 0 && runner->frameCount >= args.exitAtFrame) {",
            "            extern void hldReportAxisSprites(int frame);\n"
            "            hldReportAxisSprites(runner->frameCount);\n"
            "            if (args.exitAtFrame >= 0 && runner->frameCount >= args.exitAtFrame) {")
    replacements.update([compile_source("loop.c", axis_loop),
                         compile_source("runner.c", lambda code: with_fast_draw_sort(code, always=True)),
                         compile_source("vm.c", lambda code: with_packed_literals(with_fast_vm_values(code), math=True, variables=True)),
                         compile_source("gl/gl_renderer.c", lambda code: with_axis_sprite_draw(code, compare=True))])
if options.draw_state_packets:
    replacements.update([compile_source("gl_common/gl_common.c", lambda code: with_graphics_recording(
        code, draw_state=True, extended=options.expanded_draw_packets))])
link = [replacements.get(token, token) for token in link]
if HLDC_PART_OBJECTS:
    link[link.index("-o"):link.index("-o")] = HLDC_PART_OBJECTS
if options.vm_training or options.engine_training or os.environ.get("HLDC_PART_PGO") == "train":
    link.append("-lgcov")
if options.display_clear:
    library = build_display_clear_library(root, out, sdk, full_viewport_mask=options.full_viewport_mask,
                                         initial_scene_depth=options.initial_scene_depth)
    assert link.count("-lvitaGL") == 1
    link[link.index("-lvitaGL")] = str(library)
if options.surface_sprites:
    library = build_patched_vitagl_library(root, out, sdk, "surface-sprites", {"framebuffers": with_borrowed_surface_read})
    assert link.count("-lvitaGL") == 1
    link[link.index("-lvitaGL")] = str(library)
if options.vertex_programs:
    library = build_patched_vitagl_library(root, out, sdk, "vertex-programs", {"custom_shaders": with_vertex_program_cache})
    assert link.count("-lvitaGL") == 1
    link[link.index("-lvitaGL")] = str(library)
link += [str(bridge_obj), *[f"-Wl,--wrap={name}" for name in api]]
if os.environ.get("HLDC_DRAWSTATS") == "1":
    link += ["-Wl,--wrap=sceGxmBeginScene", "-Wl,--wrap=sceGxmDraw"]
elf = out / "butterscotch"
link[link.index("-o") + 1] = str(elf)
subprocess.run(link, cwd=build, check=True)
subprocess.run([sdk / "vita-elf-create", elf, out / "butterscotch.velf"], check=True)
subprocess.run([sdk / "vita-make-fself", "-c", out / "butterscotch.velf", out / "eboot.bin"], check=True)
# The package: eboot, a param.sfo made with the SDK's vita-mksfoex, and the LiveArea images in assets/livearea
# (placeholders made by scripts/make_livearea.py; put your own icon0.png / bg.png / startup.png there to replace them).
TITLE_ID = os.environ.get("HLD_TITLE_ID", "HLDV00001")
package = root / "build" / f"HyperLightDrifter-{TITLE_ID}.vpk"


def write_package(destination):
    sfo = out / "param.sfo"
    subprocess.run([sdk / "vita-mksfoex", "-s", f"TITLE_ID={TITLE_ID}", "-s", f"APP_VER={version}",
                    "-d", "ATTRIBUTE2=12", "Hyper Light Drifter", sfo], check=True)
    with ZipFile(destination, "w", ZIP_DEFLATED) as zipped:
        zipped.write(sfo, "sce_sys/param.sfo")
        zipped.write(out / "eboot.bin", "eboot.bin")
        art = root / "assets" / "livearea"
        zipped.write(art / "icon0.png", "sce_sys/icon0.png")
        for name in ("bg.png", "startup.png", "template.xml"):
            zipped.write(art / name, f"sce_sys/livearea/contents/{name}")
        # Optional Vita button prompt icons (app0:vita-icons), made from your own copy by scripts/prepare_game.py.
        for icon in sorted((root / "work" / "vita-icons").glob("*.png")):
            zipped.write(icon, f"vita-icons/{icon.name}")


package.parent.mkdir(parents=True, exist_ok=True)
write_package(package)
print(f"Serialized {len(api)} graphics entry points")
print(package, flush=True)
