"""v3 renderer shader patches for Azeroth Survivors v0.103.0 (used by stage_renderer.py).

Every patch is an exact-anchor text edit of one embedded shader's original source (LF). An anchor that
is missing or not unique raises, so a changed source fails the build instead of patching the wrong spot.
Each toggle is independent; the stage script applies the selected ones in a fixed order per shader.

Toggles (name -> shaders):
  terrain_light  terrain     sun diffuse per pixel on the interpolated MCNR normal (was per vertex);
                             point lights stay per vertex, the sum still clamps, now per pixel
  terrain_sheen  terrain     sun specular per pixel (shipped exponent and gain), same sheen mask
  mcsh_soft      terrain     5-tap filtered MCSH baked shadow (was one bilinear tap)
  model_rim      wow_model   rim light + specular on entity M2s (characters, creatures) only,
                             tinted by Light.dbc sun diffuse and row-9 specular colours
  fog            terrain, wow_model, static_gx, liquid, wow_effect
                             exterior fog band (start..end) shaped toward an exponential curve plus
                             low-lying haze below the camera; unchanged before fog start, never less
                             fog than shipped, same end distance, so
                             the far-clip wall stays hidden. Interior (WMO) fog is unchanged.
                             wdl is already the flat fog colour and needs no change.
  blob_soft      wow_effect  blob shadow (multiply decal) softened: 4-tap blur, slightly lighter
"""

from __future__ import annotations


def _sub(text: str, old: str, new: str, what: str) -> str:
    n = text.count(old)
    if n != 1:
        raise RuntimeError(f"{what}: anchor found {n} times, expected 1: {old[:70]!r}")
    return text.replace(old, new, 1)


# ---------------------------------------------------------------------------------------------- fog
FOG_FN = """
// renderer-upgrade v3 fog shaping. `lin` is the shipped GL_LINEAR factor (1 clear, 0 full fog), so
// 1 - lin is the position inside the fog band (0 at fog start, 1 at fog end). Only that band changes:
// the fog amount bends toward an exponential curve (more haze early in the band) and low-lying haze
// is added below the camera. Nothing changes before fog start, and never less fog than shipped, so
// the far-clip wall stays hidden.
const FOG_EXP: f32 = {exp:.4f};
const FOG_EXP_K: f32 = 3.0;
const FOG_HEIGHT: f32 = {height:.4f};
const FOG_HEIGHT_RANGE: f32 = 60.0;
fn wow_fog_shape(lin: f32, eye_z: f32, fog_end: f32, world_y: f32) -> f32 {{
    let band = clamp(1.0 - lin, 0.0, 1.0);
    let a_exp = (1.0 - exp(-FOG_EXP_K * band)) / (1.0 - exp(-FOG_EXP_K));
    var a = mix(band, max(band, a_exp), FOG_EXP);
    let below = clamp((view.world_position.y - world_y) / FOG_HEIGHT_RANGE, 0.0, 1.0);
    a = a + (1.0 - a) * FOG_HEIGHT * below * band;
    return 1.0 - clamp(a, 0.0, 1.0);
}}
"""


def _fog_fn(o: dict) -> str:
    return FOG_FN.format(exp=o["fog_exp"], height=o["fog_height"])


def _insert_before_first_vertex(text: str, block: str, what: str) -> str:
    return _sub(text, "\n@vertex\n", "\n" + block + "\n@vertex\n", what)


# ------------------------------------------------------------------------------------------ terrain
def terrain(text: str, toggles: set, o: dict) -> str:
    w = "terrain"
    if {"terrain_light", "terrain_sheen"} & toggles:
        text = _sub(text, "    @location(6) primary: vec3<f32>,\n}",
                    "    @location(6) primary: vec3<f32>,\n"
                    "    @location(7) normal: vec3<f32>,\n"
                    "    @location(8) points: vec3<f32>,\n}", w)
        text = _sub(text, "        vec3<f32>(1.0),\n    );\n    return out;\n}",
                    "        vec3<f32>(1.0),\n    );\n    out.normal = n;\n    out.points = points;\n"
                    "    return out;\n}", w)
        text = _sub(text, "    let primary = in.primary;\n",
                    "    let pn = normalize(in.normal);\n"
                    "    let pl = -normalize(wow_light.light_sun.xyz);\n"
                    + ("    let primary = clamp(wow_light.light_ambient.rgb + wow_light.light_diffuse.rgb"
                       " * max(dot(pn, pl), 0.0) + in.points, vec3<f32>(0.0), vec3<f32>(1.0));\n"
                       if "terrain_light" in toggles else "    let primary = in.primary;\n"), w)
    if "terrain_sheen" in toggles:
        text = _sub(text, "    let spec_term = in.specular * specmask * shadow_lit;\n",
                    "    let pv = normalize(view.world_position.xyz - in.world_position.xyz);\n"
                    "    let ph = normalize(pl + pv);\n"
                    f"    let pspec = clamp(wow_light.light_spec.rgb * pow(max(dot(pn, ph), 0.0), "
                    f"wow_light.light_spec.w * {o['sheen_shine']:.4f}) * {o['sheen_gain']:.4f}, "
                    "vec3<f32>(0.0), vec3<f32>(1.0));\n"
                    "    let spec_term = pspec * specmask * shadow_lit;\n", w)
    if "mcsh_soft" in toggles:
        r = o["mcsh_radius"]
        text = _sub(text,
                    "        let mcsh = textureSampleLevel(shadow_array, splat_samp, auv, i32(round(si)), 0.0).r;\n",
                    "        let sl = i32(round(si));\n"
                    f"        let sr = {r:.4f} / 64.0;\n"
                    "        let slo = vec2<f32>(0.5 / 64.0);\n"
                    "        let shi = vec2<f32>(1.0 - 0.5 / 64.0);\n"
                    "        var mcsh = 0.2 * textureSampleLevel(shadow_array, splat_samp, auv, sl, 0.0).r;\n"
                    "        mcsh += 0.2 * textureSampleLevel(shadow_array, splat_samp, clamp(auv + vec2<f32>(sr, sr), slo, shi), sl, 0.0).r;\n"
                    "        mcsh += 0.2 * textureSampleLevel(shadow_array, splat_samp, clamp(auv + vec2<f32>(-sr, sr), slo, shi), sl, 0.0).r;\n"
                    "        mcsh += 0.2 * textureSampleLevel(shadow_array, splat_samp, clamp(auv + vec2<f32>(sr, -sr), slo, shi), sl, 0.0).r;\n"
                    "        mcsh += 0.2 * textureSampleLevel(shadow_array, splat_samp, clamp(auv - vec2<f32>(sr, sr), slo, shi), sl, 0.0).r;\n",
                    w)
    if "fog" in toggles:
        text = _insert_before_first_vertex(text, _fog_fn(o), w)
        text = _sub(text,
                    "        let factor = clamp((wow_light.fog_params.y - eye_z) / denom, 0.0, 1.0);\n",
                    "        let factor = wow_fog_shape(clamp((wow_light.fog_params.y - eye_z) / denom, 0.0, 1.0), "
                    "eye_z, wow_light.fog_params.y, in.world_position.y);\n", w)
    return text


# ---------------------------------------------------------------------------------------- wow_model
def wow_model(text: str, toggles: set, o: dict) -> str:
    w = "wow_model"
    if "model_rim" in toggles:
        text = _sub(text,
                    "    if ((is_mod || is_mod2x) && !is_wmo) {\n        rgb = base.rgb;\n    }\n",
                    "    if ((is_mod || is_mod2x) && !is_wmo) {\n        rgb = base.rgb;\n    }\n"
                    "    // renderer-upgrade v3: rim light and specular on entity M2s (units: sun_scale.x >= 0.85).\n"
                    "    if (m.sun_scale.x >= 0.85 && !is_clutter && !is_emissive && !is_wmo && !is_rig && !is_interior\n"
                    "        && !is_mod && !is_mod2x\n"
                    "        && (u32(m.clutter_fade.z) & 4u) == 0u) {\n"
                    "        let rv = normalize(view.world_position.xyz - in.world_position.xyz);\n"
                    "        let rnv = clamp(dot(n_lit, rv), 0.0, 1.0);\n"
                    "        let rim = pow(1.0 - rnv, 3.0);\n"
                    "        let rh = normalize(L + rv);\n"
                    "        let lit_k = clamp(2.0 * dot(lit - wow_light.light_ambient.rgb, vec3<f32>(0.3333)), 0.0, 1.0);\n"
                    "        let spec = pow(max(dot(n_lit, rh), 0.0), 24.0) * select(0.0, 1.0, dot(n_lit, L) > 0.0) * lit_k;\n"
                    f"        rgb = clamp(rgb + albedo * wow_light.light_diffuse.rgb * (rim * {o['rim']:.4f})\n"
                    f"            + wow_light.light_spec.rgb * (spec * {o['model_spec']:.4f}), vec3<f32>(0.0), vec3<f32>(1.0));\n"
                    "    }\n", w)
    if "fog" in toggles:
        text = _insert_before_first_vertex(text, _fog_fn(o), w)
        text = _sub(text,
                    "        let factor = clamp((fog_span.y - eye_z) / denom, 0.0, 1.0);\n"
                    "        var fog_rgb = fog_color.xyz;\n",
                    "        var factor = clamp((fog_span.y - eye_z) / denom, 0.0, 1.0);\n"
                    "        if (!interior_fogged) {\n"
                    "            factor = wow_fog_shape(factor, eye_z, fog_span.y, in.world_position.y);\n"
                    "        }\n"
                    "        var fog_rgb = fog_color.xyz;\n", w)
    return text


# ---------------------------------------------------------------------------------------- static_gx
def static_gx(text: str, toggles: set, o: dict) -> str:
    w = "static_gx"
    if "fog" in toggles:
        text = _insert_before_first_vertex(text, _fog_fn(o), w)
        text = _sub(text,
                    "    if ((recs[in.word & 0xffffu].w & 16384u) != 0u) {\n"
                    "        fog_color = wow_light.wmo_fog_color;\n",
                    "    let fog_interior = (recs[in.word & 0xffffu].w & 16384u) != 0u;\n"
                    "    if (fog_interior) {\n"
                    "        fog_color = wow_light.wmo_fog_color;\n", w)
        text = _sub(text,
                    "        let factor = clamp((fog_span.y - eye_z) / denom, 0.0, 1.0);\n"
                    "        rgb = mix(fog_color.xyz, rgb, factor);\n",
                    "        var factor = clamp((fog_span.y - eye_z) / denom, 0.0, 1.0);\n"
                    "        if (!fog_interior) {\n"
                    "            factor = wow_fog_shape(factor, eye_z, fog_span.y, in.world_position.y);\n"
                    "        }\n"
                    "        rgb = mix(fog_color.xyz, rgb, factor);\n", w)
    return text


# ------------------------------------------------------------------------------------------- liquid
def liquid(text: str, toggles: set, o: dict) -> str:
    w = "liquid"
    if "fog" in toggles:
        text = _insert_before_first_vertex(text, _fog_fn(o), w)
        text = _sub(text,
                    "    var fog_color = wow_light.fog_color;\n"
                    "    var fog_span = wow_light.fog_params.xy;\n"
                    "    if (w.kind.z > 0.5 && room_fog != 0u) {\n",
                    "    var fog_color = wow_light.fog_color;\n"
                    "    var fog_span = wow_light.fog_params.xy;\n"
                    "    let fog_interior = w.kind.z > 0.5 && room_fog != 0u;\n"
                    "    if (fog_interior) {\n", w)
        text = _sub(text,
                    "    let factor = clamp((fog_span.y - eye_z) / denom, 0.0, 1.0);\n"
                    "    return mix(fog_color.xyz, rgb, factor);\n",
                    "    var factor = clamp((fog_span.y - eye_z) / denom, 0.0, 1.0);\n"
                    "    if (!fog_interior) {\n"
                    "        factor = wow_fog_shape(factor, eye_z, fog_span.y, world_pos.y);\n"
                    "    }\n"
                    "    return mix(fog_color.xyz, rgb, factor);\n", w)
    return text


# --------------------------------------------------------------------------------------- wow_effect
def wow_effect(text: str, toggles: set, o: dict) -> str:
    w = "wow_effect"
    if "blob_soft" in toggles:
        text = _sub(text,
                    "    let c = textureSampleBias(effect_texture, effect_sampler, in.uv, view.mip_bias) * in.color;\n",
                    "    var c = textureSampleBias(effect_texture, effect_sampler, in.uv, view.mip_bias) * in.color;\n"
                    "#ifdef DECAL_WORLD_CLIP\n"
                    "#ifdef BLEND_MULTIPLY\n"
                    "    // renderer-upgrade v3: soften the blob shadow's binary alpha disc and lighten it a little.\n"
                    f"    let bt = {o['blob_radius']:.4f} / vec2<f32>(textureDimensions(effect_texture));\n"
                    "    let cb = 0.25 * (textureSampleBias(effect_texture, effect_sampler, in.uv + bt, view.mip_bias)\n"
                    "        + textureSampleBias(effect_texture, effect_sampler, in.uv - bt, view.mip_bias)\n"
                    "        + textureSampleBias(effect_texture, effect_sampler, in.uv + vec2<f32>(bt.x, -bt.y), view.mip_bias)\n"
                    "        + textureSampleBias(effect_texture, effect_sampler, in.uv + vec2<f32>(-bt.x, bt.y), view.mip_bias));\n"
                    "    c = 0.5 * c + 0.5 * cb * in.color;\n"
                    f"    c = vec4<f32>(mix(c.rgb, vec3<f32>(1.0), {o['blob_lighten']:.4f}), c.a);\n"
                    "#endif\n"
                    "#endif\n", w)
    if "fog" in toggles:
        text = _insert_before_first_vertex(text, _fog_fn(o), w)
        text = _sub(text,
                    "        let factor = clamp((wow_light.fog_params.y - in.view_z) / denom, 0.0, 1.0);\n"
                    "        var fog_rgb = wow_light.fog_color.xyz;\n",
                    "        let factor = wow_fog_shape(clamp((wow_light.fog_params.y - in.view_z) / denom, 0.0, 1.0), "
                    "in.view_z, wow_light.fog_params.y, view.world_position.y);\n"
                    "        var fog_rgb = wow_light.fog_color.xyz;\n", w)
    return text


PATCHERS = {"terrain": terrain, "wow_model": wow_model, "static_gx": static_gx, "liquid": liquid,
            "wow_effect": wow_effect}
TOGGLE_SHADERS = {
    "terrain_light": ["terrain"], "terrain_sheen": ["terrain"], "mcsh_soft": ["terrain"],
    "model_rim": ["wow_model"], "fog": ["terrain", "wow_model", "static_gx", "liquid", "wow_effect"],
    "blob_soft": ["wow_effect"], "water": ["liquid"], "sharpen": ["ffx_glow"], "grade": ["ffx_glow"],
}
DEFAULTS = {"fog_exp": 0.5, "fog_height": 0.25, "sheen_shine": 1.0, "sheen_gain": 1.0, "mcsh_radius": 0.75,
            "rim": 0.35, "model_spec": 0.25, "blob_radius": 2.5, "blob_lighten": 0.15}
