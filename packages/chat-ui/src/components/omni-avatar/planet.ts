// Reused from omniOS src/omni/planet.ts with a local motion adapter; sleep keeps the full-colour palette.
// Omni, drawn: the omniOS mark come alive. A small ringed planet with a face, wrapped in energy
// that carries its mood. The body is the mark's sphere (its gradient, a soft highlight and rim, faint
// bands drifting across it like a slow-turning gas giant) floating gently; the face, big rounded
// eyes with a sparkle and a blush, blinks at irregular intervals and lets its gaze wander, never
// staring; the ring is a band of flowing light passing behind and in front of it; around it an
// aura breathes and a few sparks orbit.
//
// The energy is the mood (mood.ts): idle it drifts; listening, the aura swells with your voice;
// thinking, the ring runs and the sparks race; speaking, ripples spread from it; waiting on you, it
// glows warm and looks your way; happy, its eyes curve and sparks burst; asleep, its eyes close, the ring
// all but stops and Zs rise (drawn by the component).
//
// Canvas 2D, for every device down to a Fire TV: detail by size (a bar icon is the planet, ring
// and eyes; a stage gets the aura, sparks and ripples), frames only as often as the size needs,
// none while out of sight or under reduced motion.
import { reducedMotion, useMotion } from './motion';

export type OmniMood =
  'asleep' | 'idle' | 'attentive' | 'listening' | 'thinking' | 'speaking' | 'waiting' | 'happy' | 'restless';

export interface PlanetOptions {
  size: number;
  mood: OmniMood;
  // 0..1: how loud you are (listening) or it is (speaking)
  level?: number;
  // the mark's colours: light, mid and deep of the sphere, the ring's two ends
  palette?: Palette;
}

export interface Palette {
  light: string;
  mid: string;
  deep: string;
  ringA: string;
  ringB: string;
  energy: string;
  warm: string;
}

export const MARK_PALETTE: Palette = {
  light: '#a8e4ff',
  mid: '#5ac8fa',
  deep: '#4b48c8',
  ringA: '#8fe0ff',
  ringB: '#6f6cf0',
  energy: '#9ee8ff',
  warm: '#ffd58a',
};

type V = { x: number; y: number };
const damp = (from: number, to: number, rate: number, dt: number) => to + (from - to) * Math.exp(-rate * dt);
const TAU = Math.PI * 2;
const TILT = (-18 * Math.PI) / 180;

interface Spark {
  a: number; // angle on its orbit
  speed: number;
  rx: number; // orbit radii, in planet radii
  ry: number;
  tilt: number;
  size: number;
}

interface Ripple {
  t: number; // age, seconds
  strength: number;
}

export class OmniPlanet {
  private ctx: CanvasRenderingContext2D;
  private o: Required<PlanetOptions>;
  private t = Math.random() * 10;
  // eyes: open 0..1 (1 shut), gaze in −1..1, a spring
  private lid = 0;
  private gaze: V = { x: 0, y: 0.15 };
  private gazeTarget: V = { x: 0, y: 0.15 };
  private gazeVel: V = { x: 0, y: 0 };
  private untilBlink = 2;
  private blinking = 0;
  private untilGlance = 1.5;
  private lookingAtYou = 0;
  private lastSide = 1;
  // energy: how much there is, how fast the ring flows, where the flow is
  private energy = 0.4;
  private flow = 0;
  private flowSpeed = 0.2;
  private spin = 0;
  private glow = 0;
  private warm = 0;
  private dim = 0;
  private joy = 0;
  private bob = 0;
  private lean = 0;
  private level = 0;
  private sparks: Spark[] = [];
  private ripples: Ripple[] = [];
  private untilRipple = 0;
  private wasMood: OmniMood;
  private frame = 0;
  private last = 0;
  private paused = false;
  // (motion reduced: one still frame, drawn again as it changes, rather than a running loop;
  // followed live, as it's turned on or off)
  private get reduced() {
    return reducedMotion();
  }
  private unmotion = useMotion.subscribe((m, prev) => {
    if (m.on === prev.on) return;
    cancelAnimationFrame(this.frame);
    this.frame = 0;
    this.start();
  });

  constructor(
    private canvas: HTMLCanvasElement,
    opts: PlanetOptions
  ) {
    this.ctx = canvas.getContext('2d')!;
    this.o = { level: 0, palette: MARK_PALETTE, ...opts };
    this.wasMood = this.o.mood;
    for (let i = 0; i < 5; i++)
      this.sparks.push({
        a: Math.random() * TAU,
        speed: 0.4 + Math.random() * 0.5,
        rx: 1.35 + Math.random() * 0.5,
        ry: 0.35 + Math.random() * 0.35,
        tilt: TILT + (Math.random() - 0.5) * 0.9,
        size: 0.035 + Math.random() * 0.03,
      });
    this.settle();
    this.resize();
    this.start();
  }

  update(opts: Partial<PlanetOptions>) {
    const resized = opts.size !== undefined && opts.size !== this.o.size;
    this.o = { ...this.o, ...opts };
    if (resized) this.resize();
    this.start();
  }

  setPaused(paused: boolean) {
    if (this.paused === paused) return;
    this.paused = paused;
    if (paused) cancelAnimationFrame(this.frame);
    this.frame = 0;
    if (!paused) this.start();
  }

  dispose() {
    this.paused = true;
    cancelAnimationFrame(this.frame);
    this.unmotion();
  }

  private resize() {
    const dpr = Math.min(2, devicePixelRatio || 1);
    const px = Math.max(1, Math.round(this.o.size * dpr));
    this.canvas.width = px;
    this.canvas.height = px;
  }

  // Arrive in the mood rather than animating into it
  private settle() {
    const m = this.o.mood;
    this.energy = m === 'asleep' ? 0.1 : m === 'thinking' ? 1 : 0.4;
    this.dim = 0;
    this.lid = m === 'asleep' ? 1 : 0;
  }

  // Frames as often as the size needs: a stage at full rate, an icon a few times a second
  private get frameGap() {
    // (a TV app's page shares the TV's modest GPU with the video: half rate)
    if (this.o.size >= 120)
      return typeof document !== 'undefined' && document.documentElement.classList.contains('tv') ? 1000 / 30 : 0;
    if (this.o.size >= 48) return 1000 / 30;
    return 1000 / 12;
  }

  private start() {
    if (this.paused || this.frame) return;
    if (this.reduced) {
      this.paint();
      return;
    }
    const loop = (now: number) => {
      this.frame = 0;
      if (this.paused) return;
      const gap = this.frameGap;
      if (this.last && now - this.last < gap - 1) {
        this.frame = requestAnimationFrame(loop);
        return;
      }
      const dt = this.last && now - this.last < 250 ? (now - this.last) / 1000 : 1 / 60;
      this.last = now;
      this.step(Math.min(0.1, dt));
      this.paint();
      this.frame = requestAnimationFrame(loop);
    };
    this.frame = requestAnimationFrame(loop);
  }

  private glance(): V {
    if (Math.random() < 0.15) return { x: (Math.random() - 0.5) * 0.3, y: 0.1 };
    this.lastSide = -this.lastSide;
    return { x: this.lastSide * (0.3 + Math.random() * 0.4), y: (Math.random() - 0.3) * 0.5 };
  }

  private step(dt: number) {
    const m = this.o.mood;
    if (m !== this.wasMood) {
      if (m === 'attentive' || m === 'waiting' || m === 'listening') this.lookingAtYou = 1.6;
      if (m === 'happy') this.burst();
      this.wasMood = m;
    }
    this.t += dt;
    this.level = damp(this.level, this.o.level, this.o.level > this.level ? 18 : 4, dt);

    // ── Energy, by mood ──
    const target = {
      asleep: { energy: 0.08, flow: 0.04, spin: 0, glow: 0.05, warm: 0, dim: 0, joy: 0, lean: 0 },
      idle: { energy: 0.35, flow: 0.18, spin: 0.15, glow: 0.3, warm: 0, dim: 0, joy: 0, lean: 0 },
      restless: { energy: 0.5, flow: 0.3, spin: 0.25, glow: 0.35, warm: 0, dim: 0, joy: 0, lean: 0 },
      attentive: { energy: 0.6, flow: 0.3, spin: 0.25, glow: 0.55, warm: 0, dim: 0, joy: 0, lean: 1 },
      listening: {
        energy: 0.7 + this.level * 0.3,
        flow: -0.35,
        spin: 0.2,
        glow: 0.6 + this.level * 0.4,
        warm: 0,
        dim: 0,
        joy: 0,
        lean: 1,
      },
      thinking: { energy: 1, flow: 0.9, spin: 1.2, glow: 0.7, warm: 0, dim: 0, joy: 0, lean: 0 },
      speaking: {
        energy: 0.8,
        flow: 0.45,
        spin: 0.4,
        glow: 0.65 + this.level * 0.3,
        warm: 0.15,
        dim: 0,
        joy: 0.3,
        lean: 0.5,
      },
      waiting: { energy: 0.6, flow: 0.15, spin: 0.15, glow: 0.6, warm: 1, dim: 0, joy: 0, lean: 1 },
      happy: { energy: 0.8, flow: 0.5, spin: 0.5, glow: 0.8, warm: 0.4, dim: 0, joy: 1, lean: 0.4 },
    }[m];
    this.energy = damp(this.energy, target.energy, 3, dt);
    this.flowSpeed = damp(this.flowSpeed, target.flow, 2.5, dt);
    this.flow += this.flowSpeed * dt;
    this.spin += target.spin * dt;
    this.glow = damp(this.glow, target.glow, 4, dt);
    this.warm = damp(this.warm, target.warm, 2, dt);
    this.dim = damp(this.dim, target.dim, 1.5, dt);
    this.joy = damp(this.joy, target.joy, 5, dt);
    this.lean = damp(this.lean, target.lean, 4, dt);
    // floats: a slow bob, slower and lower asleep
    this.bob = Math.sin(this.t * (m === 'asleep' ? 0.6 : 1.3)) * (m === 'asleep' ? 0.02 : 0.035);

    for (const s of this.sparks) s.a += s.speed * dt * (0.4 + this.energy * 2.4 + (m === 'thinking' ? 1.5 : 0));

    // Ripples: speaking, with what it says; listening, with your voice; happy, once
    this.untilRipple -= dt;
    if ((m === 'speaking' || (m === 'listening' && this.level > 0.25)) && this.untilRipple <= 0) {
      this.ripples.push({ t: 0, strength: m === 'speaking' ? 0.5 + this.level * 0.5 : this.level });
      this.untilRipple = m === 'speaking' ? 0.5 - this.level * 0.25 : 0.35;
    }
    for (const r of this.ripples) r.t += dt;
    this.ripples = this.ripples.filter((r) => r.t < 1.6);

    // ── Eyes: blinks with a long tail, a gaze on a spring, never a stare ──
    const asleep = m === 'asleep';
    if (asleep) {
      this.lid = damp(this.lid, 1, 6, dt);
      this.gazeTarget = { x: 0, y: 0.3 };
    } else {
      if (this.blinking > 0) {
        this.blinking -= dt;
        this.lid = 1;
      } else {
        this.lid = damp(this.lid, 0, 30, dt);
        this.untilBlink -= dt;
        if (this.untilBlink <= 0) {
          this.blinking = Math.random() < 0.1 ? 0.3 : 0.1;
          this.untilBlink =
            Math.random() < 0.1 ? 0.35 : Math.min(9, Math.max(1.6, 2.8 - 2.2 * Math.log(1 - Math.random())));
        }
      }
      if (this.lookingAtYou > 0) {
        this.lookingAtYou -= dt;
        this.gazeTarget = { x: 0, y: 0 };
        if (this.lookingAtYou <= 0) this.gazeTarget = this.glance();
      } else if (m === 'thinking') {
        this.untilGlance -= dt;
        if (this.untilGlance <= 0) {
          this.lastSide = -this.lastSide;
          this.gazeTarget = { x: this.lastSide * 0.5, y: -0.6 };
          this.untilGlance = 1.2 + Math.random() * 1.4;
        }
      } else {
        this.untilGlance -= dt;
        if (this.untilGlance <= 0) {
          const home = Math.random() < 0.4;
          this.gazeTarget = home ? { x: 0, y: 0.15 } : this.glance();
          this.untilGlance = (m === 'restless' ? 1.5 : 3) + Math.random() * 4;
          // now and then, while it's paying attention, a look at you
          if ((m === 'attentive' || m === 'waiting' || m === 'listening') && Math.random() < 0.5)
            this.lookingAtYou = 1.4 + Math.random() * 0.8;
        }
      }
    }
    const omega = m === 'thinking' ? 7 : 5;
    const k = omega * omega;
    const c = 2 * 0.82 * omega;
    this.gazeVel = {
      x: this.gazeVel.x + (k * (this.gazeTarget.x - this.gaze.x) - c * this.gazeVel.x) * dt,
      y: this.gazeVel.y + (k * (this.gazeTarget.y - this.gaze.y) - c * this.gazeVel.y) * dt,
    };
    this.gaze = { x: this.gaze.x + this.gazeVel.x * dt, y: this.gaze.y + this.gazeVel.y * dt };
  }

  // A happy moment: sparks spin out a little and a ripple goes round
  private burst() {
    this.ripples.push({ t: 0, strength: 1 });
    for (const s of this.sparks) s.a += Math.random() * 0.8;
  }

  private paint() {
    const ctx = this.ctx;
    const S = this.canvas.width;
    const p = this.o.palette;
    const detail = this.o.size >= 64;
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, S, S);
    // The planet's radius: room around it for the ring and the energy
    // (an icon is mostly planet: no aura or sparks to leave room for, the ring just inside)
    const R = S * (detail ? 0.22 : 0.3);
    const cx = S / 2;
    const cy = S / 2 + this.bob * R * 2.2;
    const swell = 1 + this.lean * 0.04 + (this.o.mood === 'listening' ? this.level * 0.05 : 0);
    const r = R * swell;

    // ── Aura: a soft breathing glow, warm when it's waiting on you ──
    if (detail && this.glow > 0.02) {
      const breathe = 1 + Math.sin(this.t * 1.6) * 0.05;
      // (inside the canvas: cut by its edge, a glow shows a square)
      const ar = Math.min(S * 0.5, r * (1.9 + this.glow * 0.5) * breathe);
      const g = ctx.createRadialGradient(cx, cy, r * 0.6, cx, cy, ar);
      const col = mixHex(p.energy, p.warm, this.warm);
      g.addColorStop(0, rgba(col, 0.32 * this.glow * (1 - this.dim * 0.8)));
      g.addColorStop(0.5, rgba(col, 0.12 * this.glow * (1 - this.dim * 0.8)));
      g.addColorStop(1, rgba(col, 0));
      ctx.fillStyle = g;
      ctx.beginPath();
      ctx.arc(cx, cy, ar, 0, TAU);
      ctx.fill();
    }

    // ── Ripples: rings of energy going out ──
    if (detail)
      for (const rp of this.ripples) {
        const k = rp.t / 1.6;
        ctx.strokeStyle = rgba(mixHex(p.energy, p.warm, this.warm), (1 - k) * 0.5 * rp.strength);
        ctx.lineWidth = Math.max(1, r * 0.05 * (1 - k));
        ctx.beginPath();
        ctx.arc(cx, cy, Math.min(S * 0.48, r * (1.1 + k * 1.15)), 0, TAU);
        ctx.stroke();
      }

    // Sparks and ring behind the planet, then the planet, then what's in front of it
    if (detail) this.drawSparks(cx, cy, r, false);
    this.drawRing(cx, cy, r, false);
    this.drawBody(cx, cy, r);
    this.drawFace(cx, cy, r);
    this.drawRing(cx, cy, r, true);
    if (detail) this.drawSparks(cx, cy, r, true);
  }

  private drawBody(cx: number, cy: number, r: number) {
    const ctx = this.ctx;
    const p = this.o.palette;
    const dim = this.dim;
    // the sphere: lit from the upper left, as the mark is
    const g = ctx.createRadialGradient(cx - r * 0.36, cy - r * 0.42, r * 0.05, cx, cy, r * 1.15);
    g.addColorStop(0, dimHex(p.light, dim));
    g.addColorStop(0.42, dimHex(p.mid, dim));
    g.addColorStop(1, dimHex(p.deep, dim));
    ctx.fillStyle = g;
    ctx.beginPath();
    ctx.arc(cx, cy, r, 0, TAU);
    ctx.fill();
    ctx.save();
    ctx.beginPath();
    ctx.arc(cx, cy, r, 0, TAU);
    ctx.clip();
    // bands drifting across it: the planet turning
    if (this.o.size >= 40) {
      ctx.translate(cx, cy);
      ctx.rotate(TILT * 0.6);
      for (let i = 0; i < 3; i++) {
        const y = (i - 1) * r * 0.42 + r * 0.08;
        const w = r * (0.13 + i * 0.03);
        const off = ((this.spin * 0.08 + i * 0.37) % 1) * r * 0.6;
        ctx.fillStyle = rgba('#ffffff', 0.07 * (1 - dim * 0.7));
        ctx.beginPath();
        ctx.ellipse(-r * 0.3 + off, y, r * 1.3, w, 0, 0, TAU);
        ctx.fill();
      }
      ctx.setTransform(1, 0, 0, 1, 0, 0);
    }
    // the ring's shadow across the sphere, as in the mark
    ctx.translate(cx, cy);
    ctx.rotate(TILT);
    ctx.strokeStyle = rgba('#15143f', 0.35);
    ctx.lineWidth = r * 0.32;
    ctx.beginPath();
    ctx.ellipse(0, r * 0.12, r * 1.75, r * 0.6, 0, 0, Math.PI);
    ctx.stroke();
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    // rim light, and a soft highlight
    const rim = ctx.createRadialGradient(cx, cy, r * 0.75, cx, cy, r);
    rim.addColorStop(0, 'rgba(255,255,255,0)');
    rim.addColorStop(1, rgba(p.energy, 0.35 * (1 - dim)));
    ctx.fillStyle = rim;
    ctx.fillRect(cx - r, cy - r, r * 2, r * 2);
    const hl = ctx.createRadialGradient(cx - r * 0.4, cy - r * 0.48, 0, cx - r * 0.4, cy - r * 0.48, r * 0.45);
    hl.addColorStop(0, rgba('#ffffff', 0.45 * (1 - dim * 0.6)));
    hl.addColorStop(1, 'rgba(255,255,255,0)');
    ctx.fillStyle = hl;
    ctx.fillRect(cx - r, cy - r, r * 2, r * 2);
    ctx.restore();
  }

  private drawFace(cx: number, cy: number, r: number) {
    const ctx = this.ctx;
    const small = this.o.size < 40;
    // The face sits a little below centre, turned with the gaze (a head on a sphere: it shifts
    // and narrows as it turns)
    const gx = this.gaze.x * 0.32;
    const gy = this.gaze.y * 0.22;
    const fx = cx + gx * r;
    const fy = cy + r * 0.06 + gy * r;
    const squash = 1 - Math.abs(gx) * 0.5;
    const spacing = r * 0.36 * squash;
    const ink = '#17163f';
    const ew = r * (small ? 0.16 : 0.13);
    const eh = r * (small ? 0.22 : 0.2);
    const happy = this.joy > 0.5;
    const asleep = this.o.mood === 'asleep';
    // blush: soft, under the eyes
    if (!small) {
      ctx.fillStyle = rgba('#ff8fb8', 0.28 * (1 - this.dim * 0.5));
      for (const side of [-1, 1]) {
        ctx.beginPath();
        ctx.ellipse(fx + side * spacing * 1.25, fy + eh * 0.95, ew * 0.9, ew * 0.5, 0, 0, TAU);
        ctx.fill();
      }
    }
    ctx.fillStyle = ink;
    ctx.strokeStyle = ink;
    ctx.lineCap = 'round';
    for (const side of [-1, 1]) {
      const ex = fx + side * spacing;
      const ey = fy;
      if (asleep || this.lid > 0.6) {
        // closed: a content curve, corners up
        ctx.lineWidth = Math.max(1.2, ew * 0.45);
        ctx.beginPath();
        ctx.arc(ex, ey - eh * 0.15, ew * 0.95 * squash, 0.15 * Math.PI, 0.85 * Math.PI);
        ctx.stroke();
      } else if (happy) {
        // happy: arcs, ^ ^
        ctx.lineWidth = Math.max(1.2, ew * 0.5);
        ctx.beginPath();
        ctx.arc(ex, ey + eh * 0.2, ew * 0.95 * squash, 1.15 * Math.PI, 1.85 * Math.PI);
        ctx.stroke();
      } else {
        const h = eh * (1 - this.lid);
        ctx.beginPath();
        ctx.ellipse(ex, ey, ew * squash, Math.max(ew * 0.3, h), 0, 0, TAU);
        ctx.fill();
        // the sparkle that makes eyes look alive
        if (!small && h > eh * 0.5) {
          ctx.fillStyle = '#ffffff';
          ctx.beginPath();
          ctx.arc(ex - ew * 0.3 * squash + this.gaze.x * ew * 0.25, ey - h * 0.42, ew * 0.32, 0, TAU);
          ctx.fill();
          ctx.fillStyle = ink;
        }
      }
    }
    // a small smile, wider when happy or speaking
    if (!small && !asleep) {
      const open = this.o.mood === 'speaking' ? 0.5 + this.level * 0.5 : this.joy;
      ctx.lineWidth = Math.max(1, r * 0.04);
      ctx.beginPath();
      ctx.arc(fx, fy + eh * 0.9, r * (0.09 + open * 0.05), 0.15 * Math.PI, 0.85 * Math.PI);
      ctx.stroke();
    }
  }

  // The ring: a band of light around a tilted ellipse, the back half behind the planet; the energy
  // flows along it as bright dashes
  private drawRing(cx: number, cy: number, r: number, front: boolean) {
    const ctx = this.ctx;
    const p = this.o.palette;
    const rx = r * 1.75;
    const ry = r * 0.6;
    ctx.save();
    ctx.translate(cx, cy);
    ctx.rotate(TILT);
    const [from, to] = front ? [0, Math.PI] : [Math.PI, TAU];
    const lg = ctx.createLinearGradient(-rx, 0, rx, 0);
    lg.addColorStop(0, dimHex(p.ringA, this.dim));
    lg.addColorStop(1, dimHex(p.ringB, this.dim));
    ctx.strokeStyle = lg;
    ctx.lineCap = 'round';
    ctx.lineWidth = Math.max(1.2, r * 0.17);
    ctx.globalAlpha = front ? 1 : 0.75;
    ctx.beginPath();
    ctx.ellipse(0, 0, rx, ry, 0, from, to);
    ctx.stroke();
    // energy flowing along it: comets of light, each a bright head with a fading tail, glowing
    // (additive) where they pass
    if (this.o.size >= 40 && this.energy > 0.05) {
      ctx.globalAlpha = 1;
      ctx.globalCompositeOperation = 'lighter';
      const col = mixHex('#ffffff', p.warm, this.warm);
      const comets = this.o.mood === 'thinking' ? 3 : 2;
      const tail = 0.9 + this.energy * 0.6;
      const steps = 14;
      for (let k = 0; k < comets; k++) {
        const head = ((((this.flow * 0.9 + k / comets) % 1) + 1) % 1) * TAU;
        for (let i = 0; i < steps; i++) {
          const a0 = head - (tail * i) / steps;
          const a1 = head - (tail * (i + 1)) / steps;
          // only the part on this side of the planet
          const mid = ((((a0 + a1) / 2) % TAU) + TAU) % TAU;
          if (mid < Math.PI !== front) continue;
          const fade = 1 - i / steps;
          ctx.strokeStyle = rgba(col, fade * fade * Math.min(1, 0.25 + this.energy * 0.75) * (1 - this.dim * 0.7));
          ctx.lineWidth = Math.max(1, r * (0.05 + 0.08 * fade));
          ctx.beginPath();
          ctx.ellipse(0, 0, rx, ry, 0, a1, a0);
          ctx.stroke();
        }
      }
      ctx.globalCompositeOperation = 'source-over';
    }
    ctx.restore();
  }

  private drawSparks(cx: number, cy: number, r: number, front: boolean) {
    const ctx = this.ctx;
    const p = this.o.palette;
    for (const s of this.sparks) {
      const z = Math.sin(s.a);
      if (front !== z > 0) continue;
      const x0 = Math.cos(s.a) * s.rx * r;
      const y0 = z * s.ry * r;
      const x = cx + x0 * Math.cos(s.tilt) - y0 * Math.sin(s.tilt);
      const y = cy + x0 * Math.sin(s.tilt) + y0 * Math.cos(s.tilt);
      const size = r * s.size * (0.7 + (z + 1) * 0.3) * (0.6 + this.energy * 0.8);
      const a = (front ? 0.95 : 0.45) * Math.min(1, 0.2 + this.energy) * (1 - this.dim * 0.8);
      const g = ctx.createRadialGradient(x, y, 0, x, y, size * 3);
      g.addColorStop(0, rgba('#ffffff', a));
      g.addColorStop(0.3, rgba(mixHex(p.energy, p.warm, this.warm), a * 0.8));
      g.addColorStop(1, rgba(p.energy, 0));
      ctx.fillStyle = g;
      ctx.globalCompositeOperation = 'lighter';
      ctx.beginPath();
      ctx.arc(x, y, size * 3, 0, TAU);
      ctx.fill();
      ctx.globalCompositeOperation = 'source-over';
    }
  }
}

// ——— colour ———
function hex(c: string) {
  const m = /^#?([0-9a-f]{6})$/i.exec(c.trim());
  const n = m ? parseInt(m[1], 16) : 0x888888;
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}
const toHex = ([r, g, b]: number[]) =>
  '#' +
  [r, g, b]
    .map((v) =>
      Math.round(Math.max(0, Math.min(255, v)))
        .toString(16)
        .padStart(2, '0')
    )
    .join('');
function rgba(c: string, a: number) {
  const [r, g, b] = hex(c);
  return `rgba(${r},${g},${b},${Math.max(0, Math.min(1, a)).toFixed(3)})`;
}
function mixHex(a: string, b: string, t: number) {
  const x = hex(a);
  const y = hex(b);
  return toHex(x.map((v, i) => v + (y[i] - v) * t));
}
// asleep, the colours drain toward a dusky grey-blue
function dimHex(c: string, t: number) {
  if (t <= 0.01) return c;
  // Keep the original sleep hue, with less dimming against the frame’s light background.
  t *= 0.65;
  const [r, g, b] = hex(c);
  const grey = (r + g + b) / 3;
  return toHex([r, g, b].map((v) => (v + (grey - v) * 0.6 * t) * (1 - 0.4 * t)));
}
