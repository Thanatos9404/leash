import { useEffect, useRef } from "react";
import { Mesh, Program, Renderer, Triangle } from "ogl";
import { cn } from "../lib/cn";

const vertex = /* glsl */ `
attribute vec2 position;
attribute vec2 uv;
varying vec2 vUv;
void main() { vUv = uv; gl_Position = vec4(position, 0.0, 1.0); }
`;

// Domain-warped value noise blended across four palette stops, plus a light
// pointer-follow glow. Calm by design: speed is slow and contrast is low.
const fragment = /* glsl */ `
precision highp float;
uniform float uTime;
uniform vec2 uRes;
uniform vec2 uMouse;
uniform vec3 uC0; uniform vec3 uC1; uniform vec3 uC2; uniform vec3 uC3;
uniform float uIntensity;
varying vec2 vUv;

float hash(vec2 p){ return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453); }
float noise(vec2 p){
  vec2 i = floor(p), f = fract(p);
  vec2 u = f*f*(3.0-2.0*f);
  return mix(mix(hash(i), hash(i+vec2(1,0)), u.x), mix(hash(i+vec2(0,1)), hash(i+vec2(1,1)), u.x), u.y);
}
float fbm(vec2 p){ float v=0.0, a=0.5; for(int i=0;i<5;i++){ v+=a*noise(p); p*=2.02; a*=0.5; } return v; }

void main(){
  vec2 uv = vUv;
  vec2 p = uv * vec2(uRes.x/uRes.y, 1.0) * 1.6;
  float t = uTime * 0.04;
  vec2 q = vec2(fbm(p + t), fbm(p - t + 4.7));
  vec2 r = vec2(fbm(p + 3.0*q + vec2(1.7, 9.2) + t*1.3), fbm(p + 3.0*q + vec2(8.3, 2.8) - t));
  float n = fbm(p + 2.5*r);
  vec3 col = mix(uC0, uC1, smoothstep(0.0, 1.0, uv.y + (n-0.5)*0.5*uIntensity));
  col = mix(col, uC2, smoothstep(0.35, 0.95, n) * 0.55 * uIntensity);
  col = mix(col, uC3, smoothstep(0.55, 1.0, r.x) * 0.35 * uIntensity);
  float d = distance(uv, uMouse);
  col += 0.06 * uIntensity * smoothstep(0.45, 0.0, d);
  col += (hash(uv * uRes + uTime) - 0.5) * 0.025; // grain
  gl_FragColor = vec4(col, 1.0);
}
`;

function hex(h: string): [number, number, number] {
  const v = h.replace("#", "");
  const n = parseInt(v.length === 3 ? v.split("").map((c) => c + c).join("") : v, 16);
  return [((n >> 16) & 255) / 255, ((n >> 8) & 255) / 255, (n & 255) / 255];
}

/**
 * Living gradient shader for hero or CTA backgrounds. Pass four colours from
 * the project palette (top, bottom, highlight, tint). Pauses offscreen and
 * renders a single still frame under prefers-reduced-motion.
 */
export function ShaderBackdrop({
  colors = ["#9cc1e7", "#eddfd0", "#ffffff", "#f3c9a8"],
  intensity = 1,
  className,
}: {
  colors?: [string, string, string, string];
  intensity?: number;
  className?: string;
}) {
  const host = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = host.current!;
    const renderer = new Renderer({ dpr: Math.min(window.devicePixelRatio, 1.5), alpha: false, antialias: false });
    const gl = renderer.gl;
    gl.canvas.style.width = "100%";
    gl.canvas.style.height = "100%";
    el.appendChild(gl.canvas);

    const program = new Program(gl, {
      vertex,
      fragment,
      uniforms: {
        uTime: { value: 0 },
        uRes: { value: [1, 1] },
        uMouse: { value: [0.5, 0.2] },
        uC0: { value: hex(colors[0]) },
        uC1: { value: hex(colors[1]) },
        uC2: { value: hex(colors[2]) },
        uC3: { value: hex(colors[3]) },
        uIntensity: { value: intensity },
      },
    });
    const mesh = new Mesh(gl, { geometry: new Triangle(gl), program });

    const resize = () => {
      const { width, height } = el.getBoundingClientRect();
      renderer.setSize(Math.max(1, width), Math.max(1, height));
      program.uniforms.uRes.value = [width, height];
    };
    resize();
    const ro = new ResizeObserver(resize);
    ro.observe(el);

    const target = [0.5, 0.2];
    const onMove = (e: PointerEvent) => {
      const r = el.getBoundingClientRect();
      target[0] = (e.clientX - r.left) / r.width;
      target[1] = 1 - (e.clientY - r.top) / r.height;
    };
    window.addEventListener("pointermove", onMove);

    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    let visible = true;
    const io = new IntersectionObserver(([e]) => (visible = e.isIntersecting));
    io.observe(el);

    let raf = 0;
    const start = performance.now();
    const frame = (now: number) => {
      raf = requestAnimationFrame(frame);
      if (!visible) return;
      const m = program.uniforms.uMouse.value as number[];
      m[0] += (target[0] - m[0]) * 0.04;
      m[1] += (target[1] - m[1]) * 0.04;
      program.uniforms.uTime.value = (now - start) / 1000;
      renderer.render({ scene: mesh });
    };
    if (reduce) renderer.render({ scene: mesh });
    else raf = requestAnimationFrame(frame);

    return () => {
      cancelAnimationFrame(raf);
      ro.disconnect();
      io.disconnect();
      window.removeEventListener("pointermove", onMove);
      gl.getExtension("WEBGL_lose_context")?.loseContext();
      el.removeChild(gl.canvas);
    };
  }, [colors.join(), intensity]);

  return <div ref={host} aria-hidden className={cn("pointer-events-none absolute inset-0", className)} />;
}
