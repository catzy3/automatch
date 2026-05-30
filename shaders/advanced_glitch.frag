#version 330 core
// ─────────────────────────────────────────────────────────────────────────────
//  advanced_glitch — block-level horizontal shift with per-channel colour split
//
//  The screen is partitioned into rectangular blocks whose height is
//  determined by u_block_size.  Each block independently draws a random
//  value each "tick" (rate controlled by u_speed).  If that value falls
//  below u_chance the block is flagged as "glitched":
//
//    1. The entire block shifts horizontally by u_shift_amount (signed,
//       so some blocks go left and some go right — determined per-block).
//
//    2. Inside the shifted block the R channel is sampled from a position
//       offset by +u_color_shift UV units in X, and the B channel from
//       -u_color_shift.  G stays at the shifted position.  This mimics
//       the RGB fringing seen when horizontal sync is lost mid-scanline.
//
//  Blocks that don't trigger remain at their original UV coordinates with
//  no colour separation.
//
//  A second hash dimension per block randomises the shift direction (+/-)
//  independently of the glitch probability roll, giving the characteristic
//  broken-cassette look where adjacent blocks fly in opposite directions.
// ─────────────────────────────────────────────────────────────────────────────

uniform sampler2D u_tex;
uniform vec2      u_resolution;
uniform float     u_time;
uniform float     u_mix;        // 0 = dry, 1 = full wet

// ── effect-specific uniforms ─────────────────────────────────────────────────
uniform float u_block_size;    // 0..1    — normalised block height (maps to 0.01..0.15 UV)
uniform float u_shift_amount;  // 0..0.1  — max horizontal UV shift per block
uniform float u_chance;        // 0..1    — per-block glitch probability
uniform float u_color_shift;   // 0..1    — R/B channel split width in UV X (mapped 0..0.05)
uniform float u_speed;         // 0..4    — pattern update rate in ticks/second

in  vec2 v_uv;
out vec4 fragColor;

// ── hash helpers (same family as datamosh.frag / glitch.frag) ────────────────
float hash11(float p) { p = fract(p * 0.1031); p *= p + 33.33; p *= p + p; return fract(p); }
float hash21(vec2 p) {
    vec3 p3 = fract(vec3(p.xyx) * 0.1031);
    p3 += dot(p3, p3.yzx + 33.33);
    return fract((p3.x + p3.y) * p3.z);
}

void main() {
    vec3 dry = texture(u_tex, v_uv).rgb;

    // ── map normalised parameters to working ranges ───────────────────────────
    // block height: 0.01 UV (very thin scanline bands) → 0.15 UV (big macroblocks)
    float blockH = mix(0.01, 0.15, clamp(u_block_size, 0.0, 1.0));

    // colour split: 0 → 0.05 UV units
    float chSplit = clamp(u_color_shift, 0.0, 1.0) * 0.05;

    // tick counter: drives pattern refresh rate
    // u_speed = 0 → 0.25 ticks/s (near-frozen); u_speed = 4 → 60 ticks/s (frantic)
    float tickRate = mix(0.25, 60.0, clamp(u_speed, 0.0, 4.0) / 4.0);
    float tick     = floor(u_time * tickRate);

    // ── determine which block row this pixel belongs to ───────────────────────
    float blockRow = floor(v_uv.y / blockH);   // integer row index

    // seed uses both row index and tick so adjacent blocks differ AND the
    // pattern changes over time
    vec2 blockSeed = vec2(blockRow, tick);

    // ── per-block rolls ───────────────────────────────────────────────────────
    float roll      = hash21(blockSeed);                        // 0..1 glitch trigger
    float dirRoll   = hash21(blockSeed + vec2(17.3, 5.9));     // 0..1 → direction sign
    float shiftSign = (dirRoll > 0.5) ? 1.0 : -1.0;

    // ── apply glitch if roll < chance ────────────────────────────────────────
    // shiftX: the horizontal UV offset for the whole block
    float shiftX = (roll < u_chance)
                    ? shiftSign * u_shift_amount
                    : 0.0;

    // wrap-around so pixels shifted off-screen re-appear on the other side
    // (matches real hardware where the video signal wraps the scanline)
    vec2 shiftedUV = vec2(fract(v_uv.x + shiftX), v_uv.y);

    vec3 wet;
    if (roll < u_chance && chSplit > 1e-5) {
        // ── colour-split sample: R shifts right, B shifts left, G stays ──────
        //  The additional ±chSplit is applied on top of the block shift, so the
        //  fringing happens within the already-displaced block.
        float r = texture(u_tex, vec2(fract(shiftedUV.x + chSplit), shiftedUV.y)).r;
        float g = texture(u_tex, shiftedUV).g;
        float b = texture(u_tex, vec2(fract(shiftedUV.x - chSplit), shiftedUV.y)).b;
        wet = vec3(r, g, b);
    } else {
        // block is shifted but no colour split (or split is zero)
        wet = texture(u_tex, shiftedUV).rgb;
    }

    // ── dry/wet ───────────────────────────────────────────────────────────────
    fragColor = vec4(mix(dry, wet, clamp(u_mix, 0.0, 1.0)), 1.0);
}
