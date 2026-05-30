#version 330 core

// ---------------------------------------------------------------------------
// chromatic_aberration.frag
// Хроматическая аберрация с угловым, радиальным и анимированным контролем.
//
// Uniforms:
//   u_tex            — входная текстура
//   u_resolution     — разрешение фрейма (px), зарезервировано для будущих FX
//   u_time           — время в секундах (для u_chromatic_spin)
//   u_mix            — wet/dry: 0.0 = исходник, 1.0 = полный эффект
//   u_offset         — базовая величина сдвига каналов [0..0.05]
//   u_radial         — радиальное усиление к краям [0=равномерно, 1=только края]
//   u_angle          — угол направления аберрации в радианах [0..6.28]
//   u_chromatic_spin — скорость анимированного вращения угла [0..1]
// ---------------------------------------------------------------------------

uniform sampler2D u_tex;
uniform vec2      u_resolution;
uniform float     u_time;
uniform float     u_mix;

uniform float u_offset;         // базовая величина сдвига
uniform float u_radial;         // радиальное усиление [0..1]
uniform float u_angle;          // угол аберрации [0..6.28]
uniform float u_chromatic_spin; // скорость вращения угла по времени [0..1]

in  vec2 v_uv;
out vec4 out_color;

void main() {
    // --- dry сигнал ---
    vec3 dry = texture(u_tex, v_uv).rgb;

    // --- итоговый угол: статичный + анимированный спин ---
    // spin добавляет полный оборот за 1 секунду при u_chromatic_spin = 1.0
    float angle = u_angle + u_time * u_chromatic_spin * 6.28318530718;

    // --- базовый вектор сдвига по углу ---
    vec2 dir = vec2(cos(angle), sin(angle)) * u_offset;

    // --- радиальная модуляция ---
    // при u_radial=0: множитель = 1.0 (равномерно по всему кадру)
    // при u_radial=1: множитель = distance*2 (усиление только к краям, 0 в центре)
    float dist = length(v_uv - 0.5) * 2.0; // [0..1] от центра до угла
    float radialFactor = dist * u_radial + (1.0 - u_radial);
    dir *= radialFactor;

    // --- сэмплирование трёх каналов со сдвигом ---
    // R смещён в направлении dir, G без сдвига, B — в обратную сторону
    float r = texture(u_tex, v_uv + dir).r;
    float g = texture(u_tex, v_uv).g;
    float b = texture(u_tex, v_uv - dir).b;
    vec3 wet = vec3(r, g, b);

    // --- wet/dry микс ---
    out_color = vec4(mix(dry, wet, u_mix), 1.0);
}
