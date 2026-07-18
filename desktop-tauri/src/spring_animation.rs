use crate::eui_neo::SpringAnimationConfig;

pub struct SpringAnimationEngine {
    config: SpringAnimationConfig,
    from: f64,
    to: f64,
    current: f64,
    velocity: f64,
    elapsed_ms: f64,
    running: bool,
}

impl SpringAnimationEngine {
    pub fn new(config: SpringAnimationConfig) -> Self {
        Self {
            config,
            from: 0.0,
            to: 0.0,
            current: 0.0,
            velocity: 0.0,
            elapsed_ms: 0.0,
            running: false,
        }
    }

    pub fn start(&mut self, config: &SpringAnimationConfig, from: f64, to: f64) {
        self.config = config.clone();
        self.from = from;
        self.to = to;
        self.current = from;
        self.velocity = 0.0;
        self.elapsed_ms = 0.0;
        self.running = true;
    }

    pub fn interrupt(&mut self, new_target: f64) {
        if !self.config.interruptible || !self.running {
            return;
        }
        self.from = self.current;
        self.to = new_target;
        self.elapsed_ms = 0.0;
    }

    pub fn update(&mut self, delta_ms: u32) -> f64 {
        if !self.running {
            return self.current;
        }

        self.elapsed_ms += delta_ms as f64;
        let t = self.elapsed_ms / 1000.0;

        let damping = self.config.damping;
        let stiffness = self.config.stiffness;
        let mass = self.config.mass;

        let omega = (stiffness / mass).sqrt();
        let displacement = self.to - self.from;

        let exp_decay = (-damping * t).exp();
        let cos_val = (omega * t).cos();

        let value = self.to - displacement * exp_decay * cos_val;

        if value.is_nan() || value.is_infinite() {
            self.current = self.to;
            self.running = false;
            return self.current;
        }

        self.current = value;

        if self.elapsed_ms >= self.config.max_duration_ms as f64 {
            self.current = self.to;
            self.running = false;
        }

        self.current
    }

    pub fn is_running(&self) -> bool {
        self.running
    }

    pub fn current_value(&self) -> f64 {
        self.current
    }
}

pub struct RubberBandEffect {
    factor: f64,
}

impl RubberBandEffect {
    pub fn new(factor: f64) -> Self {
        Self {
            factor: factor.min(1.0).max(0.0),
        }
    }

    pub fn apply(&self, offset: f64, boundary: f64, dimension: f64) -> f64 {
        let overshoot = offset - boundary;
        if overshoot.abs() < 0.001 {
            return boundary;
        }
        let sign = if overshoot > 0.0 { 1.0 } else { -1.0 };
        let clamped = (overshoot.abs() * dimension * self.factor)
            / (dimension + self.factor * overshoot.abs());
        boundary + sign * clamped
    }
}

pub struct DirectManipulation {
    active: bool,
    last_x: f64,
    last_y: f64,
}

impl DirectManipulation {
    pub fn new() -> Self {
        Self {
            active: false,
            last_x: 0.0,
            last_y: 0.0,
        }
    }

    pub fn begin(&mut self, x: f64, y: f64) {
        self.active = true;
        self.last_x = x;
        self.last_y = y;
    }

    pub fn move_to(&mut self, x: f64, y: f64) -> (f64, f64) {
        if !self.active {
            return (0.0, 0.0);
        }
        let dx = x - self.last_x;
        let dy = y - self.last_y;
        self.last_x = x;
        self.last_y = y;
        (dx, dy)
    }

    pub fn end(&mut self) {
        self.active = false;
    }

    pub fn is_active(&self) -> bool {
        self.active
    }
}

pub struct MaterialDepth {
    pub blur_radius: f64,
    pub background_opacity: f64,
    pub blur_available: bool,
}

impl MaterialDepth {
    pub fn panel() -> Self {
        Self {
            blur_radius: 20.0,
            background_opacity: 0.6,
            blur_available: true,
        }
    }

    pub fn overlay() -> Self {
        Self {
            blur_radius: 30.0,
            background_opacity: 0.7,
            blur_available: true,
        }
    }

    pub fn fallback_opacity(&self) -> f64 {
        if self.blur_available {
            self.background_opacity
        } else {
            (self.background_opacity + 0.3).min(1.0)
        }
    }
}
