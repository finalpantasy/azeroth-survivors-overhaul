// renderer-upgrade post pass, spliced into ffx_glow.wgsl ahead of `combine_body`.
// One full-screen pass already reads the resolved world scene once per pixel (the FFX combine,
// the frame's gamma decode); this adds edge anti-aliasing and contrast-adaptive sharpening to
// that read. The scene is in gamma space, so luma here is perceptual, as FXAA expects.
// - Edges (local luma range above the threshold): an FXAA-style edge walk and blend, which
//   reaches the alpha-tested foliage, clutter and shading edges MSAA does not.
// - Elsewhere: contrast-adaptive sharpening after AMD FidelityFX CAS (MIT): a cross filter
//   whose negative lobe shrinks where the local range already nears 0 or 1.
// Taps sit one *output* pixel apart (the larger of the uv step per window pixel and one scene
// texel): at renderScale 1.05 the scene is 1344 px wide but is resampled to 1280, and texel-spaced
// taps were mostly erased by that resample (runtime A/B 2026-10-19).
// Runtime and offline checks (simulate_post.py) found FXAA on top of MSAA 4 a net softening: it
// also blurs grass and ground texture detail. It therefore defaults off; sharpening defaults on.
// Constants below are rewritten by stage_renderer.py; 0.0 disables a stage. Derivatives are taken
// once, before any branch; the branches use textureSampleLevel only.

const POST_FXAA: f32 = 0.0;
const POST_FXAA_EDGE_MIN: f32 = 0.0833;
const POST_FXAA_EDGE_REL: f32 = 0.166;
const POST_FXAA_SUBPIX: f32 = 0.5;
const POST_SHARPEN: f32 = 0.5;
const POST_VIBRANCE: f32 = 0.0;

fn post_tap(uv: vec2<f32>) -> vec3<f32> {
    return clamp(textureSampleLevel(in_tex, in_samp, uv, 0.0).rgb, vec3<f32>(0.0), vec3<f32>(1.0));
}

fn post_luma(c: vec3<f32>) -> f32 {
    return dot(c, vec3<f32>(0.299, 0.587, 0.114));
}

fn post_sharpen(c: vec3<f32>, n: vec3<f32>, s: vec3<f32>, e: vec3<f32>, w: vec3<f32>) -> vec3<f32> {
    let mn = min(c, min(min(n, s), min(e, w)));
    let mx = max(c, max(max(n, s), max(e, w)));
    // Headroom to the nearer of 0 and 1, relative to the local maximum.
    let amp = sqrt(clamp(min(mn, vec3<f32>(1.0) - mx) / max(mx, vec3<f32>(1e-4)), vec3<f32>(0.0), vec3<f32>(1.0)));
    let lobe = amp * vec3<f32>(-0.125);
    let sharp = (c + (n + s + e + w) * lobe) / (vec3<f32>(1.0) + 4.0 * lobe);
    return mix(c, clamp(sharp, vec3<f32>(0.0), vec3<f32>(1.0)), POST_SHARPEN);
}

fn post_fxaa(
    uv: vec2<f32>, texel: vec2<f32>, lc: f32, ln: f32, ls: f32, le: f32, lw: f32, range: f32,
) -> vec3<f32> {
    let lnw = post_luma(post_tap(uv + vec2<f32>(-texel.x, -texel.y)));
    let lne = post_luma(post_tap(uv + vec2<f32>(texel.x, -texel.y)));
    let lsw = post_luma(post_tap(uv + vec2<f32>(-texel.x, texel.y)));
    let lse = post_luma(post_tap(uv + vec2<f32>(texel.x, texel.y)));
    let edge_h = abs(lnw + lsw - 2.0 * lw) + 2.0 * abs(ln + ls - 2.0 * lc) + abs(lne + lse - 2.0 * le);
    let edge_v = abs(lnw + lne - 2.0 * ln) + 2.0 * abs(lw + le - 2.0 * lc) + abs(lsw + lse - 2.0 * ls);
    // A horizontal edge blends across y and is walked along x.
    let horizontal = edge_h >= edge_v;
    let l_neg = select(lw, ln, horizontal);
    let l_pos = select(le, ls, horizontal);
    let g_neg = abs(l_neg - lc);
    let g_pos = abs(l_pos - lc);
    let neg_steepest = g_neg >= g_pos;
    let grad_scaled = 0.25 * max(g_neg, g_pos);
    var step_len = select(texel.x, texel.y, horizontal);
    var local_avg = 0.5 * (l_pos + lc);
    if neg_steepest {
        step_len = -step_len;
        local_avg = 0.5 * (l_neg + lc);
    }
    var edge_uv = uv;
    if horizontal {
        edge_uv.y += 0.5 * step_len;
    } else {
        edge_uv.x += 0.5 * step_len;
    }
    let along = select(vec2<f32>(0.0, texel.y), vec2<f32>(texel.x, 0.0), horizontal);
    var uv_a = edge_uv - along;
    var uv_b = edge_uv + along;
    var end_a = post_luma(post_tap(uv_a)) - local_avg;
    var end_b = post_luma(post_tap(uv_b)) - local_avg;
    var done_a = abs(end_a) >= grad_scaled;
    var done_b = abs(end_b) >= grad_scaled;
    var steps = array<f32, 5>(1.5, 2.0, 2.0, 4.0, 8.0);
    for (var i = 0; i < 5; i++) {
        if done_a && done_b {
            break;
        }
        if !done_a {
            uv_a -= along * steps[i];
            end_a = post_luma(post_tap(uv_a)) - local_avg;
            done_a = abs(end_a) >= grad_scaled;
        }
        if !done_b {
            uv_b += along * steps[i];
            end_b = post_luma(post_tap(uv_b)) - local_avg;
            done_b = abs(end_b) >= grad_scaled;
        }
    }
    let dist_a = select(uv.y - uv_a.y, uv.x - uv_a.x, horizontal);
    let dist_b = select(uv_b.y - uv.y, uv_b.x - uv.x, horizontal);
    let a_nearer = dist_a < dist_b;
    let dist = min(dist_a, dist_b);
    let span = max(dist_a + dist_b, 1e-6);
    let center_smaller = lc < local_avg;
    let end_near = select(end_b, end_a, a_nearer);
    let good_span = (end_near < 0.0) != center_smaller;
    var offset = select(0.0, 0.5 - dist / span, good_span);
    // Sub-pixel aliasing: a lone bright or dark pixel blends toward its 3x3 average.
    let avg = (2.0 * (ln + ls + le + lw) + lnw + lne + lsw + lse) / 12.0;
    let sub_a = clamp(abs(avg - lc) / range, 0.0, 1.0);
    let sub_b = (-2.0 * sub_a + 3.0) * sub_a * sub_a;
    offset = max(offset, sub_b * sub_b * POST_FXAA_SUBPIX);
    var out_uv = uv;
    if horizontal {
        out_uv.y += offset * step_len;
    } else {
        out_uv.x += offset * step_len;
    }
    return post_tap(out_uv);
}

fn post_vibrance(c: vec3<f32>) -> vec3<f32> {
    let l = post_luma(c);
    let sat = max(c.r, max(c.g, c.b)) - min(c.r, min(c.g, c.b));
    // Lifts muted colours more than saturated ones, so skin and UI-like hues do not clip.
    return clamp(mix(vec3<f32>(l), c, 1.0 + POST_VIBRANCE * (1.0 - sat)), vec3<f32>(0.0), vec3<f32>(1.0));
}

// The scene colour the combine uses, already clamped to [0, 1] like the original `sg`.
fn post_scene(uv: vec2<f32>, center: vec3<f32>) -> vec3<f32> {
    let scene_texel = 1.0 / vec2<f32>(textureDimensions(in_tex));
    let texel = max(abs(vec2<f32>(dpdx(uv.x), dpdy(uv.y))), scene_texel);
    let c = clamp(center, vec3<f32>(0.0), vec3<f32>(1.0));
    let n = post_tap(uv + vec2<f32>(0.0, -texel.y));
    let s = post_tap(uv + vec2<f32>(0.0, texel.y));
    let e = post_tap(uv + vec2<f32>(texel.x, 0.0));
    let w = post_tap(uv + vec2<f32>(-texel.x, 0.0));
    let lc = post_luma(c);
    let ln = post_luma(n);
    let ls = post_luma(s);
    let le = post_luma(e);
    let lw = post_luma(w);
    let lmax = max(lc, max(max(ln, ls), max(le, lw)));
    let lmin = min(lc, min(min(ln, ls), min(le, lw)));
    let range = lmax - lmin;
    var res = c;
    if POST_FXAA > 0.0 && range >= max(POST_FXAA_EDGE_MIN, lmax * POST_FXAA_EDGE_REL) {
        res = post_fxaa(uv, texel, lc, ln, ls, le, lw, range);
    } else if POST_SHARPEN > 0.0 {
        res = post_sharpen(c, n, s, e, w);
    }
    if POST_VIBRANCE != 0.0 {
        res = post_vibrance(res);
    }
    return res;
}

