//! ModMatrix — LFOs (and later audio envelopes) routed to parameters, giving
//! the "human"/animated motion v1 had. Phase 6 brings the visual editor; this
//! module is the deterministic engine underneath.

#![allow(dead_code)]

use std::f32::consts::PI;

#[derive(Clone, Copy, PartialEq)]
pub enum Wave {
    Sine,
    Triangle,
    Saw,
    Square,
}

#[derive(Clone)]
pub struct Lfo {
    pub wave: Wave,
    pub freq: f32,  // Hz
    pub depth: f32, // amplitude of the raw [-1,1] shape
    pub bias: f32,  // DC offset
    pub phase: f32, // 0..1 phase offset
}

impl Lfo {
    pub fn new(wave: Wave, freq: f32) -> Self {
        Self { wave, freq, depth: 1.0, bias: 0.0, phase: 0.0 }
    }

    /// Value at time `t` seconds: `bias + depth * shape(phase)`.
    pub fn value(&self, t: f32) -> f32 {
        let p = (t * self.freq + self.phase).rem_euclid(1.0); // 0..1
        let raw = match self.wave {
            Wave::Sine => (2.0 * PI * p).sin(),
            Wave::Triangle => 1.0 - 4.0 * (p - 0.5).abs(), // +1 at p=0.5, -1 at edges
            Wave::Saw => 2.0 * p - 1.0,
            Wave::Square => {
                if p < 0.5 {
                    1.0
                } else {
                    -1.0
                }
            }
        };
        self.bias + self.depth * raw
    }
}

/// Routes an LFO's output to a named parameter target, scaled by `amount`.
#[derive(Clone)]
pub struct Route {
    pub lfo: usize,
    pub target: String,
    pub amount: f32,
}

#[derive(Clone, Default)]
pub struct ModMatrix {
    pub lfos: Vec<Lfo>,
    pub routes: Vec<Route>,
}

impl ModMatrix {
    /// Summed modulation for `target` at time `t` (None if no route hits it).
    pub fn value_for(&self, target: &str, t: f32) -> Option<f32> {
        let mut acc: Option<f32> = None;
        for r in &self.routes {
            if r.target == target {
                if let Some(l) = self.lfos.get(r.lfo) {
                    acc = Some(acc.unwrap_or(0.0) + l.value(t) * r.amount);
                }
            }
        }
        acc
    }
}

/// Headless check: LFO shapes hit known values and routing sums correctly.
pub fn selftest() -> Result<String, String> {
    let approx = |a: f32, b: f32| (a - b).abs() <= 1e-3;

    let sine = Lfo::new(Wave::Sine, 1.0); // depth 1, bias 0, phase 0
    if !approx(sine.value(0.0), 0.0) {
        return Err(format!("sine(0)={}", sine.value(0.0)));
    }
    if !approx(sine.value(0.25), 1.0) {
        return Err(format!("sine(0.25)={}", sine.value(0.25)));
    }
    if !approx(sine.value(0.5), 0.0) {
        return Err(format!("sine(0.5)={}", sine.value(0.5)));
    }

    let saw = Lfo::new(Wave::Saw, 1.0);
    if !approx(saw.value(0.0), -1.0) || !approx(saw.value(0.5), 0.0) {
        return Err(format!("saw(0)={}, saw(0.5)={}", saw.value(0.0), saw.value(0.5)));
    }

    let square = Lfo::new(Wave::Square, 1.0);
    if !approx(square.value(0.1), 1.0) || !approx(square.value(0.6), -1.0) {
        return Err("square shape wrong".into());
    }

    // routing: two LFOs onto one target sum
    let mm = ModMatrix {
        lfos: vec![Lfo::new(Wave::Sine, 1.0), Lfo::new(Wave::Saw, 1.0)],
        routes: vec![
            Route { lfo: 0, target: "mix".into(), amount: 0.5 },
            Route { lfo: 1, target: "mix".into(), amount: 0.5 },
        ],
    };
    // at t=0.25: sine=1*0.5=0.5, saw=(2*0.25-1)= -0.5 *0.5 = -0.25 → 0.25
    let v = mm.value_for("mix", 0.25).ok_or("no route value")?;
    if !approx(v, 0.25) {
        return Err(format!("routed sum={v}, want 0.25"));
    }
    if mm.value_for("none", 0.0).is_some() {
        return Err("unexpected value for unrouted target".into());
    }

    Ok("LFO sine/saw/square + routing sums verified".into())
}
